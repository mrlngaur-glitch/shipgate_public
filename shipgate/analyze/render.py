"""`shipgate analyze` rendering — `rich`-based terminal output. Deliberately separate
from `data.py`, same reasoning as `shipgate.report.render`: a wording change here must
never be able to change what's actually true there.

**Rendering order, and why:**

1. **Failures first, always visible, never a footnote** (hard constraint 2). A tampered
   or unreadable ledger is the single most important thing this command could report —
   burying it under a wall of healthy-looking numbers would be exactly the kind of
   quiet omission this product exists to catch elsewhere.
2. **The vacuous case renders loud and alone** (hard constraint 3) — zero ledgers found
   under any given root prints one unmistakable message and nothing that could be
   mistaken for an empty-but-clean table.
3. **Per-project sections**, each naming its own zero honestly (a project with zero
   sessions recorded says so in words, not as a blank row in a table that reads like
   "nothing wrong here").
4. **Cross-project summary last** — totals, the full 7-class verdict distribution, how
   often the gate was not green, and the most common grouped failure reasons.

Never renders `raw_payload_redacted` or any other raw payload text (hard constraint 4) —
every number here comes from `data.py`'s already-aggregated dataclasses.

Zero Claude-Code-specific imports.
"""

from __future__ import annotations

from rich.console import Console
from rich.table import Table
from rich.text import Text

from shipgate.verdicts import ALL_VERDICTS, Verdict

from .data import (
    AggregatedTokenMetric,
    AnalyzeResult,
    ProjectStats,
    TokenMetric,
    VerdictReasonCount,
    aggregate_failure_reasons,
    aggregate_token_metric,
    aggregate_verdicts_by_class,
)

#: Same verdict->style mapping as `shipgate.report.render`, duplicated rather than
#: imported — that mapping is private (`_VERDICT_STYLE`) to a different package, the
#: same private-boundary convention `shipgate.report.data` itself follows elsewhere.
_VERDICT_STYLE = {
    Verdict.VERIFIED.value: "bold green",
    Verdict.CONTRADICTED.value: "bold red",
    Verdict.UNVERIFIED_VACUOUS.value: "bold yellow",
    Verdict.UNVERIFIED.value: "dim white",
    Verdict.PENDING_RECHECK.value: "cyan",
    Verdict.QUARANTINED_FLAKY.value: "magenta",
    Verdict.TARGET_UNREACHABLE.value: "bold yellow",
}


#: Finding 2 (founder review): production writes never populate `events.tokens_*` —
#: see `TokenMetric`'s own docstring in `data.py`. `_format_token_metric` and
#: `_format_aggregated_token_metric` are the one place that decides how "not recorded"
#: reads, so every call site says it the same way rather than each inventing its own
#: wording for the same fact.
def _format_token_metric(metric: TokenMetric) -> str:
    return f"{metric.total:,}" if metric.recorded else "not recorded"


def _format_aggregated_token_metric(metric: AggregatedTokenMetric) -> str:
    if metric.recorded_project_count == 0:
        return "not recorded"
    if metric.recorded_project_count < metric.total_project_count:
        missing = metric.total_project_count - metric.recorded_project_count
        return f"{metric.total:,} (partial — not recorded for {missing} of {metric.total_project_count} project(s))"
    return f"{metric.total:,}"


def _token_line(prefix: str, tokens_input: TokenMetric, tokens_output: TokenMetric, tokens_cache_read: TokenMetric) -> Text:
    if not (tokens_input.recorded or tokens_output.recorded or tokens_cache_read.recorded):
        return Text(
            f"{prefix}: not recorded — hooks do not record token counts yet "
            "(a future transcript-ingest feature; this is a real gap, not a measured zero)."
        )
    return Text(
        f"{prefix}: {_format_token_metric(tokens_input)} in / {_format_token_metric(tokens_output)} out / "
        f"{_format_token_metric(tokens_cache_read)} cache-read"
    )


def _cross_project_token_line(
    tokens_input: AggregatedTokenMetric, tokens_output: AggregatedTokenMetric, tokens_cache_read: AggregatedTokenMetric
) -> Text:
    if not (tokens_input.recorded_project_count or tokens_output.recorded_project_count or tokens_cache_read.recorded_project_count):
        return Text(
            "Tokens across all projects: not recorded anywhere — hooks do not record token "
            "counts yet (a future transcript-ingest feature; this is a real gap, not a "
            "measured zero)."
        )
    return Text(
        f"Tokens across all projects: {_format_aggregated_token_metric(tokens_input)} in / "
        f"{_format_aggregated_token_metric(tokens_output)} out / "
        f"{_format_aggregated_token_metric(tokens_cache_read)} cache-read"
    )


def _vacuous_message(result: AnalyzeResult) -> Text:
    roots_str = ", ".join(str(r) for r in result.roots) if result.roots else "(no roots given)"
    return Text(
        "OBSERVED NOTHING — no *.shipgate/ledger.db found under any given root.\n"
        f"Roots searched: {roots_str}\n"
        "This is not a clean result: it means analyze had nothing at all to examine, "
        "not that every project is healthy. Check the --roots value(s) above.",
        style="bold yellow",
    )


def _failures_section(result: AnalyzeResult) -> Table | None:
    if not result.failures:
        return None
    table = Table(title=f"⚠ {len(result.failures)} ledger(s) could not be trusted or read", expand=True)
    table.add_column("Project")
    table.add_column("Status")
    table.add_column("Ledger")
    table.add_column("Detail", overflow="fold")
    for failure in result.failures:
        status_style = "bold red" if failure.kind == "tampered" else "bold yellow"
        table.add_row(
            failure.project_slug,
            Text(failure.kind.upper(), style=status_style),
            str(failure.ledger_path),
            failure.detail,
        )
    return table


def _verdicts_table(title: str, counts: dict[str, int]) -> Table:
    table = Table(title=title, expand=True)
    table.add_column("Verdict")
    table.add_column("Count", justify="right")
    for verdict in ALL_VERDICTS:
        count = counts.get(verdict.value, 0)
        style = _VERDICT_STYLE.get(verdict.value, "")
        table.add_row(Text(verdict.value, style=style), str(count))
    return table


def _reasons_table(title: str, reasons: list[VerdictReasonCount]) -> Table:
    table = Table(title=title, expand=True)
    table.add_column("Verdict")
    table.add_column("Reason", overflow="fold")
    table.add_column("Count", justify="right")
    if not reasons:
        table.add_row("(none recorded)", "", "0")
        return table
    for item in reasons:
        style = _VERDICT_STYLE.get(item.verdict, "")
        table.add_row(Text(item.verdict, style=style), item.reason or "(no reason recorded)", str(item.count))
    return table


def _project_section(project: ProjectStats, console: Console) -> None:
    console.print()
    console.rule(f"[bold]{project.project_slug}[/bold]  ({project.ledger_path})")

    if project.session_count == 0:
        console.print(
            Text(
                "0 sessions recorded — nothing has been observed in this ledger yet. "
                "This is reported as it is, not folded into the zeroed rows below as if it "
                "were a clean result.",
                style="bold yellow",
            )
        )
        return

    console.print(
        Text(
            f"{project.session_count} session(s) · {project.claim_count} claim(s) · "
            f"first activity {project.first_activity or 'unknown'} · "
            f"last activity {project.last_activity or 'unknown'}"
        )
    )
    events_table = Table(title="Events by record_type", expand=True)
    events_table.add_column("record_type")
    events_table.add_column("Count", justify="right")
    for record_type, count in sorted(project.events_by_record_type.items()):
        events_table.add_row(record_type, str(count))
    console.print(events_table)

    console.print(_verdicts_table("Verdicts by class", project.verdicts_by_class))

    green = project.gate_evaluations_green
    total = project.gate_evaluations_total
    not_green = total - green
    gate_style = "bold red" if total and not_green else "bold green" if total else "dim white"
    console.print(
        Text(
            f"Gate evaluations: {total} recorded, {green} green, {not_green} not green"
            if total
            else "Gate evaluations: none recorded",
            style=gate_style,
        )
    )
    if project.failure_reason_counts:
        console.print(_reasons_table("Most common failure reasons", project.failure_reason_counts))

    console.print(_token_line("Tokens", project.tokens_input, project.tokens_output, project.tokens_cache_read))


def _cross_project_section(result: AnalyzeResult, console: Console) -> None:
    console.print()
    console.rule("[bold]Cross-project[/bold]")

    if not result.projects:
        console.print(
            Text(
                "No project ledger was readable well enough to aggregate — see the "
                "failures section above.",
                style="bold yellow",
            )
        )
        return

    healthy = [p for p in result.projects if p.session_count > 0]
    if not healthy:
        console.print(
            Text(
                f"{len(result.projects)} ledger(s) read and verified, all with 0 sessions "
                "recorded — nothing has happened in any of them yet. This is reported as "
                "it is, not shown as an empty summary that could be mistaken for a clean "
                "result.",
                style="bold yellow",
            )
        )
        return

    total_sessions = sum(p.session_count for p in result.projects)
    total_claims = sum(p.claim_count for p in result.projects)
    total_gate = sum(p.gate_evaluations_total for p in result.projects)
    total_gate_green = sum(p.gate_evaluations_green for p in result.projects)
    total_gate_not_green = total_gate - total_gate_green

    console.print(
        Text(
            f"{len(result.projects)} project(s) read and verified · {total_sessions} session(s) · "
            f"{total_claims} claim(s)"
        )
    )
    console.print(
        _cross_project_token_line(
            aggregate_token_metric(result.projects, "tokens_input"),
            aggregate_token_metric(result.projects, "tokens_output"),
            aggregate_token_metric(result.projects, "tokens_cache_read"),
        )
    )
    if total_gate:
        pct_green = 100.0 * total_gate_green / total_gate
        console.print(
            Text(
                f"Gate evaluations across all projects: {total_gate} recorded, "
                f"{total_gate_green} green ({pct_green:.0f}%), {total_gate_not_green} not green",
                style="bold red" if total_gate_not_green else "bold green",
            )
        )
    else:
        console.print(Text("Gate evaluations across all projects: none recorded", style="dim white"))

    console.print(_verdicts_table("Verdicts by class, across all projects", aggregate_verdicts_by_class(result.projects)))
    console.print(
        _reasons_table(
            "Most common failure reasons, across all projects", aggregate_failure_reasons(result.projects)
        )
    )


def render_analyze(result: AnalyzeResult, console: Console) -> None:
    """Renders the full `shipgate analyze` output to `console`. Callers pass a real
    `rich.console.Console()` for terminal output, or `Console(record=True, ...)` in
    tests to capture the exact text without a real terminal."""
    if result.is_vacuous:
        console.print(_vacuous_message(result))
        return

    failures_table = _failures_section(result)
    if failures_table is not None:
        console.print(failures_table)

    for project in result.projects:
        _project_section(project, console)

    _cross_project_section(result, console)


__all__ = ["render_analyze"]

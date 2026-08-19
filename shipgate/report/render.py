"""Ship Report rendering (task 3.4, Phase 3) — `rich`-based terminal
output, the screenshot-able artifact report §8.1/§15 describe. Deliberately separate
from `data.py`: a wording change here must never be able to change what's actually
true there, and vice versa.

`rich>=13.9` was already a pinned dependency before this session, its own
`pyproject.toml` line already saying "Ship Report terminal rendering" — the intended
tool for this exact module, unused until now.

**Founder's framing this session, taken literally:** the Ship Report is the one
Launch-Month deliverable a stranger *looks at* rather than runs — every rendering
choice below is a judgment call made and stated, not left implicit. In order, and why:

1. **The banner is the screenshot moment**, so it comes first, and it has FOUR states,
   not two — `GREEN` / `RED` / `NOT EVALUATED` / `STATUS UNKNOWN` (P12) — collapsing to
   a green/red binary would force a lie in exactly the two cases most likely to be
   misread: nothing has run yet, or the gate itself couldn't run. `STATUS UNKNOWN`
   never renders alongside a stale green — if the P12 marker exists, the banner reports
   that state regardless of what the last real evaluation said, because a marker's mere
   existence means no successful evaluation has happened since that marker's own
   `first_seen` (see `data.py`'s module docstring).
2. **The P12 line sits directly under the banner**, not in a footnote, whenever the
   marker exists — the founder's own words: it must not "read reassuringly."
3. **The per-claim table gives all seven verdict classes their own visible style**, not
   a green/red collapse — report §5.4 calls the taxonomy a public interface; a render
   that silently flattens it isn't rendering the interface, it's replacing it.
4. **The blast-radius line's wording never changes based on the count** — P11(b)'s
   whole point is that zero must read exactly as uncertain as twelve, word for word.
5. **The token line states its own scope limit inline** rather than a separate caveat
   paragraph a reader can skip past — the honest half of the sentence has to be read to
   read the number at all.
6. **The receipt line is last, deliberately** — it's the "anyone can confirm this"
   guarantee (report §5.2/§15), which matters most to a reader who already trusts
   everything above enough to want to check it, not to the first-glance screenshot.

**Finding 7 fix (founder review, Session 015, a launch blocker): the banner cross-checks
itself against the claims table before it renders GREEN, with no hashing and no
`--verify` flag required.** The founder's own demonstration: tamper the `events` row a
green banner is built from (leave `verdicts` untouched) and the banner said GREEN
directly above a claims-table row reading `contradicted` — self-contradictory on its
face, catchable without any cryptography. `_banner` now refuses to render GREEN if any
claim in `data.claims` currently carries a verdict that can never legitimately coexist
with `all_dispatchable_green=True` (`_INCOMPATIBLE_WITH_GREEN`, below) — it renders an
explicit `GATE: INCONSISTENT` state instead and names the disagreeing claim. Deliberately
scoped to the false-GREEN direction only: a stored RED/exhausted evaluation next to an
all-VERIFIED claims table is not cross-checked, because a false RED is fail-closed —
inconvenient, never unsafe — and the founder's finding was specifically about a false
reassurance, not about being extra-suspicious of a true one. This is a second, independent
line of defense from `data.py`'s `verify_receipt` fix, not a replacement for it: this
catches the inconsistency on every plain `shipgate report` run, even when `--verify`
isn't passed; `--verify` additionally proves *how* the ledger was tampered, by name and
row id. The detection itself (`find_green_inconsistency`) lives in `data.py`, not here —
`shipgate.cli`'s exit code depends on the same fact, and an exit code is exactly the
"what's actually true" this module's own docstring says a wording change must never be
able to move.

Zero Claude-Code-specific imports — core-purity contract, `pyproject.toml`.
"""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from shipgate.verdicts import Verdict

from .data import ShipReportData, VerifyResult, find_green_inconsistency

#: One style per verdict class, not a green/red collapse — see module docstring, point 3.
_VERDICT_STYLE = {
    Verdict.VERIFIED.value: "bold green",
    Verdict.CONTRADICTED.value: "bold red",
    Verdict.UNVERIFIED_VACUOUS.value: "bold yellow",
    Verdict.UNVERIFIED.value: "dim white",
    Verdict.PENDING_RECHECK.value: "cyan",
    Verdict.QUARANTINED_FLAKY.value: "magenta",
    Verdict.TARGET_UNREACHABLE.value: "bold yellow",
}



def _banner(data: ShipReportData) -> Panel:
    if data.gate_unavailable is not None:
        info = data.gate_unavailable
        text = Text("GATE STATUS: UNKNOWN", style="bold black on yellow", justify="center")
        return Panel(text, subtitle=f"no successful check since {info.first_seen}", border_style="yellow")

    evaluation = data.latest_gate_evaluation
    if evaluation is None:
        text = Text("GATE: NOT EVALUATED", style="bold white on grey37", justify="center")
        return Panel(text, subtitle="no shipfile, or Stop has not run yet this session", border_style="grey50")

    if evaluation.get("should_block"):
        text = Text("GATE: RED", style="bold white on red", justify="center")
        return Panel(text, border_style="red")

    if evaluation.get("exhausted"):
        text = Text("GATE: RED — retry cap exhausted", style="bold white on red", justify="center")
        return Panel(text, subtitle="honest red, not a pass — the loop-breaker released the stop", border_style="red")

    if evaluation.get("all_dispatchable_green"):
        disagreeing = find_green_inconsistency(data)
        if disagreeing is not None:
            # Finding 7: the stored evaluation says green, but the claims table it
            # should agree with says otherwise — never render GREEN over that, no
            # --verify required. See module docstring.
            text = Text("GATE: INCONSISTENT — do not trust this report", style="bold white on red", justify="center")
            return Panel(
                text,
                subtitle=(
                    f"the stored evaluation says GREEN, but claim {disagreeing.claim_id!r} currently reads "
                    f"{disagreeing.verdict!r} — these cannot both be true. Run with --verify to check the "
                    "ledger's hash chain."
                ),
                border_style="red",
            )
        text = Text("GATE: GREEN", style="bold black on green", justify="center")
        return Panel(text, border_style="green")

    text = Text("GATE: NOT GREEN — nothing dispatchable", style="bold black on yellow", justify="center")
    return Panel(text, border_style="yellow")


def _gate_unavailable_section(data: ShipReportData) -> Text | None:
    info = data.gate_unavailable
    if info is None:
        return None
    lines = [
        (
            f"⚠ ShipGate's own gate check failed to run {info.consecutive_failures} consecutive "
            f"time(s) — a ShipGate infrastructure problem, not this project's code: {info.last_error}. "
            f"Last attempt: {info.last_seen}."
        )
    ]
    if info.exhausted:
        lines.append(
            f"Blocking was abandoned after {info.consecutive_failures} attempts to avoid an "
            "infinite loop — this needs manual investigation (see .shipgate/ledger.db), not a rerun."
        )
    return Text("\n".join(lines), style="yellow")


def _claims_table(data: ShipReportData) -> Table:
    table = Table(title="Claims", expand=True, show_lines=False)
    table.add_column("Claim")
    table.add_column("Verdict")
    table.add_column("Tier")
    table.add_column("Reason", overflow="fold")

    if not data.claims:
        table.add_row("(no claims recorded for this session)", "", "", "")
        return table

    for claim in data.claims:
        verdict_str = claim.verdict or Verdict.UNVERIFIED.value
        style = _VERDICT_STYLE.get(verdict_str, "")
        table.add_row(
            claim.text,
            Text(verdict_str, style=style),
            claim.evidence_tier or "—",
            claim.reason,
        )
    return table


def _blast_radius_line(data: ShipReportData) -> Text:
    # P11(b): identical wording whether the count is 0 or 12 — never a checkmark, never
    # "clean". See module docstring, point 4.
    return Text(
        f"Blast radius: {data.blast_radius_count} high-risk change(s) self-declared this session "
        "(self-declared only — ShipGate cannot detect an undeclared one)."
    )


def _token_line(data: ShipReportData) -> Text:
    return Text(
        f"Tokens this session: {data.tokens_input:,} in / {data.tokens_output:,} out / "
        f"{data.tokens_cache_read:,} cache-read "
        "(no price table exists anywhere in this project — no command computes a dollar cost)."
    )


def _receipt_line(data: ShipReportData, verify_result: VerifyResult | None) -> Text:
    receipt = data.ledger_receipt
    if receipt.last_id is None:
        return Text(f"Ledger receipt: no {receipt.table} rows exist yet for this project.", style="dim")

    header = (
        f"Ledger receipt ({receipt.table}): #{receipt.first_id} ({receipt.first_row_hash[:12]}…) "
        f"through #{receipt.last_id} ({receipt.last_row_hash[:12]}…) — this project's entire claim history."
    )
    if verify_result is None:
        return Text(header + "\nNot independently re-checked this run — pass --verify to confirm.", style="")
    style = "green" if verify_result.verified else "bold red"
    verified_word = "VERIFIED" if verify_result.verified else "TAMPERED"
    return Text(f"{header}\n--verify: {verified_word} — {verify_result.detail}", style=style)


def render_ship_report(data: ShipReportData, console: Console, *, verify_result: VerifyResult | None = None) -> None:
    """Renders the full Ship Report to `console`. Callers pass a real
    `rich.console.Console()` for terminal output, or `Console(record=True, ...)` in
    tests to capture the exact text without a real terminal."""
    console.print(_banner(data))

    gate_unavailable_text = _gate_unavailable_section(data)
    if gate_unavailable_text is not None:
        console.print(gate_unavailable_text)

    console.print()
    console.print(_claims_table(data))
    console.print()
    console.print(_blast_radius_line(data))
    console.print(_token_line(data))
    console.print()
    console.print(_receipt_line(data, verify_result))


__all__ = ["render_ship_report"]

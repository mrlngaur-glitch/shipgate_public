"""ShipGate CLI entrypoint.

Commands land per the phase plan:
  analyze             -> founder-authorized scope addition, built this session (see
                         PHASE_PLAN.md's decisions log). NOT report §8.1 Week 1's
                         original ingest/dollar-cost `analyze` -- that one is still not
                         built (no price tables exist; see shipgate.report.data's own
                         module docstring). This is a read-only cross-project ledger
                         aggregator: shipgate analyze --roots <dir> globs
                         <dir>/*/.shipgate/ledger.db, hash-chain-verifies each one, and
                         reports per-project and cross-project statistics. Never writes
                         to any project ledger; see shipgate.analyze's module docstring
                         for the five hard constraints this was built against.
  init                -> Phase 3   report §8.1 Week 3 (built, Session 009)
  declare-task-class  -> Phase 3   Gate B condition 5's wiring; a thin wrapper around the already-tested
                         `shipgate.gate.blast_radius.record_high_risk_change`. Not a
                         hook — the agent runs this as an ordinary tool call, per its
                         own `CLAUDE.md` instructions (see `shipgate/discipline/
                         templates.py`).
  doctor              -> Phase 3   built, Session 009
  report / status     -> Phase 3   built, Session 014
  evolve              -> F5        not yet built

This module exists so the packaging entrypoint resolves. Unbuilt commands say so and
exit non-zero, rather than printing a reassuring message — report §5.2: a green that
observed nothing is not a green; the same honesty applies to a CLI that did nothing.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import typer
from rich.console import Console

from shipgate.analyze import analyze as run_analyze
from shipgate.analyze import render_analyze
from shipgate.discipline import (
    InitResult,
    NoSessionRecordedError,
    current_session_id,
    open_project_ledger,
    run_init,
    utc_now_iso,
)
from shipgate.doctor import run_doctor
from shipgate.gate.blast_radius import record_high_risk_change
from shipgate.ledger.writer import LedgerWriter
from shipgate.report import (
    find_green_inconsistency,
    gather_report_data,
    read_gate_unavailable_marker,
    render_ship_report,
    verify_receipt,
)
from shipgate.shipfile import ShipfileSyntaxError, ShipfileValidationError, load_shipfile

app = typer.Typer(
    name="shipgate",
    help="The independent completion gate for AI coding agents.",
    no_args_is_help=True,
)


def _ensure_utf8_streams() -> None:
    """**Founder review finding, Gate C blocker, Session 011.** this CLI's own output
    uses U+2014 (em dash) throughout. Python's default stdout/stderr text encoding on
    Windows is the process locale's codepage — cp1252 encodes U+2014, but cp437 and
    cp932 (the *default* codepage on a Japanese-locale Windows machine) do not. On
    those codepages, `shipgate init` ran its entire job — wrote a real shipfile, a real
    `CLAUDE.md`, a real `.claude/settings.json` — then crashed with a raw
    `UnicodeEncodeError` while printing its own success report, stdout empty.

    **Blast radius, corrected (Session 012, founder finding, supersedes the original
    Session 011 framing):** the original wording here claimed
    "the stranger's first command appears to fail after silently succeeding" without
    qualification. That overstates it. The founder's own reproduction piped stdout, and
    since PEP 528 (Python 3.6+), a Windows process attached to a **real interactive
    console** talks to it through `_io._WindowsConsoleIO` (confirmed present in this
    project's own CPython 3.12 build) — `WriteConsoleW` over UTF-16, bypassing the
    active codepage entirely. A stranger typing `shipgate init` at an interactive
    cp932 prompt most likely never hit this crash at all. The **confirmed** blast
    radius is stdout/stderr that is **redirected, piped, or otherwise not a real
    console** — a CI runner, a wrapper script, a subprocess launch, an agent harness
    piping this CLI's output for its own use, or (Finding 5's discovery, Session 012)
    this project's own `communicate()`-based checker subprocesses reading
    output back for a verdict. That set is still real and still lands at Gate C's first-
    contact moment for any non-interactive invocation, which is why the fix below is
    still correct and still shipped — but it is not, as originally claimed, every
    stranger's very first command. Neither the founder's harness nor this one can spawn
    a literal interactive Windows console to test that boundary directly; the correction
    above rests on CPython's documented, source-level PEP 528 behavior, not a live
    interactive repro either side has actually run.

    **Fixed as a class, not an instance:** reconfiguring `stdout`/`stderr` to UTF-8
    explicitly, once, at the single choke point every subcommand passes through (this
    callback), makes every character this CLI ever prints — today's em dashes, any
    future character — encode-safe regardless of the console's active codepage. This is
    not "delete the em dashes"; a new non-ASCII character introduced later is covered by
    construction, not by remembering to avoid it. `hasattr` guards the case where
    `stdout`/`stderr` has already been replaced with something that doesn't support
    `reconfigure` (e.g. a test harness's own capture stream) — reconfiguring is skipped
    there rather than raising, since such a stream is usually already UTF-8-native.

    **What this does not promise:** on a real Windows console still displaying with a
    narrow codepage, the *bytes* written are always valid UTF-8 (so the process never
    crashes), but the *glyphs the user sees* may still render as mojibake unless their
    terminal is also configured for UTF-8 — a display-quality concern, not a crash, and
    outside what this function can control from inside the process.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


@app.callback()
def main() -> None:
    """ShipGate — the agent doesn't get to grade its own homework."""
    _ensure_utf8_streams()


def _print_outcome(label: str, outcome) -> None:
    marker = {"written": "+", "merged": "~", "unchanged": "=", "refused": "!"}[outcome.action]
    typer.echo(f"[{marker}] {label}: {outcome.action} — {outcome.reason}")


@app.command()
def init(
    project_dir: Path = typer.Option(Path("."), "--project-dir", help="Project to initialize."),
    intent_summary: str = typer.Option(
        None,
        "--intent-summary",
        prompt="One-line summary of what this project is for",
        help="Fills shipfile.yaml's intent.summary.",
    ),
    test_command: str = typer.Option(
        None,
        "--test-command",
        prompt="Command that runs this project's test suite",
        help="Fills the generated tests-pass done_condition.",
    ),
) -> None:
    """The minimal interview: writes shipfile.yaml, CLAUDE.md, and .claude/settings.json.

    Never overwrites an existing file — merges additively or refuses and says exactly
    what it found. See shipgate.discipline.init's module docstring for the full policy.

    Exit codes: 0 = nothing needs attention, including the routine case where
    shipfile.yaml already existed and was correctly left alone. 1 = CLAUDE.md or
    settings.json could not be safely merged (a real problem: malformed JSON, broken
    markers, an unexpected shape) — see InitResult.needs_user_attention.
    """
    project_dir = project_dir.resolve()
    result: InitResult = run_init(project_dir, intent_summary=intent_summary, test_command=test_command)

    _print_outcome("shipfile.yaml", result.shipfile)
    _print_outcome("CLAUDE.md", result.claude_md)
    _print_outcome(".claude/settings.json", result.settings_json)

    if result.needs_user_attention:
        typer.echo(
            "\nCLAUDE.md and/or .claude/settings.json could not be safely merged above — "
            "nothing was overwritten. Resolve each refusal's stated reason and re-run "
            "shipgate init.",
            err=True,
        )
        raise typer.Exit(code=1)

    if result.shipfile.action == "refused":
        # Founder review finding, Session 011: this is init's own by-design safe
        # default (never touch a real shipfile), not a failure -- the common case for
        # re-running init at all is to repair hooks/CLAUDE.md, e.g. after Finding 1's
        # moved-venv scenario, without wanting the shipfile touched. Exit 0.
        typer.echo(
            "\nshipfile.yaml already existed and was correctly left untouched. "
            "Everything else above is up to date."
        )
        return

    typer.echo("\nDone. Review the generated files, then start your agent session normally.")


@app.command("declare-task-class")
def declare_task_class(
    task_class: str = typer.Argument(..., help="A key from this project's shipfile task_classes block."),
    description: str = typer.Argument(..., help="One-line description of the change about to be made."),
    project_dir: Path = typer.Option(Path("."), "--project-dir"),
    override_reason: str = typer.Option(
        None,
        "--override-reason",
        help="Required to record a change past this session's blast-radius budget.",
    ),
) -> None:
    """Records a high-risk change against this session's blast-radius budget
    (session_policy.max_high_risk_changes_per_session). This is a self-declaration —
    ShipGate cannot detect an undeclared high-risk change; only what is declared here
    is ever counted."""
    project_dir = project_dir.resolve()
    try:
        shipfile = load_shipfile(project_dir / "shipfile.yaml")
    except FileNotFoundError:
        typer.echo(f"no shipfile.yaml found at {project_dir} — run shipgate init first.", err=True)
        raise typer.Exit(code=2) from None
    except (ShipfileSyntaxError, ShipfileValidationError) as exc:
        typer.echo(f"shipfile.yaml at {project_dir} is invalid: {exc}", err=True)
        raise typer.Exit(code=2) from None

    with open_project_ledger(project_dir) as writer:
        try:
            session_id = current_session_id(writer, project_dir)
        except NoSessionRecordedError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=3) from None

        try:
            result = record_high_risk_change(
                shipfile,
                session_id,
                writer,
                task_class=task_class,
                description=description,
                now=utc_now_iso(),
                override_reason=override_reason,
            )
        except ValueError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=2) from None

    typer.echo(result.reason)
    if result.refused:
        raise typer.Exit(code=1)


@app.command()
def doctor(
    project_dir: Path = typer.Option(Path("."), "--project-dir"),
) -> None:
    """Checks shipfile.yaml's done_conditions for references to files, scope paths, or
    grep patterns that no longer resolve to anything real — run at init, weekly, and
    before evolve. Not part of the gate; this never blocks a Stop.

    Exit codes: 0 = at least one condition checked, none stale. 1 = stale reference(s)
    found. 2 = no/invalid shipfile. 3 = nothing checked at all (every done_condition is
    a type doctor doesn't inspect) — distinct from 0 on purpose; this is never printed
    as "Clean" (see shipgate.doctor.check.DoctorReport.is_vacuous)."""
    project_dir = project_dir.resolve()
    try:
        shipfile = load_shipfile(project_dir / "shipfile.yaml")
    except FileNotFoundError:
        typer.echo(f"no shipfile.yaml found at {project_dir} — run shipgate init first.", err=True)
        raise typer.Exit(code=2) from None
    except (ShipfileSyntaxError, ShipfileValidationError) as exc:
        typer.echo(f"shipfile.yaml at {project_dir} is invalid: {exc}", err=True)
        raise typer.Exit(code=2) from None

    report = run_doctor(shipfile, project_dir)

    if report.skipped_condition_types:
        typer.echo(
            "Not checked (condition types doctor doesn't inspect — see shipgate.doctor.check's "
            f"module docstring): {', '.join(report.skipped_condition_types)}"
        )
    typer.echo(f"Checked {len(report.checked_condition_ids)} condition(s).")

    # Founder review finding, Session 010: zero checked must never render as "Clean" —
    # a shipgate-init-generated shipfile's only condition (tests_pass) isn't a type
    # doctor checks, so "Checked 0" -> "Clean" was a real green on zero observations,
    # on the out-of-the-box path. This branch is checked first and deliberately never
    # uses the word "clean" — see DoctorReport.is_vacuous's own docstring.
    if report.is_vacuous:
        typer.echo(
            "\nNothing checked — every done_condition in this shipfile is a type doctor "
            "doesn't inspect. This is NOT a clean result: it means doctor had no applicable "
            "condition to examine, not that the shipfile is healthy. Add a file_exists, "
            "forbidden_pattern_absent, or inventory_complete condition for doctor to have "
            "something real to check."
        )
        raise typer.Exit(code=3)

    if report.stale:
        typer.echo(f"\n{len(report.stale)} stale reference(s):")
        for ref in report.stale:
            typer.echo(f"  - [{ref.condition_id}] {ref.condition_type}.{ref.field}={ref.value!r}: {ref.reason}")
        raise typer.Exit(code=1)

    typer.echo(f"Clean — {len(report.checked_condition_ids)} reference(s) checked, none stale.")


@app.command()
def analyze(
    roots: list[Path] = typer.Option(
        ...,
        "--roots",
        help=(
            "Directory whose immediate subdirectories are scanned for */.shipgate/ledger.db "
            "(repeatable -- pass --roots more than once to search multiple directories)."
        ),
    ),
) -> None:
    """Read-only cross-project ledger aggregation and statistics — see
    shipgate.analyze's module docstring for the full design.

    Never opens any project ledger for write, never creates .shipgate/ anywhere, no
    central database — every project's ledger is read independently, hash-chain-
    verified, and stays exactly as it was found. A tampered or unreadable ledger is
    reported by name, never silently skipped or folded into the totals.

    Exit codes: 0 = at least one ledger found, read, and verified (its own gate-not-
    green statistics are informational content, not a command failure). 1 = one or
    more ledgers found were tampered or unreadable. 3 = nothing found at all under any
    given --roots value — the vacuous-pass rule, applied to this command too."""
    result = run_analyze([r.resolve() for r in roots])
    render_analyze(result, Console())

    if result.is_vacuous:
        raise typer.Exit(code=3)
    if result.failures:
        raise typer.Exit(code=1)


def _resolve_session(project_dir: Path, writer, session: str | None) -> str:
    if session is not None:
        return session
    try:
        return current_session_id(writer, project_dir)
    except NoSessionRecordedError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from None


def _open_ledger_or_report_p12(project_dir: Path) -> LedgerWriter:
    """`report`/`status`'s own analogue of `shipgate/hooks/stop.py`'s P12 handling.

    **Found live while demonstrating this exact scenario, not reported by the founder:**
    a corrupt ledger used to crash `shipgate report` into a bare Python traceback —
    exactly the confusing-first-contact failure this project exists to prevent, this
    time self-inflicted by the reporting tool itself rather than the gate. A broken
    ledger must never be reported as an unhandled exception; if a P12 marker exists, its
    contents are precisely what a stranger needs to see here, not a stack trace they
    can't act on."""
    try:
        return open_project_ledger(project_dir)
    except sqlite3.Error as exc:
        marker = read_gate_unavailable_marker(project_dir)
        if marker is not None:
            typer.echo(
                f"GATE STATUS: UNKNOWN — ShipGate's own ledger could not be opened just now "
                f"({exc}), consistent with the {marker.consecutive_failures} consecutive "
                f"failure(s) already recorded by the gate itself (last: {marker.last_error}). "
                "This is a ShipGate infrastructure problem, not this project's code — see "
                ".shipgate/ledger.db.",
                err=True,
            )
        else:
            typer.echo(
                f"ShipGate's own ledger could not be opened: {exc}. This is a ShipGate "
                "infrastructure problem, not necessarily anything wrong with this project — "
                "see .shipgate/ledger.db.",
                err=True,
            )
        raise typer.Exit(code=3) from None


@app.command()
def status(
    project_dir: Path = typer.Option(Path("."), "--project-dir"),
    session: str = typer.Option(None, "--session", help="Defaults to the most recently recorded session."),
) -> None:
    """Quick, plain — one line per condition from the most recently RECORDED gate
    evaluation (not a live re-run; see `shipgate.report.data`'s module docstring for
    why). Closer to `git status` than to a screenshot; see `report` for the full
    artifact. Exit codes: 0 green, 1 red/not-green, 2 no session or no evaluation
    recorded yet, 3 the gate could not run at all (ShipGate's own ledger is broken —
    see .shipgate/gate_unavailable.json)."""
    project_dir = project_dir.resolve()
    with _open_ledger_or_report_p12(project_dir) as writer:
        session_id = _resolve_session(project_dir, writer, session)
        data = gather_report_data(project_dir, writer, session_id)

    if data.gate_unavailable is not None:
        info = data.gate_unavailable
        typer.echo(
            f"GATE STATUS: UNKNOWN — ShipGate's own gate check failed to run "
            f"{info.consecutive_failures} consecutive time(s), a ShipGate infrastructure "
            f"problem, not this project's code: {info.last_error}. Last attempt: {info.last_seen}.",
            err=True,
        )
        raise typer.Exit(code=3)

    evaluation = data.latest_gate_evaluation
    if evaluation is None:
        typer.echo("no gate evaluation recorded yet for this session (no shipfile, or Stop hasn't run).")
        raise typer.Exit(code=2)

    for cond in evaluation.get("conditions", []):
        if cond.get("dispatchable"):
            typer.echo(f"[{cond['verdict']}] {cond['id']}: {cond['reason']}")

    if evaluation.get("should_block"):
        typer.echo("\nGATE: RED")
        raise typer.Exit(code=1)
    if evaluation.get("exhausted"):
        typer.echo("\nGATE: RED (retry cap exhausted — honest red, not a pass)")
        raise typer.Exit(code=1)
    if evaluation.get("all_dispatchable_green"):
        # Finding 7 fix — same cross-check as `report`'s banner: a stored green must
        # agree with the (independently read) claims table before this command says so.
        disagreeing = find_green_inconsistency(data)
        if disagreeing is not None:
            typer.echo(
                f"\nGATE: INCONSISTENT — do not trust this output. The stored evaluation says GREEN, but "
                f"claim {disagreeing.claim_id!r} currently reads {disagreeing.verdict!r} — run "
                "'shipgate report --verify' to check the ledger's hash chain.",
                err=True,
            )
            raise typer.Exit(code=1)
        typer.echo("\nGATE: GREEN")
        return
    typer.echo("\nGATE: NOT GREEN (nothing dispatchable)")
    raise typer.Exit(code=1)


@app.command()
def report(
    project_dir: Path = typer.Option(Path("."), "--project-dir"),
    session: str = typer.Option(None, "--session", help="Defaults to the most recently recorded session."),
    verify: bool = typer.Option(
        True,
        "--verify/--no-verify",
        help=(
            "Re-derive the entire ledger hash chain for real and confirm the embedded receipt "
            "matches. On by default — measured at ~7-8µs/row (69ms for a 9,000-row ledger); use "
            "--no-verify only for speed on an unusually large ledger or a tight loop."
        ),
    ),
) -> None:
    """The screenshot-able Ship Report — full verdict taxonomy per claim, the
    blast-radius line (self-declared only, never a clean pass), a warning whenever the
    gate itself couldn't run, token counts, and a self-verifying ledger receipt. See
    `shipgate.report`'s module docstrings for the reasoning behind every rendering
    choice. Exit codes mirror `status`.

    **`--verify` defaults ON.** This is the one artifact a stranger looks at rather
    than runs — an unverified receipt sitting under a banner by default asks the
    reader to remember to add a flag before trusting what they're looking at. Measured
    cost before deciding, not assumed: ~7-8µs/row, 69ms for a 9,000-row ledger
    (`events`+`claims`+`verdicts` combined) built directly via `LedgerWriter`, no
    subprocess overhead. Cheap enough that "off by default, remember to turn it on"
    was the wrong default; `--no-verify` stays available for an unusually large ledger
    or a script calling this in a loop. `shipgate status` (the quick, `git
    status`-like surface, not the screenshot) deliberately keeps no `--verify` flag at
    all — it's meant to stay the smaller, faster surface, precisely so `report` is the
    one worth this cost."""
    project_dir = project_dir.resolve()
    with _open_ledger_or_report_p12(project_dir) as writer:
        session_id = _resolve_session(project_dir, writer, session)
        data = gather_report_data(project_dir, writer, session_id)
        verify_result = verify_receipt(writer.connection, data.ledger_receipt) if verify else None

    render_ship_report(data, Console(), verify_result=verify_result)

    if data.gate_unavailable is not None:
        raise typer.Exit(code=3)
    evaluation = data.latest_gate_evaluation
    if evaluation is None:
        raise typer.Exit(code=2)
    if evaluation.get("should_block") or evaluation.get("exhausted"):
        raise typer.Exit(code=1)
    if not evaluation.get("all_dispatchable_green"):
        raise typer.Exit(code=1)
    # Finding 7 fix: a stored green whose own claims table disagrees is never exit 0,
    # matching the banner's own refusal to render GATE: GREEN over the same disagreement
    # (`render.py`'s `_banner`) — the exit code and the screenshot must never disagree
    # with each other about whether this report can be trusted.
    if find_green_inconsistency(data) is not None:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()

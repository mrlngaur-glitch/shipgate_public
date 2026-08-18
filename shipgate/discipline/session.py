"""Finds the ledger and the "current session" for `shipgate declare-task-class` (task
3.1 / Gate B condition 5's wiring).

**Why "most recently recorded session", not a session id the CLI is handed directly:**
`shipgate declare-task-class` runs as an ordinary tool call the agent makes (a Bash
invocation), not as a Claude Code hook — unlike `shipgate/hooks/*.py`, it has no hook
stdin JSON handing it a `session_id`. What it can rely on instead: Claude Code's
`PreToolUse` hook fires and completes *before* any tool call executes (that's the hook
lifecycle's own contract), and `PreToolUse` already writes this session's `sessions` row
via `shipgate.hooks._common.ensure_session` the first time it fires. So by the time an
agent is running `shipgate declare-task-class` at all, the most recently inserted row in
this project's own `.shipgate/ledger.db` `sessions` table is, in ordinary single-session
usage, this session. **Stated limitation, not silently assumed airtight:** two ShipGate
sessions running concurrently against the same project directory would attribute a
declaration to whichever session's hook fired last — a real but narrow edge case this
module doesn't attempt to solve; concurrent sessions against one project directory
aren't the primary supported shape here, and this is flagged rather than hidden.

Deliberately does not import `shipgate.hooks._common` (that module is private to the
hooks package by its own leading underscore) — the three lines needed here
(`.shipgate/ledger.db`, create parents, open a `LedgerWriter`) are duplicated rather than
reaching into another package's private helper.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from shipgate.ledger.writer import LedgerWriter

DEFAULT_LEDGER_RELPATH = Path(".shipgate") / "ledger.db"


def utc_now_iso() -> str:
    """`YYYY-MM-DDTHH:MM:SSZ` — the exact format `shipgate.hooks._common.utc_now_iso`
    uses, kept consistent rather than inventing a second convention. Duplicated (not
    imported from `shipgate.hooks._common`, which is private to that package by its own
    leading underscore) for the same reason `open_project_ledger` below is duplicated
    rather than reached-into."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class NoSessionRecordedError(RuntimeError):
    """`.shipgate/ledger.db` has no `sessions` row yet. A declaration cannot be
    attributed to a session that doesn't exist.

    **Requirement (c), founder finding this session: this message used to assert more
    than this code actually knows.** It read as "no hook has fired yet" — true for a
    genuinely fresh project, but also the exact same rendering a project would get if
    hooks HAD been firing repeatedly and refusing every time because Claude Code handed
    them a `cwd` that didn't resolve to this directory
    (`shipgate.hooks._common.ProjectRootUnresolvableError`). That failure is loud on
    stderr at the moment it happens (see that error's docstring), but by construction —
    see requirement (a) — leaves no durable trace anywhere near `project_root`, because
    if `cwd` didn't resolve, ShipGate structurally never learned where `project_root`
    even was. There is no ledger row, no marker file, nothing this function can read
    later to tell the two cases apart. Rather than let the message quietly claim
    certainty it doesn't have, it now names both possibilities and says what to check
    for the second one — an honest "I don't know which" beats a confident guess."""


def open_project_ledger(project_root: Path) -> LedgerWriter:
    """Opens (creating if needed) `<project_root>/.shipgate/ledger.db` — the same path
    `shipgate.hooks._common.open_project_ledger` uses, so a CLI-issued declaration and a
    hook-issued `gate_evaluation` event land in the same database."""
    db_path = project_root / DEFAULT_LEDGER_RELPATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return LedgerWriter(db_path, corpus_root=project_root)


def current_session_id(writer: LedgerWriter, project_root: Path) -> str:
    """Raises `NoSessionRecordedError` if no session has ever been recorded — never
    guesses or fabricates a session id."""
    row = writer.connection.execute(
        "SELECT session_id FROM sessions ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    if row is None:
        raise NoSessionRecordedError(
            f"no ShipGate session has been recorded yet for {project_root}. Two possible "
            "reasons, and this code cannot tell you which: (1) no Claude Code hook has fired "
            "here yet (shipgate init wires up PreToolUse/PostToolUse/Stop; the first tool call "
            "after that should create one), or (2) hooks HAVE been firing but were refused "
            "because the cwd Claude Code handed them didn't resolve to this directory — check "
            "recent Claude Code hook stderr output for 'the gate did not run for this event', "
            "and confirm this is the same directory your Claude Code session is actually "
            "running in."
        )
    return row[0]


__all__ = [
    "DEFAULT_LEDGER_RELPATH",
    "NoSessionRecordedError",
    "current_session_id",
    "open_project_ledger",
    "utc_now_iso",
]

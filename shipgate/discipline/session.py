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
    """`.shipgate/ledger.db` has no `sessions` row yet — no ShipGate hook has fired for
    this project. A declaration cannot be attributed to a session that doesn't exist."""


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
            f"no ShipGate session has been recorded yet for {project_root} — has a Claude "
            "Code hook fired at least once this session? (shipgate init wires up PreToolUse/"
            "PostToolUse/Stop; the first tool call after that should create one)"
        )
    return row[0]


__all__ = [
    "DEFAULT_LEDGER_RELPATH",
    "NoSessionRecordedError",
    "current_session_id",
    "open_project_ledger",
    "utc_now_iso",
]

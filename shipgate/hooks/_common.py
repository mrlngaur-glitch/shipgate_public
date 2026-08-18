"""Shared machinery for the three hook entrypoints — stdin JSON parsing, the
project-local ledger (`.shipgate/ledger.db`, never the dogfood corpus), and the
timestamp convention already established in `shipgate.ledger`'s own tests.

Confirmed hook stdin fields (verified against Claude Code's own hooks reference before
writing this module, not assumed from memory):
`session_id`, `cwd`, `hook_event_name`, `transcript_path`, `permission_mode` are common
to every hook event; `PreToolUse`/`PostToolUse` add `tool_name`, `tool_input`,
`tool_use_id`; `PostToolUse` additionally adds `tool_result`; `Stop` adds
`stop_hook_active`. No hook event includes a `model` field or a timestamp — both are
transcript-file facts, not hook-input facts, so `events.model` is left `None` for every
row this package writes; enriching it is JSONL-ingest territory (task 1.5), not this
package's job.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shipgate.ledger.writer import LedgerWriter

#: Not one of `sessions`/`subagent` per se — a live hook has no way to tell, from the
#: hook JSON alone, whether it fired inside the top-level session or a sub-agent's own
#: context (Claude Code's hook-to-subagent interaction isn't confirmed as of this
#: writing). Defaulting to "session" is a stated simplification, not a silent guess —
#: see the module docstring in `shipgate/hooks/__init__.py`.
DEFAULT_TRANSCRIPT_TIER = "session"


class HookInputError(ValueError):
    """The hook's stdin wasn't usable JSON, or was missing a field every entrypoint
    needs (`session_id`, `cwd`). Callers treat this as "skip, don't crash" — see each
    entrypoint's `run()`."""


class ProjectRootUnresolvableError(HookInputError):
    """**Founder finding, this session: hooks trusted the payload's `cwd` absolutely.**
    Reproduced live: a `cwd` that does not exist (or exists but isn't a directory) made
    `open_project_ledger`'s old unconditional `mkdir(parents=True, exist_ok=True)`
    silently create an entire new directory tree — anywhere on disk — and write a real
    45,056-byte ledger there, while the real project got no `.shipgate/` at all. Both
    `PreToolUse` and `Stop` exited 0 with empty stdout AND empty stderr — the gate was
    completely absent and every observable signal said fine. Same trust-boundary class
    as the shipfile-integrity gap already parked to F1 (PARKING_LOT.md, 2026-08-15): the
    gate trusts an external, unverified piece of data absolutely. That one is the
    shipfile; this one is the hook payload.

    Raised the moment `Path(cwd)` is found not to already be a real directory, before a
    single filesystem write is attempted. A hook must never create a project root that
    doesn't already exist — an unresolvable `cwd` is a refusal to proceed, not an
    invitation to invent one. Every caller still fails OPEN on the decision (the
    loop-breaker rule stands, exit 0, the stop/tool-call itself is never blocked by
    this), but never SILENTLY: see each entrypoint's `run()` for the stderr line this
    produces. `shipgate/hooks/stop.py`'s P12 `gate_unavailable.json` escalation
    mechanism does NOT apply to this error — that marker lives at
    `<cwd>/.shipgate/gate_unavailable.json`, and if `cwd` itself doesn't resolve there is
    structurally nowhere legitimate to write it without repeating the exact bug this
    class exists to stop. Named, accepted residual gap, the same shape as P12's own
    "if `.shipgate/` itself is locked down" gap: this failure is loud on stderr every
    single time, with no durable escalation counter, because there is no safe place to
    keep one."""


def read_hook_input(stdin_text: str | None) -> dict[str, Any]:
    """Parse the hook's stdin JSON. `stdin_text=None` reads real stdin (production);
    tests pass a literal string so no subprocess/stdin plumbing is needed to exercise
    the parsing and ledger-writing logic."""
    text = stdin_text if stdin_text is not None else sys.stdin.read()
    if not text.strip():
        raise HookInputError("empty hook input on stdin")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HookInputError(f"hook input is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise HookInputError(f"hook input must be a JSON object, got {type(data).__name__}")
    for required in ("session_id", "cwd"):
        if not data.get(required):
            raise HookInputError(f"hook input is missing required field {required!r}")
    return data


def ensure_utf8_streams() -> None:
    """**Investigated as a hypothesis alongside `shipgate.cli`'s confirmed Gate C
    blocker (Session 011), then precisely scoped rather than assumed to be a second
    instance of the identical bug.** Every hook entrypoint's stderr messages use U+2014
    (em dash), several written **outside any try/except** (they *are* the handler for a
    routine condition — missing hook input, an invalid shipfile) — the same shape that
    crashed `shipgate.cli`'s stdout writes. Verified directly rather than assumed
    symmetric: `sys.stdout`'s default error handler on a narrow codepage is `'strict'`
    (crashes) — confirmed by the CLI's own real crash — but **`sys.stderr`'s default
    error handler is `'backslashreplace'`, a CPython built-in, independent of platform
    or locale.** A negative-control test (temporarily disabling this function) proved
    empirically that none of this package's existing stderr writes actually crash on
    cp437/cp932 — `sys.stderr` already degrades a non-encodable character to `\\uXXXX`
    text instead of raising. `stop.py`'s one stdout write is `json.dumps(...)`, which
    ASCII-escapes non-ASCII by construction (`ensure_ascii=True` default) and was never
    at risk either. **This function is kept as defense-in-depth, not as the fix for a
    proven live crash in this package:** it makes both streams explicitly UTF-8 rather
    than relying on an easy-to-forget CPython default, so a *future* stderr write added
    without this context in mind (or any stdout write ever added to a hook) is already
    covered. Wrapped in its own `try`/`except`, consistent with this package's zero-
    crash discipline, since even a defensive fix must never be what breaks a hook."""
    try:
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001, S110 — silent by design: logging here could itself
        pass  # write to the very stream this function exists to make safe


def utc_now_iso() -> str:
    """`YYYY-MM-DDTHH:MM:SSZ` — the exact format already used throughout
    `tests/unit/test_ledger.py`'s fixtures, kept consistent rather than inventing a
    second timestamp convention."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def open_project_ledger(cwd: str) -> LedgerWriter:
    """Opens (creating if needed) `<cwd>/.shipgate/ledger.db`. `corpus_root=Path(cwd)`
    so `sessions.source_dir` can honestly be `"."` — a live hook-originated session
    isn't an ingest of some external corpus; it *is* the corpus root, trivially, of
    itself. This function never references `shipgate.ledger.paths.DEFAULT_CORPUS_ROOT`
    or `~/.claude/projects` in any way — the dogfood corpus is not merely convention-
    protected here, it is unreachable from this code path by construction.

    **Root-cause fix, this session: refuses to create `cwd` itself.** `.shipgate/` is
    created under `cwd` (expected — that's this project's own state directory, not the
    project root), but `cwd` must already exist as a real directory *before* that
    happens. The old code's `mkdir(parents=True, exist_ok=True)` made no such
    distinction — given an unresolvable `cwd` it happily built out the whole missing
    tree from wherever `cwd` started, anywhere on disk, and wrote a real ledger there.
    See `ProjectRootUnresolvableError`'s docstring for the full reproduction and
    reasoning."""
    root = Path(cwd)
    if not root.is_dir():
        raise ProjectRootUnresolvableError(
            f"hook payload cwd {cwd!r} does not exist (or is not a directory) — refusing to "
            "create a project root ShipGate did not find"
        )
    db_path = root / ".shipgate" / "ledger.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return LedgerWriter(db_path, corpus_root=root)


def ensure_session(writer: LedgerWriter, *, session_id: str, cwd: str) -> None:
    """Idempotent: writes a `sessions` row the first time this `session_id` is seen,
    does nothing on every hook fired afterward in the same session. Reads via the raw
    connection (`LedgerWriter.connection` is documented as available to callers that
    aren't bypassing the append-only triggers — a `SELECT` isn't a bypass)."""
    exists = writer.connection.execute(
        "SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    if exists is None:
        writer.insert_session(
            session_id=session_id,
            project_slug=Path(cwd).name or "project",
            source_dir=".",
            cwd=cwd,
        )


__all__ = [
    "DEFAULT_TRANSCRIPT_TIER",
    "HookInputError",
    "ProjectRootUnresolvableError",
    "ensure_session",
    "ensure_utf8_streams",
    "open_project_ledger",
    "read_hook_input",
    "utc_now_iso",
]

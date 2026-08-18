"""`PreToolUse` hook entrypoint. Observational only this session (see package
docstring) — writes one `events` row per tool call about to happen, never blocks.

Wired into `settings.json` as:

    {"hooks": {"PreToolUse": [{"hooks": [{"type": "command",
        "command": "python -m shipgate.hooks.pretooluse"}]}]}}

Reads the hook's JSON from stdin, same as Claude Code invokes it.
"""

from __future__ import annotations

import sys

from ._common import (
    DEFAULT_TRANSCRIPT_TIER,
    HookInputError,
    ensure_session,
    ensure_utf8_streams,
    open_project_ledger,
    read_hook_input,
    utc_now_iso,
)

_SOURCE_FILE = "<live-hook:PreToolUse>"


def run(stdin_text: str | None = None) -> int:
    """Returns the process exit code. Never raises — a hook that crashes is a hook that
    breaks the user's session; every failure path here degrades to "wrote nothing this
    time" plus a stderr note, not an uncaught traceback."""
    ensure_utf8_streams()
    try:
        payload = read_hook_input(stdin_text)
    except HookInputError as exc:
        sys.stderr.write(f"shipgate PreToolUse hook: {exc} — skipping, not blocking\n")
        return 0

    session_id = payload["session_id"]
    cwd = payload["cwd"]

    try:
        with open_project_ledger(cwd) as writer:
            ensure_session(writer, session_id=session_id, cwd=cwd)
            writer.insert_event(
                session_id=session_id,
                source_file=_SOURCE_FILE,
                source_offset=0,  # live write, not an ingest offset into a file
                transcript_tier=DEFAULT_TRANSCRIPT_TIER,
                record_type="pretooluse_hook",
                uuid=payload.get("tool_use_id"),
                permission_mode=payload.get("permission_mode"),
                timestamp=utc_now_iso(),
                raw_payload=payload,
            )
    except Exception as exc:  # noqa: BLE001 — deliberate fail-open boundary, see package docstring
        sys.stderr.write(f"shipgate PreToolUse hook: internal error (non-blocking): {exc}\n")
        return 0

    return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    sys.exit(main())

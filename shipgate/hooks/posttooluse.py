"""`PostToolUse` hook entrypoint. Observational only this session (see package
docstring) — writes one `events` row per completed tool call, including its result,
never blocks.

Wired into `settings.json` as:

    {"hooks": {"PostToolUse": [{"hooks": [{"type": "command",
        "command": "python -m shipgate.hooks.posttooluse"}]}]}}
"""

from __future__ import annotations

import sys

from ._common import (
    DEFAULT_TRANSCRIPT_TIER,
    HookInputError,
    ProjectRootUnresolvableError,
    ensure_session,
    ensure_utf8_streams,
    open_project_ledger,
    read_hook_input,
    utc_now_iso,
)

_SOURCE_FILE = "<live-hook:PostToolUse>"


def run(stdin_text: str | None = None) -> int:
    """Same fail-open contract as `pretooluse.run` — see that module and the package
    docstring. `payload` (including `tool_result`, which is exactly where a command's
    real stdout/stderr — and therefore any secret it printed — would land) goes through
    `insert_event`'s existing redact-before-hash-before-write pipeline unchanged; this
    module adds no redaction logic of its own, it reuses the one already hardened in
    `shipgate.ledger.redaction` (Session 003)."""
    ensure_utf8_streams()
    try:
        payload = read_hook_input(stdin_text)
    except HookInputError as exc:
        sys.stderr.write(f"shipgate PostToolUse hook: {exc} — skipping, not blocking\n")
        return 0

    session_id = payload["session_id"]
    cwd = payload["cwd"]

    try:
        with open_project_ledger(cwd) as writer:
            ensure_session(writer, session_id=session_id, cwd=cwd)
            writer.insert_event(
                session_id=session_id,
                source_file=_SOURCE_FILE,
                source_offset=0,
                transcript_tier=DEFAULT_TRANSCRIPT_TIER,
                record_type="posttooluse_hook",
                uuid=payload.get("tool_use_id"),
                permission_mode=payload.get("permission_mode"),
                timestamp=utc_now_iso(),
                raw_payload=payload,
            )
    except ProjectRootUnresolvableError as exc:
        # See _common.ProjectRootUnresolvableError's docstring — a hook must never
        # create a project root it didn't find. Fails open on the decision (this is an
        # observational hook, it never blocked anyway), but loudly: silence here is
        # exactly the bug this branch exists to stop being possible.
        sys.stderr.write(f"shipgate PostToolUse hook: {exc} — the gate did not run for this event, not blocking\n")
        return 0
    except Exception as exc:  # noqa: BLE001 — deliberate fail-open boundary, see package docstring
        sys.stderr.write(f"shipgate PostToolUse hook: internal error (non-blocking): {exc}\n")
        return 0

    return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    sys.exit(main())

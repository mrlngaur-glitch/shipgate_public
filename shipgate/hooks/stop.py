"""`Stop` hook entrypoint. **This is where the gate actually gates** (task 2.6,
Phase 2): if the project has a shipfile at `<cwd>/shipfile.yaml`, every
`Stop` calls `shipgate.gate.evaluate_gate` and blocks the stop when the gate isn't green,
up to the retry cap — Gate B condition #1 ("a false completion is blocked") and
condition #4 ("the retry cap produces an honest red") both live here.

**No shipfile at `<cwd>/shipfile.yaml`** — nothing to gate on; behaves exactly as before
(writes the observational event, always allows the stop).

**Shipfile present but invalid** (`ShipfileSyntaxError` / `ShipfileValidationError`) —
fail OPEN, not blocked: a broken shipfile is not a `done_conditions` failure, and
hard-blocking a project with no way to know what to fix would be its own bug, not a
completion gate. Logged to stderr and recorded as an event; the stop is allowed.

**Shipfile present and valid** — `evaluate_gate` runs every dispatchable condition for
real (real subprocesses, real filesystem, real ledger reads) and returns one of three
outcomes:

- green (`all_dispatchable_green`) — allowed, exit 0, no `decision` key.
- not green, retries remain (`should_block`) — `{"decision": "block", "reason": ...}`,
  naming every failing condition and why, so the agent has something concrete to act on.
- not green, retry cap hit (`exhausted`) — **allowed anyway** (the loop-breaker: an
  infinite block loop is a worse failure than releasing an honest red — this project's
  own standing rule). `evaluation.release_reason` — the same failing conditions, explicitly labelled
  "honest red, not a pass" — is written to stderr (no verified stdout field exists yet
  for a non-blocking informational message; a Ship Report, later phase, will read this
  from the ledger's own `gate_evaluation` event, which always carries it too).

**Founder review finding, fixed this session: a retry-cap ceiling that silently narrows
what the shipfile declared is exactly the behavior this product blocks agents for.** If
`evaluate_gate` reports `ceiling_binding=True` (the shipfile's own `max_retries` exceeded
`shipgate.gate.orchestrator`'s defensive cap and was actually truncated), this hook writes
a stderr warning **at shipfile-load time**, every attempt, not just once — cheap, and
never silently dropped. `block_reason`/`release_reason` also name the effective number
whenever the ceiling is the reason this attempt's retry budget ran out sooner than the
shipfile requested — see `shipgate.gate.orchestrator`'s module docstring, design decision
3, and its standing rule: **if the code knows something the user would want to know, the
verdict or the visible output carries it, never a comment alone.**

Respects Claude Code's own loop-prevention contract exactly as before: `stop_hook_active`
is read but not specially branched on. The documented idiom (official hooks reference,
same page as the 8-block citation below) is to check this field and exit early the moment
it's `true` — this hook deliberately does not follow that idiom, because it collapses to
"retry at most once, ever," while this module's whole point is a *counted* retry budget.
`shipgate.gate.orchestrator`'s own ledger-derived `prior_blocking_attempts` count is a strictly
more precise signal than that boolean flag for exactly this purpose, and its retry ceiling stays
verified well below Claude Code's own default 8-consecutive-block override regardless
(https://code.claude.com/docs/en/hooks-guide, "Stop hook hits the block cap": "Claude Code
overrides a Stop hook after it blocks eight times in a row without progress.") — see that
module's docstring for the full citation and the `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP` caveat.

Wired into `settings.json` as:

    {"hooks": {"Stop": [{"hooks": [{"type": "command",
        "command": "python -m shipgate.hooks.stop"}]}]}}

**Investigated for the same encoding-crash class as `shipgate.cli`'s confirmed Gate C
blocker (Session 011), then precisely scoped rather than assumed identical:** this
module's stderr writes use U+2014 with several sitting outside any try/except — but
`sys.stderr`'s default error handler is CPython's own `'backslashreplace'` (confirmed by
direct interpreter inspection and a negative-control test), so none of them actually
crash on cp437/cp932 the way the CLI's stdout writes did. The block-decision JSON path
(`json.dumps`) was never at risk either — it ASCII-escapes non-ASCII by default.
`_common.ensure_utf8_streams()` (called first thing in `run()`, here and in
`pretooluse.py`/`posttooluse.py`) is kept anyway as defense-in-depth against a future
write that doesn't have this default in mind — see its own docstring for the full
reasoning, including why it is not being reported as a second confirmed blocker.

**Founder review finding, Finding 6, a decision recorded before task 3.4 was built:
the outer fail-open catch below also swallows a broken
ledger, and the thing that failed is also the thing that would have recorded evidence of
the failure.** Reproduced live: a corrupted `ledger.db`, `ledger.db`'s path occupied by a
directory, and an ACL-denied `.shipgate/` all released the stop with empty stdout (no
`decision` key — **allowed**) and a stderr line nothing durable ever captures — silence
indistinguishable from a healthy, green project, forever, because a corrupted ledger does
not self-heal. Third appearance of this exact shape (Finding 1's dead interpreter,
Finding 5's destroyed pipe, now a dead ledger).

**Decision (P12): keep fail-open as the mechanism — the loop-breaker rationale for it is
real — but make it loud and durable, escalating from silent-but-recorded to a loud block
only once a failure proves it isn't self-healing**, mirroring
`shipgate.gate.orchestrator.evaluate_gate`'s own already-proven retry-cap/exhaustion shape
in the opposite direction (escalating INTO a block, not out of one). `sqlite3.Error` gets
its own boundary, narrower than the existing generic `except Exception` below (which stays
exactly as-is for anything else — a bug in `evaluate_gate` unrelated to ledger I/O is a
different, still-genuinely-unanticipated class this decision doesn't touch):

1. Every ledger failure is durably tracked in `<cwd>/.shipgate/gate_unavailable.json` — a
   plain sibling file of `ledger.db`, deliberately NOT part of the hash-chained ledger
   (it has to survive the ledger being broken, so it can't live inside it).
2. The 1st consecutive failure fails open exactly as before (a genuinely transient
   hiccup — a momentary lock, a brief disk issue — gets one free pass), but is now durably
   recorded rather than vanishing into unrecorded stderr noise.
3. The 2nd through `_GATE_UNAVAILABLE_GRACE + _GATE_UNAVAILABLE_MAX_BLOCKS`th consecutive
   failure BLOCKS — the only channel this project has that's actually visible to the
   user/agent in the moment (stderr on a non-blocking hook isn't surfaced to either). The
   reason text is explicit this is a ShipGate infrastructure problem, not a code problem.
4. Past that cap, this mechanism exhausts — stops blocking (an infinite block loop on an
   unrecoverable condition is exactly what this project's own standing rule warns against) and fails open again,
   but says so explicitly rather than silently reverting to Finding 6's original behavior.
5. A successful ledger open clears the marker — a self-healed ledger returns to fully
   invisible, normal operation.
6. **Named, accepted residual gap, not silently glossed over:** if `.shipgate/` itself is
   locked down enough that even the marker file can't be written, there is no way to
   distinguish a 1st from a 5th failure without guessing — "never guess on
   architecture" rules that out. Falls back to the original Finding 6 behavior for this
   one sub-case only: fail open, stderr only.

Rejected alternatives: always hard-blocking on the very
first ledger failure (rejected — a corrupted-file-class error can be transient on a real
machine, and hard-blocking a user's legitimate work on the first occurrence of something
that might resolve itself next turn is report §2.2's "a gate that blocks wrongly once at
launch kills the product," self-inflicted); doing nothing (rejected — this is the status
quo Finding 6 reports, and task 3.4's Ship Report is the only surface that could ever say
"the gate did not run this session," which today it cannot).
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from shipgate.gate import DEFAULT_SHIPFILE_FILENAME, evaluate_gate
from shipgate.shipfile import ShipfileSyntaxError, ShipfileValidationError, load_shipfile

from ._common import (
    DEFAULT_TRANSCRIPT_TIER,
    HookInputError,
    ensure_session,
    ensure_utf8_streams,
    open_project_ledger,
    read_hook_input,
    utc_now_iso,
)

_SOURCE_FILE = "<live-hook:Stop>"

_GATE_UNAVAILABLE_MARKER_RELPATH = Path(".shipgate") / "gate_unavailable.json"

#: 1 free, silent-but-recorded pass before escalating to a block — a momentary lock or
#: disk hiccup plausibly resolves within one retry; recurring identically past that is a
#: real signal, not noise.
_GATE_UNAVAILABLE_GRACE = 1

#: Consecutive failures BLOCKED after the grace period before this mechanism gives up and
#: exhausts (fails open) to avoid an infinite block loop. `1 grace + 5
#: blocks` = 6 total, deliberately reusing `shipgate.gate.orchestrator.
#: _MAX_EFFECTIVE_RETRIES`'s own verified-safe margin under Claude Code's default
#: 8-consecutive-block override, for the identical reason that number is sized that way.
_GATE_UNAVAILABLE_MAX_BLOCKS = 5


def _gate_unavailable_marker_path(cwd: str) -> Path:
    return Path(cwd) / _GATE_UNAVAILABLE_MARKER_RELPATH


def _read_gate_unavailable_marker(cwd: str) -> dict[str, Any]:
    """`{}` for "no marker, or unreadable" — both mean "treat as no prior failures,"
    never an error in their own right; this file is a best-effort aid, not a source of
    truth anything else depends on."""
    try:
        return json.loads(_gate_unavailable_marker_path(cwd).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_gate_unavailable_marker(cwd: str, data: dict[str, Any]) -> bool:
    """Best-effort, never raises. Returns whether the write succeeded — callers use this
    to know whether the escalation counter is trustworthy at all (see the module
    docstring's accepted residual gap when `.shipgate/` itself can't be written)."""
    path = _gate_unavailable_marker_path(cwd)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        return False
    return True


def _clear_gate_unavailable_marker(cwd: str) -> None:
    """Best-effort, never raises — called the moment the ledger opens successfully, so a
    self-healed ledger returns to fully invisible, normal operation with no stale count
    carried forward."""
    try:
        _gate_unavailable_marker_path(cwd).unlink(missing_ok=True)
    except OSError:
        pass


def _handle_ledger_unavailable(cwd: str, session_id: str, exc: Exception, now: str) -> None:
    """Called when opening or using the ledger raised a `sqlite3.Error` — see the module
    docstring's P12 decision for the full reasoning behind this escalation."""
    prior = _read_gate_unavailable_marker(cwd)
    consecutive = prior.get("consecutive_failures", 0) + 1
    marker = {
        "consecutive_failures": consecutive,
        "last_error": str(exc),
        "last_seen": now,
        "first_seen": prior.get("first_seen", now),
        "session_id": session_id,
    }
    tracked = _write_gate_unavailable_marker(cwd, marker)

    if not tracked:
        sys.stderr.write(
            f"shipgate Stop hook: internal error (non-blocking): {exc} — could not even "
            "record this failure (.shipgate/ itself may be unwritable); not blocking\n"
        )
        return

    if consecutive <= _GATE_UNAVAILABLE_GRACE:
        sys.stderr.write(f"shipgate Stop hook: internal error (non-blocking): {exc}\n")
        return

    if consecutive <= _GATE_UNAVAILABLE_GRACE + _GATE_UNAVAILABLE_MAX_BLOCKS:
        sys.stdout.write(
            json.dumps(
                {
                    "decision": "block",
                    "reason": (
                        f"ShipGate could not run its gate check for {consecutive} consecutive "
                        "attempts — this is a ShipGate infrastructure problem, not your code "
                        f"(run `shipgate doctor` or check .shipgate/ledger.db). Error: {exc}"
                    ),
                }
            )
        )
        return

    marker["exhausted"] = True
    _write_gate_unavailable_marker(cwd, marker)
    sys.stderr.write(
        f"shipgate Stop hook: gate unavailable for {consecutive} consecutive attempts — "
        "giving up blocking to avoid an infinite loop (not a pass; needs manual "
        f"investigation, see .shipgate/gate_unavailable.json). Error: {exc}\n"
    )


def run(stdin_text: str | None = None) -> int:
    """Same fail-open contract as `pretooluse.run` — see that module and the package
    docstring. A block decision is written to stdout as JSON (Claude Code reads it on
    exit 0); every other path (no shipfile, invalid shipfile, green, exhausted, or any
    internal error) allows the stop, exit 0 — stdout stays empty in every non-blocking
    case (the only channel Claude Code actually reads as a decision), though exhausted
    and ceiling-binding cases still write an explanatory line to stderr (see module
    docstring)."""
    ensure_utf8_streams()
    try:
        payload = read_hook_input(stdin_text)
    except HookInputError as exc:
        sys.stderr.write(f"shipgate Stop hook: {exc} — skipping, not blocking\n")
        return 0

    session_id = payload["session_id"]
    cwd = payload["cwd"]

    try:
        try:
            with open_project_ledger(cwd) as writer:
                # The ledger opened -- if it was previously failing, it has healed. See
                # the module docstring's P12 decision: cleared here, immediately, rather
                # than deferred, so a later failure in this same block (a disk filling up
                # mid-write, say) is correctly treated as a fresh 1st occurrence, not
                # conflated with old, already-resolved failures.
                _clear_gate_unavailable_marker(cwd)
                ensure_session(writer, session_id=session_id, cwd=cwd)
                now = utc_now_iso()
                writer.insert_event(
                    session_id=session_id,
                    source_file=_SOURCE_FILE,
                    source_offset=0,
                    transcript_tier=DEFAULT_TRANSCRIPT_TIER,
                    record_type="stop_hook",
                    permission_mode=payload.get("permission_mode"),
                    timestamp=now,
                    raw_payload=payload,
                )

                shipfile_path = Path(cwd) / DEFAULT_SHIPFILE_FILENAME
                if not shipfile_path.exists():
                    return 0

                try:
                    shipfile = load_shipfile(shipfile_path)
                except (ShipfileSyntaxError, ShipfileValidationError) as exc:
                    sys.stderr.write(f"shipgate Stop hook: invalid shipfile, not gating: {exc} — not blocking\n")
                    writer.insert_event(
                        session_id=session_id,
                        source_file=_SOURCE_FILE,
                        source_offset=0,
                        transcript_tier=DEFAULT_TRANSCRIPT_TIER,
                        record_type="gate_shipfile_invalid",
                        timestamp=now,
                        raw_payload={"error": str(exc)},
                    )
                    return 0

                evaluation = evaluate_gate(shipfile, Path(cwd), session_id, writer, now=now)
                if evaluation.ceiling_binding:
                    sys.stderr.write(
                        f"shipgate Stop hook: shipfile gate_policy.max_retries="
                        f"{shipfile['gate_policy']['max_retries']} exceeds ShipGate's own safety "
                        f"ceiling and was capped at {evaluation.effective_max_retries} for this "
                        "session — the requested retry budget will not be fully honored "
                        "(shipgate/gate/orchestrator.py, design decision 3)\n"
                    )
                if evaluation.should_block:
                    sys.stdout.write(json.dumps({"decision": "block", "reason": evaluation.block_reason}))
                elif evaluation.exhausted:
                    sys.stderr.write(f"shipgate Stop hook: {evaluation.release_reason}\n")
        except sqlite3.Error as exc:
            # Narrower than the generic catch below -- see the module docstring's P12
            # decision. The ledger itself is the thing that failed, so it cannot be used
            # to record that it failed; _handle_ledger_unavailable uses a plain sibling
            # file instead.
            _handle_ledger_unavailable(cwd, session_id, exc, utc_now_iso())
            return 0
    except Exception as exc:  # noqa: BLE001 — deliberate fail-open boundary, see package docstring
        sys.stderr.write(f"shipgate Stop hook: internal error (non-blocking): {exc}\n")
        return 0

    return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    sys.exit(main())

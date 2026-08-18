"""The session blast-radius counter (task 2.8, Phase 2) — report §2.2
(D5): "one evening, one agent, 7 gate bypasses — individually reasonable, collectively
undiagnosable." Counts high-risk changes against a shipfile's own declared
`session_policy.max_high_risk_changes_per_session`, and refuses the N+1th pending a
logged human override (`session_policy.on_exceeded: require_logged_override`, frozen at
Gate A — the only legal value, so there is nothing to configure away here).

**Founder review finding, resolved this session: what counts as "a high-risk change" is
not invented here.** The report doesn't specify a detection heuristic, and a guessed one
(e.g. "any `PreToolUse` call touching more than N files") risks building the wrong
mechanism under a name that's hard to walk back later ("never guess on
architecture" is this project's own standing rule). Instead, this module uses what the shipfile **already declares**:
`task_classes[name].risk_tier` (frozen Gate-A shipfile schema, `shipgate/shipfile/
schema.py`). A change is high-risk exactly when the caller names the task class it
belongs to and that class's `risk_tier` is `"high"` — a declarative classification the
project's own author already committed to in writing, not a runtime inference this
module invents. What stays an honest, stated gap: **automatically detecting which task
class a given real-world change belongs to.** No `PreToolUse` wiring, no CLI command
calls `record_high_risk_change` automatically — nothing does, yet. This module is the
counter and the refusal; the classifier is future, separate, unbuilt work.

**Deliberately not wired into `shipgate.gate.orchestrator.evaluate_gate` / `Stop`.**
Report §8.1: blast-radius sits alongside routing/budgets as a "while the agent works"
concern, distinct from `Stop`'s "is the work done" concern — forcing this through `Stop`
would mean answering "which condition, when it goes green, constitutes a high-risk
change" automatically, which is exactly the undecided classification problem this module
avoids. Demonstrated live via direct calls against a real ledger and a real shipfile —
Gate B condition #5's demonstration — not through the hook path.

Zero Claude-Code-specific imports — core-purity contract, `pyproject.toml`. Depends on
`shipgate.shipfile` and `shipgate.ledger` only.
"""

from __future__ import annotations

from dataclasses import dataclass

from shipgate.ledger.writer import LedgerWriter
from shipgate.shipfile import Shipfile


@dataclass(frozen=True)
class BlastRadiusResult:
    """Always carries a `reason` — a call that changes nothing (an unrecognized risk
    tier, a refusal) is never silently a no-op; the caller always has something to show
    or log. See the module docstring's standing rule (`shipgate.gate.orchestrator`,
    design decision 3): if the code knows something, the return value carries it."""

    recorded: bool
    refused: bool
    overridden: bool
    count_this_session: int  # count AFTER this call, whether recorded or refused
    max_allowed: int
    reason: str


def record_high_risk_change(
    shipfile: Shipfile,
    session_id: str,
    writer: LedgerWriter,
    *,
    task_class: str,
    description: str,
    now: str,
    override_reason: str | None = None,
) -> BlastRadiusResult:
    """Records (or refuses) one high-risk change against this session's budget.

    Raises `ValueError` (specific, named — same discipline as `run_checker`'s
    unknown-condition-type error) if `task_class` isn't a name the shipfile's own
    `task_classes` block declares: a caller citing a task class that doesn't exist is a
    caller bug, not something to silently ignore.

    If the named class's `risk_tier` isn't `"high"`, this call is a documented no-op —
    `recorded=False, refused=False` — with a `reason` stating exactly why, never a
    silent pass-through that looks identical to "nothing happened."

    If it is `"high"`: records normally while the session's count is below
    `session_policy.max_high_risk_changes_per_session`. At or above that count, refuses
    unless `override_reason` is given — and even a refusal writes a ledger event (never
    a silent drop). An override is recorded, never silent: the event payload names it
    and the reason given, matching `session_policy.on_exceeded: require_logged_override`
    exactly.
    """
    task_classes = shipfile["task_classes"]
    if task_class not in task_classes:
        raise ValueError(
            f"task_class {task_class!r} is not declared in this shipfile's task_classes "
            f"block — known classes: {sorted(task_classes)}"
        )

    risk_tier = task_classes[task_class]["risk_tier"]
    max_allowed = shipfile["session_policy"]["max_high_risk_changes_per_session"]

    if risk_tier != "high":
        count = writer.connection.execute(
            "SELECT COUNT(*) FROM events WHERE session_id = ? AND record_type = 'high_risk_change'",
            (session_id,),
        ).fetchone()[0]
        return BlastRadiusResult(
            recorded=False,
            refused=False,
            overridden=False,
            count_this_session=count,
            max_allowed=max_allowed,
            reason=(
                f"task_class {task_class!r} is risk_tier {risk_tier!r}, not 'high' — "
                "not counted against the session blast-radius budget"
            ),
        )

    count_before = writer.connection.execute(
        "SELECT COUNT(*) FROM events WHERE session_id = ? AND record_type = 'high_risk_change'",
        (session_id,),
    ).fetchone()[0]

    if count_before < max_allowed:
        writer.insert_event(
            session_id=session_id,
            source_file="<blast-radius-counter>",
            source_offset=0,
            transcript_tier="session",
            record_type="high_risk_change",
            timestamp=now,
            raw_payload={
                "task_class": task_class,
                "description": description,
                "count_this_session": count_before + 1,
                "max_allowed": max_allowed,
                "override": False,
            },
        )
        return BlastRadiusResult(
            recorded=True,
            refused=False,
            overridden=False,
            count_this_session=count_before + 1,
            max_allowed=max_allowed,
            reason=f"recorded high-risk change {count_before + 1} of {max_allowed} allowed this session",
        )

    if override_reason:
        writer.insert_event(
            session_id=session_id,
            source_file="<blast-radius-counter>",
            source_offset=0,
            transcript_tier="session",
            record_type="high_risk_change",
            timestamp=now,
            raw_payload={
                "task_class": task_class,
                "description": description,
                "count_this_session": count_before + 1,
                "max_allowed": max_allowed,
                "override": True,
                "override_reason": override_reason,
            },
        )
        return BlastRadiusResult(
            recorded=True,
            refused=False,
            overridden=True,
            count_this_session=count_before + 1,
            max_allowed=max_allowed,
            reason=(
                f"high-risk change {count_before + 1} exceeds the {max_allowed}-per-session "
                f"budget — recorded via LOGGED OVERRIDE: {override_reason!r}"
            ),
        )

    # Refused — but still visible in the ledger, never a silent drop.
    writer.insert_event(
        session_id=session_id,
        source_file="<blast-radius-counter>",
        source_offset=0,
        transcript_tier="session",
        record_type="high_risk_change_refused",
        timestamp=now,
        raw_payload={
            "task_class": task_class,
            "description": description,
            "count_this_session": count_before,
            "max_allowed": max_allowed,
        },
    )
    return BlastRadiusResult(
        recorded=False,
        refused=True,
        overridden=False,
        count_this_session=count_before,
        max_allowed=max_allowed,
        reason=(
            f"high-risk change {count_before + 1} refused — {max_allowed} per session already "
            "used and no override_reason was given; pass one to record it via a logged override"
        ),
    )


__all__ = ["BlastRadiusResult", "record_high_risk_change"]

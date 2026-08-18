"""Supersession logic: how a claim's verdict changes over time without ever editing
history (report §5.2 — "changing the public verdict schema after
Gate A" is a stop-and-ask; editing a verdict row never is, it is simply forbidden).

This is a **storage-agnostic reference model** — a plain in-memory object graph, not a
SQLite table. `shipgate.ledger` (Task 1.2, design-only this session — see
`docs/ledger_schema_design.md`) is where verdict rows actually persist; its `verdicts`
and `supersessions` tables are designed to mirror the shape defined here exactly, so the
rules proven here in-memory are the rules the ledger enforces on disk.

The rule, stated once: a claim never has its verdict *changed*. It only ever gains a
**new** verdict record that names the record it supersedes. The claim's current verdict
is whichever record is at the head of that chain. Nothing is ever deleted or mutated.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .taxonomy import EvidenceTier, Verdict
from .transitions import (
    IllegalTierTransitionError,
    IllegalTransitionError,
    validate_transition,
    validate_verified_tier_transition,
)


class MalformedVerdictError(ValueError):
    """Raised when a verdict record violates the taxonomy's own shape rules — e.g. a
    `VERIFIED` verdict with no evidence tier, or a non-`VERIFIED` verdict carrying one.
    This is checked before transition legality: a malformed record is refused
    regardless of what it would transition from."""


def validate_verdict_shape(
    verdict: Verdict, evidence_tier: EvidenceTier | None, *, record_id: str = "(unassigned)"
) -> None:
    """The taxonomy's shape rule, standalone: `VERIFIED` verdicts must carry an
    `EvidenceTier`; no other verdict may. Factored out of `VerdictRecord.__post_init__`
    so `shipgate.ledger` can run the identical check before a row exists (and therefore
    before it has a real id) — one source of truth for the rule, not a re-implementation
    at the storage layer."""
    if verdict is Verdict.VERIFIED and evidence_tier is None:
        raise MalformedVerdictError(
            f"record {record_id!r}: VERIFIED verdicts must carry an "
            "EvidenceTier (disk-verified or runtime-verified) — report §5.4"
        )
    if verdict is not Verdict.VERIFIED and evidence_tier is not None:
        raise MalformedVerdictError(
            f"record {record_id!r}: only VERIFIED verdicts may carry an "
            f"EvidenceTier; got {verdict.value!r} with {evidence_tier.value!r}"
        )


@dataclass(frozen=True)
class VerdictRecord:
    """One immutable verdict row for one claim.

    `record_id` is caller-supplied (the ledger will use its row id / hash-chain
    position; tests may use anything unique). `supersedes` names the `record_id` of the
    record this one replaces as the claim's current verdict — `None` only for a claim's
    first-ever verdict record.
    """

    record_id: str
    claim_id: str
    verdict: Verdict
    evidence_tier: EvidenceTier | None = None
    reason: str = ""
    supersedes: str | None = None

    def __post_init__(self) -> None:
        validate_verdict_shape(self.verdict, self.evidence_tier, record_id=self.record_id)


@dataclass
class ClaimVerdictHistory:
    """The append-only verdict chain for a single claim.

    Every mutation goes through `append()`, which (a) refuses a record that does not
    chain onto the current head — no back-dated inserts, no edits disguised as appends
    — and (b) refuses a record whose transition is illegal per
    `shipgate.verdicts.transitions`. A record that clears both checks is added to
    `records`; nothing already in `records` is ever changed or removed.
    """

    claim_id: str
    records: list[VerdictRecord] = field(default_factory=list)

    @property
    def head(self) -> VerdictRecord | None:
        """The claim's current verdict record, or None if it has never had one."""
        return self.records[-1] if self.records else None

    @property
    def current_verdict(self) -> Verdict | None:
        head = self.head
        return head.verdict if head is not None else None

    def append(self, record: VerdictRecord) -> None:
        """Append `record` as the claim's new current verdict.

        Raises `ValueError` if `record.claim_id` doesn't match this history, if
        `record.supersedes` doesn't name the current head (append-only chain
        integrity), or if `record.record_id` collides with one already recorded.
        Raises `IllegalTransitionError` if the verdict transition itself is not legal.
        """
        if record.claim_id != self.claim_id:
            raise ValueError(
                f"record for claim {record.claim_id!r} appended to history for "
                f"claim {self.claim_id!r}"
            )
        existing_ids = {r.record_id for r in self.records}
        if record.record_id in existing_ids:
            raise ValueError(
                f"record_id {record.record_id!r} already exists in claim "
                f"{self.claim_id!r}'s history — records are append-only, never reused"
            )

        head = self.head
        expected_supersedes = head.record_id if head is not None else None
        if record.supersedes != expected_supersedes:
            raise ValueError(
                f"record {record.record_id!r} names supersedes={record.supersedes!r}, "
                f"but the current head for claim {self.claim_id!r} is "
                f"{expected_supersedes!r} — a supersession must chain onto the head, "
                "never edit or bypass it"
            )

        # Raises IllegalTransitionError on refusal; nothing is appended if it does.
        head_verdict = head.verdict if head is not None else None
        validate_transition(head_verdict, record.verdict, claim_id=self.claim_id)

        # A VERIFIED -> VERIFIED transition is legal at the class level (it's the
        # disk-verified -> runtime-verified upgrade path) but has a second, finer rule:
        # the evidence tier itself may never downgrade. Only meaningful when both the
        # prior and new verdict are VERIFIED — checked here, not in validate_transition,
        # because the class-level table has no notion of tiers.
        if head_verdict is Verdict.VERIFIED and record.verdict is Verdict.VERIFIED:
            validate_verified_tier_transition(
                head.evidence_tier, record.evidence_tier, claim_id=self.claim_id
            )

        self.records.append(record)


__all__ = [
    "ClaimVerdictHistory",
    "IllegalTierTransitionError",
    "IllegalTransitionError",
    "MalformedVerdictError",
    "VerdictRecord",
    "validate_verdict_shape",
]

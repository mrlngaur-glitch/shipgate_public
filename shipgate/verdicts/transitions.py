"""Legal verdict transitions (report §5.4; Phase 1 done-condition: "an illegal
transition is refused, demonstrated").

A "transition" here is: the claim already has a latest verdict of class `frm`, and a
new verdict row of class `to` is about to be appended for the same claim (append-only —
see `supersession.py`). This module answers two questions: *is that
new row legal to append?* (`is_legal_transition` / `validate_transition`), and, for the
one class that carries a second dimension, *is the evidence-tier change legal?*
(`is_legal_verified_tier_transition` / `validate_verified_tier_transition`). Neither
touches storage; `shipgate.ledger` (Task 1.2) is what actually persists rows.

Design notes (no report passage specifies the transition graph directly, so this
follows this project's own preference order — smaller build, local-first, keeps the gate
honest). **Revised on founder review of Session 002** — the first draft wrongly assumed
`CONTRADICTED` could never be a claim's first verdict; corrected reasoning below.

- `UNVERIFIED`, `VERIFIED`, `UNVERIFIED_VACUOUS`, `PENDING_RECHECK`, `CONTRADICTED`, and
  `TARGET_UNREACHABLE` may all be a claim's *first* verdict. The first draft excluded
  `CONTRADICTED` on the theory that "contradicted" is inherently a claim about a
  *change* from prior belief. That doesn't hold: report §5.4 defines `CONTRADICTED` as
  "ledger evidence contradicts the claim — including retroactive flips from
  pending-recheck." The retroactive flip is one case the definition calls out, not the
  whole of the definition. A claim can be false the very first time anyone checks it —
  the gate catching a false completion on first inspection is the product's *core*
  moment, and it must be directly representable as `(no prior verdict) -> CONTRADICTED`,
  not forced through an artificial `UNVERIFIED` stepping-stone first.
- `QUARANTINED_FLAKY` is the one class that still cannot be a claim's first verdict: it
  specifically means a check flip-flopped on *unchanged* code, which is meaningless
  without at least two prior observations to flip between.
- `CONTRADICTED` is reachable from `UNVERIFIED` and `UNVERIFIED_VACUOUS` as well as from
  `VERIFIED` and `PENDING_RECHECK` (the retroactive-flip cases) — evidence contradicting
  a claim doesn't care whether anyone previously believed it; "we didn't know" and "we
  vacuously observed nothing" are exactly the states a real refutation should be able to
  overturn. A vacuous check proved nothing, so it cannot stand in the way of a later
  real one finding the claim false.
- `VERIFIED -> VERIFIED` is legal **at the verdict-class level** — this is the
  evidence-tier upgrade path (report §12: "share of runtime-verified (vs disk-only)
  claims rising"; the D1 story itself — code inert on disk, then a restart makes it
  real). But class-level legality isn't the whole rule for this one case: see
  `is_legal_verified_tier_transition` below for why a *downgrade* (runtime-verified ->
  disk-verified) is refused even though the class transition is the same.
- `TARGET_UNREACHABLE` remains terminal: no legal outgoing transition. Report §5.4 says
  it "never gaslights"; this project's own rule says goalposts freeze and a target proven
  unreachable is corrected only by an explicit founder-approved amendment — a new claim
  under an amended goalpost, never a quiet transition of this one back to `VERIFIED` or
  anywhere else. This is the illegal transition this phase's done-condition demonstrates.
- The remaining edges each mirror an explicit report scenario: `VERIFIED` ->
  `CONTRADICTED` is the F2 "green flipped to contradicted 24h later" demo;
  `PENDING_RECHECK` -> `CONTRADICTED` is the same demo via the `verify_after` path;
  `VERIFIED`/`CONTRADICTED` -> `QUARANTINED_FLAKY` is "a check flipped verdict on
  unchanged code"; `QUARANTINED_FLAKY` -> `UNVERIFIED` is quarantine lifting back to a
  clean baseline pending fresh evidence, never straight back to a hard verdict.
"""

from __future__ import annotations

from .taxonomy import ALL_VERDICTS, EvidenceTier, TaxonomyIntegrityError, Verdict

#: `None` as a source means "this verdict may be a claim's first-ever verdict row."
#: The legal transition table. Keys are source verdicts (or `None` for "no prior
#: verdict"); values are the set of verdicts legal to append next.
_LEGAL_TRANSITIONS: dict[Verdict | None, frozenset[Verdict]] = {
    None: frozenset(
        {
            Verdict.UNVERIFIED,
            Verdict.VERIFIED,
            Verdict.UNVERIFIED_VACUOUS,
            Verdict.PENDING_RECHECK,
            Verdict.CONTRADICTED,
            Verdict.TARGET_UNREACHABLE,
        }
    ),
    Verdict.UNVERIFIED: frozenset(
        {
            Verdict.VERIFIED,
            Verdict.UNVERIFIED_VACUOUS,
            Verdict.PENDING_RECHECK,
            Verdict.CONTRADICTED,
            Verdict.TARGET_UNREACHABLE,
        }
    ),
    Verdict.VERIFIED: frozenset(
        {
            Verdict.VERIFIED,  # evidence-tier upgrade path — see is_legal_verified_tier_transition
            Verdict.CONTRADICTED,
            Verdict.QUARANTINED_FLAKY,
        }
    ),
    Verdict.UNVERIFIED_VACUOUS: frozenset(
        {
            Verdict.VERIFIED,
            Verdict.UNVERIFIED_VACUOUS,  # re-run, still observed nothing
            Verdict.PENDING_RECHECK,
            Verdict.CONTRADICTED,  # a vacuous check proved nothing; a real one may refute
        }
    ),
    Verdict.PENDING_RECHECK: frozenset(
        {
            Verdict.VERIFIED,
            Verdict.CONTRADICTED,
            Verdict.PENDING_RECHECK,  # window extended, still waiting
        }
    ),
    Verdict.CONTRADICTED: frozenset(
        {
            Verdict.VERIFIED,  # fixed, re-verified
            Verdict.QUARANTINED_FLAKY,
        }
    ),
    Verdict.QUARANTINED_FLAKY: frozenset(
        {
            Verdict.UNVERIFIED,  # quarantine lifted, back to a clean baseline
        }
    ),
    Verdict.TARGET_UNREACHABLE: frozenset(),  # terminal — see module docstring
}

# --- Structural self-checks, run at import time -----------------------------------
# A transition table that silently drops a verdict class is worse than no table: it
# would look complete while quietly making a class unreachable. Fail fast on import,
# not on first use. Raised, not asserted — see TaxonomyIntegrityError's docstring.

_missing_as_source = [v for v in ALL_VERDICTS if v not in _LEGAL_TRANSITIONS]
if _missing_as_source:
    raise TaxonomyIntegrityError(f"verdict(s) missing as transition source: {_missing_as_source}")

_reachable = {v for targets in _LEGAL_TRANSITIONS.values() for v in targets}
_unreachable = [v for v in ALL_VERDICTS if v not in _reachable]
if _unreachable:
    raise TaxonomyIntegrityError(
        f"verdict(s) unreachable by any legal transition (orphan state): {_unreachable}"
    )


class IllegalTransitionError(ValueError):
    """Raised when a proposed verdict-class transition is not in the legal table.

    Carries the claim id (if known), the attempted `frm -> to` pair, and a plain-
    language reason, so a refusal is diagnosable rather than a bare exception.
    """

    def __init__(self, frm: Verdict | None, to: Verdict, *, claim_id: str | None = None):
        self.frm = frm
        self.to = to
        self.claim_id = claim_id
        frm_label = frm.value if frm is not None else "(no prior verdict)"
        claim_note = f" for claim {claim_id!r}" if claim_id else ""
        super().__init__(
            f"illegal verdict transition{claim_note}: {frm_label!r} -> {to.value!r} is not "
            "a legal transition. See shipgate.verdicts.transitions for the legal table "
            "and the reasoning behind each edge."
        )


class IllegalTierTransitionError(ValueError):
    """Raised specifically when a `VERIFIED -> VERIFIED` transition would *downgrade*
    the evidence tier (runtime-verified -> disk-verified) for the same claim.

    This is deliberately a separate check from `IllegalTransitionError`: at the
    verdict-class level, `VERIFIED -> VERIFIED` is legal (it's the upgrade path from
    disk-verified to runtime-verified — report §12's "share of runtime-verified rising",
    and the D1 story: code inert on disk, then a restart makes it real). But an upgrade
    and a downgrade are not symmetric. Once a claim has been shown to work at runtime,
    re-asserting it as merely disk-verified would let the *outward* verdict stay
    "verified" while the *evidence backing it* quietly got weaker — exactly the silent
    regression the taxonomy exists to prevent (this project's own disk/runtime honesty
    rule, applied to a claim's own history, not just its current state). If a claim's
    evidence genuinely weakens — the runtime check can no longer be re-run, the log
    marker is gone — the honest verdict is a fresh `UNVERIFIED` or `PENDING_RECHECK`,
    which says plainly "we're not sure anymore," not a `VERIFIED` that quietly got
    easier to earn.
    """

    def __init__(self, frm_tier: EvidenceTier, to_tier: EvidenceTier, *, claim_id: str | None = None):
        self.frm_tier = frm_tier
        self.to_tier = to_tier
        self.claim_id = claim_id
        claim_note = f" for claim {claim_id!r}" if claim_id else ""
        super().__init__(
            f"illegal evidence-tier downgrade{claim_note}: {frm_tier.value!r} -> "
            f"{to_tier.value!r}. A VERIFIED claim's evidence tier may upgrade "
            "(disk-verified -> runtime-verified) but never downgrade while remaining "
            "VERIFIED; weakened evidence must be reported as UNVERIFIED or "
            "PENDING_RECHECK instead, never as a quieter VERIFIED."
        )


def is_legal_transition(frm: Verdict | None, to: Verdict) -> bool:
    """True iff appending verdict `to` is legal when the claim's current latest verdict
    is `frm` (or `frm is None` for a claim with no verdict row yet)."""
    return to in _LEGAL_TRANSITIONS.get(frm, frozenset())


def validate_transition(frm: Verdict | None, to: Verdict, *, claim_id: str | None = None) -> None:
    """Raise `IllegalTransitionError` if `frm -> to` is not legal. No return value —
    call this before appending a verdict row; a clean return means proceed."""
    if not is_legal_transition(frm, to):
        raise IllegalTransitionError(frm, to, claim_id=claim_id)


def legal_next_verdicts(frm: Verdict | None) -> frozenset[Verdict]:
    """The set of verdicts legal to append next, given current latest verdict `frm`."""
    return _LEGAL_TRANSITIONS.get(frm, frozenset())


def is_legal_verified_tier_transition(frm_tier: EvidenceTier | None, to_tier: EvidenceTier) -> bool:
    """True unless this is a `VERIFIED -> VERIFIED` evidence-tier *downgrade*
    (runtime-verified -> disk-verified). `frm_tier is None` means the claim's previous
    verdict wasn't `VERIFIED` at all (or had none) — any tier is legal as a first or
    fresh `VERIFIED`. Same-tier reconfirmation and the disk -> runtime upgrade are both
    always legal."""
    if frm_tier is None:
        return True
    return not (frm_tier is EvidenceTier.RUNTIME_VERIFIED and to_tier is EvidenceTier.DISK_VERIFIED)


def validate_verified_tier_transition(
    frm_tier: EvidenceTier | None, to_tier: EvidenceTier, *, claim_id: str | None = None
) -> None:
    """Raise `IllegalTierTransitionError` if `frm_tier -> to_tier` is a downgrade.
    Only meaningful when both the previous and new verdict are `VERIFIED`; callers
    (`supersession.ClaimVerdictHistory.append`) only invoke this in that case."""
    if not is_legal_verified_tier_transition(frm_tier, to_tier):
        raise IllegalTierTransitionError(frm_tier, to_tier, claim_id=claim_id)

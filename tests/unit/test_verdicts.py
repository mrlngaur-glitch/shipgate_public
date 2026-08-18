"""Task 1.1 — verdict taxonomy tests (Phase 1 brief, done-condition):

    "All 7 verdict classes representable; an illegal transition is refused, demonstrated"

Every test here is runtime-verified by definition — pytest actually executes it. The
Ship Report entry for this session pastes the `pytest -v` output for this file as the
evidence.
"""

import pytest

from shipgate.verdicts import (
    ALL_VERDICTS,
    ClaimVerdictHistory,
    EvidenceTier,
    IllegalTierTransitionError,
    IllegalTransitionError,
    MalformedVerdictError,
    TaxonomyIntegrityError,
    Verdict,
    VerdictRecord,
    is_legal_transition,
    is_legal_verified_tier_transition,
    legal_next_verdicts,
    validate_transition,
    validate_verified_tier_transition,
)
from shipgate.verdicts.transitions import (
    _LEGAL_TRANSITIONS,  # test-only introspection of the raw table
)

# --- All 7 classes representable ---------------------------------------------------


def test_exactly_seven_verdict_classes():
    """Report §5.4 lists exactly 7 verdict classes. Not 6, not 8."""
    assert len(ALL_VERDICTS) == 7
    assert len(set(ALL_VERDICTS)) == 7  # no duplicates


def test_all_seven_verdict_strings_match_report_5_4():
    """The wire format is the exact token report §5.4 names — a Ship Report or a CI
    rule parsing these strings must see precisely this vocabulary."""
    expected = {
        "verified",
        "unverified",
        "unverified-vacuous",
        "pending-recheck",
        "contradicted",
        "quarantined-flaky",
        "target-unreachable",
    }
    assert {v.value for v in ALL_VERDICTS} == expected


def test_every_verdict_class_is_constructible_as_a_record():
    """Each of the 7 classes can be the verdict of an actual VerdictRecord — proves
    representability, not just enum membership."""
    for i, verdict in enumerate(ALL_VERDICTS):
        tier = EvidenceTier.RUNTIME_VERIFIED if verdict is Verdict.VERIFIED else None
        record = VerdictRecord(
            record_id=f"r{i}",
            claim_id="claim-representability-check",
            verdict=verdict,
            evidence_tier=tier,
        )
        assert record.verdict is verdict


# --- The disk/runtime dimension on VERIFIED -----------------------------------------


def test_verified_requires_an_evidence_tier():
    with pytest.raises(MalformedVerdictError, match="must carry an EvidenceTier"):
        VerdictRecord(record_id="r1", claim_id="c1", verdict=Verdict.VERIFIED, evidence_tier=None)


def test_non_verified_verdict_may_not_carry_an_evidence_tier():
    with pytest.raises(MalformedVerdictError, match="only VERIFIED verdicts may carry"):
        VerdictRecord(
            record_id="r1",
            claim_id="c1",
            verdict=Verdict.UNVERIFIED,
            evidence_tier=EvidenceTier.DISK_VERIFIED,
        )


def test_disk_verified_and_runtime_verified_are_distinct_tiers():
    disk = VerdictRecord(
        record_id="r1", claim_id="c1", verdict=Verdict.VERIFIED, evidence_tier=EvidenceTier.DISK_VERIFIED
    )
    runtime = VerdictRecord(
        record_id="r2", claim_id="c1", verdict=Verdict.VERIFIED, evidence_tier=EvidenceTier.RUNTIME_VERIFIED
    )
    assert disk.evidence_tier != runtime.evidence_tier
    assert disk.evidence_tier.value == "disk-verified"
    assert runtime.evidence_tier.value == "runtime-verified"


# --- No orphan states -----------------------------------------------------------


def test_transition_table_has_no_orphan_states():
    """Every one of the 7 verdicts must be reachable by at least one legal transition
    (or be the legal first verdict), and every verdict must appear as a source in the
    table (even if, like TARGET_UNREACHABLE, its only legal transitions are none)."""
    sources = set(_LEGAL_TRANSITIONS.keys()) - {None}
    assert sources == set(ALL_VERDICTS), f"missing source states: {set(ALL_VERDICTS) - sources}"

    reachable = {v for targets in _LEGAL_TRANSITIONS.values() for v in targets}
    assert reachable == set(ALL_VERDICTS), f"unreachable (orphan) states: {set(ALL_VERDICTS) - reachable}"


def test_quarantined_flaky_cannot_be_a_claims_first_verdict():
    """QUARANTINED_FLAKY specifically means a check flip-flopped on unchanged code —
    meaningless without at least two prior observations to flip between."""
    assert Verdict.QUARANTINED_FLAKY not in legal_next_verdicts(None)


def test_contradicted_CAN_be_a_claims_first_verdict():
    """Corrected on founder review of Session 002: report §5.4 defines CONTRADICTED as
    'ledger evidence contradicts the claim — including retroactive flips', naming the
    flip as one case, not the definition. A false claim caught on first inspection —
    the product's core moment — must be directly representable, not forced through an
    artificial UNVERIFIED stepping-stone first."""
    assert Verdict.CONTRADICTED in legal_next_verdicts(None)

    history = ClaimVerdictHistory(claim_id="c-first-contradiction")
    history.append(
        VerdictRecord(
            "fc1",
            "c-first-contradiction",
            Verdict.CONTRADICTED,
            reason="agent claimed the file was created; grep found no such file",
            supersedes=None,
        )
    )
    assert history.current_verdict is Verdict.CONTRADICTED
    assert len(history.records) == 1


def test_unverified_to_contradicted_is_legal():
    """The default state, directly refuted by evidence — no detour through VERIFIED
    first required."""
    assert is_legal_transition(Verdict.UNVERIFIED, Verdict.CONTRADICTED)
    history = ClaimVerdictHistory(claim_id="c-unv-contra")
    history.append(VerdictRecord("u1", "c-unv-contra", Verdict.UNVERIFIED, supersedes=None))
    history.append(
        VerdictRecord("u2", "c-unv-contra", Verdict.CONTRADICTED, supersedes="u1")
    )
    assert history.current_verdict is Verdict.CONTRADICTED


def test_unverified_vacuous_to_contradicted_is_legal():
    """A vacuous check (zero tests collected) proves nothing, so it cannot block a
    later real check from finding the claim false."""
    assert is_legal_transition(Verdict.UNVERIFIED_VACUOUS, Verdict.CONTRADICTED)
    history = ClaimVerdictHistory(claim_id="c-vac-contra")
    history.append(VerdictRecord("v1", "c-vac-contra", Verdict.UNVERIFIED_VACUOUS, supersedes=None))
    history.append(
        VerdictRecord(
            "v2",
            "c-vac-contra",
            Verdict.CONTRADICTED,
            reason="zero tests collected, then a real suite ran and failed",
            supersedes="v1",
        )
    )
    assert history.current_verdict is Verdict.CONTRADICTED


# --- VERIFIED -> VERIFIED: the evidence-tier upgrade path -------------------------


def test_verified_to_verified_disk_to_runtime_upgrade_is_legal():
    """report §12: 'share of runtime-verified (vs disk-only) claims rising' — the D1
    story: code inert on disk, then a restart makes it real."""
    assert is_legal_transition(Verdict.VERIFIED, Verdict.VERIFIED)
    assert is_legal_verified_tier_transition(EvidenceTier.DISK_VERIFIED, EvidenceTier.RUNTIME_VERIFIED)

    history = ClaimVerdictHistory(claim_id="c-upgrade")
    history.append(
        VerdictRecord(
            "up1", "c-upgrade", Verdict.VERIFIED, evidence_tier=EvidenceTier.DISK_VERIFIED, supersedes=None
        )
    )
    history.append(
        VerdictRecord(
            "up2",
            "c-upgrade",
            Verdict.VERIFIED,
            evidence_tier=EvidenceTier.RUNTIME_VERIFIED,
            reason="restarted; the log marker fired",
            supersedes="up1",
        )
    )
    assert history.current_verdict is Verdict.VERIFIED
    assert history.head.evidence_tier is EvidenceTier.RUNTIME_VERIFIED


def test_verified_to_verified_same_tier_reconfirmation_is_legal():
    for tier in (EvidenceTier.DISK_VERIFIED, EvidenceTier.RUNTIME_VERIFIED):
        assert is_legal_verified_tier_transition(tier, tier)


def test_verified_to_verified_runtime_to_disk_downgrade_is_illegal():
    """The asymmetry is deliberate: once shown to work at runtime, re-asserting the
    claim as merely disk-verified would let the outward VERIFIED verdict stay green
    while the evidence backing it quietly weakened — exactly the silent regression the
    taxonomy exists to prevent. Weakened evidence must become UNVERIFIED or
    PENDING_RECHECK, never a quieter VERIFIED."""
    assert not is_legal_verified_tier_transition(EvidenceTier.RUNTIME_VERIFIED, EvidenceTier.DISK_VERIFIED)

    history = ClaimVerdictHistory(claim_id="c-downgrade")
    history.append(
        VerdictRecord(
            "dg1", "c-downgrade", Verdict.VERIFIED, evidence_tier=EvidenceTier.RUNTIME_VERIFIED, supersedes=None
        )
    )
    with pytest.raises(IllegalTierTransitionError) as exc_info:
        history.append(
            VerdictRecord(
                "dg2",
                "c-downgrade",
                Verdict.VERIFIED,
                evidence_tier=EvidenceTier.DISK_VERIFIED,
                supersedes="dg1",
            )
        )
    assert "downgrade" in str(exc_info.value)
    # Refused — history unchanged, same as an illegal class-level transition.
    assert len(history.records) == 1
    assert history.head.evidence_tier is EvidenceTier.RUNTIME_VERIFIED


def test_validate_verified_tier_transition_raises_directly():
    with pytest.raises(IllegalTierTransitionError):
        validate_verified_tier_transition(
            EvidenceTier.RUNTIME_VERIFIED, EvidenceTier.DISK_VERIFIED, claim_id="c-direct"
        )


# --- Structural guards are real raises, not assert (python -O safety) -------------


def test_taxonomy_integrity_error_is_a_runtime_error_not_an_assertion():
    """`assert` is stripped under `python -O`; the frozen-taxonomy guards in
    taxonomy.py and transitions.py must survive that flag regardless. Proven here by
    the class
    hierarchy — the guards themselves already ran successfully at import time (this
    whole test file imports cleanly), which is the actual proof; this asserts the
    exception type is the raise-based one they use, not AssertionError."""
    assert issubclass(TaxonomyIntegrityError, RuntimeError)
    assert not issubclass(TaxonomyIntegrityError, AssertionError)


# --- Legal transitions succeed ---------------------------------------------------


def test_legal_transition_unverified_to_verified_succeeds():
    assert is_legal_transition(Verdict.UNVERIFIED, Verdict.VERIFIED)
    history = ClaimVerdictHistory(claim_id="c1")
    history.append(VerdictRecord("r1", "c1", Verdict.UNVERIFIED, supersedes=None))
    history.append(
        VerdictRecord(
            "r2", "c1", Verdict.VERIFIED, evidence_tier=EvidenceTier.RUNTIME_VERIFIED, supersedes="r1"
        )
    )
    assert history.current_verdict is Verdict.VERIFIED
    assert len(history.records) == 2


def test_legal_transition_verified_to_contradicted_the_f2_retroactive_flip_demo():
    """Report §8.2 F2: 'the green flipped to contradicted 24h later' — the founding
    demo for retroactive verdict flips. Must be a legal transition today even though
    the automated producer arrives at F2 (Phase 1 brief: build the vocabulary now)."""
    assert is_legal_transition(Verdict.VERIFIED, Verdict.CONTRADICTED)
    history = ClaimVerdictHistory(claim_id="c-f2-demo")
    history.append(
        VerdictRecord(
            "v1", "c-f2-demo", Verdict.VERIFIED, evidence_tier=EvidenceTier.RUNTIME_VERIFIED, supersedes=None
        )
    )
    history.append(
        VerdictRecord(
            "v2",
            "c-f2-demo",
            Verdict.CONTRADICTED,
            reason="24h-later recheck found the claimed file deleted",
            supersedes="v1",
        )
    )
    assert history.current_verdict is Verdict.CONTRADICTED
    # Original record is untouched — supersession, not an edit.
    assert history.records[0].verdict is Verdict.VERIFIED


# --- The illegal transition: refused, demonstrated -------------------------------


def test_illegal_transition_target_unreachable_to_verified_is_refused():
    """report §5.4: target-unreachable 'never gaslights'. This project's own standing
    rule: goalposts freeze; a target proven unreachable is corrected only by an explicit founder
    amendment, never a silent transition of the same claim back to VERIFIED. This is
    the illegal transition this phase's done-condition demonstrates."""
    assert not is_legal_transition(Verdict.TARGET_UNREACHABLE, Verdict.VERIFIED)
    with pytest.raises(IllegalTransitionError) as exc_info:
        validate_transition(Verdict.TARGET_UNREACHABLE, Verdict.VERIFIED, claim_id="c-goalpost")
    assert "target-unreachable" in str(exc_info.value)
    assert "verified" in str(exc_info.value)


def test_illegal_transition_is_refused_before_it_ever_reaches_history():
    """The refusal must happen at append time, and the claim's history must be
    unchanged afterward — an illegal transition never partially applies."""
    history = ClaimVerdictHistory(claim_id="c-goalpost-2")
    history.append(
        VerdictRecord("g1", "c-goalpost-2", Verdict.TARGET_UNREACHABLE, supersedes=None)
    )
    assert len(history.records) == 1

    with pytest.raises(IllegalTransitionError):
        history.append(
            VerdictRecord(
                "g2",
                "c-goalpost-2",
                Verdict.VERIFIED,
                evidence_tier=EvidenceTier.RUNTIME_VERIFIED,
                supersedes="g1",
            )
        )

    # Refused — history is exactly as it was before the illegal attempt.
    assert len(history.records) == 1
    assert history.current_verdict is Verdict.TARGET_UNREACHABLE


def test_target_unreachable_is_terminal_with_no_legal_outgoing_transition_at_all():
    assert legal_next_verdicts(Verdict.TARGET_UNREACHABLE) == frozenset()


# --- Append-only / supersession chain integrity -----------------------------------


def test_supersession_must_chain_onto_the_current_head_not_bypass_it():
    """A record naming the wrong (or no) predecessor is refused — this is what makes
    the history append-only rather than editable."""
    history = ClaimVerdictHistory(claim_id="c-chain")
    history.append(VerdictRecord("h1", "c-chain", Verdict.UNVERIFIED, supersedes=None))
    with pytest.raises(ValueError, match="must chain onto the head"):
        history.append(
            VerdictRecord(
                "h2",
                "c-chain",
                Verdict.VERIFIED,
                evidence_tier=EvidenceTier.DISK_VERIFIED,
                supersedes=None,  # wrong: should be "h1"
            )
        )


def test_a_record_id_can_never_be_reused_within_a_claims_history():
    history = ClaimVerdictHistory(claim_id="c-dup")
    history.append(VerdictRecord("d1", "c-dup", Verdict.UNVERIFIED, supersedes=None))
    with pytest.raises(ValueError, match="already exists"):
        history.append(VerdictRecord("d1", "c-dup", Verdict.VERIFIED, evidence_tier=EvidenceTier.DISK_VERIFIED, supersedes="d1"))


def test_existing_records_are_never_mutated_by_a_later_append():
    history = ClaimVerdictHistory(claim_id="c-immutable")
    history.append(VerdictRecord("m1", "c-immutable", Verdict.UNVERIFIED, supersedes=None))
    original = history.records[0]
    history.append(
        VerdictRecord("m2", "c-immutable", Verdict.UNVERIFIED_VACUOUS, supersedes="m1")
    )
    # The object appended first is bit-for-bit the same object, untouched.
    assert history.records[0] is original
    assert original.verdict is Verdict.UNVERIFIED

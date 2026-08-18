"""The frozen verdict taxonomy.

Seven verdict classes. This is a **public interface**: users screenshot Ship Reports,
CI rules parse these strings. Frozen at Gate A; changing a member
after launch requires a founder-approved major version.

Zero Claude-Code-specific imports here or anywhere in `shipgate.verdicts` — this module
is enforced core-purity by the `import-linter` contract in `pyproject.toml`.

Producers for `PENDING_RECHECK`'s automated recheck, retroactive `CONTRADICTED` flips,
and `TARGET_UNREACHABLE`'s automated detection arrive in later releases (F2, F5). The
vocabulary below is complete now regardless — a taxonomy that grows after launch is a
taxonomy that churns.
"""

from __future__ import annotations

from enum import Enum


class Verdict(str, Enum):
    """The seven verdict classes. String-valued so a verdict serializes to
    exactly the token a Ship Report or a CI rule would parse — no translation layer."""

    #: No evidence either way. The default verdict when a claim has no verdict row yet.
    #: Informational, never a hard-block on its own.
    UNVERIFIED = "unverified"

    #: Claim confirmed against ledger evidence. Always carries an `EvidenceTier` — see
    #: `VerifiedVerdict` below. There is no bare "verified" without a tier; the
    #: disk/runtime distinction is the D1 differentiator (Phase 1 brief, Gate A doc).
    VERIFIED = "verified"

    #: The check ran but observed nothing (zero tests collected, empty trace window).
    #: Never renders green — reported as a failure, not a pass.
    UNVERIFIED_VACUOUS = "unverified-vacuous"

    #: Claim carries `verify_after`; evidence is expected later. Treated as unproven
    #: until it appears — the wire is assumed broken, not assumed good, while pending.
    #: Automated recheck producer arrives at F2; the state exists now.
    PENDING_RECHECK = "pending-recheck"

    #: Ledger evidence contradicts the claim. This class's definition names retroactive
    #: flips ("including retroactive flips from pending-recheck") as one case of this,
    #: not the whole of it — a check contradicting a claim on its *first* inspection is
    #: the product's core moment (a false completion caught) and is exactly as much
    #: `CONTRADICTED` as a later flip. See `transitions.py` for the corrected reasoning
    #: on which states may reach this one.
    CONTRADICTED = "contradicted"

    #: A check flipped verdict on unchanged code. Degraded to advisory; never a
    #: hard-block. The flake-detection producer is
    #: Phase 2; the state and its transition rules exist now.
    QUARANTINED_FLAKY = "quarantined-flaky"

    #: The honest amber. Evidence shows the frozen target cannot be met as specified
    #: ("wanted 70%, data allows 2.8%") — distinct from `CONTRADICTED`, never gaslit
    #: into looking like ordinary failure or silently relaxed into a pass
    #: ("goalposts freeze" is this project's own standing rule). Label exists at F2; automated detection
    #: is F5. Manually assignable now.
    TARGET_UNREACHABLE = "target-unreachable"


class EvidenceTier(str, Enum):
    """The dimension carried by every `VERIFIED` verdict, and only by `VERIFIED`
    verdicts. This is the D1 differentiator: "the code is written" and
    "the code works when executed" are different claims."""

    #: File/test state on disk looked correct. Not executed. Reports progress, not
    #: completion — this class is never allowed to close a done-condition.
    DISK_VERIFIED = "disk-verified"

    #: Exercised end-to-end: a log marker fired, an endpoint behaved, a DB row exists,
    #: an emission was traced. The only tier that closes a done-condition.
    RUNTIME_VERIFIED = "runtime-verified"


#: Every verdict class, in the fixed canonical order. **Correction (Category C finding,
#: this repository's own systematic integrity pass, `PHASE_PLAN.md`): this tuple does
#: NOT itself assert that nothing was left out or crept in — a length/duplicate check
#: on `ALL_VERDICTS` alone can only catch `ALL_VERDICTS` disagreeing with itself; it
#: cannot catch a member added to `Verdict` without this tuple being updated to match.
#: Confirmed live before the fix below existed: an 8th `Verdict` member, `ALL_VERDICTS`
#: left untouched, imported cleanly, no error.** The guard immediately below is what
#: actually asserts completeness — checked against `Verdict` itself, not against this
#: tuple's own shape.
ALL_VERDICTS: tuple[Verdict, ...] = (
    Verdict.UNVERIFIED,
    Verdict.VERIFIED,
    Verdict.UNVERIFIED_VACUOUS,
    Verdict.PENDING_RECHECK,
    Verdict.CONTRADICTED,
    Verdict.QUARANTINED_FLAKY,
    Verdict.TARGET_UNREACHABLE,
)


class TaxonomyIntegrityError(RuntimeError):
    """Raised at import time if a structural invariant of the frozen taxonomy has been
    broken by an edit to `taxonomy.py` or `transitions.py`.

    Deliberately a raised exception, not an `assert`. `assert` statements are stripped
    entirely under `python -O` / `PYTHONOPTIMIZE=1` — a guard protecting a frozen public
    interface must not be removable by a runtime flag.
    """


#: Checked first, before the length/duplicate checks below, so this is the error that
#: wins when both would fire (e.g. a Verdict member added with ALL_VERDICTS untouched is
#: both "out of sync" and, incidentally, "not 7 long" in the direction that matters) —
#: this is the guard that actually protects the frozen taxonomy; the length/duplicate
#: checks below only protect ALL_VERDICTS against disagreeing with itself.
if set(ALL_VERDICTS) != set(Verdict):
    missing_from_all_verdicts = sorted(v.value for v in set(Verdict) - set(ALL_VERDICTS))
    extra_in_all_verdicts = sorted(v.value for v in set(ALL_VERDICTS) - set(Verdict))
    raise TaxonomyIntegrityError(
        "ALL_VERDICTS is out of sync with the Verdict enum. "
        f"In Verdict but missing from ALL_VERDICTS: {missing_from_all_verdicts}. "
        f"In ALL_VERDICTS but not a real Verdict member: {extra_in_all_verdicts}."
    )
if len(ALL_VERDICTS) != 7:
    raise TaxonomyIntegrityError(
        f"the verdict taxonomy is frozen at exactly 7 classes; "
        f"ALL_VERDICTS currently has {len(ALL_VERDICTS)}"
    )
if len(set(ALL_VERDICTS)) != len(ALL_VERDICTS):
    raise TaxonomyIntegrityError("ALL_VERDICTS contains a duplicate verdict class")

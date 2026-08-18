"""The frozen verdict taxonomy (report §5.4) — a public interface.

Public API:

- `Verdict` — the 7 verdict classes.
- `EvidenceTier` — `disk-verified` / `runtime-verified`, carried only by `VERIFIED`.
- `VerdictRecord`, `ClaimVerdictHistory` — the append-only, supersession-only model of
  how a claim's verdict changes over time.
- `is_legal_transition`, `validate_transition`, `legal_next_verdicts` — the
  verdict-class transition table.
- `is_legal_verified_tier_transition`, `validate_verified_tier_transition` — the
  finer-grained rule for `VERIFIED -> VERIFIED` (disk/runtime evidence-tier changes).
- `IllegalTransitionError`, `IllegalTierTransitionError`, `MalformedVerdictError`,
  `TaxonomyIntegrityError` — refusal / integrity exceptions.

Zero Claude-Code-specific imports anywhere under this package — enforced by the
`import-linter` core-purity contract in `pyproject.toml`.
"""

from .supersession import (
    ClaimVerdictHistory,
    IllegalTierTransitionError,
    MalformedVerdictError,
    VerdictRecord,
    validate_verdict_shape,
)
from .taxonomy import ALL_VERDICTS, EvidenceTier, TaxonomyIntegrityError, Verdict
from .transitions import (
    IllegalTransitionError,
    is_legal_transition,
    is_legal_verified_tier_transition,
    legal_next_verdicts,
    validate_transition,
    validate_verified_tier_transition,
)

__all__ = [
    "ALL_VERDICTS",
    "ClaimVerdictHistory",
    "EvidenceTier",
    "IllegalTierTransitionError",
    "IllegalTransitionError",
    "MalformedVerdictError",
    "TaxonomyIntegrityError",
    "Verdict",
    "VerdictRecord",
    "is_legal_transition",
    "is_legal_verified_tier_transition",
    "legal_next_verdicts",
    "validate_transition",
    "validate_verdict_shape",
    "validate_verified_tier_transition",
]

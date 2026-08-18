"""The append-only, hash-chained ledger (Task 1.2; report §13.2 step 1).

Five tables — `sessions`, `events`, `claims`, `verdicts`, `supersessions` — written
through `LedgerWriter`, the only code path allowed to touch them. Corrections happen
only as new rows in `supersessions` (for `sessions`/`events`/`claims`) or as a new
`verdicts` row plus its matching `supersessions` row (`insert_verdict` does both,
atomically) — never as an `UPDATE` or `DELETE`, which SQLite triggers refuse outright.

`events`, `claims`, and `verdicts` are hash-chained; `verify_chains` (or the standalone
`shipgate.ledger.integrity.verify_chain`) recomputes the chain and names the first row
whose stored hash no longer matches its content.

Design record: `docs/ledger_schema_design.md`. Zero Claude-Code-specific imports
anywhere under this package — core-purity contract, `pyproject.toml`.
"""

from .integrity import ChainTamperedError, UnchainedTableError, verify_all_chains, verify_chain
from .paths import DEFAULT_CORPUS_ROOT, relative_source_dir
from .redaction import redact_json
from .writer import LedgerWriter

__all__ = [
    "DEFAULT_CORPUS_ROOT",
    "ChainTamperedError",
    "LedgerWriter",
    "UnchainedTableError",
    "redact_json",
    "relative_source_dir",
    "verify_all_chains",
    "verify_chain",
]

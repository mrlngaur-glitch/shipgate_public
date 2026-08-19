"""`shipgate analyze` — read-only cross-project ledger aggregation and statistics.

Founder-authorized scope addition (see `PHASE_PLAN.md`'s decisions log). Reads other
projects' own `.shipgate/ledger.db` files, never writes to any of them, creates no
central database. See `data.py`'s module docstring for the full design and the five
hard constraints this package is built against.

Zero Claude-Code-specific imports.
"""

from .data import (
    FAILURE_VERDICT_CLASSES,
    AggregatedTokenMetric,
    AnalyzeResult,
    LedgerFailure,
    ProjectStats,
    TokenMetric,
    VerdictReasonCount,
    aggregate_failure_reasons,
    aggregate_token_metric,
    aggregate_verdicts_by_class,
    analyze,
    discover_ledgers,
    gather_one_ledger,
    open_ledger_readonly,
)
from .render import render_analyze

__all__ = [
    "FAILURE_VERDICT_CLASSES",
    "AggregatedTokenMetric",
    "AnalyzeResult",
    "LedgerFailure",
    "ProjectStats",
    "TokenMetric",
    "VerdictReasonCount",
    "aggregate_failure_reasons",
    "aggregate_token_metric",
    "aggregate_verdicts_by_class",
    "analyze",
    "discover_ledgers",
    "gather_one_ledger",
    "open_ledger_readonly",
    "render_analyze",
]

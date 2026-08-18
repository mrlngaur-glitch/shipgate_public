"""Hash-chain primitives shared by every chained table.

Scope of chaining, decided across two sessions (Session 002 review; `docs/
ledger_schema_design.md`):

- `events` — chained. Report §13.2 names this one directly ("every event row carries a
  hash chained to the previous row's hash").
- `verdicts` — chained, by founder decision on Session 002 review: report decision 32
  has the Ship Report embed the hash range of the rows it was computed from, and a Ship
  Report's headline numbers come from verdict rows. An unchained `verdicts` table would
  make that "self-verifying receipt" verify the wrong thing.
- `claims` — chained, analyst decision (founder's call to make, logged here): a chained
  `verdicts` row is only as trustworthy as the `claims` row it judges. If `claims.text`
  could be silently rewritten, an attacker wouldn't need to forge a verdict at all —
  they could just rewrite what the verdict was allegedly about, leaving the (honest,
  chained) verdict pointing at a now-different claim. Chaining `claims` closes that gap
  with the same mechanism already being paid for.
- `sessions` — **not** chained. This is a deliberate smaller-build choice: `sessions`
  rows are ingest bookkeeping (project slug, working directory, git branch), not
  evidence or judgment. Nothing in the Ship Report's self-verifying receipt depends on
  `sessions` being tamper-evident the way a claim or a verdict is. Revisit if that
  changes.
- `supersessions` — not chained. It is itself the correction mechanism for the other
  four tables; append-only enforcement (triggers, `schema.py`) covers it. Chaining the
  corrections log to itself buys little extra.

Each chained table has **its own** independent chain (three separate chains, not one
interleaved global chain across tables) — simpler to reason about and to verify, and
each names its own broken row unambiguously. Verification walks a table in SQLite
`rowid` order (not the declared primary key, which for `claims` is a caller-supplied
TEXT) so ordering is always insertion order regardless of primary-key type.

Zero Claude-Code-specific imports — core-purity contract, `pyproject.toml`.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

#: Chained tables and the column that identifies a row to a human reading a tamper
#: report ("names the row" — Phase 1 done-condition). Order matches insertion order
#: expectations; values are unrelated to hash content, purely descriptive.
CHAINED_TABLES: tuple[str, ...] = ("events", "claims", "verdicts")

ID_COLUMN: dict[str, str] = {
    "events": "event_id",
    "claims": "claim_id",
    "verdicts": "verdict_id",
}


def canonical_json(content: dict[str, Any]) -> str:
    """Deterministic JSON serialization of a row's content fields. `sort_keys=True`
    means callers don't have to agree on column order between insert and verify —
    only on which keys are included and what their values are."""
    return json.dumps(content, sort_keys=True, separators=(",", ":"), default=str)


def compute_row_hash(prev_row_hash: str | None, content: dict[str, Any]) -> str:
    """The row's hash commits to both its own content and the previous row's hash —
    the standard hash-chain construction. `prev_row_hash is None` only for the very
    first row a chain has ever written."""
    preimage = f"{prev_row_hash or ''}|{canonical_json(content)}"
    return hashlib.sha256(preimage.encode("utf-8")).hexdigest()

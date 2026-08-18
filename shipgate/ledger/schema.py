"""The five ledger tables (report §13.2) — DDL only.

`sessions`, `events`, `claims`, `verdicts`, `supersessions`. Column choices and the
reasoning behind each are in `docs/ledger_schema_design.md`; this module is that design
turned into `CREATE TABLE` statements, nothing more.

Two enforcement layers on top of the five tables, both decided on Session 002 review:

1. **`CHECK` constraints** mirror the taxonomy's own shape rules (`shipgate.verdicts`)
   at the SQL layer, not only in Python — the same reasoning in both places: a raw
   `INSERT` that bypassed `shipgate.ledger.writer` entirely must still be unable to
   write an 8th verdict class, or a `VERIFIED` row with no evidence tier, into a frozen
   public interface.
2. **`BEFORE UPDATE` / `BEFORE DELETE` triggers on every table** refuse the statement
   outright. Founder decision on Session 002 review: Python-layer discipline alone
   ("the writer module just never issues UPDATE/DELETE") was rejected in favor of a
   trigger, on the same logic as the CHECK constraints — append-only is a stronger
   invariant than the verdict enum, and both deserve the same enforcement floor.

Zero Claude-Code-specific imports — core-purity contract, `pyproject.toml`.
"""

from __future__ import annotations

import sqlite3

#: The seven frozen verdict strings, duplicated here deliberately (not imported from
#: `shipgate.verdicts.Verdict`) — this CHECK constraint is meant to hold even if the
#: Python enum were somehow bypassed or wrong; it is SQLite's own copy of the frozen
#: list, not a re-export of the Python one.
_VERDICT_VALUES = (
    "unverified",
    "verified",
    "unverified-vacuous",
    "pending-recheck",
    "contradicted",
    "quarantined-flaky",
    "target-unreachable",
)
_EVIDENCE_TIER_VALUES = ("disk-verified", "runtime-verified")

_VERDICT_LIST_SQL = ", ".join(f"'{v}'" for v in _VERDICT_VALUES)
_TIER_LIST_SQL = ", ".join(f"'{v}'" for v in _EVIDENCE_TIER_VALUES)

DDL = f"""
CREATE TABLE IF NOT EXISTS sessions (
    session_id      TEXT PRIMARY KEY,
    project_slug    TEXT NOT NULL,
    source_dir      TEXT NOT NULL,   -- relative to the configured corpus root; see ledger.paths
    cwd             TEXT,
    git_branch      TEXT,
    started_at      TEXT,
    ended_at        TEXT
);

CREATE TABLE IF NOT EXISTS events (
    event_id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id                TEXT NOT NULL REFERENCES sessions(session_id),
    source_file                TEXT NOT NULL,
    source_offset               INTEGER NOT NULL,
    transcript_tier            TEXT NOT NULL CHECK (transcript_tier IN ('session', 'subagent')),
    record_type                TEXT NOT NULL,
    uuid                        TEXT,
    parent_uuid                 TEXT,
    cc_version                  TEXT,
    model                       TEXT,
    timestamp                   TEXT,
    request_id                  TEXT,
    prompt_id                   TEXT,
    permission_mode             TEXT,
    effort                      TEXT,
    tokens_input                 INTEGER,
    tokens_output                INTEGER,
    tokens_cache_read            INTEGER,
    tokens_cache_creation_5m      INTEGER,
    tokens_cache_creation_1h      INTEGER,
    tokens_thinking               INTEGER,
    cache_miss_reason            TEXT,
    raw_payload_redacted        TEXT,   -- JSON text; already redacted before this row exists
    row_hash                    TEXT NOT NULL,
    prev_row_hash                TEXT
);

CREATE TABLE IF NOT EXISTS claims (
    claim_id                TEXT PRIMARY KEY,
    session_id               TEXT NOT NULL REFERENCES sessions(session_id),
    source_event_id           INTEGER REFERENCES events(event_id),
    source                   TEXT NOT NULL CHECK (source IN ('shipfile_condition', 'extracted_from_transcript')),
    shipfile_condition_ref    TEXT,
    text                     TEXT NOT NULL,
    created_at                TEXT NOT NULL,
    row_hash                 TEXT NOT NULL,
    prev_row_hash             TEXT
);

CREATE TABLE IF NOT EXISTS verdicts (
    verdict_id               INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id                  TEXT NOT NULL REFERENCES claims(claim_id),
    verdict                  TEXT NOT NULL CHECK (verdict IN ({_VERDICT_LIST_SQL})),
    evidence_tier              TEXT CHECK (evidence_tier IN ({_TIER_LIST_SQL})),
    reason                    TEXT NOT NULL DEFAULT '',
    supersedes_verdict_id       INTEGER REFERENCES verdicts(verdict_id),
    created_at                 TEXT NOT NULL,
    row_hash                  TEXT NOT NULL,
    prev_row_hash              TEXT,
    CHECK (
        (verdict = 'verified' AND evidence_tier IS NOT NULL)
        OR (verdict != 'verified' AND evidence_tier IS NULL)
    )
);

CREATE TABLE IF NOT EXISTS supersessions (
    supersession_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    target_table                TEXT NOT NULL CHECK (target_table IN ('sessions', 'events', 'claims', 'verdicts')),
    superseded_row_id            TEXT NOT NULL,
    superseding_row_id            TEXT NOT NULL,
    reason                      TEXT NOT NULL,
    created_at                   TEXT NOT NULL
);
"""

#: (table, operation) -> trigger body. One BEFORE UPDATE and one BEFORE DELETE trigger
#: per table, all five tables, refusing the statement via RAISE(ABORT, ...).
_ALL_TABLES = ("sessions", "events", "claims", "verdicts", "supersessions")

_TRIGGER_DDL = "\n".join(
    f"""
CREATE TRIGGER IF NOT EXISTS {table}_no_update
BEFORE UPDATE ON {table}
BEGIN
    SELECT RAISE(ABORT, '{table} is append-only: corrections are supersession rows, never edits');
END;

CREATE TRIGGER IF NOT EXISTS {table}_no_delete
BEFORE DELETE ON {table}
BEGIN
    SELECT RAISE(ABORT, '{table} is append-only: rows are never deleted');
END;
"""
    for table in _ALL_TABLES
)

DDL += _TRIGGER_DDL


def init_schema(conn: sqlite3.Connection) -> None:
    """Create all five tables and their append-only triggers if they don't already
    exist. Idempotent — safe to call on every `LedgerWriter` open."""
    conn.executescript(DDL)
    conn.commit()

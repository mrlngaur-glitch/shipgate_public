"""The ledger writer — the only code path allowed to touch the five tables.

Task 1.2. Implements the design in `docs/ledger_schema_design.md` plus the founder's
decisions on Session 002 review: verdicts (and claims) are hash-chained alongside
events; UPDATE/DELETE are refused by SQLite triggers, not just Python discipline; claims
participate in `supersessions`; `sessions.source_dir` is relative to one configurable
corpus root (`shipgate.ledger.paths`).

Verdict legality is not re-implemented here — `insert_verdict` calls straight into
`shipgate.verdicts` (the same shape check, transition table, and tier-downgrade rule
proven by Task 1.1's 24 tests) before it writes anything. One source of truth for what a
legal verdict transition is; the ledger just persists what `shipgate.verdicts` already
approved.

Single-writer by design (this project's own no-background-daemons rule; ingest is foreground
and serial): `LedgerWriter` wraps one `sqlite3.Connection` and every chained insert runs
inside its own transaction, which is what makes "read the current chain tail, then
append" safe without a lock manager.

Zero Claude-Code-specific imports — core-purity contract, `pyproject.toml`.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Self

from shipgate.verdicts import (
    EvidenceTier,
    IllegalTransitionError,
    Verdict,
    validate_transition,
    validate_verdict_shape,
    validate_verified_tier_transition,
)

from .hashing import compute_row_hash
from .integrity import verify_all_chains
from .paths import DEFAULT_CORPUS_ROOT
from .redaction import redact_json
from .schema import init_schema

_EVENT_COLUMNS = [
    "session_id",
    "source_file",
    "source_offset",
    "transcript_tier",
    "record_type",
    "uuid",
    "parent_uuid",
    "cc_version",
    "model",
    "timestamp",
    "request_id",
    "prompt_id",
    "permission_mode",
    "effort",
    "tokens_input",
    "tokens_output",
    "tokens_cache_read",
    "tokens_cache_creation_5m",
    "tokens_cache_creation_1h",
    "tokens_thinking",
    "cache_miss_reason",
    "raw_payload_redacted",
]
_CLAIM_COLUMNS = ["session_id", "source_event_id", "source", "shipfile_condition_ref", "text", "created_at"]
_VERDICT_COLUMNS = ["claim_id", "verdict", "evidence_tier", "reason", "supersedes_verdict_id", "created_at"]

_VALID_CLAIM_SOURCES = ("shipfile_condition", "extracted_from_transcript")
_SUPERSESSION_TARGET_TABLES = ("sessions", "events", "claims", "verdicts")


class LedgerWriter:
    """Open (or create) a ledger SQLite file and write to it. The five tables and their
    append-only triggers are created on open if they don't already exist."""

    def __init__(self, db_path: str | Path, *, corpus_root: Path = DEFAULT_CORPUS_ROOT):
        self.db_path = Path(db_path)
        self.corpus_root = corpus_root
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.execute("PRAGMA foreign_keys = ON")
        init_schema(self._conn)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @property
    def connection(self) -> sqlite3.Connection:
        """Raw access to the underlying connection — for tests and for
        `shipgate.ledger.integrity.verify_all_chains`. Not for bypassing the tables'
        append-only triggers in product code."""
        return self._conn

    # --- internal chaining machinery ------------------------------------------

    def _tail_hash(self, table: str) -> str | None:
        row = self._conn.execute(f"SELECT row_hash FROM {table} ORDER BY rowid DESC LIMIT 1").fetchone()
        return row[0] if row else None

    def _insert_chained_row(
        self,
        table: str,
        columns: list[str],
        content: dict[str, Any],
        *,
        explicit_pk_column: str | None = None,
        explicit_pk_value: Any = None,
    ) -> tuple[int, str]:
        """Append one row to a chained table. Returns `(rowid, row_hash)`. Must be
        called from inside a `with self._conn:` block so the tail-hash read and the
        insert are atomic against concurrent writers (there are none, by design, but
        the transaction also gives us all-or-nothing commit for callers that chain
        several inserts together, e.g. `insert_verdict`'s matching supersession row)."""
        prev_hash = self._tail_hash(table)
        row_hash = compute_row_hash(prev_hash, content)

        col_names = list(columns)
        values: list[Any] = [content[c] for c in columns]
        if explicit_pk_column is not None:
            col_names = [explicit_pk_column, *col_names]
            values = [explicit_pk_value, *values]
        col_names += ["row_hash", "prev_row_hash"]
        values += [row_hash, prev_hash]

        placeholders = ", ".join("?" for _ in col_names)
        col_list = ", ".join(col_names)
        cur = self._conn.execute(f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})", values)
        return cur.lastrowid, row_hash

    # --- sessions ---------------------------------------------------------------

    def insert_session(
        self,
        *,
        session_id: str,
        project_slug: str,
        source_dir: str,
        cwd: str | None = None,
        git_branch: str | None = None,
        started_at: str | None = None,
        ended_at: str | None = None,
    ) -> None:
        """`source_dir` must already be relative to the configured corpus root — see
        `shipgate.ledger.paths.relative_source_dir`. Not chained (see `hashing.py`)."""
        with self._conn:
            self._conn.execute(
                "INSERT INTO sessions (session_id, project_slug, source_dir, cwd, git_branch, "
                "started_at, ended_at) VALUES (?,?,?,?,?,?,?)",
                (session_id, project_slug, source_dir, cwd, git_branch, started_at, ended_at),
            )

    # --- events -------------------------------------------------------------------

    def insert_event(
        self,
        *,
        session_id: str,
        source_file: str,
        source_offset: int,
        transcript_tier: str,
        record_type: str,
        uuid: str | None = None,
        parent_uuid: str | None = None,
        cc_version: str | None = None,
        model: str | None = None,
        timestamp: str | None = None,
        request_id: str | None = None,
        prompt_id: str | None = None,
        permission_mode: str | None = None,
        effort: str | None = None,
        tokens_input: int | None = None,
        tokens_output: int | None = None,
        tokens_cache_read: int | None = None,
        tokens_cache_creation_5m: int | None = None,
        tokens_cache_creation_1h: int | None = None,
        tokens_thinking: int | None = None,
        cache_miss_reason: str | None = None,
        raw_payload: Any = None,
    ) -> int:
        """Insert one `events` row. Returns the new `event_id`.

        `raw_payload`, if given, is a JSON-serializable structure (dict/list/scalars) —
        it is redacted (`shipgate.ledger.redaction.redact_json`, Task 1.3) *before* it
        is hashed or stored. Write order is fixed: parse -> redact -> hash -> write,
        never any other order (`docs/ledger_schema_design.md`).
        """
        if transcript_tier not in ("session", "subagent"):
            raise ValueError(f"invalid events.transcript_tier: {transcript_tier!r}")

        redacted_payload_json: str | None = None
        if raw_payload is not None:
            redacted_obj, _found = redact_json(raw_payload)
            redacted_payload_json = json.dumps(redacted_obj, sort_keys=True)

        content = {
            "session_id": session_id,
            "source_file": source_file,
            "source_offset": source_offset,
            "transcript_tier": transcript_tier,
            "record_type": record_type,
            "uuid": uuid,
            "parent_uuid": parent_uuid,
            "cc_version": cc_version,
            "model": model,
            "timestamp": timestamp,
            "request_id": request_id,
            "prompt_id": prompt_id,
            "permission_mode": permission_mode,
            "effort": effort,
            "tokens_input": tokens_input,
            "tokens_output": tokens_output,
            "tokens_cache_read": tokens_cache_read,
            "tokens_cache_creation_5m": tokens_cache_creation_5m,
            "tokens_cache_creation_1h": tokens_cache_creation_1h,
            "tokens_thinking": tokens_thinking,
            "cache_miss_reason": cache_miss_reason,
            "raw_payload_redacted": redacted_payload_json,
        }
        with self._conn:
            event_id, _row_hash = self._insert_chained_row("events", _EVENT_COLUMNS, content)
        return event_id

    # --- claims -------------------------------------------------------------------

    def insert_claim(
        self,
        *,
        claim_id: str,
        session_id: str,
        text: str,
        source: str,
        source_event_id: int | None = None,
        shipfile_condition_ref: str | None = None,
        created_at: str,
    ) -> str:
        """Insert one `claims` row. Returns `claim_id` unchanged, for chaining calls."""
        if source not in _VALID_CLAIM_SOURCES:
            raise ValueError(f"invalid claims.source: {source!r}; must be one of {_VALID_CLAIM_SOURCES}")

        content = {
            "session_id": session_id,
            "source_event_id": source_event_id,
            "source": source,
            "shipfile_condition_ref": shipfile_condition_ref,
            "text": text,
            "created_at": created_at,
        }
        with self._conn:
            self._insert_chained_row(
                "claims",
                _CLAIM_COLUMNS,
                content,
                explicit_pk_column="claim_id",
                explicit_pk_value=claim_id,
            )
        return claim_id

    # --- verdicts + supersessions --------------------------------------------------

    def current_verdict(self, claim_id: str) -> tuple[int, Verdict, EvidenceTier | None] | None:
        """The claim's current `(verdict_id, verdict, evidence_tier)`, or `None` if it
        has no verdict row yet (an absent row *is* `Verdict.UNVERIFIED` conceptually,
        per report §5.4's default, but this method reports storage fact, not the
        default — callers that want the default applied should do so explicitly)."""
        row = self._conn.execute(
            "SELECT verdict_id, verdict, evidence_tier FROM verdicts WHERE claim_id = ? "
            "ORDER BY verdict_id DESC LIMIT 1",
            (claim_id,),
        ).fetchone()
        if row is None:
            return None
        verdict_id, verdict_str, tier_str = row
        return verdict_id, Verdict(verdict_str), (EvidenceTier(tier_str) if tier_str is not None else None)

    def insert_verdict(
        self,
        *,
        claim_id: str,
        verdict: Verdict,
        evidence_tier: EvidenceTier | None = None,
        reason: str = "",
        created_at: str,
    ) -> int:
        """Insert one `verdicts` row for `claim_id`. Returns the new `verdict_id`.

        Refuses (raises, writes nothing) exactly like the in-memory
        `shipgate.verdicts.ClaimVerdictHistory` does, because it calls the same
        validation functions:

        1. `validate_verdict_shape` — VERIFIED must carry a tier; nothing else may.
        2. `validate_transition` — the class-level legal transition table.
        3. `validate_verified_tier_transition` — VERIFIED -> VERIFIED may only upgrade
           the evidence tier, never downgrade it.

        If this is a supersession (the claim already had a verdict), a matching
        `supersessions` row is written in the same transaction — atomic: both rows
        commit together or neither does.
        """
        validate_verdict_shape(verdict, evidence_tier, record_id=f"claim={claim_id}")

        current = self.current_verdict(claim_id)
        head_verdict_id = current[0] if current is not None else None
        head_verdict = current[1] if current is not None else None
        head_tier = current[2] if current is not None else None

        validate_transition(head_verdict, verdict, claim_id=claim_id)
        if head_verdict is Verdict.VERIFIED and verdict is Verdict.VERIFIED:
            validate_verified_tier_transition(head_tier, evidence_tier, claim_id=claim_id)

        content = {
            "claim_id": claim_id,
            "verdict": verdict.value,
            "evidence_tier": evidence_tier.value if evidence_tier is not None else None,
            "reason": reason,
            "supersedes_verdict_id": head_verdict_id,
            "created_at": created_at,
        }
        with self._conn:
            new_verdict_id, _row_hash = self._insert_chained_row("verdicts", _VERDICT_COLUMNS, content)
            if head_verdict_id is not None:
                self._insert_supersession_row(
                    target_table="verdicts",
                    superseded_row_id=head_verdict_id,
                    superseding_row_id=new_verdict_id,
                    reason=reason or "verdict superseded",
                    created_at=created_at,
                )
        return new_verdict_id

    def _insert_supersession_row(
        self,
        *,
        target_table: str,
        superseded_row_id: Any,
        superseding_row_id: Any,
        reason: str,
        created_at: str,
    ) -> int:
        """Internal — assumes it is called inside an existing transaction (see
        `insert_verdict`) or that the caller wraps it with `with self._conn:`."""
        if target_table not in _SUPERSESSION_TARGET_TABLES:
            raise ValueError(f"invalid supersessions.target_table: {target_table!r}")
        if not reason:
            raise ValueError("supersessions.reason is required and must not be blank")
        cur = self._conn.execute(
            "INSERT INTO supersessions (target_table, superseded_row_id, superseding_row_id, "
            "reason, created_at) VALUES (?,?,?,?,?)",
            (target_table, str(superseded_row_id), str(superseding_row_id), reason, created_at),
        )
        return cur.lastrowid

    def insert_supersession(
        self,
        *,
        target_table: str,
        superseded_row_id: Any,
        superseding_row_id: Any,
        reason: str,
        created_at: str,
    ) -> int:
        """Public entry point for a direct correction to `sessions`, `events`, or
        `claims` (verdict corrections should go through `insert_verdict`, which writes
        the matching `supersessions` row itself, atomically, alongside the new verdict).
        """
        with self._conn:
            return self._insert_supersession_row(
                target_table=target_table,
                superseded_row_id=superseded_row_id,
                superseding_row_id=superseding_row_id,
                reason=reason,
                created_at=created_at,
            )

    # --- integrity ------------------------------------------------------------

    def verify_chains(self) -> None:
        """Verify every chained table (`events`, `claims`, `verdicts`). Raises
        `shipgate.ledger.integrity.ChainTamperedError` on the first broken row found."""
        verify_all_chains(self._conn)


__all__ = ["IllegalTransitionError", "LedgerWriter"]

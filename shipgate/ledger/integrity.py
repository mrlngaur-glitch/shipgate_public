"""Hash-chain verification (Task 1.2 done-condition: "alter one row → chain
verification fails and names the row — `runtime-verified`, output pasted").

Walks a chained table (`events`, `claims`, or `verdicts` — see `hashing.py` for which
tables are chained and why) in SQLite `rowid` order and recomputes each row's hash from
its stored content, comparing against what's stored. The first mismatch — either the
row's own `row_hash` not matching its recomputed value, or its `prev_row_hash` not
matching the previous row's `row_hash` — is reported by the table's own id column
(`event_id` / `claim_id` / `verdict_id`), not by `rowid`, so the report names the row
the way a human (or a Ship Report) would refer to it.

This is what catches tampering the append-only triggers (`schema.py`) don't: a write
that bypasses SQLite entirely (direct file editing, a connection with triggers
disabled, `PRAGMA writable_schema`). The triggers are the first line of defense against
an ordinary `UPDATE`/`DELETE`; this is the second line against out-of-band tampering.

Zero Claude-Code-specific imports — core-purity contract, `pyproject.toml`.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .hashing import CHAINED_TABLES, ID_COLUMN, compute_row_hash


class UnchainedTableError(ValueError):
    """Raised if `verify_chain` is asked to verify a table that isn't chained."""


@dataclass
class ChainTamperedError(ValueError):
    """Raised by `verify_chain` on the first broken link. Names the row via the
    table's own id column, per the done-condition."""

    table: str
    id_column: str
    row_id: object
    reason: str

    def __str__(self) -> str:
        return (
            f"hash chain broken in {self.table!r} at {self.id_column}={self.row_id!r}: "
            f"{self.reason}"
        )


def _content_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    """Every column except the id column, `row_hash`, and `prev_row_hash` — the same
    set `writer.py` hashed at insert time, in the table's declared order (irrelevant
    for the hash itself, since `hashing.canonical_json` sorts keys, but kept consistent
    for readability)."""
    id_col = ID_COLUMN[table]
    cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [row[1] for row in cols if row[1] not in (id_col, "row_hash", "prev_row_hash")]


def verify_chain(conn: sqlite3.Connection, table: str) -> None:
    """Verify `table`'s hash chain from the first row to the last.

    Returns `None` (silently) if the chain is intact. Raises `ChainTamperedError` on
    the first broken row. Raises `UnchainedTableError` if `table` isn't one of the
    chained tables at all.
    """
    if table not in CHAINED_TABLES:
        raise UnchainedTableError(f"{table!r} is not a chained table; chained tables are {CHAINED_TABLES}")

    id_col = ID_COLUMN[table]
    content_cols = _content_columns(conn, table)
    select_cols = ", ".join([id_col, "row_hash", "prev_row_hash", *content_cols])
    rows = conn.execute(f"SELECT {select_cols} FROM {table} ORDER BY rowid").fetchall()

    expected_prev: str | None = None
    for row in rows:
        row_id = row[0]
        stored_row_hash = row[1]
        stored_prev_hash = row[2]
        content = dict(zip(content_cols, row[3:], strict=True))

        if stored_prev_hash != expected_prev:
            raise ChainTamperedError(
                table=table,
                id_column=id_col,
                row_id=row_id,
                reason=(
                    f"stored prev_row_hash {stored_prev_hash!r} does not match the "
                    f"previous row's row_hash {expected_prev!r}"
                ),
            )

        recomputed = compute_row_hash(stored_prev_hash, content)
        if recomputed != stored_row_hash:
            raise ChainTamperedError(
                table=table,
                id_column=id_col,
                row_id=row_id,
                reason=(
                    f"stored row_hash {stored_row_hash!r} does not match the hash "
                    f"recomputed from this row's current content ({recomputed!r}) — "
                    "the row's content was altered after it was written"
                ),
            )

        expected_prev = stored_row_hash


def verify_all_chains(conn: sqlite3.Connection) -> None:
    """Verify every chained table. Raises on the first tampered table found (in
    `CHAINED_TABLES` order); returns `None` if all are intact."""
    for table in CHAINED_TABLES:
        verify_chain(conn, table)

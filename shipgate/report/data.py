"""Ship Report data-gathering (task 3.4, Phase 3) — pure functions that
read a project's ledger and return plain dataclasses. Deliberately separate from
`render.py` (the `rich`-based rendering) so a rendering/wording change never risks
changing what's actually true here, and so a future consumer (F1's merge-gate PR
comment, for instance) can reuse this layer without pulling in a terminal-rendering
dependency.

**Reads the MOST RECENTLY RECORDED evaluation — never re-runs
`shipgate.gate.orchestrator.evaluate_gate` live.** A live re-run could disagree with
what actually gated the session (flake, a code edit made since), and a Ship Report's
whole point is to be a receipt of what was decided — independently checkable via the
hash chain (`verify_receipt`, below) — not a fresh opinion recomputed at report time.

**Both requirements below, implemented here:**
(a) the ledger receipt (`gather_ledger_receipt` / `verify_receipt`) is a thin wrapper
around the already-tested `shipgate.ledger.integrity` verification primitives — no new
hashing scheme; (b) the blast-radius count is read the same way every session, whether
zero or not — `render.py` is the layer that renders the caveat, but the count itself
carries no "clean" framing here either, it's just `int`.

**Finding 7 fix (founder review, Session 015, a launch blocker): `verify_receipt` now
re-derives EVERY chained table, not just `verdicts`.** The original version of this
function called `verify_chain(conn, "verdicts")` only, while the banner
(`_latest_gate_evaluation`), the blast-radius count (`_blast_radius_count`), and the
token totals (`_token_totals`) are all read from `events` — a different table, outside
that check's range entirely. The founder demonstrated the consequence directly: drop
`events`' append-only trigger, edit a `gate_evaluation` event's stored payload from
`should_block=True` to `all_dispatchable_green=True`, leave `verdicts` untouched — the
banner renders GREEN, and `--verify` prints VERIFIED underneath it, because VERIFIED was
true of the one table it actually checked. Fixed by calling
`shipgate.ledger.integrity.verify_all_chains` (already built, already tested at task
1.2 — not new machinery, just the whole-ledger entry point instead of the single-table
one) so nothing this module reads to build a report is outside `--verify`'s range. The
wording changed to match: every VERIFIED/TAMPERED sentence now names "the entire ledger
hash chain (events, claims, verdicts)," not an unqualified "the chain," so the sentence
never claims more than what was actually re-derived — see `render.py`'s own Finding 7
note for the second half of this fix, the banner/claims cross-check that catches the
same attack even when `--verify` isn't passed at all.

**The gate-unavailable marker (Session 013's Finding 6), implemented here:**
`read_gate_unavailable_marker` reads `.shipgate/gate_unavailable.json` — the same marker
file and JSON shape `shipgate/hooks/stop.py` writes (Session 013). **Deliberately
duplicates the marker's relative path rather than importing it from
`shipgate.hooks.stop`** — that name is private to the hooks package by its own leading
underscore, the same boundary `shipgate.discipline.session`'s own docstring already
states and follows for `open_project_ledger`/`utc_now_iso`. Three lines, not worth
reaching across a private boundary for.

**Public (not `_`-prefixed) on purpose, unlike this module's other helpers:** reading
the marker does not require the ledger to be open at all — it's a plain sibling file —
which matters because `shipgate.cli`'s `report`/`status` commands need to read it even
when the ledger itself cannot be opened (a live instance of the exact scenario P12
exists for, discovered live while demonstrating this module: a corrupt ledger used to
crash the CLI into a bare traceback instead of reporting the P12 state a stranger
actually needs to see — see `shipgate/cli.py`'s `_open_ledger_or_report_p12`).

**Token counts, not dollar cost — a conscious, stated scope decision, not a silent
gap.** `shipgate/analyze/` is confirmed (by inspection, not assumption) to be an empty
stub package with no price table anywhere in the repo; "cost per verified outcome"
(report §8.1) needs task 1.6's price table, which does not exist. Rendering a dollar
figure here would mean inventing pricing machinery that is explicitly unbuilt Phase-1
scope. What IS true and already on every `events` row: real token counts. This module
reports those, honestly labelled as token counts, not cost — `render.py` states the gap
inline rather than fabricating or silently dropping the line.

Zero Claude-Code-specific imports — core-purity contract, `pyproject.toml`. Depends on
`shipgate.ledger` and `shipgate.verdicts` only.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from shipgate.ledger.integrity import ChainTamperedError, verify_all_chains
from shipgate.ledger.writer import LedgerWriter
from shipgate.verdicts import Verdict

#: Duplicated from `shipgate/hooks/stop.py` on purpose — see the module docstring's P12
#: paragraph for why this isn't imported instead.
_GATE_UNAVAILABLE_MARKER_RELPATH = Path(".shipgate") / "gate_unavailable.json"

#: Finding 7 fix — verdict classes `shipgate.gate.orchestrator` never marks
#: `advisory_only` (only `QUARANTINED_FLAKY`, and the `UNVERIFIED` it lifts back to, are
#: ever advisory — see that module's design decision 4). A claim carrying one of these
#: classes is always a real, counted, non-advisory outcome, so it can never legitimately
#: coexist with a stored `all_dispatchable_green=True` on the same session. Lives here
#: (not `render.py`) because this is a fact about the data, not a rendering choice —
#: both `render.py`'s banner and `shipgate.cli`'s exit code depend on it, and an exit
#: code is exactly the kind of "what's actually true" that a wording change must never
#: be able to move.
_INCOMPATIBLE_WITH_GREEN = frozenset(
    {
        Verdict.CONTRADICTED.value,
        Verdict.UNVERIFIED_VACUOUS.value,
        Verdict.TARGET_UNREACHABLE.value,
        Verdict.PENDING_RECHECK.value,
    }
)

#: Which ledger table the Ship Report's receipt is drawn from. `verdicts`, not `events`
#: or `claims` — it's the table a reader actually cares about ("what was decided"), and
#: the one `shipgate.ledger.integrity.verify_chain` already knows how to walk.
RECEIPT_TABLE = "verdicts"

_RECEIPT_ID_COLUMN = {"verdicts": "verdict_id", "claims": "claim_id", "events": "event_id"}


@dataclass(frozen=True)
class ClaimRow:
    """One claim from this session and its current verdict. `verdict is None` means
    the claim has no verdict row yet — the schema's own documented default
    (`Verdict.UNVERIFIED` conceptually, report §5.4) — reported as storage fact here,
    exactly like `LedgerWriter.current_verdict` does, not silently defaulted."""

    claim_id: str
    text: str
    verdict: str | None
    evidence_tier: str | None
    reason: str


@dataclass(frozen=True)
class GateUnavailableInfo:
    """Session 013 / P12's marker, read as-is. `None` (not this dataclass) means no
    marker file exists — the gate has not failed to open its own ledger since the last
    time it healed, or ever."""

    consecutive_failures: int
    last_error: str
    first_seen: str
    last_seen: str
    exhausted: bool


@dataclass(frozen=True)
class LedgerReceipt:
    """P11(a): the checkable range. `first_id`/`last_id` are `None` only when the table
    has zero rows — a brand-new project's ledger before any verdict has ever been
    written; `render.py` states this plainly rather than printing a hash for nothing."""

    table: str
    first_id: int | None
    first_row_hash: str | None
    last_id: int | None
    last_row_hash: str | None


@dataclass(frozen=True)
class VerifyResult:
    """The outcome of actually re-deriving the chain (`shipgate report --verify`), not
    just reading stored hashes. `verified=True` with growth noted is a real, distinct
    outcome from `verified=True` with no growth — both are "not tampered," but they are
    different claims and `detail` says which one this is, never blurred into one word."""

    verified: bool
    detail: str


@dataclass(frozen=True)
class ShipReportData:
    project_dir: Path
    session_id: str
    claims: list[ClaimRow]
    latest_gate_evaluation: dict | None
    gate_unavailable: GateUnavailableInfo | None
    blast_radius_count: int
    tokens_input: int
    tokens_output: int
    tokens_cache_read: int
    ledger_receipt: LedgerReceipt


def read_gate_unavailable_marker(project_dir: Path) -> GateUnavailableInfo | None:
    path = project_dir / _GATE_UNAVAILABLE_MARKER_RELPATH
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return GateUnavailableInfo(
        consecutive_failures=raw.get("consecutive_failures", 0),
        last_error=raw.get("last_error", ""),
        first_seen=raw.get("first_seen", ""),
        last_seen=raw.get("last_seen", ""),
        exhausted=bool(raw.get("exhausted", False)),
    )


def _latest_gate_evaluation(conn: sqlite3.Connection, session_id: str) -> dict | None:
    row = conn.execute(
        "SELECT raw_payload_redacted FROM events WHERE session_id = ? AND record_type = 'gate_evaluation' "
        "ORDER BY event_id DESC LIMIT 1",
        (session_id,),
    ).fetchone()
    if row is None or row[0] is None:
        return None
    return json.loads(row[0])


def _claims_for_session(conn: sqlite3.Connection, session_id: str) -> list[ClaimRow]:
    """Each claim joined against its OWN latest verdict row (a correlated subquery, not
    a plain join against the whole `verdicts` table) — a claim with more than one
    verdict row over its history must not fan out into duplicate report rows."""
    rows = conn.execute(
        "SELECT c.claim_id, c.text, v.verdict, v.evidence_tier, v.reason "
        "FROM claims c LEFT JOIN verdicts v ON v.verdict_id = ("
        "  SELECT verdict_id FROM verdicts WHERE claim_id = c.claim_id ORDER BY verdict_id DESC LIMIT 1"
        ") WHERE c.session_id = ? ORDER BY c.created_at, c.claim_id",
        (session_id,),
    ).fetchall()
    return [
        ClaimRow(claim_id=r[0], text=r[1], verdict=r[2], evidence_tier=r[3], reason=r[4] or "") for r in rows
    ]


def _blast_radius_count(conn: sqlite3.Connection, session_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM events WHERE session_id = ? AND record_type = 'high_risk_change'",
        (session_id,),
    ).fetchone()
    return row[0]


def _token_totals(conn: sqlite3.Connection, session_id: str) -> tuple[int, int, int]:
    row = conn.execute(
        "SELECT COALESCE(SUM(tokens_input), 0), COALESCE(SUM(tokens_output), 0), "
        "COALESCE(SUM(tokens_cache_read), 0) FROM events WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    return row[0], row[1], row[2]


def gather_ledger_receipt(conn: sqlite3.Connection, table: str = RECEIPT_TABLE) -> LedgerReceipt:
    id_col = _RECEIPT_ID_COLUMN[table]
    first = conn.execute(f"SELECT {id_col}, row_hash FROM {table} ORDER BY rowid ASC LIMIT 1").fetchone()
    last = conn.execute(f"SELECT {id_col}, row_hash FROM {table} ORDER BY rowid DESC LIMIT 1").fetchone()
    return LedgerReceipt(
        table=table,
        first_id=first[0] if first else None,
        first_row_hash=first[1] if first else None,
        last_id=last[0] if last else None,
        last_row_hash=last[1] if last else None,
    )


def verify_receipt(conn: sqlite3.Connection, receipt: LedgerReceipt) -> VerifyResult:
    """P11(a)'s actual verification work — `shipgate report --verify`.

    **Finding 7 fix:** re-derives the ENTIRE ledger's hash chain — `events`, `claims`,
    AND `verdicts` (`shipgate.ledger.integrity.verify_all_chains`, already built,
    already tested; no new hashing scheme, no new subsystem) — not just the table
    `receipt` happens to be drawn from. The banner, blast-radius count, and token totals
    are all read from `events`; checking only `verdicts` left every one of those numbers
    outside what `--verify` could actually attest to. If `verify_all_chains` doesn't
    raise, every row in every chained table has already been proven to match its
    recomputed hash — so a mismatch in the receipt-row check below can only mean the
    embedded row itself no longer exists at that id, never a value that silently drifted
    without the chain noticing.
    """
    if receipt.last_id is None:
        return VerifyResult(True, f"no {receipt.table} rows exist yet — nothing to verify, honestly reported")

    try:
        verify_all_chains(conn)
    except ChainTamperedError as exc:
        return VerifyResult(False, str(exc))

    id_col = _RECEIPT_ID_COLUMN[receipt.table]
    row = conn.execute(f"SELECT row_hash FROM {receipt.table} WHERE {id_col} = ?", (receipt.last_id,)).fetchone()
    if row is None or row[0] != receipt.last_row_hash:
        return VerifyResult(
            False,
            f"the row this report's range ends at ({id_col}={receipt.last_id}) no longer matches what was "
            "embedded — the receipt does not match the current ledger",
        )

    current = gather_ledger_receipt(conn, receipt.table)
    if current.last_id is not None and current.last_id > receipt.last_id:
        grown_by = current.last_id - receipt.last_id
        return VerifyResult(
            True,
            f"entire ledger hash chain (events, claims, verdicts) intact; the embedded {receipt.table} "
            f"range matches exactly; {grown_by} newer {receipt.table} row(s) have been recorded since "
            "this report was generated",
        )
    return VerifyResult(
        True,
        f"entire ledger hash chain (events, claims, verdicts) intact; the embedded {receipt.table} range "
        "matches the ledger exactly, unchanged",
    )


def find_green_inconsistency(data: ShipReportData) -> ClaimRow | None:
    """Finding 7 fix: `None` means the stored evaluation and the claims table agree (the
    ordinary case, including a genuine green). A non-`None` `ClaimRow` means the stored
    `gate_evaluation` event says `all_dispatchable_green=True` while this claim's own
    current verdict is one `_INCOMPATIBLE_WITH_GREEN` says can never be true at the same
    time — the two facts this report is built from disagree, and neither `render.py`'s
    banner nor `shipgate.cli`'s exit code may report GREEN in that state. Returns the
    first disagreeing claim found (deterministic claim ordering, `_claims_for_session`'s
    own `ORDER BY`), not every one — one is enough to prove the report cannot be trusted
    as printed; `--verify` is what names the exact tampered row."""
    evaluation = data.latest_gate_evaluation
    if evaluation is None or not evaluation.get("all_dispatchable_green"):
        return None
    return next((c for c in data.claims if c.verdict in _INCOMPATIBLE_WITH_GREEN), None)


def gather_report_data(project_dir: Path, writer: LedgerWriter, session_id: str) -> ShipReportData:
    conn = writer.connection
    tokens_input, tokens_output, tokens_cache_read = _token_totals(conn, session_id)
    return ShipReportData(
        project_dir=project_dir,
        session_id=session_id,
        claims=_claims_for_session(conn, session_id),
        latest_gate_evaluation=_latest_gate_evaluation(conn, session_id),
        gate_unavailable=read_gate_unavailable_marker(project_dir),
        blast_radius_count=_blast_radius_count(conn, session_id),
        tokens_input=tokens_input,
        tokens_output=tokens_output,
        tokens_cache_read=tokens_cache_read,
        ledger_receipt=gather_ledger_receipt(conn),
    )


__all__ = [
    "RECEIPT_TABLE",
    "ClaimRow",
    "GateUnavailableInfo",
    "LedgerReceipt",
    "ShipReportData",
    "VerifyResult",
    "find_green_inconsistency",
    "gather_ledger_receipt",
    "gather_report_data",
    "read_gate_unavailable_marker",
    "verify_receipt",
]

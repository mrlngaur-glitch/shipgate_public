"""`shipgate analyze` — read-only cross-project ledger aggregation.

**Founder-authorized scope addition**, not part of the original Launch Month plan — see
`PHASE_PLAN.md`'s decisions log for the reasoning and the exact authorization. Recorded
there, not built silently as if this had always been in scope.

Reads OTHER projects' own `.shipgate/ledger.db` files — ledgers this process did not
write and never opens for write. Every project's ledger stays sealed and independently
hash-chained; there is no central database, no writer, and this module creates nothing
(hard constraint 1). "Read-only" is enforced structurally, not by writer-method
discipline alone: `open_ledger_readonly` connects via a SQLite `mode=ro` URI, which
SQLite itself refuses to accept a write through — see that function's own docstring, and
`tests/unit/test_analyze.py`'s mtime-and-hash-unchanged regression, which is what this
module's hard constraint 1 is actually proven against, not just asserted.

**Every ledger found is independently hash-chain-verified before anything is read from
it for real** (`shipgate.ledger.integrity.verify_all_chains`, already built, already
tested — no new hashing scheme). A tampered or unreadable ledger is reported as its own
`LedgerFailure`, never silently skipped and never folded into the aggregate totals (hard
constraint 2) — `analyze()`'s own return shape (`projects` and `failures` as two
separate lists) makes accidentally merging the two a type error, not just a discipline
a future call site has to remember.

**The vacuous-pass rule applies here too** (hard constraint 3): `AnalyzeResult.is_vacuous`
is `True` when no ledger was found under any given root at all. A ledger that was found,
opened, and verified but has zero sessions recorded is reported honestly as zero, not
hidden — but it does not by itself make the WHOLE run vacuous the way finding nothing at
all does; `render.py` states each empty project's own "nothing recorded yet" plainly
rather than blending a real zero into a summary table that could be misread as clean.

**Never reads `raw_payload_redacted` for display, only to extract two booleans it has no
other structured column for** (`should_block` / `all_dispatchable_green` /`exhausted`,
on `gate_evaluation` events — hard constraint 4). Verdict reasons come from `verdicts.
reason`, a first-class column, not from parsing payload JSON. Nothing in this module
ever prints, logs, or returns the payload text itself.

No writer, no daemon, no network — this module's only I/O is read-only SQLite queries
against files the caller names explicitly via `--roots`.

Not part of the `import-linter` core-purity contract (that covers `ledger`/`gate`/
`verdicts`/`shipfile` only), but written to the same standard anyway: depends on
`shipgate.ledger` and `shipgate.verdicts` only, zero Claude-Code-specific imports.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from shipgate.ledger.integrity import ChainTamperedError, verify_all_chains
from shipgate.verdicts import ALL_VERDICTS, Verdict

#: Verdict classes that can never legitimately coexist with a green gate — the same
#: semantics as `shipgate.report.data`'s own `_INCOMPATIBLE_WITH_GREEN`, kept as a
#: separate, analyze-local copy rather than importing a private name across a package
#: boundary (the same convention `shipgate.report.data` itself follows for
#: `_GATE_UNAVAILABLE_MARKER_RELPATH`). `QUARANTINED_FLAKY` is deliberately excluded —
#: advisory-only by design (`CLAUDE.md` §3.6), never a real failure signal — and so is
#: `UNVERIFIED`, the default "no evidence either way" state, not itself a failure.
FAILURE_VERDICT_CLASSES: frozenset[str] = frozenset(
    {
        Verdict.CONTRADICTED.value,
        Verdict.UNVERIFIED_VACUOUS.value,
        Verdict.TARGET_UNREACHABLE.value,
        Verdict.PENDING_RECHECK.value,
    }
)


@dataclass(frozen=True)
class TokenMetric:
    """One token counter (input / output / cache-read) — `total` alone is not the whole
    claim. Founder finding: `events.tokens_input`/`tokens_output`/`tokens_cache_read`
    are `NULL` on every event this codebase has ever written — grepped, not assumed:
    none of the 8 `insert_event` call sites in `shipgate/hooks/` or `shipgate/gate/`
    passes a token count; that data doesn't exist yet (a future transcript-ingest task,
    not built). Rendering `0` there reads as "measured, and it was zero" — the exact
    vacuous-pass shape hard constraint 3 exists to catch, just at the per-metric level
    instead of the whole-command level. `recorded` is `True` only if at least one event
    actually carried a non-`NULL` value for this counter; `total` is the real sum over
    whatever *was* recorded, honest either way — `render.py` is what decides how to
    word the difference, this dataclass only carries the fact."""

    total: int
    recorded: bool


@dataclass(frozen=True)
class VerdictReasonCount:
    """One (verdict class, reason text) pair and how many times it was recorded.
    Grouped, not listed — per the founder's own framing: "grouping identical reasons
    and counting them matters more than listing them.\""""

    verdict: str
    reason: str
    count: int


@dataclass(frozen=True)
class ProjectStats:
    """One successfully-read, hash-chain-verified project ledger."""

    ledger_path: Path
    project_slug: str
    session_count: int
    events_by_record_type: dict[str, int]
    claim_count: int
    verdicts_by_class: dict[str, int]  # all 7 keys always present, zero-filled
    tokens_input: TokenMetric
    tokens_output: TokenMetric
    tokens_cache_read: TokenMetric
    first_activity: str | None
    last_activity: str | None
    gate_evaluations_total: int
    gate_evaluations_green: int
    failure_reason_counts: list[VerdictReasonCount] = field(default_factory=list)


@dataclass(frozen=True)
class LedgerFailure:
    """A ledger that was FOUND but could not be trusted or read — reported separately,
    on its own, never folded into `ProjectStats` totals (hard constraint 2)."""

    ledger_path: Path
    project_slug: str
    kind: str  # "tampered" | "unreadable"
    detail: str


@dataclass(frozen=True)
class AnalyzeResult:
    roots: list[Path]
    projects: list[ProjectStats]
    failures: list[LedgerFailure]

    @property
    def is_vacuous(self) -> bool:
        """`True` only when nothing at all was found under any given root — no ledger
        file, tampered or not, readable or not. Finding ledgers that turn out to be
        tampered/unreadable is NOT vacuous (something real was observed, even if it's
        bad news); finding a genuinely empty-but-healthy project is reported honestly
        per-project by `render.py`, not collapsed into this flag."""
        return not self.projects and not self.failures


def discover_ledgers(roots: list[Path]) -> list[Path]:
    """`<root>/*/.shipgate/ledger.db` for every root given, deduplicated (the same
    resolved path found under two different `--roots` values counts once) and sorted
    for deterministic output. A root that doesn't exist is silently skipped at this
    layer — an empty glob is not an error here; `AnalyzeResult.is_vacuous` is what
    applies the vacuous-pass rule to the final zero-ledgers-found case, once across all
    roots together, not per root."""
    found: set[Path] = set()
    for raw_root in roots:
        root = Path(raw_root)
        if not root.is_dir():
            continue
        for candidate in root.glob("*/.shipgate/ledger.db"):
            if candidate.is_file():
                found.add(candidate.resolve())
    return sorted(found)


def open_ledger_readonly(db_path: Path) -> sqlite3.Connection:
    """Opens `db_path` via a SQLite `mode=ro` URI connection — refused at the SQLite/OS
    level if anything ever attempted a write through it, not merely "nothing in this
    module happens to call an insert method." This is hard constraint 1's actual
    enforcement mechanism; `tests/unit/test_analyze.py` proves it by asserting the
    ledger file's mtime and sha256 are byte-identical before and after a full `analyze()`
    run, not by asserting on this function's implementation.

    `immutable=0` (SQLite's default) is left as-is deliberately: `mode=ro` alone is
    already proven sufficient by that same regression test; `immutable=1` would
    additionally disable SQLite's own change-detection locking, an extra assumption not
    worth taking for a read pass that already never intends to write.
    """
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _project_slug_from_ledger_path(ledger_path: Path) -> str:
    """`<root>/<slug>/.shipgate/ledger.db` -> `<slug>`."""
    return ledger_path.parent.parent.name


def _verdicts_by_class(conn: sqlite3.Connection) -> dict[str, int]:
    counts: dict[str, int] = dict.fromkeys((v.value for v in ALL_VERDICTS), 0)
    rows = conn.execute("SELECT verdict, COUNT(*) FROM verdicts GROUP BY verdict").fetchall()
    for verdict, count in rows:
        counts[verdict] = count
    return counts


def _events_by_record_type(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT record_type, COUNT(*) FROM events GROUP BY record_type ORDER BY record_type"
    ).fetchall()
    return {record_type: count for record_type, count in rows}


def _token_totals(conn: sqlite3.Connection) -> tuple[TokenMetric, TokenMetric, TokenMetric]:
    """`COUNT(col)` — not `COUNT(*)` — counts only the non-`NULL` rows for that column,
    which is exactly "was this ever recorded at all," distinct from `SUM`'s "what did
    the recorded rows add up to." See `TokenMetric`'s own docstring for why this
    distinction is load-bearing here and not just a style choice."""
    row = conn.execute(
        "SELECT COALESCE(SUM(tokens_input), 0), COUNT(tokens_input), "
        "COALESCE(SUM(tokens_output), 0), COUNT(tokens_output), "
        "COALESCE(SUM(tokens_cache_read), 0), COUNT(tokens_cache_read) FROM events"
    ).fetchone()
    return (
        TokenMetric(total=row[0], recorded=row[1] > 0),
        TokenMetric(total=row[2], recorded=row[3] > 0),
        TokenMetric(total=row[4], recorded=row[5] > 0),
    )


def _activity_window(conn: sqlite3.Connection) -> tuple[str | None, str | None]:
    """First/last activity — **deliberately `events.timestamp`, not `sessions.
    started_at`/`ended_at`.** Founder finding, confirmed against a real hook-written
    ledger (`sessions.started_at`/`ended_at` both `NULL` in every row): `shipgate.
    hooks._common.ensure_session` — the one production code path that ever inserts a
    `sessions` row — calls `insert_session` without either column, so on every ledger
    this project's own hooks have ever written, both are `NULL`, always, structurally,
    not occasionally. The original version of this function read them anyway and
    passed only because its own test hand-seeded `started_at` directly via
    `LedgerWriter` — a state production cannot reach. Root-caused, not papered over:
    the smaller of the two possible fixes (`data.py`-only vs. teaching `ensure_session`
    to record `started_at`, a hook-write-behaviour change) was chosen because it closes
    the gap completely, not partially — every one of this codebase's 8 `insert_event`
    call sites was enumerated (`shipgate/hooks/pretooluse.py`, `posttooluse.py`,
    `stop.py` x2, `shipgate/gate/orchestrator.py`, `shipgate/gate/blast_radius.py` x3)
    and every one of them passes a real `timestamp` explicitly — `events.timestamp` is
    not just a workaround, it is a strictly richer signal than `sessions.started_at`/
    `ended_at` ever were: every event in a session bounds the window, not just its
    first and last hook fire."""
    row = conn.execute("SELECT MIN(timestamp), MAX(timestamp) FROM events WHERE timestamp IS NOT NULL").fetchone()
    return row[0], row[1]


def _gate_evaluations(conn: sqlite3.Connection) -> tuple[int, int]:
    """Total `gate_evaluation` events recorded, and how many were green — read from each
    event's own structured fields (`should_block` / `exhausted` / `all_dispatchable_
    green`), the same fields `shipgate.cli`'s `status`/`report` commands already key
    off of. **Never returns or prints the payload text itself** (hard constraint 4) —
    only the two booleans this module has no other structured column to read them from.
    A payload that fails to parse as JSON still counts toward the total (something was
    recorded) but not toward green (unproven, not assumed green)."""
    rows = conn.execute(
        "SELECT raw_payload_redacted FROM events WHERE record_type = 'gate_evaluation' "
        "AND raw_payload_redacted IS NOT NULL"
    ).fetchall()
    total = 0
    green = 0
    for (payload_text,) in rows:
        total += 1
        try:
            payload = json.loads(payload_text)
        except (TypeError, ValueError):
            continue
        is_green = (
            bool(payload.get("all_dispatchable_green"))
            and not payload.get("should_block")
            and not payload.get("exhausted")
        )
        if is_green:
            green += 1
    return total, green


def _failure_reason_counts(conn: sqlite3.Connection) -> list[VerdictReasonCount]:
    """Grouped `(verdict, reason)` counts, restricted to `FAILURE_VERDICT_CLASSES` — see
    that constant's own docstring for which classes and why."""
    placeholders = ", ".join("?" for _ in FAILURE_VERDICT_CLASSES)
    rows = conn.execute(
        f"SELECT verdict, reason, COUNT(*) AS n FROM verdicts WHERE verdict IN ({placeholders}) "
        "GROUP BY verdict, reason ORDER BY n DESC, verdict, reason",
        tuple(FAILURE_VERDICT_CLASSES),
    ).fetchall()
    return [VerdictReasonCount(verdict=v, reason=r or "", count=n) for v, r, n in rows]


def gather_one_ledger(ledger_path: Path) -> ProjectStats | LedgerFailure:
    """Opens, verifies, and reads exactly one ledger, read-only throughout. Returns a
    `LedgerFailure` — never raises, never silently returns a zeroed `ProjectStats` — for
    every way this can go wrong: the file isn't a SQLite database at all, one of the
    five tables is missing, or the hash chain itself is broken."""
    slug = _project_slug_from_ledger_path(ledger_path)
    try:
        conn = open_ledger_readonly(ledger_path)
        conn.execute("SELECT 1").fetchone()  # mode=ro connections open lazily; force it now
    except sqlite3.Error as exc:
        return LedgerFailure(ledger_path=ledger_path, project_slug=slug, kind="unreadable", detail=str(exc))

    try:
        try:
            verify_all_chains(conn)
        except ChainTamperedError as exc:
            return LedgerFailure(ledger_path=ledger_path, project_slug=slug, kind="tampered", detail=str(exc))
        except sqlite3.Error as exc:
            # The file opened, but isn't shaped like a ShipGate ledger (missing a
            # table, wrong schema) — unreadable, never silently treated as "0 rows".
            return LedgerFailure(ledger_path=ledger_path, project_slug=slug, kind="unreadable", detail=str(exc))

        try:
            session_count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            claim_count = conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
            events_by_type = _events_by_record_type(conn)
            verdicts_by_class = _verdicts_by_class(conn)
            tokens_input, tokens_output, tokens_cache_read = _token_totals(conn)
            first_activity, last_activity = _activity_window(conn)
            gate_total, gate_green = _gate_evaluations(conn)
            failure_reasons = _failure_reason_counts(conn)
        except sqlite3.Error as exc:
            return LedgerFailure(ledger_path=ledger_path, project_slug=slug, kind="unreadable", detail=str(exc))
    finally:
        conn.close()

    return ProjectStats(
        ledger_path=ledger_path,
        project_slug=slug,
        session_count=session_count,
        events_by_record_type=events_by_type,
        claim_count=claim_count,
        verdicts_by_class=verdicts_by_class,
        tokens_input=tokens_input,
        tokens_output=tokens_output,
        tokens_cache_read=tokens_cache_read,
        first_activity=first_activity,
        last_activity=last_activity,
        gate_evaluations_total=gate_total,
        gate_evaluations_green=gate_green,
        failure_reason_counts=failure_reasons,
    )


def analyze(roots: list[Path]) -> AnalyzeResult:
    """The entry point. Discovers every ledger under every given root, reads each one
    independently (one failure never affects another ledger's own result), and returns
    the full, ungrouped set — `render.py` decides how to display it, this function only
    gathers what's true."""
    ledger_paths = discover_ledgers(roots)
    projects: list[ProjectStats] = []
    failures: list[LedgerFailure] = []
    for path in ledger_paths:
        outcome = gather_one_ledger(path)
        if isinstance(outcome, LedgerFailure):
            failures.append(outcome)
        else:
            projects.append(outcome)
    return AnalyzeResult(roots=[Path(r) for r in roots], projects=projects, failures=failures)


@dataclass(frozen=True)
class AggregatedTokenMetric:
    """Cross-project sum of one token counter — summed only over the projects that
    actually recorded it, never silently blending an unrecorded project in as a real
    zero. `recorded_project_count`/`total_project_count` let `render.py` distinguish
    "not recorded anywhere," "recorded everywhere," and "recorded in some but not all"
    — collapsing those three into one number would repeat Finding 2's own mistake one
    level up, at the cross-project rollup instead of the per-project reading."""

    total: int
    recorded_project_count: int
    total_project_count: int


def aggregate_token_metric(projects: list[ProjectStats], attr: str) -> AggregatedTokenMetric:
    metrics: list[TokenMetric] = [getattr(project, attr) for project in projects]
    recorded = [m for m in metrics if m.recorded]
    return AggregatedTokenMetric(
        total=sum(m.total for m in recorded),
        recorded_project_count=len(recorded),
        total_project_count=len(metrics),
    )


def aggregate_verdicts_by_class(projects: list[ProjectStats]) -> dict[str, int]:
    totals: dict[str, int] = dict.fromkeys((v.value for v in ALL_VERDICTS), 0)
    for project in projects:
        for verdict, count in project.verdicts_by_class.items():
            totals[verdict] += count
    return totals


def aggregate_failure_reasons(
    projects: list[ProjectStats], *, limit: int | None = None
) -> list[VerdictReasonCount]:
    """Cross-project grouped failure reasons — the same `(verdict, reason)` merged
    across every project, not one list per project concatenated. `limit=None` (the
    default) returns every distinct reason; a caller that limits display rows is
    responsible for saying how many were left out (no silent caps — `CLAUDE.md`'s own
    inventory rule, applied to this module's own output)."""
    merged: dict[tuple[str, str], int] = {}
    for project in projects:
        for item in project.failure_reason_counts:
            key = (item.verdict, item.reason)
            merged[key] = merged.get(key, 0) + item.count
    ordered = sorted(
        (VerdictReasonCount(verdict=v, reason=r, count=n) for (v, r), n in merged.items()),
        key=lambda item: (-item.count, item.verdict, item.reason),
    )
    return ordered[:limit] if limit is not None else ordered


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
]

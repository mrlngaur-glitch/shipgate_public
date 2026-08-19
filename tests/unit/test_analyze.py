"""`shipgate analyze` — the data layer (`shipgate/analyze/data.py`). Founder-authorized
scope addition; see `PHASE_PLAN.md`'s decisions log. Every test here is runtime-verified
by definition — pytest actually executes it.

Organized around the five hard constraints named at authorization time:
  1. READ-ONLY — `test_ledger_file_is_byte_identical_after_a_full_analyze_run`
  2. Every ledger verified, failures reported separately — the `test_tampered_*` and
     `test_unreadable_*` tests
  3. The vacuous-pass rule — `test_no_ledgers_found_anywhere_is_vacuous` and
     `test_ledgers_found_but_all_empty_is_not_the_same_as_vacuous`
  4. Never print raw payloads — `test_render_never_prints_the_raw_payload_text`
  5. No daemon, no network — `test_analyze_package_imports_nothing_network_capable`

Plus **Findings 1 and 2** (founder review, real-data verification against a real,
hook-written ledger from a live project): `_seed_healthy_ledger` was hand-seeding
`sessions.started_at`/`ended_at` and
`events.tokens_*` — columns no production code path ever populates — which hid both
defects from every test in this file. The fixture now deliberately mirrors production's
real column population (see its own docstring), and
`test_real_hook_written_ledger_gets_a_populated_activity_window_and_unrecorded_tokens`
builds its fixture by actually driving `shipgate.hooks.pretooluse` as a real subprocess,
per the founder's explicit instruction that at least one test do so rather than
hand-seed columns.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from rich.console import Console

from shipgate.analyze import (
    ProjectStats,
    TokenMetric,
    aggregate_failure_reasons,
    aggregate_token_metric,
    aggregate_verdicts_by_class,
    analyze,
    discover_ledgers,
    render_analyze,
)
from shipgate.analyze.render import _format_aggregated_token_metric, _format_token_metric
from shipgate.ledger.writer import LedgerWriter
from shipgate.verdicts import EvidenceTier, Verdict


def _make_project(root: Path, slug: str) -> Path:
    """`<root>/<slug>/.shipgate/ledger.db`, matching the real on-disk shape
    `shipgate init` produces in a real project."""
    ledger_dir = root / slug / ".shipgate"
    ledger_dir.mkdir(parents=True)
    return ledger_dir / "ledger.db"


def _seed_healthy_ledger(
    db_path: Path,
    *,
    session_id: str = "s1",
    n_contradicted: int = 2,
    n_verified: int = 1,
    gate_payload_marker: str = "",
    event_timestamp: str = "2026-08-15T00:01:00Z",
) -> None:
    """A small, realistic ledger: one session, one gate_evaluation event (with the
    given secret-shaped marker buried in its tool_input, to prove hard constraint 4),
    and a mix of CONTRADICTED/VERIFIED claims — matching the founder's own described
    data shape (two of three pilots render `contradicted` every Stop; one renders
    `verified` on a collection-only proxy condition).

    **Deliberately mirrors production's real column population, not a convenient
    fixture shape** (Findings 1 and 2, founder review): `insert_session` is called
    WITHOUT `started_at`/`ended_at` — exactly what `shipgate.hooks._common.
    ensure_session` does, and the only thing that ever inserts a `sessions` row in
    production — so those two columns are `NULL` here too, same as on every real
    ledger. `insert_event` passes `timestamp=` (every one of this codebase's 8 real
    call sites does) but never `tokens_input`/`tokens_output`/`tokens_cache_read`
    (no call site anywhere ever does). A fixture that hand-seeds a column production
    cannot populate is how Finding 1 got past review in the first place — this
    docstring exists so the next person editing this fixture sees why those two
    absences are load-bearing, not accidental."""
    writer = LedgerWriter(db_path)
    try:
        writer.insert_session(
            session_id=session_id,
            project_slug="proj",
            source_dir=f"proj/{session_id}",
        )
        event_id = writer.insert_event(
            session_id=session_id,
            source_file=f"proj/{session_id}.jsonl",
            source_offset=0,
            transcript_tier="session",
            record_type="assistant",
            timestamp=event_timestamp,
        )
        writer.insert_event(
            session_id=session_id,
            source_file=f"proj/{session_id}.jsonl",
            source_offset=1,
            transcript_tier="session",
            record_type="gate_evaluation",
            timestamp=event_timestamp,
            raw_payload={
                "should_block": n_contradicted > 0,
                "all_dispatchable_green": n_contradicted == 0,
                "exhausted": False,
                "conditions": [{"tool_input": gate_payload_marker}] if gate_payload_marker else [],
            },
        )
        for i in range(n_contradicted):
            claim_id = f"c-contra-{session_id}-{i}"
            writer.insert_claim(
                claim_id=claim_id, session_id=session_id, text=f"claim {i}",
                source="extracted_from_transcript", source_event_id=event_id,
                created_at="2026-08-15T00:01:00Z",
            )
            writer.insert_verdict(
                claim_id=claim_id, verdict=Verdict.CONTRADICTED,
                reason="pytest genuinely not installed", created_at="2026-08-15T00:01:00Z",
            )
        for i in range(n_verified):
            claim_id = f"c-verified-{session_id}-{i}"
            writer.insert_claim(
                claim_id=claim_id, session_id=session_id, text=f"verified claim {i}",
                source="extracted_from_transcript", source_event_id=event_id,
                created_at="2026-08-15T00:01:00Z",
            )
            writer.insert_verdict(
                claim_id=claim_id, verdict=Verdict.VERIFIED, evidence_tier=EvidenceTier.RUNTIME_VERIFIED,
                reason="collection-only proxy condition", created_at="2026-08-15T00:01:00Z",
            )
    finally:
        writer.close()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- hard constraint 1: read-only, proven not asserted ------------------------------


def test_ledger_file_is_byte_identical_after_a_full_analyze_run(tmp_path):
    db_path = _make_project(tmp_path, "proj-a")
    _seed_healthy_ledger(db_path)

    mtime_before = db_path.stat().st_mtime_ns
    hash_before = _sha256(db_path)

    result = analyze([tmp_path])
    assert len(result.projects) == 1  # sanity: the run actually read this ledger

    mtime_after = db_path.stat().st_mtime_ns
    hash_after = _sha256(db_path)
    assert mtime_before == mtime_after
    assert hash_before == hash_after


def test_open_ledger_readonly_refuses_a_real_write_attempt(tmp_path):
    """Structural proof, not just "nothing calls insert": a write attempted through the
    read-only connection this module actually uses is refused by SQLite itself."""
    db_path = _make_project(tmp_path, "proj-a")
    _seed_healthy_ledger(db_path)

    from shipgate.analyze.data import open_ledger_readonly

    conn = open_ledger_readonly(db_path)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO sessions (session_id, project_slug, source_dir) VALUES ('x','x','x')")
    finally:
        conn.close()


# --- hard constraint 2: every ledger verified, failures reported separately ---------


def test_tampered_ledger_reported_as_failure_not_folded_into_totals(tmp_path):
    healthy_path = _make_project(tmp_path, "proj-healthy")
    _seed_healthy_ledger(healthy_path)

    tampered_path = _make_project(tmp_path, "proj-tampered")
    _seed_healthy_ledger(tampered_path)
    w = LedgerWriter(tampered_path)
    w.connection.execute("DROP TRIGGER verdicts_no_update")
    w.connection.execute("UPDATE verdicts SET verdict = 'verified', evidence_tier = 'runtime-verified'")
    w.connection.commit()
    w.close()

    result = analyze([tmp_path])

    assert len(result.projects) == 1
    assert result.projects[0].project_slug == "proj-healthy"
    assert len(result.failures) == 1
    failure = result.failures[0]
    assert failure.project_slug == "proj-tampered"
    assert failure.kind == "tampered"
    assert "verdicts" in failure.detail
    assert result.is_vacuous is False  # something real was found, this is not "nothing observed"


def test_unreadable_ledger_reported_as_failure_not_silently_skipped(tmp_path):
    garbage_path = _make_project(tmp_path, "proj-garbage")
    garbage_path.write_bytes(b"not a sqlite database at all")

    result = analyze([tmp_path])

    assert result.projects == []
    assert len(result.failures) == 1
    assert result.failures[0].project_slug == "proj-garbage"
    assert result.failures[0].kind == "unreadable"


def test_a_healthy_and_a_tampered_project_both_appear_and_are_never_confused(tmp_path):
    healthy_path = _make_project(tmp_path, "proj-a")
    _seed_healthy_ledger(healthy_path)
    garbage_path = _make_project(tmp_path, "proj-b")
    garbage_path.write_bytes(b"garbage")

    console = Console(record=True, width=140)
    result = analyze([tmp_path])
    render_analyze(result, console)
    text = console.export_text()

    assert "proj-a" in text
    assert "proj-b" in text
    assert "UNREADABLE" in text


# --- hard constraint 3: the vacuous-pass rule ---------------------------------------


def test_no_ledgers_found_anywhere_is_vacuous(tmp_path):
    result = analyze([tmp_path])
    assert result.is_vacuous is True
    assert result.projects == []
    assert result.failures == []

    console = Console(record=True, width=140)
    render_analyze(result, console)
    text = console.export_text()
    assert "OBSERVED NOTHING" in text


def test_a_root_that_does_not_exist_is_also_vacuous_not_an_error(tmp_path):
    result = analyze([tmp_path / "does-not-exist"])
    assert result.is_vacuous is True


def test_ledgers_found_but_all_empty_is_not_the_same_as_vacuous(tmp_path):
    """A project ledger that exists, opens, and verifies clean but has zero sessions
    recorded is real information (a project that has never run the gate) — not the
    same 'nothing found at all' case, and must not render as an empty, clean-looking
    summary."""
    empty_path = _make_project(tmp_path, "proj-empty")
    LedgerWriter(empty_path).close()  # creates the schema, writes nothing

    result = analyze([tmp_path])
    assert result.is_vacuous is False
    assert len(result.projects) == 1
    assert result.projects[0].session_count == 0

    console = Console(record=True, width=140)
    render_analyze(result, console)
    text = console.export_text()
    assert "0 sessions recorded" in text
    assert "nothing has happened in any of them yet" in text


def test_verdicts_by_class_always_reports_all_seven_even_at_zero(tmp_path):
    db_path = _make_project(tmp_path, "proj-a")
    _seed_healthy_ledger(db_path, n_contradicted=2, n_verified=0)

    result = analyze([tmp_path])
    counts = result.projects[0].verdicts_by_class
    assert counts[Verdict.CONTRADICTED.value] == 2
    for verdict in Verdict:
        assert verdict.value in counts  # every class present, zero-filled if unused
    assert counts[Verdict.QUARANTINED_FLAKY.value] == 0


# --- hard constraint 4: never print raw payloads ------------------------------------


def test_render_never_prints_the_raw_payload_text(tmp_path):
    db_path = _make_project(tmp_path, "proj-a")
    secret_marker = "sk-TOTALLY-NOT-A-REAL-SECRET-abcdef1234567890"
    _seed_healthy_ledger(db_path, gate_payload_marker=secret_marker)

    result = analyze([tmp_path])
    console = Console(record=True, width=140)
    render_analyze(result, console)
    text = console.export_text()

    # The marker is inside raw_payload_redacted's own JSON (a *_input field, itself
    # already redacted at write time) — analyze must never dump that JSON regardless.
    assert secret_marker not in text
    assert "tool_input" not in text
    assert "raw_payload" not in text


def test_gather_one_ledger_never_returns_the_raw_payload_on_the_dataclass(tmp_path):
    """Structural check on the data layer itself, not just the render: ProjectStats has
    no field that could carry payload text through to a future, less careful caller."""
    field_names = {f for f in ProjectStats.__dataclass_fields__}
    assert not any("payload" in f for f in field_names)


# --- hard constraint 5: no daemon, no network ---------------------------------------


def test_analyze_package_imports_nothing_network_capable():
    import shipgate.analyze.data as data_module
    import shipgate.analyze.render as render_module

    forbidden = ("socket", "http", "urllib", "requests", "ssl")
    for module in (data_module, render_module):
        source = Path(module.__file__).read_text(encoding="utf-8")
        for name in forbidden:
            assert f"import {name}" not in source
            assert f"from {name}" not in source


def test_analyze_never_references_the_default_dogfood_corpus_root():
    """Hard constraint 6: the dogfood corpus (~/.claude/projects/) is read-only and out
    of scope for this command — `discover_ledgers` only ever globs the roots it was
    explicitly given."""
    source = Path("shipgate/analyze/data.py").read_text(encoding="utf-8")
    assert "DEFAULT_CORPUS_ROOT" not in source
    assert ".claude" not in source


# --- discovery, dedup, and aggregation -----------------------------------------------


def test_discover_ledgers_globs_each_root_and_dedups_across_roots(tmp_path):
    db_path = _make_project(tmp_path, "proj-a")
    _seed_healthy_ledger(db_path)

    found = discover_ledgers([tmp_path, tmp_path])  # same root passed twice
    assert found == [db_path.resolve()]


def test_aggregate_verdicts_by_class_sums_across_projects(tmp_path):
    p1 = _make_project(tmp_path, "proj-a")
    _seed_healthy_ledger(p1, n_contradicted=2, n_verified=0)
    p2 = _make_project(tmp_path, "proj-b")
    _seed_healthy_ledger(p2, session_id="s2", n_contradicted=1, n_verified=1)

    result = analyze([tmp_path])
    totals = aggregate_verdicts_by_class(result.projects)
    assert totals[Verdict.CONTRADICTED.value] == 3
    assert totals[Verdict.VERIFIED.value] == 1


def test_aggregate_failure_reasons_groups_identical_reasons_across_projects(tmp_path):
    p1 = _make_project(tmp_path, "proj-a")
    _seed_healthy_ledger(p1, n_contradicted=2, n_verified=0)
    p2 = _make_project(tmp_path, "proj-b")
    _seed_healthy_ledger(p2, session_id="s2", n_contradicted=1, n_verified=0)

    result = analyze([tmp_path])
    reasons = aggregate_failure_reasons(result.projects)
    matching = [r for r in reasons if r.reason == "pytest genuinely not installed"]
    assert len(matching) == 1  # grouped, not one row per project
    assert matching[0].count == 3  # 2 + 1 across both projects


# --- Findings 1 and 2 (founder review): unobserved values must never render as measurements


def test_real_hook_written_ledger_gets_a_populated_activity_window_and_unrecorded_tokens(tmp_path):
    """**Builds its fixture by actually driving `shipgate.hooks.pretooluse` as a real
    subprocess** — exactly how Claude Code invokes it, and exactly how `tests/
    integration/test_hooks_e2e.py` proves the hooks themselves — rather than by
    hand-seeding columns via `LedgerWriter`. This is the test Finding 1 asked for: a
    fixture that cannot express a state production cannot reach, because it IS
    production, one function-call layer down from a live Claude Code session."""
    project_dir = tmp_path / "real-hook-proj"
    project_dir.mkdir()
    payload = {
        "session_id": "real-hook-sess-1",
        "cwd": str(project_dir),
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "pytest -q"},
        "tool_use_id": "tu-real-1",
    }
    proc = subprocess.run(
        [sys.executable, "-m", "shipgate.hooks.pretooluse"],
        input=json.dumps(payload),
        cwd=project_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert proc.returncode == 0, proc.stderr

    result = analyze([tmp_path])
    assert len(result.projects) == 1
    project = result.projects[0]

    # Confirm the fixture is genuinely faithful, not assumed: sessions.started_at IS
    # NULL in this real, hook-written ledger, same as in the founder's own real,
    # hook-written ledger from a live project.
    conn = sqlite3.connect(project_dir / ".shipgate" / "ledger.db")
    raw_started_at = conn.execute("SELECT started_at FROM sessions").fetchone()[0]
    conn.close()
    assert raw_started_at is None

    # The activity window is still populated -- read from events.timestamp, which the
    # real hook DOES set (Finding 1's fix).
    assert project.first_activity is not None
    assert project.last_activity is not None

    # A real hook never records token counts (Finding 2) -- this must read "not
    # recorded", never a silent, measured-looking 0.
    assert project.tokens_input.recorded is False
    assert project.tokens_output.recorded is False
    assert project.tokens_cache_read.recorded is False

    console = Console(record=True, width=140)
    render_analyze(result, console)
    text = console.export_text()
    assert "not recorded" in text
    assert "hooks do not record token counts yet" in text
    assert "0 in / 0 out" not in text  # never render the unobserved value as measured


def test_activity_window_spans_the_min_and_max_of_multiple_events_timestamps(tmp_path):
    db_path = _make_project(tmp_path, "proj-a")
    _seed_healthy_ledger(db_path, session_id="s1", event_timestamp="2026-08-10T00:00:00Z")
    _seed_healthy_ledger(db_path, session_id="s2", event_timestamp="2026-08-15T00:00:00Z")

    result = analyze([tmp_path])
    project = result.projects[0]
    assert project.first_activity == "2026-08-10T00:00:00Z"
    assert project.last_activity == "2026-08-15T00:00:00Z"


def test_tokens_not_recorded_when_the_standard_fixture_is_used(tmp_path):
    """The standard fixture mirrors production (see `_seed_healthy_ledger`'s own
    docstring) — no event it writes ever carries a token count, so every metric must
    read `recorded=False`, not a plausible-looking `0`."""
    db_path = _make_project(tmp_path, "proj-a")
    _seed_healthy_ledger(db_path)

    result = analyze([tmp_path])
    project = result.projects[0]
    assert project.tokens_input == TokenMetric(total=0, recorded=False)
    assert project.tokens_output == TokenMetric(total=0, recorded=False)
    assert project.tokens_cache_read == TokenMetric(total=0, recorded=False)


def test_token_metric_correctly_distinguishes_recorded_from_unrecorded_at_the_sql_layer(tmp_path):
    """A forward-looking test of the mechanism itself, honestly labelled as such: no
    production code path populates `events.tokens_*` today (Finding 2), so this seeds
    a value explicitly to prove `COUNT(col) > 0` correctly reports `recorded=True` once
    something eventually does — not a claim about what today's real ledgers contain."""
    db_path = _make_project(tmp_path, "proj-a")
    writer = LedgerWriter(db_path)
    writer.insert_session(session_id="s1", project_slug="proj", source_dir="proj/s1")
    writer.insert_event(
        session_id="s1", source_file="proj/s1.jsonl", source_offset=0,
        transcript_tier="session", record_type="assistant",
        timestamp="2026-08-15T00:00:00Z", tokens_input=42, tokens_output=7,
    )
    writer.close()

    result = analyze([tmp_path])
    project = result.projects[0]
    assert project.tokens_input == TokenMetric(total=42, recorded=True)
    assert project.tokens_output == TokenMetric(total=7, recorded=True)
    assert project.tokens_cache_read == TokenMetric(total=0, recorded=False)  # never touched


def test_format_token_metric_wording():
    assert _format_token_metric(TokenMetric(total=0, recorded=False)) == "not recorded"
    assert _format_token_metric(TokenMetric(total=1234, recorded=True)) == "1,234"
    assert _format_token_metric(TokenMetric(total=0, recorded=True)) == "0"  # a REAL measured zero


def test_format_aggregated_token_metric_distinguishes_none_all_and_partial_recorded():
    from shipgate.analyze.data import AggregatedTokenMetric

    assert _format_aggregated_token_metric(AggregatedTokenMetric(0, 0, 3)) == "not recorded"
    assert _format_aggregated_token_metric(AggregatedTokenMetric(900, 3, 3)) == "900"
    partial = _format_aggregated_token_metric(AggregatedTokenMetric(500, 2, 3))
    assert "500" in partial
    assert "partial" in partial
    assert "1 of 3" in partial


def test_aggregate_token_metric_sums_only_over_projects_that_recorded_it(tmp_path):
    p1 = _make_project(tmp_path, "proj-a")
    _seed_healthy_ledger(p1)  # standard fixture -- never records tokens
    p2 = _make_project(tmp_path, "proj-b")
    writer = LedgerWriter(p2)
    writer.insert_session(session_id="s2", project_slug="proj", source_dir="proj/s2")
    writer.insert_event(
        session_id="s2", source_file="proj/s2.jsonl", source_offset=0,
        transcript_tier="session", record_type="assistant",
        timestamp="2026-08-15T00:00:00Z", tokens_input=100,
    )
    writer.close()

    result = analyze([tmp_path])
    agg = aggregate_token_metric(result.projects, "tokens_input")
    assert agg.total == 100  # only proj-b's real recorded value, proj-a's absence not blended in as 0
    assert agg.recorded_project_count == 1
    assert agg.total_project_count == 2

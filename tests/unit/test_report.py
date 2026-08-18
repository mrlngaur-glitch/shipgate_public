"""Task 3.4 — the Ship Report's data-gathering layer (`shipgate.report.data`).

Every test runs against a REAL `tmp_path` ledger via `LedgerWriter` — no mocked
connections. `evaluate_gate`/`shipgate.hooks.stop` are exercised for real where the
scenario needs an actual `gate_evaluation` event or `gate_unavailable.json` marker,
rather than hand-building JSON payloads that could silently drift from what those
modules actually write.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from shipgate.gate.blast_radius import record_high_risk_change
from shipgate.gate.orchestrator import evaluate_gate
from shipgate.ledger.writer import LedgerWriter
from shipgate.report.data import (
    gather_ledger_receipt,
    gather_report_data,
    read_gate_unavailable_marker,
    verify_receipt,
)
from shipgate.shipfile import Shipfile

PY = sys.executable
_NOW = "2026-08-17T00:00:00Z"


def _shipfile(conditions: list[dict], *, max_retries: int = 3, max_high_risk: int = 3) -> Shipfile:
    return Shipfile(
        raw={
            "task_classes": {"high_risk_change": {"risk_tier": "high"}},
            "gate_policy": {"max_retries": max_retries},
            "session_policy": {"max_high_risk_changes_per_session": max_high_risk},
            "done_conditions": conditions,
        }
    )


def _ledger(tmp_path: Path, session_id: str = "s1") -> LedgerWriter:
    db_path = tmp_path / ".shipgate" / "ledger.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    writer = LedgerWriter(db_path, corpus_root=tmp_path)
    writer.insert_session(session_id=session_id, project_slug="proj", source_dir=".")
    return writer


def _passing_command() -> dict:
    return {"id": "ok", "type": "command_succeeds", "command": f'"{PY}" -c "import sys; sys.exit(0)"'}


def _failing_command() -> dict:
    return {"id": "broken", "type": "command_succeeds", "command": f'"{PY}" -c "import sys; sys.exit(1)"'}


# --- gather_report_data: the basic shapes -----------------------------------------------


def test_report_data_on_a_real_red_evaluation(tmp_path):
    shipfile = _shipfile([_failing_command()])
    with _ledger(tmp_path) as writer:
        evaluate_gate(shipfile, tmp_path, "s1", writer, now=_NOW)
        data = gather_report_data(tmp_path, writer, "s1")

    assert len(data.claims) == 1
    assert data.claims[0].verdict == "contradicted"
    assert data.latest_gate_evaluation["should_block"] is True
    assert data.gate_unavailable is None
    assert data.blast_radius_count == 0


def test_report_data_on_a_real_green_evaluation(tmp_path):
    shipfile = _shipfile([_passing_command()])
    with _ledger(tmp_path) as writer:
        evaluate_gate(shipfile, tmp_path, "s1", writer, now=_NOW)
        data = gather_report_data(tmp_path, writer, "s1")

    assert data.claims[0].verdict == "verified"
    assert data.claims[0].evidence_tier == "runtime-verified"
    assert data.latest_gate_evaluation["all_dispatchable_green"] is True


def test_report_data_with_no_gate_evaluation_at_all_is_honestly_none(tmp_path):
    """A session with hook activity but no shipfile-driven evaluation yet -- must not
    be confused with a green or a red; None is the honest answer."""
    with _ledger(tmp_path) as writer:
        data = gather_report_data(tmp_path, writer, "s1")
    assert data.latest_gate_evaluation is None
    assert data.claims == []


def test_report_data_reads_the_most_recent_evaluation_not_the_first(tmp_path):
    """Fixed then broken again: two evaluate_gate calls in sequence, second one red --
    the report must reflect the LATEST recorded state, not the first."""
    shipfile_red = _shipfile([_failing_command()])
    shipfile_green = _shipfile([_passing_command()])
    with _ledger(tmp_path) as writer:
        evaluate_gate(shipfile_green, tmp_path, "s1", writer, now=_NOW)
        evaluate_gate(shipfile_red, tmp_path, "s1", writer, now=_NOW)
        data = gather_report_data(tmp_path, writer, "s1")
    assert data.latest_gate_evaluation["should_block"] is True


# --- P11(b): blast-radius count -----------------------------------------------------


def test_blast_radius_count_is_zero_when_nothing_declared(tmp_path):
    with _ledger(tmp_path) as writer:
        data = gather_report_data(tmp_path, writer, "s1")
    assert data.blast_radius_count == 0


def test_blast_radius_count_reflects_real_declarations(tmp_path):
    shipfile = _shipfile([_passing_command()])
    with _ledger(tmp_path) as writer:
        record_high_risk_change(
            shipfile, "s1", writer, task_class="high_risk_change", description="touch auth", now=_NOW
        )
        record_high_risk_change(
            shipfile, "s1", writer, task_class="high_risk_change", description="touch billing", now=_NOW
        )
        data = gather_report_data(tmp_path, writer, "s1")
    assert data.blast_radius_count == 2


# --- P12: the gate-unavailable marker ------------------------------------------------


def test_gate_unavailable_marker_absent_by_default(tmp_path):
    with _ledger(tmp_path) as writer:
        data = gather_report_data(tmp_path, writer, "s1")
    assert data.gate_unavailable is None


def test_gate_unavailable_marker_read_when_a_real_stop_hook_wrote_one(tmp_path):
    """Real end-to-end: corrupt the ledger, fire the real Stop hook twice (1 grace + 1
    escalation, Session 013's own mechanism), then confirm the report layer reads the
    exact marker that mechanism wrote -- not a hand-built stand-in."""
    from shipgate.hooks import stop as stop_module

    (tmp_path / ".shipgate").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".shipgate" / "ledger.db").write_bytes(b"not a sqlite database")
    payload = json.dumps({"session_id": "s1", "cwd": str(tmp_path), "hook_event_name": "Stop"})
    stop_module.run(payload)
    stop_module.run(payload)

    marker = read_gate_unavailable_marker(tmp_path)
    assert marker is not None
    assert marker.consecutive_failures == 2
    assert "database" in marker.last_error


def test_gate_unavailable_marker_malformed_json_is_honestly_absent(tmp_path):
    marker_path = tmp_path / ".shipgate" / "gate_unavailable.json"
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text("{not valid json", encoding="utf-8")
    assert read_gate_unavailable_marker(tmp_path) is None


# --- tokens ---------------------------------------------------------------------------


def test_token_totals_sum_across_events_in_the_session(tmp_path):
    with _ledger(tmp_path) as writer:
        writer.insert_event(
            session_id="s1", source_file="<t>", source_offset=0, transcript_tier="session",
            record_type="assistant_message", tokens_input=100, tokens_output=50, tokens_cache_read=10,
        )
        writer.insert_event(
            session_id="s1", source_file="<t>", source_offset=1, transcript_tier="session",
            record_type="assistant_message", tokens_input=200, tokens_output=75, tokens_cache_read=0,
        )
        data = gather_report_data(tmp_path, writer, "s1")
    assert (data.tokens_input, data.tokens_output, data.tokens_cache_read) == (300, 125, 10)


def test_token_totals_are_zero_not_none_when_no_events_carry_tokens(tmp_path):
    with _ledger(tmp_path) as writer:
        data = gather_report_data(tmp_path, writer, "s1")
    assert (data.tokens_input, data.tokens_output, data.tokens_cache_read) == (0, 0, 0)


# --- P11(a): the ledger receipt and --verify -----------------------------------------


def test_ledger_receipt_is_none_when_no_verdicts_exist_yet(tmp_path):
    with _ledger(tmp_path) as writer:
        receipt = gather_ledger_receipt(writer.connection)
    assert receipt.first_id is None
    assert receipt.last_id is None


def test_verify_receipt_on_an_empty_table_is_honestly_reported_not_a_crash(tmp_path):
    with _ledger(tmp_path) as writer:
        receipt = gather_ledger_receipt(writer.connection)
        result = verify_receipt(writer.connection, receipt)
    assert result.verified is True
    assert "nothing to verify" in result.detail


def test_verify_receipt_passes_on_a_healthy_chain(tmp_path):
    shipfile = _shipfile([_passing_command()])
    with _ledger(tmp_path) as writer:
        evaluate_gate(shipfile, tmp_path, "s1", writer, now=_NOW)
        receipt = gather_ledger_receipt(writer.connection)
        result = verify_receipt(writer.connection, receipt)
    assert result.verified is True
    assert "unchanged" in result.detail


def test_verify_receipt_catches_a_real_tampered_row(tmp_path):
    """Real out-of-band tampering: drop the append-only trigger (the exact threat model
    shipgate.ledger.integrity's own docstring names), then edit a row directly."""
    shipfile = _shipfile([_passing_command()])
    with _ledger(tmp_path) as writer:
        evaluate_gate(shipfile, tmp_path, "s1", writer, now=_NOW)
        receipt = gather_ledger_receipt(writer.connection)
        writer.connection.execute("DROP TRIGGER verdicts_no_update")
        writer.connection.execute("UPDATE verdicts SET reason = 'TAMPERED' WHERE verdict_id = ?", (receipt.last_id,))
        writer.connection.commit()
        result = verify_receipt(writer.connection, receipt)
    assert result.verified is False
    assert "verdict_id=" in result.detail


def test_verify_receipt_catches_tampering_in_events_not_just_verdicts(tmp_path):
    """Finding 7 (founder review, launch blocker): the original `verify_receipt` only
    re-derived the `verdicts` chain. The banner, blast-radius count, and token totals are
    all read from `events` -- tampering `events` directly, leaving `verdicts` completely
    untouched, used to pass `--verify` clean. Real out-of-band tampering, same technique
    as the `verdicts` case: drop the append-only trigger, edit the row, re-commit."""
    shipfile = _shipfile([_passing_command()])
    with _ledger(tmp_path) as writer:
        evaluate_gate(shipfile, tmp_path, "s1", writer, now=_NOW)
        receipt = gather_ledger_receipt(writer.connection)  # the verdicts receipt -- untouched by this attack
        writer.connection.execute("DROP TRIGGER events_no_update")
        writer.connection.execute(
            "UPDATE events SET raw_payload_redacted = REPLACE(raw_payload_redacted, "
            "'\"should_block\": false', '\"should_block\": true') WHERE record_type = 'gate_evaluation'"
        )
        writer.connection.commit()
        result = verify_receipt(writer.connection, receipt)
    assert result.verified is False
    assert "events" in result.detail
    assert "event_id=" in result.detail


def test_verify_receipt_wording_names_all_three_tables_checked(tmp_path):
    """Finding 7's narrowing requirement: the passing sentence must say what was actually
    re-derived (events, claims, verdicts), not an unqualified 'the chain' that reads as
    whole-ledger assurance while covering less than that."""
    shipfile = _shipfile([_passing_command()])
    with _ledger(tmp_path) as writer:
        evaluate_gate(shipfile, tmp_path, "s1", writer, now=_NOW)
        receipt = gather_ledger_receipt(writer.connection)
        result = verify_receipt(writer.connection, receipt)
    assert result.verified is True
    assert "events" in result.detail
    assert "claims" in result.detail
    assert "verdicts" in result.detail


def test_verify_receipt_reports_growth_since_generation_not_as_a_failure(tmp_path):
    """A report embeds range #1..#1; by the time --verify runs, a second verdict has
    been legitimately recorded. Growth is not tampering -- must stay verified=True and
    say so, distinctly from the unchanged case."""
    shipfile1 = _shipfile([_passing_command()])
    shipfile2 = _shipfile([_passing_command(), {**_passing_command(), "id": "ok2"}])
    with _ledger(tmp_path) as writer:
        evaluate_gate(shipfile1, tmp_path, "s1", writer, now=_NOW)
        receipt = gather_ledger_receipt(writer.connection)  # captured BEFORE the second condition's claim exists
        evaluate_gate(shipfile2, tmp_path, "s1", writer, now=_NOW)  # writes a second verdict row
        result = verify_receipt(writer.connection, receipt)
    assert result.verified is True
    assert "newer" in result.detail

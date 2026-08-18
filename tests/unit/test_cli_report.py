"""Task 3.3 (`shipgate status`) and task 3.4 (`shipgate report`) at the CLI layer.
`shipgate.report.data`'s own module owns the data-gathering tests
(`tests/unit/test_report.py`); this file is about the CLI's wiring — exit codes,
`--session`/`--verify` flags, and (Session 014, found live while demonstrating the
build, not reported by the founder) that a broken ledger renders an honest P12-aware
message instead of crashing into a bare traceback.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from typer.testing import CliRunner

from shipgate.cli import app
from shipgate.discipline.session import open_project_ledger
from shipgate.gate.orchestrator import evaluate_gate
from shipgate.hooks import stop as stop_module
from shipgate.shipfile import Shipfile

runner = CliRunner()
PY = sys.executable
_NOW = "2026-08-17T00:00:00Z"


def _shipfile(conditions: list[dict], *, max_retries: int = 3) -> Shipfile:
    return Shipfile(
        raw={
            "task_classes": {"high_risk_change": {"risk_tier": "high"}},
            "gate_policy": {"max_retries": max_retries},
            "session_policy": {"max_high_risk_changes_per_session": 3},
            "done_conditions": conditions,
        }
    )


def _seed_evaluation(project: Path, conditions: list[dict], session_id: str = "s1") -> None:
    with open_project_ledger(project) as writer:
        writer.insert_session(session_id=session_id, project_slug="proj", source_dir=".")
        evaluate_gate(_shipfile(conditions), project, session_id, writer, now=_NOW)


def _passing_command() -> dict:
    return {"id": "ok", "type": "command_succeeds", "command": f'"{PY}" -c "import sys; sys.exit(0)"'}


def _failing_command() -> dict:
    return {"id": "broken", "type": "command_succeeds", "command": f'"{PY}" -c "import sys; sys.exit(1)"'}


# --- shipgate status --------------------------------------------------------------------


def test_status_green_exits_0(tmp_path):
    _seed_evaluation(tmp_path, [_passing_command()])
    result = runner.invoke(app, ["status", "--project-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "GATE: GREEN" in result.output


def test_status_red_exits_1(tmp_path):
    _seed_evaluation(tmp_path, [_failing_command()])
    result = runner.invoke(app, ["status", "--project-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "GATE: RED" in result.output
    assert "broken" in result.output


def test_status_no_session_exits_2(tmp_path):
    result = runner.invoke(app, ["status", "--project-dir", str(tmp_path)])
    assert result.exit_code == 2


def test_status_no_evaluation_yet_exits_2(tmp_path):
    with open_project_ledger(tmp_path) as writer:
        writer.insert_session(session_id="s1", project_slug="proj", source_dir=".")
    result = runner.invoke(app, ["status", "--project-dir", str(tmp_path)])
    assert result.exit_code == 2
    assert "no gate evaluation recorded" in result.output


def test_status_refuses_to_report_green_over_a_tampered_events_row(tmp_path):
    """Finding 7 fix applies to `status` too, not just `report` -- `status` reads the
    same `events`-sourced evaluation and would otherwise be fooled by the identical
    attack."""
    _seed_evaluation(tmp_path, [_failing_command()])
    with open_project_ledger(tmp_path) as writer:
        writer.connection.execute("DROP TRIGGER events_no_update")
        writer.connection.execute(
            "UPDATE events SET raw_payload_redacted = REPLACE(REPLACE(raw_payload_redacted, "
            "'\"should_block\": true', '\"should_block\": false'), "
            "'\"all_dispatchable_green\": false', '\"all_dispatchable_green\": true') "
            "WHERE record_type = 'gate_evaluation'"
        )
        writer.connection.commit()

    result = runner.invoke(app, ["status", "--project-dir", str(tmp_path)])
    assert "GATE: GREEN" not in result.output
    assert "INCONSISTENT" in result.output
    assert result.exit_code != 0


def test_status_gate_unavailable_exits_3(tmp_path):
    (tmp_path / ".shipgate").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".shipgate" / "ledger.db").write_bytes(b"not a sqlite database")
    payload = json.dumps({"session_id": "s1", "cwd": str(tmp_path), "hook_event_name": "Stop"})
    stop_module.run(payload)
    stop_module.run(payload)  # 2nd consecutive -- marker now has consecutive_failures=2

    result = runner.invoke(app, ["status", "--project-dir", str(tmp_path)])
    assert result.exit_code == 3
    assert "GATE STATUS: UNKNOWN" in result.output
    assert "2 consecutive" in result.output


# --- shipgate report ---------------------------------------------------------------------


def test_report_renders_the_banner_and_claims_table_green(tmp_path):
    _seed_evaluation(tmp_path, [_passing_command()])
    result = runner.invoke(app, ["report", "--project-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "GATE: GREEN" in result.output
    assert "verified" in result.output


def test_report_renders_red_with_the_condition_reason(tmp_path):
    _seed_evaluation(tmp_path, [_failing_command()])
    result = runner.invoke(app, ["report", "--project-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "GATE: RED" in result.output
    assert "contradicted" in result.output


def test_report_blast_radius_line_present_and_worded_identically_at_zero(tmp_path):
    """P11(b): the exact self-declared-only caveat, present even at zero -- never a
    checkmark, never 'clean'. `rich` word-wraps at the captured console's width, so
    assertions are made against whitespace-collapsed output rather than an exact
    substring that could straddle a wrap boundary."""
    _seed_evaluation(tmp_path, [_passing_command()])
    result = runner.invoke(app, ["report", "--project-dir", str(tmp_path)])
    flat = " ".join(result.output.split())
    assert "0 high-risk change" in flat
    assert "self-declared only" in flat
    assert "cannot detect an undeclared one" in flat


def test_report_p12_line_absent_when_no_marker_exists(tmp_path):
    _seed_evaluation(tmp_path, [_passing_command()])
    result = runner.invoke(app, ["report", "--project-dir", str(tmp_path)])
    assert "GATE STATUS: UNKNOWN" not in result.output
    assert "infrastructure problem" not in result.output


def test_report_on_a_fully_broken_ledger_reports_p12_not_a_traceback(tmp_path):
    """Session 014's own live-found gap: a corrupt ledger used to crash `shipgate
    report` with a bare Python exception instead of the honest message a stranger
    actually needs."""
    (tmp_path / ".shipgate").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".shipgate" / "ledger.db").write_bytes(b"not a sqlite database")
    payload = json.dumps({"session_id": "s1", "cwd": str(tmp_path), "hook_event_name": "Stop"})
    stop_module.run(payload)
    stop_module.run(payload)

    result = runner.invoke(app, ["report", "--project-dir", str(tmp_path)])
    assert result.exit_code == 3
    assert "Traceback" not in result.output
    assert "GATE STATUS: UNKNOWN" in result.output
    assert "infrastructure problem" in result.output


def test_report_verify_flag_passes_on_a_healthy_chain(tmp_path):
    _seed_evaluation(tmp_path, [_passing_command()])
    result = runner.invoke(app, ["report", "--project-dir", str(tmp_path), "--verify"])
    assert result.exit_code == 0
    assert "VERIFIED" in result.output


def test_report_verify_flag_catches_a_real_tampered_row(tmp_path):
    _seed_evaluation(tmp_path, [_passing_command()])
    with open_project_ledger(tmp_path) as writer:
        writer.connection.execute("DROP TRIGGER verdicts_no_update")
        writer.connection.execute("UPDATE verdicts SET reason = 'TAMPERED' WHERE verdict_id = 1")
        writer.connection.commit()

    result = runner.invoke(app, ["report", "--project-dir", str(tmp_path), "--verify"])
    assert "TAMPERED" in result.output  # rendered plainly, both as the (fake) claim reason and the verify verdict word
    assert "hash chain broken" in result.output


def test_report_verify_defaults_to_on(tmp_path):
    """P13: --verify is on by default, cost measured
    cheap (~7-8us/row) before deciding -- a plain `shipgate report`, no flags, actually
    re-derives the chain and prints VERIFIED, not "not independently re-checked"."""
    _seed_evaluation(tmp_path, [_passing_command()])
    result = runner.invoke(app, ["report", "--project-dir", str(tmp_path)])
    assert "VERIFIED" in result.output
    assert "not independently re-checked" not in result.output.lower()


def test_report_no_verify_flag_states_it_was_not_independently_checked(tmp_path):
    _seed_evaluation(tmp_path, [_passing_command()])
    result = runner.invoke(app, ["report", "--project-dir", str(tmp_path), "--no-verify"])
    assert "not independently re-checked" in result.output.lower()


def test_report_refuses_to_render_green_over_a_tampered_events_row(tmp_path):
    """Finding 7 (founder review, launch blocker), the founder's own exact repro: seed a
    real RED evaluation (a real `contradicted` claim), then tamper ONLY the `events` row
    the banner is built from -- flip `should_block` false and `all_dispatchable_green`
    true, leave `verdicts` completely untouched. Before this fix: banner rendered GATE:
    GREEN. The claims table (read from the untouched `verdicts` table) still shows
    `contradicted` right below it -- the banner must refuse to render GREEN over that,
    with no --verify flag and no hashing involved."""
    _seed_evaluation(tmp_path, [_failing_command()])
    with open_project_ledger(tmp_path) as writer:
        writer.connection.execute("DROP TRIGGER events_no_update")
        writer.connection.execute(
            "UPDATE events SET raw_payload_redacted = REPLACE(REPLACE(raw_payload_redacted, "
            "'\"should_block\": true', '\"should_block\": false'), "
            "'\"all_dispatchable_green\": false', '\"all_dispatchable_green\": true') "
            "WHERE record_type = 'gate_evaluation'"
        )
        writer.connection.commit()

    result = runner.invoke(app, ["report", "--project-dir", str(tmp_path)])
    assert "GATE: GREEN" not in result.output
    assert "GATE: INCONSISTENT" in result.output
    assert "contradicted" in result.output
    assert result.exit_code != 0


def test_report_verify_names_events_when_events_is_the_tampered_table(tmp_path):
    """The same attack, this time confirmed with --verify: the tampered table is named
    explicitly (event_id=...), and a passing run's wording names all three tables
    actually re-derived, not just the one the printed receipt happens to be drawn from."""
    _seed_evaluation(tmp_path, [_passing_command()])
    with open_project_ledger(tmp_path) as writer:
        writer.connection.execute("DROP TRIGGER events_no_update")
        writer.connection.execute(
            "UPDATE events SET raw_payload_redacted = REPLACE(raw_payload_redacted, "
            "'\"should_block\": false', '\"should_block\": true') WHERE record_type = 'gate_evaluation'"
        )
        writer.connection.commit()

    result = runner.invoke(app, ["report", "--project-dir", str(tmp_path), "--verify"])
    assert "TAMPERED" in result.output
    assert "event_id=" in result.output


def test_report_verify_wording_names_all_three_tables_on_a_healthy_chain(tmp_path):
    _seed_evaluation(tmp_path, [_passing_command()])
    result = runner.invoke(app, ["report", "--project-dir", str(tmp_path), "--verify"])
    flat = " ".join(result.output.split())
    assert "events" in flat and "claims" in flat and "verdicts" in flat


def test_report_session_flag_overrides_the_default_most_recent_session(tmp_path):
    _seed_evaluation(tmp_path, [_passing_command()], session_id="s-old")
    _seed_evaluation(tmp_path, [_failing_command()], session_id="s-new")

    default_result = runner.invoke(app, ["report", "--project-dir", str(tmp_path)])
    assert "GATE: RED" in default_result.output  # most recent session, s-new

    explicit_result = runner.invoke(app, ["report", "--project-dir", str(tmp_path), "--session", "s-old"])
    assert "GATE: GREEN" in explicit_result.output

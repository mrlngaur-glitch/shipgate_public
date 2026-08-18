"""Gate B condition 5's wiring (Decisions log P10) — the agent declares
its own task class via `shipgate declare-task-class`, a thin CLI wrapper around the
already-tested `record_high_risk_change`. Every test here uses a real shipfile on disk
and a real ledger; the "current session" lookup is exercised against a real Claude Code
hook subprocess firing first (combined-realistic: the actual sequence a live session
produces), not a hand-inserted session row, at least once, per this project's own stated
preference for the messy realistic case over the isolated one.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from shipgate.cli import app
from shipgate.discipline.session import (
    NoSessionRecordedError,
    current_session_id,
    open_project_ledger,
)

runner = CliRunner()

_NOW = "2026-08-15T00:00:00Z"


def _write_shipfile(project: Path, *, max_high_risk: int = 3) -> None:
    doc = {
        "task_classes": {
            "high_risk_change": {
                "risk_tier": "high",
                "starting_model_tier": "frontier",
                "max_tokens": 200000,
                "gate_strictness": "strict",
            },
            "docs": {
                "risk_tier": "low",
                "starting_model_tier": "cheap",
                "max_tokens": 50000,
                "gate_strictness": "advisory",
            },
        },
        "done_conditions": [],
        "routing": {},
        "budgets": {},
        "context_policy": {},
        "intent": {"summary": "test"},
        "gate_policy": {},
        "session_policy": {"max_high_risk_changes_per_session": max_high_risk},
    }
    (project / "shipfile.yaml").write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


def _fire_real_pretooluse_hook(project: Path, session_id: str) -> None:
    """A real `python -m shipgate.hooks.pretooluse` subprocess — the exact mechanism
    that creates the `sessions` row `declare-task-class` depends on in live use."""
    payload = {
        "session_id": session_id,
        "cwd": str(project),
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "echo hi"},
    }
    result = subprocess.run(
        [sys.executable, "-m", "shipgate.hooks.pretooluse"],
        input=json.dumps(payload),
        cwd=project,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr


# --- shipgate.discipline.session, directly --------------------------------------------


def test_no_session_recorded_raises_a_specific_named_error(tmp_path: Path):
    with open_project_ledger(tmp_path) as writer, pytest.raises(NoSessionRecordedError):
        current_session_id(writer, tmp_path)


def test_current_session_id_is_the_most_recently_recorded_session(tmp_path: Path):
    _write_shipfile(tmp_path)
    _fire_real_pretooluse_hook(tmp_path, "session-A")
    _fire_real_pretooluse_hook(tmp_path, "session-B")

    with open_project_ledger(tmp_path) as writer:
        assert current_session_id(writer, tmp_path) == "session-B"


# --- CLI: no session yet --------------------------------------------------------------


def test_declare_before_any_hook_fired_fails_with_a_clear_message_not_a_crash(tmp_path: Path):
    _write_shipfile(tmp_path)

    result = runner.invoke(
        app, ["declare-task-class", "high_risk_change", "a change", "--project-dir", str(tmp_path)]
    )

    assert result.exit_code == 3
    assert "no ShipGate session has been recorded" in result.output


def test_declare_with_no_shipfile_fails_clearly(tmp_path: Path):
    result = runner.invoke(
        app, ["declare-task-class", "high_risk_change", "a change", "--project-dir", str(tmp_path)]
    )
    assert result.exit_code == 2
    assert "shipgate init" in result.output


# --- CLI: real session exists, real declaration recorded -------------------------------


def test_declare_after_a_real_hook_fired_is_recorded_in_the_real_ledger(tmp_path: Path):
    _write_shipfile(tmp_path)
    _fire_real_pretooluse_hook(tmp_path, "sess-1")

    result = runner.invoke(
        app,
        [
            "declare-task-class",
            "high_risk_change",
            "a real high-risk change",
            "--project-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert "recorded" in result.output

    with open_project_ledger(tmp_path) as writer:
        count = writer.connection.execute(
            "SELECT COUNT(*) FROM events WHERE record_type = 'high_risk_change'"
        ).fetchone()[0]
    assert count == 1


def test_declare_unknown_task_class_fails_clearly_not_silently(tmp_path: Path):
    _write_shipfile(tmp_path)
    _fire_real_pretooluse_hook(tmp_path, "sess-1")

    result = runner.invoke(
        app, ["declare-task-class", "no_such_class", "x", "--project-dir", str(tmp_path)]
    )

    assert result.exit_code == 2
    assert "no_such_class" in result.output


def test_declare_a_low_risk_task_class_is_not_counted(tmp_path: Path):
    _write_shipfile(tmp_path)
    _fire_real_pretooluse_hook(tmp_path, "sess-1")

    result = runner.invoke(app, ["declare-task-class", "docs", "typo fix", "--project-dir", str(tmp_path)])

    assert result.exit_code == 0
    assert "not counted" in result.output


# --- CLI: the founder's exact scenario -- 4th refused, override recorded ---------------


def test_the_founders_exact_scenario_4th_high_risk_change_refused_then_overridden(tmp_path: Path):
    _write_shipfile(tmp_path, max_high_risk=3)
    _fire_real_pretooluse_hook(tmp_path, "sess-1")

    for i in range(3):
        result = runner.invoke(
            app,
            ["declare-task-class", "high_risk_change", f"change {i}", "--project-dir", str(tmp_path)],
        )
        assert result.exit_code == 0

    fourth = runner.invoke(
        app, ["declare-task-class", "high_risk_change", "change 4", "--project-dir", str(tmp_path)]
    )
    assert fourth.exit_code == 1
    assert "refused" in fourth.output

    overridden = runner.invoke(
        app,
        [
            "declare-task-class",
            "high_risk_change",
            "change 4",
            "--project-dir",
            str(tmp_path),
            "--override-reason",
            "founder approved in chat",
        ],
    )
    assert overridden.exit_code == 0
    assert "OVERRIDE" in overridden.output

    with open_project_ledger(tmp_path) as writer:
        recorded = writer.connection.execute(
            "SELECT COUNT(*) FROM events WHERE record_type = 'high_risk_change'"
        ).fetchone()[0]
        refused = writer.connection.execute(
            "SELECT COUNT(*) FROM events WHERE record_type = 'high_risk_change_refused'"
        ).fetchone()[0]
    assert recorded == 4  # 3 normal + 1 override
    assert refused == 1

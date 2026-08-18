"""Task 2.8 — the session blast-radius counter (`shipgate.gate.blast_radius.
record_high_risk_change`). Every test runs against a real ledger; several call it
multiple times in sequence against the same session, the messy realistic case, not just
one isolated call.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from shipgate.gate.blast_radius import record_high_risk_change
from shipgate.ledger.writer import LedgerWriter
from shipgate.shipfile import Shipfile

_NOW = "2026-08-15T00:00:00Z"


def _shipfile(max_high_risk: int = 3) -> Shipfile:
    return Shipfile(
        raw={
            "task_classes": {
                "schema_change": {
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
            "session_policy": {"max_high_risk_changes_per_session": max_high_risk},
        }
    )


def _ledger(tmp_path: Path, session_id: str = "s1") -> LedgerWriter:
    db_path = tmp_path / ".shipgate" / "ledger.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    writer = LedgerWriter(db_path, corpus_root=tmp_path)
    writer.insert_session(session_id=session_id, project_slug="proj", source_dir=".")
    return writer


def test_unknown_task_class_raises_a_specific_error(tmp_path):
    with _ledger(tmp_path) as writer, pytest.raises(ValueError, match="not declared"):
        record_high_risk_change(
            _shipfile(), "s1", writer, task_class="nonexistent", description="x", now=_NOW
        )


def test_low_risk_task_class_is_not_counted_but_is_visible(tmp_path):
    with _ledger(tmp_path) as writer:
        result = record_high_risk_change(_shipfile(), "s1", writer, task_class="docs", description="x", now=_NOW)
    assert result.recorded is False
    assert result.refused is False
    assert "not 'high'" in result.reason


def test_high_risk_change_recorded_under_budget(tmp_path):
    with _ledger(tmp_path) as writer:
        result = record_high_risk_change(
            _shipfile(), "s1", writer, task_class="schema_change", description="x", now=_NOW
        )
    assert result.recorded is True
    assert result.refused is False
    assert result.count_this_session == 1
    assert result.max_allowed == 3


def test_founders_exact_scenario_the_fourth_high_risk_change_is_refused(tmp_path):
    """Gate B condition #5, verbatim: the 4th high-risk change in a session is refused
    pending a logged override, given the default max_high_risk_changes_per_session=3."""
    shipfile = _shipfile(max_high_risk=3)
    with _ledger(tmp_path) as writer:
        results = [
            record_high_risk_change(
                shipfile, "s1", writer, task_class="schema_change", description=f"change {i}", now=_NOW
            )
            for i in range(4)
        ]

        assert [r.recorded for r in results] == [True, True, True, False]
        assert [r.refused for r in results] == [False, False, False, True]
        assert "logged override" in results[3].reason.lower()

        # Refused, but not silently -- a real ledger row records the refusal.
        refused_events = writer.connection.execute(
            "SELECT COUNT(*) FROM events WHERE session_id='s1' AND record_type='high_risk_change_refused'"
        ).fetchone()[0]
        assert refused_events == 1
        recorded_events = writer.connection.execute(
            "SELECT COUNT(*) FROM events WHERE session_id='s1' AND record_type='high_risk_change'"
        ).fetchone()[0]
        assert recorded_events == 3


def test_override_records_the_fourth_change_and_logs_the_override_reason(tmp_path):
    import json

    shipfile = _shipfile(max_high_risk=3)
    with _ledger(tmp_path) as writer:
        for i in range(3):
            record_high_risk_change(
                shipfile, "s1", writer, task_class="schema_change", description=f"change {i}", now=_NOW
            )
        result = record_high_risk_change(
            shipfile,
            "s1",
            writer,
            task_class="schema_change",
            description="change 3",
            now=_NOW,
            override_reason="founder approved via chat, 2026-08-15",
        )
        assert result.recorded is True
        assert result.overridden is True
        assert "LOGGED OVERRIDE" in result.reason

        payload = json.loads(
            writer.connection.execute(
                "SELECT raw_payload_redacted FROM events WHERE session_id='s1' AND record_type='high_risk_change' "
                "ORDER BY event_id DESC LIMIT 1"
            ).fetchone()[0]
        )
        assert payload["override"] is True
        assert payload["override_reason"] == "founder approved via chat, 2026-08-15"


def test_max_high_risk_changes_zero_refuses_immediately(tmp_path):
    shipfile = _shipfile(max_high_risk=0)
    with _ledger(tmp_path) as writer:
        result = record_high_risk_change(
            shipfile, "s1", writer, task_class="schema_change", description="x", now=_NOW
        )
    assert result.refused is True
    assert result.recorded is False


def test_low_risk_changes_interleaved_do_not_consume_the_high_risk_budget(tmp_path):
    """Combined-realistic: a session doing a mix of low- and high-risk work only spends
    budget on the high-risk ones."""
    shipfile = _shipfile(max_high_risk=2)
    with _ledger(tmp_path) as writer:
        r1 = record_high_risk_change(shipfile, "s1", writer, task_class="docs", description="readme", now=_NOW)
        r2 = record_high_risk_change(
            shipfile, "s1", writer, task_class="schema_change", description="ddl 1", now=_NOW
        )
        r3 = record_high_risk_change(shipfile, "s1", writer, task_class="docs", description="changelog", now=_NOW)
        r4 = record_high_risk_change(
            shipfile, "s1", writer, task_class="schema_change", description="ddl 2", now=_NOW
        )
    assert [r1.recorded, r2.recorded, r3.recorded, r4.recorded] == [False, True, False, True]
    assert r4.count_this_session == 2  # only the two schema_change calls counted

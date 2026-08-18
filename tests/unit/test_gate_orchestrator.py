"""Task 2.6 — the retry-cap loop-breaker (`shipgate.gate.orchestrator.evaluate_gate`).

Every test runs against a REAL `tmp_path` project, a REAL ledger, and REAL checkers
(real subprocesses for `command_succeeds`, a real filesystem check for `file_exists`) —
standing note: the messy realistic case, not just the clean isolated
one. Several tests below call `evaluate_gate` multiple times in sequence against the
same session, the way a real multi-turn agent session actually would, rather than
asserting one call in isolation.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from shipgate.gate.orchestrator import (
    _MAX_EFFECTIVE_RETRIES,
    _should_write_new_verdict_row,
    evaluate_gate,
)
from shipgate.ledger.writer import LedgerWriter
from shipgate.shipfile import Shipfile
from shipgate.verdicts import Verdict

PY = sys.executable
_NOW = "2026-08-15T00:00:00Z"


def _shipfile(conditions: list[dict], *, max_retries: int = 3) -> Shipfile:
    return Shipfile(raw={"gate_policy": {"max_retries": max_retries}, "done_conditions": conditions})


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


# --- _should_write_new_verdict_row -----------------------------------------------------


@pytest.mark.parametrize(
    ("current", "new", "expected"),
    [
        (None, Verdict.UNVERIFIED, True),  # first-ever verdict, always written
        (None, Verdict.CONTRADICTED, True),
        (Verdict.UNVERIFIED, Verdict.CONTRADICTED, True),  # class change
        (Verdict.UNVERIFIED, Verdict.UNVERIFIED, False),  # no legal self-loop -- the founder's exact concern
        (Verdict.CONTRADICTED, Verdict.CONTRADICTED, False),  # no legal self-loop
        (Verdict.VERIFIED, Verdict.VERIFIED, True),  # legal self-loop (tier reconfirm/upgrade)
        (Verdict.UNVERIFIED_VACUOUS, Verdict.UNVERIFIED_VACUOUS, True),  # legal self-loop, "re-run, still nothing"
        (Verdict.PENDING_RECHECK, Verdict.PENDING_RECHECK, True),  # legal self-loop, "window extended"
        (Verdict.CONTRADICTED, Verdict.VERIFIED, True),  # fixed, re-verified
        (Verdict.VERIFIED, Verdict.CONTRADICTED, True),  # regressed
    ],
)
def test_should_write_new_verdict_row(current, new, expected):
    assert _should_write_new_verdict_row(current, new) is expected


# --- evaluate_gate: basic shapes --------------------------------------------------------


def test_all_green_on_first_attempt_is_not_blocked(tmp_path):
    shipfile = _shipfile([_passing_command()])
    with _ledger(tmp_path) as writer:
        result = evaluate_gate(shipfile, tmp_path, "s1", writer, now=_NOW)
    assert result.all_dispatchable_green is True
    assert result.should_block is False
    assert result.exhausted is False
    assert result.prior_blocking_attempts == 0


def test_zero_dispatchable_conditions_is_not_green_the_vacuous_gate_case(tmp_path):
    """An ears-only shipfile has nothing this orchestrator can check -- that must not
    silently render green, the same discipline every individual checker already applies
    to its own zero-observed case."""
    shipfile = _shipfile([{"id": "e1", "ears": "WHEN x THE SYSTEM SHALL y"}])
    with _ledger(tmp_path) as writer:
        result = evaluate_gate(shipfile, tmp_path, "s1", writer, now=_NOW)
    assert result.all_dispatchable_green is False
    assert result.conditions[0].dispatchable is False


def test_undispatchable_condition_types_do_not_crash_and_do_not_count_toward_green(tmp_path):
    shipfile = _shipfile(
        [
            _passing_command(),
            {"id": "inv1", "type": "inventory_complete", "grep_pattern": "x", "required_dispositions": ["updated"]},
            {"id": "et1", "type": "emission_traced", "marker": "x"},
            {"id": "e1", "ears": "WHEN x THE SYSTEM SHALL y"},
        ]
    )
    with _ledger(tmp_path) as writer:
        result = evaluate_gate(shipfile, tmp_path, "s1", writer, now=_NOW)
    assert result.all_dispatchable_green is True  # the one dispatchable, real condition passed
    dispatch_map = {o.condition_id: o.dispatchable for o in result.conditions}
    assert dispatch_map == {"ok": True, "inv1": False, "et1": False, "e1": False}


# --- the founder's own scenario: retry cap must not crash on a real persistent failure --


def test_retry_cap_the_founders_exact_scenario_persistent_failure_blocks_then_exhausts_honest_red(tmp_path):
    """A real, persistently failing command_succeeds condition run 4 times in sequence
    against the same session (default max_retries=3, so 4 total attempts). This is the
    test that would raise IllegalTransitionError before the self-loop fix: attempts 2-4
    all try to record the SAME CONTRADICTED verdict again, which the frozen transition
    table does not allow to self-loop."""
    shipfile = _shipfile([_failing_command()], max_retries=3)
    with _ledger(tmp_path) as writer:
        r1 = evaluate_gate(shipfile, tmp_path, "s1", writer, now=_NOW)
        r2 = evaluate_gate(shipfile, tmp_path, "s1", writer, now=_NOW)
        r3 = evaluate_gate(shipfile, tmp_path, "s1", writer, now=_NOW)
        r4 = evaluate_gate(shipfile, tmp_path, "s1", writer, now=_NOW)  # must not raise

        assert [r.should_block for r in (r1, r2, r3, r4)] == [True, True, True, False]
        assert [r.exhausted for r in (r1, r2, r3, r4)] == [False, False, False, True]
        assert r4.all_dispatchable_green is False
        assert "broken" in r1.block_reason

        # The claim's verdict history has exactly ONE row -- three of the four attempts
        # were legitimately skipped as illegal no-op self-loops, not four duplicate rows.
        current = writer.current_verdict("s1:broken")
        assert current[1] == Verdict.CONTRADICTED
        # Every attempt is still fully recorded as a raw event, even the three that
        # produced no new verdicts row -- the complete observation trail lives in
        # events regardless.
        attempt_count = writer.connection.execute(
            "SELECT COUNT(*) FROM events WHERE session_id='s1' AND record_type='gate_evaluation'"
        ).fetchone()[0]
        assert attempt_count == 4


def test_max_retries_zero_exhausts_immediately(tmp_path):
    """gate_policy.max_retries: 0 is documented (shipgate/shipfile/schema.py) as 'never
    retry, fail straight to an honest red report on the first failure' -- must exhaust
    immediately, never block even once."""
    shipfile = _shipfile([_failing_command()], max_retries=0)
    with _ledger(tmp_path) as writer:
        result = evaluate_gate(shipfile, tmp_path, "s1", writer, now=_NOW)
    assert result.should_block is False
    assert result.exhausted is True


def test_effective_retry_ceiling_stays_below_the_claude_code_override_with_margin(tmp_path):
    """A shipfile requesting the schema-legal maximum (max_retries=10) must still
    exhaust at this module's defensive ceiling, not at 10 -- otherwise Claude Code's own
    8-consecutive-block override would silently take over first, with no honest-red
    ledger marker at all. See orchestrator.py's module docstring, design decision 2."""
    shipfile = _shipfile([_failing_command()], max_retries=10)
    with _ledger(tmp_path) as writer:
        results = [evaluate_gate(shipfile, tmp_path, "s1", writer, now=_NOW) for _ in range(_MAX_EFFECTIVE_RETRIES + 2)]
    exhausted_at = next(i for i, r in enumerate(results) if r.exhausted)
    assert exhausted_at == _MAX_EFFECTIVE_RETRIES
    assert exhausted_at < 8, "must exhaust strictly before Claude Code's 8-consecutive-block override"
    for r in results[:exhausted_at]:
        assert r.should_block is True
    assert results[exhausted_at].effective_max_retries == _MAX_EFFECTIVE_RETRIES


# --- founder review finding: the ceiling must never silently narrow the user's config --


def test_ceiling_binding_is_false_when_configured_retries_is_within_the_safe_range(tmp_path):
    shipfile = _shipfile([_failing_command()], max_retries=3)  # the documented default
    with _ledger(tmp_path) as writer:
        result = evaluate_gate(shipfile, tmp_path, "s1", writer, now=_NOW)
    assert result.ceiling_binding is False
    assert result.effective_max_retries == 3  # not silently changed when it doesn't need to be


def test_ceiling_binding_is_visible_in_the_block_reason_when_it_actually_binds(tmp_path):
    """A shipfile requesting more retries than ShipGate's safety ceiling allows must not
    silently get fewer -- the truncation has to be visible in the one place a blocking
    attempt says anything to the agent."""
    shipfile = _shipfile([_failing_command()], max_retries=10)
    with _ledger(tmp_path) as writer:
        result = evaluate_gate(shipfile, tmp_path, "s1", writer, now=_NOW)
    assert result.ceiling_binding is True
    assert "10" in result.block_reason  # names what was requested
    assert "6" in result.block_reason  # names what was actually granted


def test_ceiling_binding_is_visible_in_the_release_reason_on_exhaustion(tmp_path):
    shipfile = _shipfile([_failing_command()], max_retries=10)
    with _ledger(tmp_path) as writer:
        results = [evaluate_gate(shipfile, tmp_path, "s1", writer, now=_NOW) for _ in range(_MAX_EFFECTIVE_RETRIES + 1)]
    final = results[-1]
    assert final.exhausted is True
    assert final.release_reason is not None
    assert "honest red" in final.release_reason
    assert "10" in final.release_reason and "6" in final.release_reason
    # And the block_reason on every earlier attempt names it too -- not just the last one.
    for r in results[:-1]:
        assert r.ceiling_binding is True
        assert "6" in r.block_reason


# --- combined-realistic: one condition genuinely gets fixed mid-retry ------------------


def test_combined_realistic_one_condition_fixed_between_attempts_the_other_stays_red(tmp_path):
    """Mirrors a real agent session: a mixed shipfile (dispatchable + undispatchable
    conditions, like the project's own shipfile.yaml), where the agent fixes ONE failing
    condition between attempt 1 and attempt 2 while another stays broken. The fixed
    condition must flip to VERIFIED; the gate must stay red because of the other one;
    the retry counter must keep advancing correctly across the mixed outcome."""
    target = tmp_path / "needed_file.txt"
    shipfile = _shipfile(
        [
            {"id": "file-check", "type": "file_exists", "path": "needed_file.txt"},
            _failing_command(),  # never fixed in this test -- keeps the gate red throughout
            {"id": "e1", "ears": "WHEN x THE SYSTEM SHALL y"},  # undispatchable, must not crash or count
        ]
    )
    with _ledger(tmp_path) as writer:
        r1 = evaluate_gate(shipfile, tmp_path, "s1", writer, now=_NOW)
        assert r1.should_block is True
        by_id = {o.condition_id: o.check_result.verdict for o in r1.conditions if o.dispatchable}
        assert by_id["file-check"] == Verdict.CONTRADICTED
        assert by_id["broken"] == Verdict.CONTRADICTED

        target.write_text("now it exists\n", encoding="utf-8")  # the agent's fix, between attempts

        r2 = evaluate_gate(shipfile, tmp_path, "s1", writer, now=_NOW)
        assert r2.should_block is True  # still red -- "broken" was never fixed
        assert r2.all_dispatchable_green is False
        by_id2 = {o.condition_id: o.check_result.verdict for o in r2.conditions if o.dispatchable}
        assert by_id2["file-check"] == Verdict.VERIFIED  # the fix is reflected
        assert by_id2["broken"] == Verdict.CONTRADICTED  # still broken

        # The claim history proves the flip really happened, not just the latest read.
        current = writer.current_verdict("s1:file-check")
        assert current[1] == Verdict.VERIFIED


# --- task 2.7: flake quarantine ---------------------------------------------------------


def _flip_flop_command(tmp_path: Path) -> str:
    """A command whose exit code alternates between calls, independent of any file
    `compute_project_fingerprint` would hash (.shipgate/ is in IGNORED_DIR_NAMES) --
    simulates a genuinely flaky check (same code, different result) without actually
    changing the project's content fingerprint between attempts."""
    script = tmp_path / ".shipgate" / "flip_flop.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    counter_path = tmp_path / ".shipgate" / "flip_counter.txt"
    script.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        f"counter_path = Path(r'{counter_path}')\n"
        "n = int(counter_path.read_text()) if counter_path.exists() else 0\n"
        "counter_path.write_text(str(n + 1))\n"
        "sys.exit(0 if n % 2 == 0 else 1)\n",
        encoding="utf-8",
    )
    return f'"{PY}" "{script}"'


def test_flake_quarantine_the_founders_exact_scenario_flip_on_unchanged_code(tmp_path):
    """Report §5.4: 'a check that flips verdict on unchanged code' -> QUARANTINED_FLAKY,
    advisory, never hard-block. Four real attempts against a real, genuinely flaky
    command whose exit code alternates while the project's tracked files never change:
    VERIFIED, then a flip at the SAME fingerprint (quarantined), then the forced lift to
    UNVERIFIED, then a fresh real verdict again."""
    shipfile = _shipfile([{"id": "flaky", "type": "command_succeeds", "command": _flip_flop_command(tmp_path)}])
    with _ledger(tmp_path) as writer:
        r1 = evaluate_gate(shipfile, tmp_path, "s1", writer, now=_NOW)
        r2 = evaluate_gate(shipfile, tmp_path, "s1", writer, now=_NOW)
        r3 = evaluate_gate(shipfile, tmp_path, "s1", writer, now=_NOW)
        r4 = evaluate_gate(shipfile, tmp_path, "s1", writer, now=_NOW)

        outcomes = [r.conditions[0] for r in (r1, r2, r3, r4)]
        assert [o.check_result.verdict for o in outcomes] == [
            Verdict.VERIFIED,
            Verdict.QUARANTINED_FLAKY,
            Verdict.UNVERIFIED,
            Verdict.CONTRADICTED,
        ]
        assert [o.advisory_only for o in outcomes] == [False, True, True, False]
        # Advisory attempts never block -- report §5.4, "degraded to advisory, never
        # hard-block". Only r4's real CONTRADICTED (not advisory) blocks.
        assert [r.should_block for r in (r1, r2, r3, r4)] == [False, False, False, True]

        # The claim history has exactly the four rows -- no illegal transition was ever
        # attempted (VERIFIED->QUARANTINED_FLAKY, QUARANTINED_FLAKY->UNVERIFIED,
        # UNVERIFIED->CONTRADICTED are all legal per shipgate.verdicts.transitions).
        rows = writer.connection.execute(
            "SELECT verdict FROM verdicts WHERE claim_id='s1:flaky' ORDER BY verdict_id"
        ).fetchall()
        assert [r[0] for r in rows] == ["verified", "quarantined-flaky", "unverified", "contradicted"]


def test_flake_quarantine_does_not_fire_on_the_very_first_attempt(tmp_path):
    """QUARANTINED_FLAKY can never be a claim's first verdict (shipgate.verdicts.
    transitions) -- there is no prior fingerprint to compare against yet."""
    shipfile = _shipfile([_failing_command()])
    with _ledger(tmp_path) as writer:
        result = evaluate_gate(shipfile, tmp_path, "s1", writer, now=_NOW)
    assert result.conditions[0].check_result.verdict == Verdict.CONTRADICTED
    assert result.conditions[0].advisory_only is False


def test_flake_quarantine_does_not_fire_when_the_fingerprint_actually_changed(tmp_path):
    """A real code change between two looks must never be mislabeled as a flake, even
    if it happens to flip VERIFIED<->CONTRADICTED -- the safer direction to be wrong in
    (see orchestrator.py's design decision 4)."""
    target = tmp_path / "src.py"
    target.write_text("x = 1\n", encoding="utf-8")
    condition = {"id": "exists-and-nonempty", "type": "file_exists", "path": "src.py"}
    shipfile = _shipfile([condition])
    with _ledger(tmp_path) as writer:
        r1 = evaluate_gate(shipfile, tmp_path, "s1", writer, now=_NOW)
        assert r1.conditions[0].check_result.verdict == Verdict.VERIFIED

        target.unlink()  # a real change -- the fingerprint WILL differ
        r2 = evaluate_gate(shipfile, tmp_path, "s1", writer, now=_NOW)
        assert r2.conditions[0].check_result.verdict == Verdict.CONTRADICTED
        assert r2.conditions[0].advisory_only is False  # a real regression, not a flake
        assert r2.should_block is True


def test_project_fingerprint_is_stable_across_unchanged_attempts_and_changes_with_real_edits(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    shipfile = _shipfile([_passing_command()])
    with _ledger(tmp_path) as writer:
        evaluate_gate(shipfile, tmp_path, "s1", writer, now=_NOW)
        evaluate_gate(shipfile, tmp_path, "s1", writer, now=_NOW)
        fp1 = _last_gate_event_fingerprint(writer, "s1", offset=1)
        fp2 = _last_gate_event_fingerprint(writer, "s1", offset=0)
        assert fp1 == fp2

        (tmp_path / "a.py").write_text("x = 2\n", encoding="utf-8")
        evaluate_gate(shipfile, tmp_path, "s1", writer, now=_NOW)
        fp3 = _last_gate_event_fingerprint(writer, "s1", offset=0)
        assert fp3 != fp2


def test_project_fingerprint_survives_the_redaction_pipeline_unredacted(tmp_path):
    """Self-caught bug, regression-guarded: a full 64-char sha256 hexdigest is exactly
    the shape shipgate.ledger.redaction's generic secret-run pattern (24+ contiguous
    alnum chars) exists to catch -- it was silently replaced with the literal string
    "[REDACTED]" on every write, which made every fingerprint comparison compare
    "[REDACTED]" against a freshly computed real digest and never match, silently
    disabling flake detection entirely. Fixed by truncating to 16 hex chars, comfortably
    under the 24-char threshold. This test fails loudly if that margin is ever eroded
    (e.g. a future edit lengthens the digest back toward 24+ chars)."""
    shipfile = _shipfile([_passing_command()])
    with _ledger(tmp_path) as writer:
        evaluate_gate(shipfile, tmp_path, "s1", writer, now=_NOW)
        fp = _last_gate_event_fingerprint(writer, "s1", offset=0)
    assert fp != "[REDACTED]"
    assert len(fp) < 24, "the fingerprint must stay short enough to never trigger generic secret-shape redaction"


def _last_gate_event_fingerprint(writer, session_id, *, offset):
    import json

    rows = writer.connection.execute(
        "SELECT raw_payload_redacted FROM events WHERE session_id=? AND record_type='gate_evaluation' "
        "ORDER BY event_id DESC",
        (session_id,),
    ).fetchall()
    return json.loads(rows[offset][0])["project_fingerprint"]


# --- task 2.6->2.7 follow-up: the inventory_complete claims sidecar ---------------------


def _write_sidecar(tmp_path: Path, condition_id: str, claimed_items) -> None:
    import json

    claims_dir = tmp_path / ".shipgate" / "claims"
    claims_dir.mkdir(parents=True, exist_ok=True)
    (claims_dir / f"{condition_id}.json").write_text(json.dumps(claimed_items), encoding="utf-8")


def test_inventory_complete_dispatches_for_real_when_a_valid_sidecar_exists(tmp_path):
    """Gate B condition #3, through evaluate_gate for real: the founder's own scenario,
    agent lists 3, grep finds 5 -> blocks."""
    for i in range(5):
        (tmp_path / f"mod_{i}.py").write_text("from shipgate.ledger.writer import LedgerWriter\n", encoding="utf-8")
    claimed = [{"match": f"mod_{i}.py:1", "disposition": "updated"} for i in range(3)]
    _write_sidecar(tmp_path, "inv1", claimed)

    condition = {
        "id": "inv1",
        "type": "inventory_complete",
        "grep_pattern": "from shipgate.ledger.writer import",
        "required_dispositions": ["updated"],
    }
    shipfile = _shipfile([condition])
    with _ledger(tmp_path) as writer:
        result = evaluate_gate(shipfile, tmp_path, "s1", writer, now=_NOW)
    outcome = result.conditions[0]
    assert outcome.dispatchable is True
    assert outcome.check_result.verdict == Verdict.CONTRADICTED
    assert result.should_block is True


def test_inventory_complete_stays_non_dispatchable_with_no_sidecar_unchanged_behavior(tmp_path):
    condition = {
        "id": "inv1",
        "type": "inventory_complete",
        "grep_pattern": "x",
        "required_dispositions": ["updated"],
    }
    shipfile = _shipfile([condition])
    with _ledger(tmp_path) as writer:
        result = evaluate_gate(shipfile, tmp_path, "s1", writer, now=_NOW)
    assert result.conditions[0].dispatchable is False


def test_inventory_complete_malformed_sidecar_is_unverified_not_a_crash(tmp_path):
    claims_dir = tmp_path / ".shipgate" / "claims"
    claims_dir.mkdir(parents=True, exist_ok=True)
    (claims_dir / "inv1.json").write_text("{not valid json", encoding="utf-8")

    condition = {
        "id": "inv1",
        "type": "inventory_complete",
        "grep_pattern": "x",
        "required_dispositions": ["updated"],
    }
    shipfile = _shipfile([condition])
    with _ledger(tmp_path) as writer:
        result = evaluate_gate(shipfile, tmp_path, "s1", writer, now=_NOW)
    outcome = result.conditions[0]
    assert outcome.dispatchable is True
    assert outcome.check_result.verdict == Verdict.UNVERIFIED
    assert outcome.check_result.observed is False
    assert "malformed" in outcome.check_result.reason


# --- Finding 5: a launch blocker -- the vacuous-pass detector could be
# silenced by its own input. The founder's own A/B/C control, and fix 2's fail-closed
# dispatch wrapper proven independently of decoding at all. -----------------------------


def _tests_pass_condition() -> dict:
    return {"id": "tests-pass", "type": "tests_pass", "command": f'"{PY}" -m pytest -q'}


def test_finding5_control_a_zero_tests_no_conftest_blocks_vacuous(tmp_path):
    """Control A: the plain vacuous case, unaffected by this fix either way."""
    shipfile = _shipfile([_tests_pass_condition()])
    with _ledger(tmp_path) as writer:
        result = evaluate_gate(shipfile, tmp_path, "s1", writer, now=_NOW)
    outcome = result.conditions[0]
    assert outcome.check_result.verdict == Verdict.UNVERIFIED_VACUOUS
    assert outcome.check_result.observed is False
    assert result.should_block is True


def test_finding5_control_b_zero_tests_ascii_conftest_blocks_vacuous(tmp_path):
    """Control B: a conftest that prints something the host locale can decode fine --
    already worked correctly before this fix; must stay unchanged."""
    (tmp_path / "conftest.py").write_text('print("collection note: nothing to see here")\n', encoding="utf-8")
    shipfile = _shipfile([_tests_pass_condition()])
    with _ledger(tmp_path) as writer:
        result = evaluate_gate(shipfile, tmp_path, "s1", writer, now=_NOW)
    outcome = result.conditions[0]
    assert outcome.check_result.verdict == Verdict.UNVERIFIED_VACUOUS
    assert outcome.check_result.observed is False
    assert result.should_block is True


def test_finding5_control_c_zero_tests_emoji_conftest_now_also_blocks_vacuous(tmp_path):
    """Control C: the founder's exact finding. A conftest that writes a raw UTF-8 byte
    sequence the HOST locale can't decode used to crash `evaluate_gate` with an
    uncaught `AttributeError` (`collect_test_count`'s `proc.stdout.splitlines()` on
    `None`) -- which `shipgate/hooks/stop.py`'s outer fail-open catch then silently
    turned into an ALLOWED stop, no block, no per-condition ledger detail. Must now
    match A and B exactly: a real, blocking, `unverified-vacuous` result -- not a
    crash, and not a pass."""
    (tmp_path / "conftest.py").write_text(
        'import sys\nsys.stdout.buffer.write("collection note: \\U0001F40D\\n".encode("utf-8"))\n'
        "sys.stdout.buffer.flush()\n",
        encoding="utf-8",
    )
    shipfile = _shipfile([_tests_pass_condition()])
    with _ledger(tmp_path) as writer:
        result = evaluate_gate(shipfile, tmp_path, "s1", writer, now=_NOW)  # used to raise AttributeError
    outcome = result.conditions[0]
    assert outcome.check_result.verdict == Verdict.UNVERIFIED_VACUOUS
    assert outcome.check_result.observed is False
    assert result.should_block is True
    # Same shape as A and B, not a special-cased message -- the whole point is that this
    # is now an ordinary, correctly-classified vacuous result, not a distinct failure mode.
    assert "collected 0 tests" in outcome.check_result.reason


def test_finding5_fix2_a_checker_exception_unrelated_to_decoding_also_fails_closed(tmp_path):
    """Fix 2, proven independently of fix 1 / of decoding at all: `command_succeeds`
    with no `command` key raises a plain `KeyError` inside the checker, nothing to do
    with subprocess output. Before this session's fix, that exception propagated out
    of `evaluate_gate` uncaught -- exactly the same silent-fail-open shape as Control C,
    just triggered a different way. Must render as a real, blocking, per-condition
    `unverified-vacuous` result naming the real exception -- never a crash, and the
    exception's own type/message are what's reported, not a fabricated diagnosis."""
    shipfile = _shipfile([{"id": "broken-condition", "type": "command_succeeds"}])
    with _ledger(tmp_path) as writer:
        result = evaluate_gate(shipfile, tmp_path, "s1", writer, now=_NOW)  # used to raise KeyError
    outcome = result.conditions[0]
    assert outcome.check_result.verdict == Verdict.UNVERIFIED_VACUOUS
    assert outcome.check_result.observed is False
    assert result.should_block is True
    assert "KeyError" in outcome.check_result.reason
    assert "'command'" in outcome.check_result.reason


def test_finding5_fix2_the_ledger_records_a_real_verdict_row_not_silence(tmp_path):
    """The fail-closed CheckResult isn't just returned in memory -- it must actually
    reach the ledger as a real `verdicts` row, the same as any other checker outcome,
    so a Ship Report or a re-run `doctor` sees a genuine claim history rather than a
    gap where this attempt should be."""
    shipfile = _shipfile([{"id": "broken-condition", "type": "command_succeeds"}])
    with _ledger(tmp_path) as writer:
        evaluate_gate(shipfile, tmp_path, "s1", writer, now=_NOW)
        row = writer.connection.execute(
            "SELECT v.verdict, v.reason FROM verdicts v JOIN claims c ON v.claim_id = c.claim_id "
            "WHERE c.shipfile_condition_ref = ?",
            ("broken-condition",),
        ).fetchone()
    assert row is not None
    assert row[0] == "unverified-vacuous"
    assert "KeyError" in row[1]

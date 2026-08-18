"""Task 2.2 — deterministic checkers (`tests_pass`, `file_exists`,
`forbidden_pattern_absent`, `command_succeeds`), each with vacuous-pass detection built
in where the concept applies (task 2.5, built in from the start — see
`shipgate/gate/checkers.py`'s module docstring for the per-checker audit of where it
does and honestly doesn't).

Every test runs a REAL check against a REAL `tmp_path` project — real subprocesses for
`tests_pass`/`command_succeeds`, a real filesystem walk for `file_exists`/
`forbidden_pattern_absent`. Standing note (Session 004 close): the
messy realistic case, not just the clean isolated one — several tests below combine a
condition with something a real project would actually have (a vendor directory full of
noise, a nested match, an ignored-directory false-negative risk) rather than a single
minimal fixture.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

from shipgate.gate.checkers import (
    DEFAULT_CHECKER_TIMEOUT_SECONDS,
    _run_shell_command_bounded,
    check_command_succeeds,
    check_file_exists,
    check_forbidden_pattern_absent,
    check_inventory_complete,
    check_runtime_evidence,
    check_tests_pass,
    run_checker,
)
from shipgate.ledger.writer import LedgerWriter
from shipgate.verdicts import EvidenceTier, Verdict

PY = sys.executable


def _write(tmp_path: Path, rel: str, content: str) -> Path:
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# --- tests_pass -------------------------------------------------------------------------


def test_tests_pass_pytest_command_all_passing_is_runtime_verified(tmp_path):
    _write(tmp_path, "test_x.py", "def test_a():\n    assert True\n")
    result = check_tests_pass({"id": "c1", "type": "tests_pass", "command": "pytest -q"}, tmp_path)
    assert result.verdict == Verdict.VERIFIED
    assert result.evidence_tier == EvidenceTier.RUNTIME_VERIFIED
    assert result.observed is True


def test_tests_pass_python_dash_m_pytest_form_is_recognized(tmp_path):
    _write(tmp_path, "test_x.py", "def test_a():\n    assert True\n")
    result = check_tests_pass({"id": "c1", "type": "tests_pass", "command": "python -m pytest -q"}, tmp_path)
    assert result.verdict == Verdict.VERIFIED
    assert result.evidence_tier == EvidenceTier.RUNTIME_VERIFIED


def test_tests_pass_a_real_failure_is_contradicted_not_verified(tmp_path):
    _write(tmp_path, "test_x.py", "def test_a():\n    assert 1 == 2\n")
    result = check_tests_pass({"id": "c1", "type": "tests_pass", "command": "pytest -q"}, tmp_path)
    assert result.verdict == Verdict.CONTRADICTED
    assert result.evidence_tier is None
    assert "1 failed" in result.reason


def test_tests_pass_zero_collected_is_the_vacuous_case_the_founders_scenario(tmp_path):
    """The exact false-completion shape the report names: an agent claims 'tests pass'
    but the suite collected nothing. Must never render VERIFIED."""
    result = check_tests_pass({"id": "c1", "type": "tests_pass", "command": "pytest -q"}, tmp_path)
    assert result.verdict == Verdict.UNVERIFIED_VACUOUS
    assert result.observed is False
    assert result.verdict != Verdict.VERIFIED


def test_tests_pass_non_pytest_command_success_is_unverified_not_a_pass(tmp_path):
    """Founder review finding, fixed: an exit-code-0 from an unrecognized runner used
    to render VERIFIED. It must not -- a bare exit code doesn't confirm tests ran."""
    command = f'"{PY}" -c "import sys; sys.exit(0)"'
    result = check_tests_pass({"id": "c1", "type": "tests_pass", "command": command}, tmp_path)
    assert result.verdict == Verdict.UNVERIFIED
    assert result.evidence_tier is None
    assert result.observed is False
    assert result.verdict != Verdict.VERIFIED


def test_tests_pass_non_pytest_runner_that_finds_nothing_and_exits_zero_is_the_founders_exact_case(tmp_path):
    """The founder's own adversarial reproduction: a command that prints 'No tests
    found' and exits 0 -- exactly what a default Jest config does against a directory
    with no matching test files. Must not render green."""
    command = f'"{PY}" -c "print(\'No tests found\'); import sys; sys.exit(0)"'
    result = check_tests_pass({"id": "c1", "type": "tests_pass", "command": command}, tmp_path)
    assert result.verdict == Verdict.UNVERIFIED
    assert result.verdict != Verdict.VERIFIED
    assert result.observed is False


def test_tests_pass_pytest_timeout_is_unverified_not_a_hang(tmp_path):
    """The retry-cap/loop-breaker (task 2.6) doesn't exist yet, but this checker must
    still never block forever -- a real subprocess that outlives a short timeout must
    come back as a verdict, not an uncaught exception or an infinite wait."""
    _write(tmp_path, "test_slow.py", "import time\n\ndef test_slow():\n    time.sleep(30)\n")
    result = check_tests_pass({"id": "c1", "type": "tests_pass", "command": "pytest -q"}, tmp_path, timeout=1)
    assert result.verdict == Verdict.UNVERIFIED
    assert result.observed is False
    assert "did not finish within 1s" in result.reason


def test_tests_pass_non_pytest_timeout_is_unverified_not_a_hang(tmp_path):
    command = f'"{PY}" -c "import time; time.sleep(30)"'
    result = check_tests_pass({"id": "c1", "type": "tests_pass", "command": command}, tmp_path, timeout=1)
    assert result.verdict == Verdict.UNVERIFIED
    assert result.observed is False
    assert "did not finish within 1s" in result.reason


def test_tests_pass_non_pytest_command_failure_is_contradicted(tmp_path):
    command = f'"{PY}" -c "import sys; sys.exit(1)"'
    result = check_tests_pass({"id": "c1", "type": "tests_pass", "command": command}, tmp_path)
    assert result.verdict == Verdict.CONTRADICTED


def test_tests_pass_scopes_to_a_k_expression_consistently(tmp_path):
    _write(
        tmp_path,
        "test_x.py",
        "def test_keep():\n    assert True\n\ndef test_drop():\n    assert False\n",
    )
    result = check_tests_pass({"id": "c1", "type": "tests_pass", "command": "pytest -q -k test_keep"}, tmp_path)
    assert result.verdict == Verdict.VERIFIED
    assert "1 passed, 1 collected" in result.reason


# --- file_exists -------------------------------------------------------------------------


def test_file_exists_true_is_disk_verified(tmp_path):
    _write(tmp_path, "SESSION_LOG.md", "hello")
    result = check_file_exists({"id": "c2", "type": "file_exists", "path": "SESSION_LOG.md"}, tmp_path)
    assert result.verdict == Verdict.VERIFIED
    assert result.evidence_tier == EvidenceTier.DISK_VERIFIED


def test_file_exists_false_is_contradicted(tmp_path):
    result = check_file_exists({"id": "c2", "type": "file_exists", "path": "SESSION_LOG.md"}, tmp_path)
    assert result.verdict == Verdict.CONTRADICTED
    assert result.evidence_tier is None


def test_file_exists_nested_path(tmp_path):
    _write(tmp_path, "shipgate/gate/checkers.py", "# code")
    result = check_file_exists(
        {"id": "c2", "type": "file_exists", "path": "shipgate/gate/checkers.py"}, tmp_path
    )
    assert result.verdict == Verdict.VERIFIED


# --- forbidden_pattern_absent --------------------------------------------------------------


def test_forbidden_pattern_found_nested_is_contradicted_with_the_path_named(tmp_path):
    _write(tmp_path, "src/deep/nested/file.py", "x = 1\n# TODO: fix this\n")
    _write(tmp_path, "src/clean.py", "y = 2\n")
    result = check_forbidden_pattern_absent(
        {"id": "c3", "type": "forbidden_pattern_absent", "pattern": "TODO", "paths": ["src"]}, tmp_path
    )
    assert result.verdict == Verdict.CONTRADICTED
    assert "deep" in result.reason and "nested" in result.reason
    assert "src/deep/nested/file.py" in result.reason  # POSIX separators on every OS


def test_forbidden_pattern_absent_with_real_files_scanned_is_disk_verified(tmp_path):
    _write(tmp_path, "src/clean_a.py", "x = 1\n")
    _write(tmp_path, "src/clean_b.py", "y = 2\n")
    result = check_forbidden_pattern_absent(
        {"id": "c3", "type": "forbidden_pattern_absent", "pattern": "TODO", "paths": ["src"]}, tmp_path
    )
    assert result.verdict == Verdict.VERIFIED
    assert result.evidence_tier == EvidenceTier.DISK_VERIFIED
    assert result.observed is True


def test_forbidden_pattern_absent_on_a_nonexistent_path_is_vacuous_not_a_pass(tmp_path):
    result = check_forbidden_pattern_absent(
        {"id": "c3", "type": "forbidden_pattern_absent", "pattern": "TODO", "paths": ["does_not_exist"]}, tmp_path
    )
    assert result.verdict == Verdict.UNVERIFIED_VACUOUS
    assert result.observed is False


def test_forbidden_pattern_absent_on_an_empty_directory_is_also_vacuous(tmp_path):
    (tmp_path / "src").mkdir()
    result = check_forbidden_pattern_absent(
        {"id": "c3", "type": "forbidden_pattern_absent", "pattern": "TODO", "paths": ["src"]}, tmp_path
    )
    assert result.verdict == Verdict.UNVERIFIED_VACUOUS


def test_forbidden_pattern_ignores_vendor_directories_without_becoming_falsely_vacuous(tmp_path):
    """The messy realistic case: a real project has noise directories (here, .venv)
    that must be excluded from the scan -- but a TODO planted ONLY inside .venv must
    not (a) be flagged, since vendor code isn't the target, and (b) must not make the
    checker fall back to 'scanned 0 files' just because the excluded files happened to
    be the only ones present with a match -- there's a real, non-vendor file too, and
    that's what should register as scanned."""
    _write(tmp_path, ".venv/lib/vendored.py", "# TODO: not ours to fix\n")
    _write(tmp_path, "src/real_code.py", "z = 3\n")
    result = check_forbidden_pattern_absent(
        {"id": "c3", "type": "forbidden_pattern_absent", "pattern": "TODO", "paths": ["."]}, tmp_path
    )
    assert result.verdict == Verdict.VERIFIED  # the only real TODO is in an ignored dir
    assert result.observed is True


def test_forbidden_pattern_defaults_paths_to_project_root_when_omitted(tmp_path):
    _write(tmp_path, "anywhere.py", "# TODO later\n")
    result = check_forbidden_pattern_absent(
        {"id": "c3", "type": "forbidden_pattern_absent", "pattern": "TODO"}, tmp_path
    )
    assert result.verdict == Verdict.CONTRADICTED


# --- command_succeeds ----------------------------------------------------------------------


def test_command_succeeds_exit_zero_is_runtime_verified(tmp_path):
    command = f'"{PY}" -c "import sys; sys.exit(0)"'
    result = check_command_succeeds({"id": "c4", "type": "command_succeeds", "command": command}, tmp_path)
    assert result.verdict == Verdict.VERIFIED
    assert result.evidence_tier == EvidenceTier.RUNTIME_VERIFIED


def test_command_succeeds_nonzero_exit_is_contradicted(tmp_path):
    command = f'"{PY}" -c "import sys; sys.exit(1)"'
    result = check_command_succeeds({"id": "c4", "type": "command_succeeds", "command": command}, tmp_path)
    assert result.verdict == Verdict.CONTRADICTED


def test_command_succeeds_timeout_is_unverified_not_a_hang(tmp_path):
    command = f'"{PY}" -c "import time; time.sleep(30)"'
    result = check_command_succeeds({"id": "c4", "type": "command_succeeds", "command": command}, tmp_path, timeout=1)
    assert result.verdict == Verdict.UNVERIFIED
    assert result.evidence_tier is None
    assert result.observed is False
    assert "did not finish within 1s" in result.reason


def test_command_succeeds_runs_in_the_given_project_root(tmp_path):
    """Proves cwd is actually threaded through, not just accepted and ignored --
    the command writes a file relative to its own cwd; if this checker silently ran
    somewhere else, the file wouldn't land where the test looks for it."""
    command = f'"{PY}" -c "open(\'marker.txt\', \'w\').write(\'here\')"'
    check_command_succeeds({"id": "c4", "type": "command_succeeds", "command": command}, tmp_path)
    assert (tmp_path / "marker.txt").read_text(encoding="utf-8") == "here"


# --- Finding 5 (a launch blocker): the host locale must never be able to
# destroy a checker's ability to read a child process's output --------------------------


def test_run_shell_command_bounded_survives_a_byte_the_host_locale_cannot_decode(tmp_path):
    """Direct, byte-for-byte reproduction of the founder's own finding: a command that
    writes a raw UTF-8 byte sequence the HOST machine's locale can't decode (nothing to
    do with what the command itself intended to print) used to kill
    `subprocess.communicate()`'s background reader thread silently -- the exception
    never reached the caller, and `proc.stdout` came back `None` instead of a string.
    `\\U0001F40D` (snake emoji) encodes to bytes cp1252 (this dev machine's real,
    unforced locale) cannot decode -- confirmed via direct interpreter inspection
    before this fix existed. Decoding explicitly as UTF-8 (rather than the host locale)
    doesn't just avoid crashing -- it decodes CORRECTLY, since the bytes really are
    valid UTF-8; the fix's `errors="replace"` never even has to fire for this exact
    byte sequence, which is the point (see the next test for genuinely malformed
    UTF-8, where "replace" is what actually saves the read)."""
    command = f'"{PY}" -c "import sys; sys.stdout.buffer.write(\'\\U0001F40D\'.encode(\'utf-8\')); sys.stdout.buffer.flush()"'
    proc = _run_shell_command_bounded(command, cwd=tmp_path, timeout=10)
    assert proc.returncode == 0
    assert proc.stdout is not None, "the exact defect: used to be None, silently, with no exception reaching here"
    assert proc.stdout == "\U0001f40d"


def test_run_shell_command_bounded_survives_genuinely_malformed_utf8(tmp_path):
    """Not every undecodable byte is valid-UTF-8-that-the-host-locale-rejects -- a
    command could also emit genuinely malformed UTF-8 (a lone continuation byte, here).
    `errors="replace"` is what actually saves this read: never raises (unlike the old
    host-locale `"strict"` default), and never silently discards the evidence something
    was wrong the way `errors="ignore"` would -- the replacement character stays in the
    captured text rather than the byte vanishing without a trace."""
    command = f'"{PY}" -c "import sys; sys.stdout.buffer.write(b\\"ok \\" + bytes([0x80]) + b\\" ok\\"); sys.stdout.buffer.flush()"'
    proc = _run_shell_command_bounded(command, cwd=tmp_path, timeout=10)
    assert proc.returncode == 0
    assert proc.stdout is not None
    assert proc.stdout == "ok \ufffd ok"


def test_command_succeeds_no_longer_leaks_an_unhandled_thread_exception_on_undecodable_output(tmp_path, recwarn):
    """Same undecodable-byte scenario, through the public checker this time.
    `command_succeeds` never reads stdout content for its own verdict (by design --
    see the module docstring's stated limitation), so the verdict itself was never
    false here -- but the reader thread still died with an unhandled exception on
    every such invocation, silently, visible only as raw noise on the real process
    stderr and invisible to anything ShipGate records. pytest's own thread-exception
    hook turns an unhandled thread exception into a `PytestUnhandledThreadException`
    warning; asserting none fired is the regression guard for this fix's hygiene half,
    independent of the verdict (which is asserted unchanged, not a gap being closed)."""
    command = f'"{PY}" -c "import sys; sys.stdout.buffer.write(\'\\U0001F40D\'.encode(\'utf-8\')); sys.exit(0)"'
    result = check_command_succeeds({"id": "c4", "type": "command_succeeds", "command": command}, tmp_path)
    assert result.verdict == Verdict.VERIFIED  # unchanged -- return-code-only by design, not a gap
    thread_warnings = [w for w in recwarn.list if "Thread" in w.category.__name__]
    assert not thread_warnings, [str(w.message) for w in thread_warnings]


# --- inventory_complete (task 2.3) --------------------------------------------------------


def test_inventory_complete_the_founders_exact_scenario_agent_lists_3_grep_finds_5(tmp_path):
    """report §2.2 D3, Gate B condition 3, verbatim: 'agent lists 3, grep
    finds 5 -> blocks'. Five real call sites planted; the agent's own claim lists three
    of them. Must be CONTRADICTED, not VERIFIED, and must name what's missing."""
    for i in range(5):
        _write(tmp_path, f"src/mod_{i}.py", "from shipgate.ledger.writer import LedgerWriter\n")

    real_matches = []
    for i in range(5):
        real_matches.append(f"src/mod_{i}.py:1")

    claimed_items = [{"match": m, "disposition": "updated"} for m in real_matches[:3]]  # agent only lists 3

    result = check_inventory_complete(
        {
            "id": "inv1",
            "type": "inventory_complete",
            "grep_pattern": "from shipgate.ledger.writer import",
            "required_dispositions": ["updated", "intentionally-unchanged"],
        },
        tmp_path,
        claimed_items,
    )
    assert result.verdict == Verdict.CONTRADICTED
    assert "2 real match(es) not in the claimed inventory" in result.reason


def test_inventory_complete_full_enumeration_with_allowed_dispositions_is_disk_verified(tmp_path):
    _write(tmp_path, "src/mod_a.py", "from shipgate.ledger.writer import LedgerWriter\n")
    _write(tmp_path, "src/mod_b.py", "from shipgate.ledger.writer import LedgerWriter\n")
    claimed_items = [
        {"match": "src/mod_a.py:1", "disposition": "updated"},
        {"match": "src/mod_b.py:1", "disposition": "intentionally-unchanged"},
    ]
    result = check_inventory_complete(
        {
            "id": "inv1",
            "type": "inventory_complete",
            "grep_pattern": "from shipgate.ledger.writer import",
            "required_dispositions": ["updated", "intentionally-unchanged"],
        },
        tmp_path,
        claimed_items,
    )
    assert result.verdict == Verdict.VERIFIED
    assert result.evidence_tier == EvidenceTier.DISK_VERIFIED


def test_inventory_complete_a_fabricated_claimed_item_is_contradicted(tmp_path):
    """The agent claims a match that no real grep finds -- stale or fabricated, and
    must be caught, not just missing real matches."""
    _write(tmp_path, "src/mod_a.py", "from shipgate.ledger.writer import LedgerWriter\n")
    claimed_items = [
        {"match": "src/mod_a.py:1", "disposition": "updated"},
        {"match": "src/mod_ghost.py:1", "disposition": "updated"},
    ]
    result = check_inventory_complete(
        {
            "id": "inv1",
            "type": "inventory_complete",
            "grep_pattern": "from shipgate.ledger.writer import",
            "required_dispositions": ["updated"],
        },
        tmp_path,
        claimed_items,
    )
    assert result.verdict == Verdict.CONTRADICTED
    assert "no real grep found" in result.reason


def test_inventory_complete_a_disallowed_disposition_is_contradicted(tmp_path):
    _write(tmp_path, "src/mod_a.py", "from shipgate.ledger.writer import LedgerWriter\n")
    claimed_items = [{"match": "src/mod_a.py:1", "disposition": "will_fix_later"}]
    result = check_inventory_complete(
        {
            "id": "inv1",
            "type": "inventory_complete",
            "grep_pattern": "from shipgate.ledger.writer import",
            "required_dispositions": ["updated", "intentionally-unchanged"],
        },
        tmp_path,
        claimed_items,
    )
    assert result.verdict == Verdict.CONTRADICTED
    assert "disallowed disposition" in result.reason


def test_inventory_complete_match_strings_use_posix_separators_not_native_ones(tmp_path):
    """Regression guard: a bare str(Path) on Windows uses backslashes, which would make
    a real match silently unmatchable against a claimed_items list written with forward
    slashes (the realistic form -- and what this session's own first draft of these
    tests used, which is exactly what caught this). Match strings must be POSIX-style
    on every OS, matching the same convention shipgate/ledger/paths.py already uses for
    source_dir and for the identical reason."""
    _write(tmp_path, "src/nested/mod.py", "from shipgate.ledger.writer import LedgerWriter\n")
    claimed_items = [{"match": "src/nested/mod.py:1", "disposition": "updated"}]
    result = check_inventory_complete(
        {
            "id": "inv1",
            "type": "inventory_complete",
            "grep_pattern": "from shipgate.ledger.writer import",
            "required_dispositions": ["updated"],
        },
        tmp_path,
        claimed_items,
    )
    assert result.verdict == Verdict.VERIFIED
    assert "\\" not in result.reason


def test_inventory_complete_zero_grep_matches_is_vacuous(tmp_path):
    result = check_inventory_complete(
        {
            "id": "inv1",
            "type": "inventory_complete",
            "grep_pattern": "this_pattern_matches_nothing_anywhere",
            "required_dispositions": ["updated"],
        },
        tmp_path,
        claimed_items=[],
    )
    assert result.verdict == Verdict.UNVERIFIED_VACUOUS
    assert result.observed is False


# --- runtime_evidence, log_marker method (task 2.4) ----------------------------------------


def _ledger_with_event(tmp_path: Path, payload: dict) -> Path:
    db_path = tmp_path / ".shipgate" / "ledger.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with LedgerWriter(db_path, corpus_root=tmp_path) as writer:
        writer.insert_session(session_id="s1", project_slug="proj", source_dir=".")
        writer.insert_event(
            session_id="s1",
            source_file="<test>",
            source_offset=0,
            transcript_tier="session",
            record_type="test_event",
            timestamp="2026-08-15T00:00:00Z",
            raw_payload=payload,
        )
    return db_path


def test_runtime_evidence_marker_found_in_a_real_recorded_event_is_runtime_verified(tmp_path):
    _ledger_with_event(tmp_path, {"note": "SHIPGATE_GATE_FIRED happened here"})
    result = check_runtime_evidence(
        {"id": "re1", "type": "runtime_evidence", "method": "log_marker", "detail": "SHIPGATE_GATE_FIRED"},
        tmp_path,
    )
    assert result.verdict == Verdict.VERIFIED
    assert result.evidence_tier == EvidenceTier.RUNTIME_VERIFIED


def test_runtime_evidence_marker_in_source_but_never_run_is_contradicted_the_d1_lie(tmp_path):
    """The exact D1 scenario: code ON DISK contains the marker (an agent could grep the
    source and claim 'it's there, so it must have run'), but the ledger -- real recorded
    events -- never shows it firing. Must be CONTRADICTED, not VERIFIED: grepping source
    would be exactly the disk-only lie this checker exists to catch."""
    _write(tmp_path, "src/emitter.py", 'print("SHIPGATE_GATE_FIRED")  # the marker exists on disk\n')
    _ledger_with_event(tmp_path, {"note": "some unrelated event, the marker never actually fired"})

    result = check_runtime_evidence(
        {"id": "re1", "type": "runtime_evidence", "method": "log_marker", "detail": "SHIPGATE_GATE_FIRED"},
        tmp_path,
    )
    assert result.verdict == Verdict.CONTRADICTED
    assert result.verdict != Verdict.VERIFIED


def test_runtime_evidence_no_ledger_at_all_is_vacuous(tmp_path):
    result = check_runtime_evidence(
        {"id": "re1", "type": "runtime_evidence", "method": "log_marker", "detail": "X"}, tmp_path
    )
    assert result.verdict == Verdict.UNVERIFIED_VACUOUS
    assert result.observed is False


def test_runtime_evidence_ledger_exists_but_has_no_payload_events_is_vacuous(tmp_path):
    db_path = tmp_path / ".shipgate" / "ledger.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with LedgerWriter(db_path, corpus_root=tmp_path) as writer:
        writer.insert_session(session_id="s1", project_slug="proj", source_dir=".")
        writer.insert_event(
            session_id="s1",
            source_file="<test>",
            source_offset=0,
            transcript_tier="session",
            record_type="test_event",
            timestamp="2026-08-15T00:00:00Z",
            raw_payload=None,
        )
    result = check_runtime_evidence(
        {"id": "re1", "type": "runtime_evidence", "method": "log_marker", "detail": "X"}, tmp_path
    )
    assert result.verdict == Verdict.UNVERIFIED_VACUOUS


def test_runtime_evidence_unimplemented_method_raises_a_specific_error(tmp_path):
    with pytest.raises(ValueError, match="endpoint_probe"):
        check_runtime_evidence(
            {"id": "re1", "type": "runtime_evidence", "method": "endpoint_probe", "detail": "X"}, tmp_path
        )


def test_runtime_evidence_hash_chain_still_verifies_after_a_checker_read(tmp_path):
    """A checker only ever reads the ledger -- proves it doesn't accidentally write
    anything (which would corrupt the append-only chain the ledger's own triggers
    otherwise enforce on writes, but a raw SELECT bypasses the writer entirely)."""
    from shipgate.ledger.integrity import verify_all_chains

    db_path = _ledger_with_event(tmp_path, {"note": "SHIPGATE_GATE_FIRED"})
    check_runtime_evidence(
        {"id": "re1", "type": "runtime_evidence", "method": "log_marker", "detail": "SHIPGATE_GATE_FIRED"},
        tmp_path,
    )
    conn = sqlite3.connect(db_path)
    verify_all_chains(conn)


# --- dispatch ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "condition,expected_verdict",
    [
        ({"id": "d1", "type": "file_exists", "path": "SESSION_LOG.md"}, Verdict.CONTRADICTED),
        ({"id": "d2", "type": "forbidden_pattern_absent", "pattern": "TODO"}, Verdict.UNVERIFIED_VACUOUS),
    ],
)
def test_run_checker_dispatches_to_the_right_checker(tmp_path, condition, expected_verdict):
    result = run_checker(condition, tmp_path)
    assert result.verdict == expected_verdict


def test_run_checker_on_runtime_evidence_dispatches_correctly(tmp_path):
    _ledger_with_event(tmp_path, {"note": "MARKER_X"})
    result = run_checker(
        {"id": "re1", "type": "runtime_evidence", "method": "log_marker", "detail": "MARKER_X"}, tmp_path
    )
    assert result.verdict == Verdict.VERIFIED


def test_run_checker_on_inventory_complete_gives_a_specific_redirect_not_a_generic_error():
    """inventory_complete IS implemented (task 2.3) -- the error must say so and say
    where to call it instead, not lump it in with the truly-unimplemented types."""
    with pytest.raises(ValueError, match="check_inventory_complete directly"):
        run_checker(
            {"id": "d3", "type": "inventory_complete", "grep_pattern": "x", "required_dispositions": []}, Path(".")
        )


def test_run_checker_on_a_truly_not_yet_implemented_type_raises_a_specific_error_not_a_silent_pass():
    """emission_traced has no assigned task yet -- must fail loudly and specifically,
    never fall through to something that looks like a pass."""
    with pytest.raises(ValueError, match="emission_traced"):
        run_checker({"id": "d4", "type": "emission_traced", "marker": "x"}, Path("."))


def test_default_checker_timeout_leaves_headroom_inside_the_verified_hook_budget():
    """Founder review finding: the 600s Claude Code default hook timeout this constant
    is sized against was previously cited with no recorded source. Now verified
    (https://code.claude.com/docs/en/hooks.md): a command hook with no `timeout` field
    defaults to 600 seconds. A single tests_pass pytest check spends this constant
    TWICE (see reporters/pytest_reporter.py's run_pytest), so its worst case is 2x this
    constant. Regression guard, not a design test -- fails loudly if a future edit
    raises the constant back toward consuming the hook's own budget."""
    worst_case_for_one_tests_pass_check = 2 * DEFAULT_CHECKER_TIMEOUT_SECONDS
    verified_hook_default_timeout_seconds = 600
    assert worst_case_for_one_tests_pass_check < verified_hook_default_timeout_seconds / 2, (
        "a single tests_pass check's worst case must leave at least half of the "
        "verified 600s hook budget free for other done_conditions and overhead"
    )

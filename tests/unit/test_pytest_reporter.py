"""Task 2.9 — pytest reporter with collected-count (Phase 2 done-condition
for this module: it is what makes task 2.5's vacuous-pass detection possible at all).

Every test here runs a **real** `pytest` subprocess against a real, throwaway project
written to `tmp_path` — not a mocked subprocess. `reporters.pytest_reporter` exists
specifically to turn "did this actually run and observe something" into a number a
checker can trust; mocking the subprocess would test nothing about whether that number
is actually right.

Standing note (Session 004 close): the messy realistic case, not just
the clean isolated one. The suite below deliberately covers a passing suite, a failing
suite, an empty (zero-test) directory, AND a suite that can't even be collected due to a
broken import — the four shapes a real project's test command can actually take, not
just "it passed."
"""

import subprocess
from pathlib import Path

import pytest

from reporters.pytest_reporter import (
    DEFAULT_PYTEST_TIMEOUT_SECONDS,
    PYTEST_EXIT_NO_TESTS_COLLECTED,
    collect_test_count,
    run_pytest,
)

_SLOW_TEST = """
import time

def test_slow():
    time.sleep(30)
"""

_PASSING_TEST = """
def test_one():
    assert 1 + 1 == 2

def test_two():
    assert "a" in "abc"
"""

_MIXED_TEST = """
def test_passes():
    assert True

def test_also_passes():
    assert True

def test_fails():
    assert 1 == 2, "deliberately wrong"
"""

_BROKEN_IMPORT_TEST = """
import this_module_does_not_exist_anywhere  # noqa

def test_never_reached():
    assert True
"""


def _write(tmp_path: Path, name: str, content: str) -> None:
    (tmp_path / name).write_text(content, encoding="utf-8")


# --- collect_test_count --------------------------------------------------------------


def test_collect_test_count_on_a_real_passing_suite(tmp_path):
    _write(tmp_path, "test_sample.py", _PASSING_TEST)
    assert collect_test_count(tmp_path) == 2


def test_collect_test_count_on_an_empty_directory_is_zero(tmp_path):
    assert collect_test_count(tmp_path) == 0


def test_collect_test_count_matches_the_same_technique_ci_uses_on_this_project():
    """Not a tmp_path fixture -- runs against this repo's OWN real test suite, the same
    directory `.github/workflows/ci.yml`'s anti-vacuous check counts. If this module's
    counting technique ever silently diverged from CI's `grep -c '::'`, this is where
    it would show up."""
    repo_root = Path(__file__).resolve().parents[2]
    count = collect_test_count(repo_root)
    assert count > 100  # MIN_TESTS is already >100 as of this session; a loose floor,
    # not a hardcoded exact number that would need updating every time a test is added.


# --- run_pytest: the four realistic shapes --------------------------------------------


def test_run_pytest_on_a_passing_suite(tmp_path):
    _write(tmp_path, "test_sample.py", _PASSING_TEST)
    result = run_pytest(tmp_path)
    assert result.collected == 2
    assert result.passed == 2
    assert result.failed == 0
    assert result.errors == 0
    assert result.exit_code == 0
    assert result.ran_any_tests is True
    assert result.all_passed is True


def test_run_pytest_on_a_suite_with_a_real_failure(tmp_path):
    _write(tmp_path, "test_sample.py", _MIXED_TEST)
    result = run_pytest(tmp_path)
    assert result.collected == 3
    assert result.passed == 2
    assert result.failed == 1
    assert result.exit_code != 0
    assert result.ran_any_tests is True
    assert result.all_passed is False


def test_run_pytest_on_zero_tests_is_the_vacuous_signal(tmp_path):
    """The one case this whole module exists to get right: an empty project must not
    be indistinguishable from a passing one. Two independently-derived signals both
    have to agree it's empty -- the collected-count AND pytest's own dedicated
    no-tests-collected exit code -- a cross-check, not just trusting one source."""
    result = run_pytest(tmp_path)
    assert result.collected == 0
    assert result.ran_any_tests is False
    assert result.all_passed is False  # never accidentally "passed" on nothing
    assert result.exit_code == PYTEST_EXIT_NO_TESTS_COLLECTED


def test_run_pytest_on_a_suite_that_cannot_even_be_collected(tmp_path):
    """A broken import means pytest can't collect anything at all -- a different failure
    shape from "genuinely zero tests," but one this module must not silently paper over
    into looking clean. Collected-count lands at 0 (nothing WAS collected) and the exit
    code is non-zero, so this reaches the same "never renders green" vacuous path a
    truly empty project does -- which is the safe direction to fail in, not a bug."""
    _write(tmp_path, "test_broken.py", _BROKEN_IMPORT_TEST)
    result = run_pytest(tmp_path)
    assert result.collected == 0
    assert result.ran_any_tests is False
    assert result.exit_code != 0
    assert "this_module_does_not_exist_anywhere" in (result.stdout + result.stderr)


def test_run_pytest_result_command_records_what_actually_ran(tmp_path):
    _write(tmp_path, "test_sample.py", _PASSING_TEST)
    result = run_pytest(tmp_path)
    assert "pytest" in " ".join(result.command)
    assert "-q" in result.command


def test_a_caller_supplied_verbosity_flag_does_not_break_the_collected_count(tmp_path):
    """Regression guard for the exact bug this module shipped with and a test caught:
    a caller passing '-q' (a completely realistic shipfile command like `pytest -q`)
    must not silently double up with this module's own '-q' and collapse
    --collect-only's output to a per-file summary with no '::' lines -- which would
    make collect_test_count report 0 for a project that really has tests."""
    _write(tmp_path, "test_sample.py", _PASSING_TEST)
    assert collect_test_count(tmp_path, pytest_args=["-q"]) == 2

    result = run_pytest(tmp_path, pytest_args=["-q"])
    assert result.collected == 2
    assert result.passed == 2
    assert result.ran_any_tests is True


def test_pytest_args_scope_collected_count_and_run_to_the_same_subset(tmp_path):
    """Passing pytest_args (e.g. -k) must narrow BOTH the collected-count and the real
    run to the same subset -- otherwise a caller filtering to one test would see a
    collected-count for the whole directory and a pass/fail count for one test, an
    internally inconsistent result no checker could trust."""
    _write(tmp_path, "test_sample.py", _MIXED_TEST)
    result = run_pytest(tmp_path, pytest_args=["-k", "test_passes"])
    assert result.collected == 1
    assert result.passed == 1
    assert result.failed == 0


# --- timeout (founder review finding, Session close) -----------------------------------


def test_run_pytest_raises_timeout_expired_on_a_real_hang(tmp_path):
    """Founder review finding: neither subprocess call in this module had a timeout.
    A real subprocess that sleeps past a short timeout must raise the standard
    subprocess.TimeoutExpired, not hang forever and not silently return a fabricated
    result -- this module doesn't decide what a timeout MEANS (that's
    shipgate.gate.checkers' job), it only guarantees the call returns."""
    _write(tmp_path, "test_slow.py", _SLOW_TEST)
    with pytest.raises(subprocess.TimeoutExpired):
        run_pytest(tmp_path, timeout=1)


def test_collect_test_count_raises_timeout_expired_on_a_real_hang(tmp_path):
    """A slow-to-import conftest.py can hang collection itself, before any test even
    runs -- collect_test_count needs its own bound, independent of run_pytest's."""
    _write(tmp_path, "conftest.py", "import time\ntime.sleep(30)\n")
    with pytest.raises(subprocess.TimeoutExpired):
        collect_test_count(tmp_path, timeout=1)


# --- Finding 5 (a launch blocker): a byte the HOST locale can't decode
# must never be able to silence collected-count reporting ------------------------------

_CONFTEST_WRITES_UNDECODABLE_BYTES = """
import sys
sys.stdout.buffer.write("\\U0001F40D collection note\\n".encode("utf-8"))
sys.stdout.buffer.flush()
"""


def test_collect_test_count_survives_a_conftest_that_prints_a_byte_the_host_cannot_decode(tmp_path):
    """The founder's own exact A/B/C repro, at the layer where it actually originates:
    a project's conftest.py prints something (an emoji here) during collection. Before
    this fix, `subprocess.run(..., text=True)` decoded the captured stdout using this
    HOST machine's locale (cp1252), which cannot decode the emoji's UTF-8 bytes --
    `subprocess.communicate()`'s background reader thread died silently, and
    `proc.stdout` came back `None`, crashing `collect_test_count` on
    `.splitlines()` with an `AttributeError` that propagated all the way out of
    `evaluate_gate` (see `shipgate/gate/orchestrator.py`'s design decision 7).
    A conftest print having nothing to do with the project's own
    correctness must never be able to make a real, checkable zero-collected result
    silently uncheckable."""
    _write(tmp_path, "conftest.py", _CONFTEST_WRITES_UNDECODABLE_BYTES)
    assert collect_test_count(tmp_path) == 0  # used to raise AttributeError instead


def test_run_pytest_survives_a_conftest_that_prints_a_byte_the_host_cannot_decode(tmp_path):
    """Same scenario through `run_pytest` (which calls `collect_test_count` internally,
    then a second real subprocess run that shares the identical decode risk) -- the
    vacuous-pass signal (`ran_any_tests is False`) must still come through cleanly."""
    _write(tmp_path, "conftest.py", _CONFTEST_WRITES_UNDECODABLE_BYTES)
    result = run_pytest(tmp_path)
    assert result.collected == 0
    assert result.ran_any_tests is False


def test_default_timeout_leaves_headroom_inside_the_verified_hook_budget():
    """Founder review finding: the 600s Claude Code default hook timeout this constant
    is sized against was previously cited with no recorded source -- an unverified
    number doing load-bearing work. Now verified against the official hooks reference
    (https://code.claude.com/docs/en/hooks.md): a command hook with no `timeout` field
    defaults to 600 seconds. run_pytest spends this constant TWICE (collect phase, then
    the real run -- see run_pytest's docstring), so the worst case for a single
    tests_pass check is 2x this constant, not the constant alone. This is a regression
    guard, not a design test: it fails loudly if a future edit raises the constant back
    toward the point where one tests_pass check could consume most of the hook's own
    budget, silently reintroducing the exact risk this session's finding fixed."""
    worst_case_for_one_tests_pass_check = 2 * DEFAULT_PYTEST_TIMEOUT_SECONDS
    verified_hook_default_timeout_seconds = 600
    assert worst_case_for_one_tests_pass_check < verified_hook_default_timeout_seconds / 2, (
        "a single tests_pass check's worst case must leave at least half of the "
        "verified 600s hook budget free for other done_conditions and overhead"
    )

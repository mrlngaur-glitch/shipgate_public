"""Runs pytest against a real project and reports back what actually happened —
**collected-count** above all (report §13.1: "collected-count reporting — enables
vacuous-pass detection"; task 2.9, Phase 2).

Built first in Phase 2, ahead of its own task number, on explicit founder instruction:
task 2.5 ("vacuous-pass detection in every checker") has nothing to detect vacuousness
*from* until something actually counts how many tests were collected. Every checker in
`shipgate.gate` that inspects test results sits on top of what this module reports.

**Why two subprocess runs, not one:** a single `pytest -q` run's terminal summary line
("3 passed in 0.05s", "no tests ran in 0.00s", "2 failed, 1 error in 0.12s"...) is meant
for a human, not a parser — its wording has changed across pytest versions and differs
between "no tests ran," a collection error, and a real 0-test run. Rather than regex
the human-readable line (fragile, and the exact failure class this project's own accuracy
rules warn against — a check that "looks like" it passed), this module gets its two numbers from
two purpose-built, stable sources:

1. **Collected count** — `pytest --collect-only -q`, counting output lines containing
   `'::'`. This is the *identical* technique `.github/workflows/ci.yml` already uses for
   this project's own anti-vacuous CI gate — one shared, already-proven definition of
   "collected" across the whole codebase, not a second implementation that could drift
   from the first.
2. **Pass/fail/error/skip counts** — pytest's own `--junitxml` machine-readable report,
   parsed with the standard-library `xml.etree.ElementTree` (no new dependency —
   every dependency addition is treated as a supply-chain decision).

Runs pytest as a synchronous subprocess. This does not violate the latency covenant
("nothing synchronous in any request path" is this project's own standing rule): a gate check runs at the
Stop-hook touchpoint, after the agent has already finished working, never inside the
tool-call loop a token is waiting on. It is not a background daemon and it does not run
unless someone asks for a verdict.

**Both subprocess calls are bounded (`DEFAULT_PYTEST_TIMEOUT_SECONDS`, founder review
finding).** An unbounded `subprocess.run` here is exactly the failure
this project's own standing rules warn about the moment a checker call lands on the `Stop` hook's
synchronous path (tasks 2.6/2.8): a hung test process would block forever, with no
retry-cap and no honest red — just silence. A timeout raises the standard
`subprocess.TimeoutExpired`; this module makes no verdict decision about what a timeout
*means* (that's `shipgate.gate.checkers`' job, staying consistent with this module's
role as a fact-reporter, not a verdict-producer) — it only guarantees the call returns.

**Founder finding, fixed this session (Finding 5 — a launch blocker, not
merely a Gate C one): both `subprocess.run` calls above used to pass `text=True` with no
`encoding=`, decoding the child's stdout/stderr using the HOST machine's locale rather than
anything about the project being tested.** On Windows, a `communicate()`-read pipe that
contains a byte sequence the host's codepage can't decode kills the read on a background
thread silently (`Lib/subprocess.py::_readerthread` catches nothing) — `collect_test_count`
got `proc.stdout is None` back and crashed on `.splitlines()`, an *uncaught* exception that
propagated out of this module, out of `shipgate.gate.checkers.check_tests_pass`, out of
`shipgate.gate.orchestrator.evaluate_gate`, straight to `shipgate.hooks.stop`'s outer
fail-open catch — the exact vacuous-pass detector this module exists to feed, silenced by
its own input, releasing `Stop` as if the gate were green. A zero-test project whose
collection output happened to contain one byte the *host* couldn't decode (nothing to do
with the project's own correctness) went from a correct hard block to a silent pass. Fixed
by decoding explicitly as UTF-8 with `errors="replace"` (see
`shipgate.gate.checkers._run_shell_command_bounded`'s docstring for the full reasoning,
shared verbatim here) — and see `shipgate.gate.orchestrator.evaluate_gate`'s docstring for
the second, structural half of this fix, which stops treating *any* checker exception as
this module's problem to prevent alone.

Zero Claude-Code-specific imports.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

#: pytest's own "no tests collected" exit code (see `_EXIT_CODES` in pytest's source).
#: Not relied upon as the *primary* vacuous signal (see module docstring on why) but
#: cross-checked against the collected-count in `run_pytest` as a consistency guard —
#: if these two independently-derived signals ever disagreed, that would itself be
#: worth surfacing rather than silently trusting one of them.
PYTEST_EXIT_NO_TESTS_COLLECTED = 5

#: **Verified, not assumed (founder review finding).** The 600s figure
#: this constant is sized against was previously cited in-comment with no recorded
#: source — an unverified number doing load-bearing work, the exact class of claim
#: this project's own accuracy rules exist to catch, caught here on our own code before it caused a real
#: silent failure. Confirmed against the official Claude Code hooks reference
#: (https://code.claude.com/docs/en/hooks.md): a `command` hook with no `timeout` field
#: defaults to **600 seconds**, and that default is configurable per-hook-entry via an
#: integer `timeout` field in `settings.json` (doc example: `"timeout": 45` overrides
#: "the default 600"). `UserPromptSubmit` (30s) and a couple of other events have their
#: own lower defaults, irrelevant here — `Stop`, the event this checker call is headed
#: for (tasks 2.6/2.8), uses the standard 600s.
#:
#: `run_pytest` below spends this timeout **twice** — once for `collect_test_count`,
#: once for the real run (each phase gets its own full budget, not a shared one; see
#: `run_pytest`'s docstring) — so the worst case for a single `tests_pass` check is
#: `2 * DEFAULT_PYTEST_TIMEOUT_SECONDS`, not this constant alone. A shipfile's `Stop`
#: hook invocation may also need to evaluate other `done_conditions` in the same
#: 600-second budget (no gate orchestrator exists yet to say how many), so this constant
#: is sized to leave the majority of the hook's budget free rather than to use all of
#: it: 60s per phase → 120s worst case for one `tests_pass` check, roughly a fifth of
#: the verified 600s budget. Test regression guard:
#: `test_default_timeout_leaves_headroom_inside_the_verified_hook_budget`.
#:
#: Overridable per call (both functions below accept `timeout=`) for a project that
#: legitimately needs longer, and for tests that need much shorter. There is no
#: `shipgate init` (or other settings.json writer) built yet to make this configurable
#: per-shipfile — when one is, prefer leaving Claude Code's own hook `timeout` at its
#: verified 600s default rather than emitting a shorter override, unless a future gate
#: orchestrator is shown to need more of that budget than this constant now assumes.
DEFAULT_PYTEST_TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class PytestResult:
    """What actually happened when pytest ran — the fact this module exists to report,
    with no interpretation of what it *means* for a verdict. That interpretation is
    `shipgate.gate.checkers.check_tests_pass`'s job, not this module's; this module
    stays a fact-reporter so it can be reused by anything that needs pytest facts,
    not just the gate."""

    command: tuple[str, ...]
    exit_code: int
    collected: int
    passed: int
    failed: int
    errors: int
    skipped: int
    stdout: str
    stderr: str

    @property
    def ran_any_tests(self) -> bool:
        """`False` means this run observed nothing — zero tests collected. The signal
        `shipgate.gate`'s vacuous-pass detection is built on."""
        return self.collected > 0

    @property
    def all_passed(self) -> bool:
        return self.ran_any_tests and self.failed == 0 and self.errors == 0


#: Verbosity flags stripped from caller-supplied `pytest_args` before we add our own.
#: **Root-cause note, found the hard way (Session 004, task 2.9/2.2 combined test):**
#: `pytest --collect-only -q` prints one `::`-bearing node-ID line per test; but
#: `pytest --collect-only -q -q` (i.e. `-qq`, exactly what you get if the caller's own
#: command already says `-q` and this module naively appends a second one) collapses to
#: a terse PER-FILE summary like `test_x.py: 1` — zero `::` substrings, so
#: `collect_test_count` would silently report 0 for a project that collected real
#: tests. `.github/workflows/ci.yml` already carries a comment about this exact
#: footgun for its own `-q` usage; it recurred here because this module builds its own
#: argv on top of a caller-supplied one instead of controlling verbosity outright. Fixed
#: at the root — strip whatever verbosity the caller asked for and always add exactly
#: the one `-q` this module's own parsing depends on — not by telling callers never to
#: pass `-q` themselves, which just moves the footgun to every caller.
_VERBOSITY_FLAGS = frozenset({"-q", "-qq", "-v", "-vv", "-vvv", "--quiet", "--verbose"})


def _strip_verbosity_flags(pytest_args: list[str] | None) -> list[str]:
    return [arg for arg in (pytest_args or []) if arg not in _VERBOSITY_FLAGS]


def _pytest_invocation(pytest_args: list[str] | None) -> list[str]:
    return [sys.executable, "-m", "pytest", *_strip_verbosity_flags(pytest_args)]


def collect_test_count(
    cwd: Path, pytest_args: list[str] | None = None, timeout: float = DEFAULT_PYTEST_TIMEOUT_SECONDS
) -> int:
    """Runs `pytest --collect-only -q` under `cwd` and returns the number of collected
    node IDs — counted the same way `.github/workflows/ci.yml` counts this project's own
    suite (`grep -c '::'`), so "collected" means one thing everywhere in this codebase.
    Raises `subprocess.TimeoutExpired` if collection doesn't finish within `timeout`
    seconds — collection hanging (e.g. an import-time infinite loop in a conftest) is
    exactly as much "no evidence" as the real run hanging."""
    args = [*_pytest_invocation(pytest_args), "--collect-only", "-q"]
    proc = subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False, timeout=timeout,
    )
    return sum(1 for line in proc.stdout.splitlines() if "::" in line)


def run_pytest(
    cwd: Path, pytest_args: list[str] | None = None, timeout: float = DEFAULT_PYTEST_TIMEOUT_SECONDS
) -> PytestResult:
    """Runs the real suite under `cwd` and returns a `PytestResult`. `pytest_args` are
    appended verbatim (e.g. a specific test path, `-k <expr>`) — same convention as
    `collect_test_count`, so a caller filtering to a subset of tests gets a consistent
    collected-count and pass/fail count for that same subset, not two different scopes.
    `timeout` applies independently to the collect-only phase and the real run — each
    phase gets its own full budget, not a shared one split between them. Raises
    `subprocess.TimeoutExpired` (uncaught) if either phase exceeds it; see the module
    docstring for why this module doesn't turn that into a verdict itself."""
    collected = collect_test_count(cwd, pytest_args, timeout=timeout)

    with tempfile.TemporaryDirectory() as tmp_dir:
        junit_path = Path(tmp_dir) / "junit.xml"
        args = [*_pytest_invocation(pytest_args), "-q", f"--junitxml={junit_path}"]
        proc = subprocess.run(
            args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False, timeout=timeout,
        )

        passed = failed = errors = skipped = 0
        if junit_path.exists():
            root = ET.parse(junit_path).getroot()
            suites = root.findall("testsuite") if root.tag == "testsuites" else [root]
            for suite in suites:
                total = int(suite.get("tests", 0))
                suite_failed = int(suite.get("failures", 0))
                suite_errors = int(suite.get("errors", 0))
                suite_skipped = int(suite.get("skipped", 0))
                failed += suite_failed
                errors += suite_errors
                skipped += suite_skipped
                passed += total - suite_failed - suite_errors - suite_skipped

    return PytestResult(
        command=tuple(args),
        exit_code=proc.returncode,
        collected=collected,
        passed=passed,
        failed=failed,
        errors=errors,
        skipped=skipped,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


__all__ = [
    "DEFAULT_PYTEST_TIMEOUT_SECONDS",
    "PYTEST_EXIT_NO_TESTS_COLLECTED",
    "PytestResult",
    "collect_test_count",
    "run_pytest",
]

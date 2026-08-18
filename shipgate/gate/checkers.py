"""Deterministic checkers (task 2.2, Phase 2) — `tests_pass`,
`file_exists`, `forbidden_pattern_absent`, `command_succeeds`. Each takes one
`done_conditions` entry (already validated by `shipgate.shipfile`) and a project root,
and returns a `CheckResult`: a verdict, the evidence tier when applicable, a reason, and
whether the checker actually observed something.

**Vacuous-pass detection is built in per checker from day one (task 2.5), not bolted on
after — and stated honestly per checker, including where it structurally doesn't apply,
rather than forced everywhere it doesn't fit:**

- `tests_pass` — **yes**, when the command is recognized as a pytest invocation:
  `reporters.pytest_reporter` reports a real collected-count; zero collected renders
  `unverified-vacuous`, never green. For a non-pytest test command, no collected-count
  machinery exists yet (report §13.1: "reporters/ — pytest first, vitest second") — this
  is a **stated gap**, not a silently skipped one; see `_pytest_extra_args`.
- `forbidden_pattern_absent` — **yes**: zero files scanned (an empty/missing `paths`
  target) is exactly as vacuous as zero tests collected — "found nothing" must not look
  identical to "searched nothing." Counted and checked explicitly.
- `file_exists` — **no vacuous mode exists, and that's a considered answer, not an
  oversight.** Checking whether one path exists is inherently binary; there is no
  "searched nothing" state to distinguish from "confirmed absent" — an existence check
  always observes something.
- `command_succeeds` — **no generic vacuous mode.** An arbitrary shell command has no
  universal "did this actually do real work" signal this checker can assert on without
  inventing an unreliable heuristic (empty stdout ≠ "ran nothing," for instance) — a
  structural limitation, stated here rather than faked with a guess.
- `inventory_complete` (task 2.3) — **yes**: 0 real grep matches under the whole project
  is exactly as vacuous as 0 files scanned or 0 tests collected — nothing to inventory
  means a clean claimed list confirms nothing.
- `runtime_evidence`, `log_marker` method (task 2.4) — **yes**: no ledger at all, or a
  ledger with zero events carrying a payload, means nothing was ever observed to search
  — rendered vacuous, not treated as "marker absent" (which is a real negative result
  from a real, non-empty search — see below).

**Founder review finding, fixed this session: the `tests_pass`
non-pytest fallback used to render `VERIFIED`/`RUNTIME_VERIFIED` on a bare exit code
0 — the exact D1 lie this checker exists to catch, produced by the checker itself. A
command like `npm test` can exit 0 having run zero tests (common in default Jest
configs with no test files matched). An exit code alone does not confirm "tests
actually ran"; for `tests_pass` specifically, that IS the question. The caveat used
to live only in the `reason` string — prose a Ship Report screenshot doesn't
re-render as a verdict. It is now `Verdict.UNVERIFIED` with `observed=False`: the
check ran and could not establish the claim either way. **Not `UNVERIFIED_VACUOUS`,
a deliberate choice**: that verdict is reserved for a *positively confirmed* empty
observation (zero tests collected IS itself a real, counted signal); a bare exit
code from an unrecognized runner confirms nothing about whether tests ran at all,
which is `UNVERIFIED`'s own definition ("no evidence either way") more precisely
than vacuous's "ran and confirmed nothing was there."

**Second founder finding, fixed this session: neither `subprocess.run` call in this
module had a timeout.** A hung test command or build step would block forever —
two of this project's own standing rules — "nothing synchronous in any request path"
and "an infinite enforcement loop is a worse failure than a lie" — violated simultaneously the moment
task 2.6/2.8 wire a checker call into the `Stop` hook's synchronous path. Both
`subprocess.run` calls now pass `timeout=`; a timeout renders `Verdict.UNVERIFIED`
with `observed=False` and a reason naming the timeout — the same verdict as the
non-pytest-fallback fix above, and for the identical reason: a process killed before
it reports anything is "no evidence either way," not a confirmed-empty observation.
`gate_policy` is a **frozen Gate-A block** — no field was added there; the bound is a
plain module constant (`DEFAULT_CHECKER_TIMEOUT_SECONDS`), overridable per call for
callers (and tests) that need a different one, exactly the way `reporters.
pytest_reporter`'s own timeout is handled.

**Third founder finding, fixed this session — the timeout fix above was incomplete.**
`subprocess.run(command, shell=True, timeout=...)` does not reliably bound execution
time on Windows: `shell=True`'s immediate child is `cmd.exe`; killing `cmd.exe` on
timeout does not kill a grandchild `cmd.exe` spawned (Windows has no POSIX-style
process-group signal by default), and `subprocess.run`'s own cleanup path blocks
draining stdout/stderr pipes the still-running grandchild holds open — so the call
doesn't actually return until the grandchild eventually exits on its own. Caught by
this session's own timeout tests running far longer than their stated timeout (a
30-second sleep, given `timeout=1`, took the full 30 seconds — the exact "isolated
test passed, realistic test would have failed" gap the founder has now named three
times running). Fixed with the standard, dependency-free tree-kill pattern
(`_run_shell_command_bounded`, below): the child starts in its own process group
(POSIX `start_new_session`, Windows `CREATE_NEW_PROCESS_GROUP`), and a timeout kills
the **whole tree** — `os.killpg` on POSIX, the OS-provided `taskkill /T /F` on Windows
(no new dependency; both ship with their respective OS). Both `shell=True` call sites
(`check_tests_pass`'s non-pytest fallback, `check_command_succeeds`) now go through
this helper instead of a bare `subprocess.run`.

**Fourth founder finding, fixed this session: the 600-second hook-timeout figure the
timeout constant above was sized against (see "Second founder finding") was never
verified against a source — it was an assumed number doing load-bearing work, exactly
the class of claim this project's own accuracy rules exist to catch, and the founder caught it on our own
code rather than trusting the comment. Verified against the official Claude Code hooks
reference (https://code.claude.com/docs/en/hooks.md): a `command` hook with no
`timeout` field defaults to 600 seconds, configurable per-hook via an integer `timeout`
field. The number itself turned out correct, but "assumed and happened to be right" is
not the same claim as "verified" — see `reporters.pytest_reporter`'s docstring for the
full source quote and the headroom math this constant is now sized against (60s per
subprocess call, not 300s: a single `tests_pass` pytest check makes two calls, so its
worst case is 120s — about a fifth of the verified 600s hook budget, not effectively
the whole thing).

Zero Claude-Code-specific imports — core-purity contract, `pyproject.toml`. This module
depends on `reporters.pytest_reporter` (a sibling top-level package, also
harness-agnostic) and `shipgate.verdicts` only.
"""

from __future__ import annotations

import os
import re
import shlex
import signal
import sqlite3
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from reporters.pytest_reporter import DEFAULT_PYTEST_TIMEOUT_SECONDS, run_pytest
from shipgate.verdicts import EvidenceTier, Verdict

#: Deliberately the *same* constant as `reporters.pytest_reporter`'s, not just the same
#: value — see that module's docstring for the verified 600s hook-timeout source and
#: the headroom math this number is sized against. Kept as one definition, imported
#: here rather than re-declared, so the two can never silently drift apart the way the
#: 600s figure itself was silently unverified before this session.
DEFAULT_CHECKER_TIMEOUT_SECONDS = DEFAULT_PYTEST_TIMEOUT_SECONDS

def _kill_process_tree(proc: subprocess.Popen) -> None:
    """Kills `proc` and everything it spawned, not just `proc` itself. See the module
    docstring's "Third founder finding" for why a bare `proc.kill()` isn't enough when
    `proc` is a shell (`cmd.exe` / `/bin/sh`) wrapping the real command."""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
        )
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass  # already gone
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass  # best-effort reap; the tree is dead either way, just not yet reaped


def _run_shell_command_bounded(command: str, *, cwd: Path, timeout: float) -> subprocess.CompletedProcess:
    """`subprocess.run(command, shell=True, timeout=...)`, but with a timeout that
    actually bounds wall-clock time regardless of platform — see the module docstring.
    Starts the child in its own process group/job so a timeout can kill the whole tree,
    not just the shell wrapping the real command.

    **Fifth founder finding, fixed this session — `text=True`
    with no `encoding=` decodes the child's stdout/stderr using the HOST machine's locale
    (`locale.getpreferredencoding()`), `errors="strict"` by default.** On Windows,
    `communicate()` reads each pipe on a background thread (`Lib/subprocess.py::
    _readerthread`) that does not catch exceptions — a `UnicodeDecodeError` there kills
    the thread silently, `communicate()` returns as if nothing happened, and the affected
    stream comes back `None` instead of a string. A project whose own test output (or a
    conftest's collection-time print) contains a byte sequence the *host's* codepage can't
    decode — nothing to do with what the project intended to print, or in what encoding —
    silently destroys that stream's capture. Fixed by decoding explicitly as UTF-8 (what a
    project's own tooling overwhelmingly actually emits today, the same choice
    `shipgate.cli`'s Finding-4 fix made) with `errors="replace"`, never `"strict"` (would
    still raise, just deterministically instead of only on a mismatched host locale) and
    never `"ignore"` (would silently discard the evidence that something was
    undecodable — an inserted U+FFFD is honest, dropped bytes are not). See
    `shipgate.gate.orchestrator.evaluate_gate`'s docstring for the second, structural half
    of this fix — decoding safely narrows the trigger; it doesn't by itself guarantee a
    checker can never silently escape to `Stop`'s fail-open catch for some other reason."""
    popen_kwargs: dict[str, Any] = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(
        command,
        shell=True,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        **popen_kwargs,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc)
        # The tree is now actually dead, so draining the pipes should return promptly —
        # unlike the bare subprocess.run path this replaces, which could block here
        # for as long as the (still-alive) grandchild kept the pipes open.
        stdout, stderr = proc.communicate(timeout=5)
        raise subprocess.TimeoutExpired(command, timeout, output=stdout, stderr=stderr) from None
    return subprocess.CompletedProcess(command, proc.returncode, stdout, stderr)


#: Directory names never descended into while scanning for `forbidden_pattern_absent`,
#: `inventory_complete`, and (task 2.7) `shipgate.gate.orchestrator`'s project-wide
#: content fingerprint — tool/VCS noise that is never itself the target of a shipfile's
#: forbidden-pattern check, and scanning it would make every project's own dependency
#: tree part of every grep (slow, and a false-positive risk against generated/vendored
#: code). **Public** (not `_`-prefixed): shared across modules, not private to this one.
IGNORED_DIR_NAMES = frozenset(
    {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache",
     ".import_linter_cache", ".shipgate", ".mypy_cache"}
)


@dataclass(frozen=True)
class CheckResult:
    """One checker's answer for one `done_conditions` entry. Does not write to the
    ledger — that's the caller's job (a hook, or a future gate-orchestrator module);
    this stays a pure function of (condition, project state) so it's testable without a
    database in every test."""

    condition_id: str
    verdict: Verdict
    evidence_tier: EvidenceTier | None
    reason: str
    observed: bool


# --- tests_pass -----------------------------------------------------------------------


def _pytest_extra_args(command: str) -> list[str] | None:
    """Returns the argv tail to pass to `reporters.pytest_reporter.run_pytest` if
    `command` is recognized as a pytest invocation (`pytest ...`, `py.test ...`,
    `python -m pytest ...`), or `None` if it isn't — the signal `check_tests_pass` uses
    to decide whether real collected-count machinery is available for this command."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if not tokens:
        return None

    first = Path(tokens[0]).name
    if first in ("pytest", "pytest.exe", "py.test"):
        return tokens[1:]
    if first in ("python", "python3", "python.exe") and tokens[1:3] == ["-m", "pytest"]:
        return tokens[3:]
    return None


def check_tests_pass(
    condition: dict[str, Any], project_root: Path, timeout: float = DEFAULT_CHECKER_TIMEOUT_SECONDS
) -> CheckResult:
    condition_id = condition["id"]
    command = condition["command"]
    pytest_args = _pytest_extra_args(command)

    if pytest_args is not None:
        try:
            result = run_pytest(project_root, pytest_args=pytest_args, timeout=timeout)
        except subprocess.TimeoutExpired:
            return CheckResult(
                condition_id,
                Verdict.UNVERIFIED,
                None,
                reason=(
                    f"pytest did not finish within {timeout}s under {project_root} "
                    f"(command: {command!r}) — killed before it reported anything; "
                    "no evidence either way, not a pass"
                ),
                observed=False,
            )
        if not result.ran_any_tests:
            return CheckResult(
                condition_id,
                Verdict.UNVERIFIED_VACUOUS,
                None,
                reason=(
                    f"pytest collected 0 tests under {project_root} (command: {command!r}) — "
                    "a clean run here confirms nothing was checked, not that everything passed"
                ),
                observed=False,
            )
        if result.all_passed:
            return CheckResult(
                condition_id,
                Verdict.VERIFIED,
                EvidenceTier.RUNTIME_VERIFIED,
                reason=f"{result.passed} passed, {result.collected} collected (command: {command!r})",
                observed=True,
            )
        return CheckResult(
            condition_id,
            Verdict.CONTRADICTED,
            None,
            reason=(
                f"{result.failed} failed, {result.errors} errored, of {result.collected} "
                f"collected (command: {command!r})"
            ),
            observed=True,
        )

    # Non-pytest test command: no collected-count reporter exists for it yet (stated
    # gap, see module docstring) — falls back to a bare exit-code check.
    try:
        proc = _run_shell_command_bounded(command, cwd=project_root, timeout=timeout)
    except subprocess.TimeoutExpired:
        return CheckResult(
            condition_id,
            Verdict.UNVERIFIED,
            None,
            reason=(
                f"command did not finish within {timeout}s (command: {command!r}) — "
                "killed before it reported anything; no evidence either way, not a pass"
            ),
            observed=False,
        )
    if proc.returncode == 0:
        # Founder review finding, fixed: an exit code alone does not confirm "tests
        # actually ran" for a runner this module doesn't have collected-count
        # machinery for — UNVERIFIED, not VERIFIED. See module docstring.
        return CheckResult(
            condition_id,
            Verdict.UNVERIFIED,
            None,
            reason=(
                f"command exited 0 (command: {command!r}), but this checker has no "
                "collected-count machinery for a non-pytest runner and cannot confirm "
                "any test actually ran — not a pass. See reporters/pytest_reporter.py"
            ),
            observed=False,
        )
    return CheckResult(
        condition_id,
        Verdict.CONTRADICTED,
        None,
        reason=f"command exited {proc.returncode} (command: {command!r})",
        observed=True,
    )


# --- file_exists ------------------------------------------------------------------------


def check_file_exists(
    condition: dict[str, Any], project_root: Path, timeout: float = DEFAULT_CHECKER_TIMEOUT_SECONDS
) -> CheckResult:
    """`timeout` is accepted but unused — no subprocess runs here. Kept in the
    signature so every entry in `CHECKERS` is callable identically by `run_checker`,
    rather than special-casing which checkers take a timeout and which don't."""
    del timeout
    condition_id = condition["id"]
    rel_path = condition["path"]
    full_path = project_root / rel_path
    if full_path.exists():
        return CheckResult(
            condition_id, Verdict.VERIFIED, EvidenceTier.DISK_VERIFIED,
            reason=f"{rel_path} exists", observed=True,
        )
    return CheckResult(
        condition_id, Verdict.CONTRADICTED, None,
        reason=f"{rel_path} does not exist under {project_root}", observed=True,
    )


# --- forbidden_pattern_absent -----------------------------------------------------------


def iter_scanned_files(project_root: Path, paths: list[str]):
    """Public (task 2.7): the same file walk `forbidden_pattern_absent` and
    `inventory_complete` already used privately, now shared with
    `shipgate.gate.orchestrator`'s content-fingerprint computation so there is one
    definition of "which files this project's scan-based checks look at," not two that
    could silently drift apart."""
    for rel in paths:
        target = project_root / rel
        if target.is_file():
            yield target
        elif target.is_dir():
            for candidate in target.rglob("*"):
                if candidate.is_file() and not (IGNORED_DIR_NAMES & set(candidate.relative_to(project_root).parts)):
                    yield candidate
        # a path that doesn't exist yields nothing -- feeds the vacuous count honestly,
        # rather than being treated as an error or silently skipped


def check_forbidden_pattern_absent(
    condition: dict[str, Any], project_root: Path, timeout: float = DEFAULT_CHECKER_TIMEOUT_SECONDS
) -> CheckResult:
    """`timeout` is accepted but unused — a filesystem walk + regex scan has no
    subprocess to bound. Kept in the signature for the same reason as
    `check_file_exists`."""
    del timeout
    condition_id = condition["id"]
    pattern = condition["pattern"]
    paths = condition.get("paths") or ["."]
    regex = re.compile(pattern)

    scanned = 0
    matches: list[str] = []
    for file_path in iter_scanned_files(project_root, paths):
        scanned += 1
        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if regex.search(text):
            # .as_posix(), not str(): same cross-platform reasoning as
            # check_inventory_complete's match strings, below — a Ship Report
            # shouldn't render Windows-only backslashes in a path.
            matches.append(file_path.relative_to(project_root).as_posix())

    if scanned == 0:
        return CheckResult(
            condition_id,
            Verdict.UNVERIFIED_VACUOUS,
            None,
            reason=f"0 files scanned under {paths} — nothing was searched, so a clean result proves nothing",
            observed=False,
        )
    if matches:
        shown = matches[:5]
        suffix = f" (+{len(matches) - 5} more)" if len(matches) > 5 else ""
        return CheckResult(
            condition_id,
            Verdict.CONTRADICTED,
            None,
            reason=f"pattern {pattern!r} found in {len(matches)} file(s): {shown}{suffix}",
            observed=True,
        )
    return CheckResult(
        condition_id,
        Verdict.VERIFIED,
        EvidenceTier.DISK_VERIFIED,
        reason=f"pattern {pattern!r} absent across {scanned} scanned file(s) under {paths}",
        observed=True,
    )


# --- inventory_complete (task 2.3) ------------------------------------------------------
#
# report §2.2 (D3): "'Documented' ≠ 'exhaustively enumerated' — four worst incidents were
# fixing 1 call site when a grep would find 5." The shipfile gives a grep pattern and the
# allowed dispositions; the AGENT is the one who is supposed to produce the enumerated
# list with a disposition per hit. That enumeration isn't part of the shipfile (the
# shipfile is written once, before any specific incident) and doesn't exist anywhere else
# this checker has access to yet — no claims-extraction pipeline is wired up this session
# — so `claimed_items` is an explicit parameter here, not something this function derives
# on its own. This is why `check_inventory_complete` is deliberately NOT in `CHECKERS`/
# `run_checker`'s uniform dispatch: its signature is architecturally different from the
# other four, not just unimplemented.


def check_inventory_complete(
    condition: dict[str, Any],
    project_root: Path,
    claimed_items: list[dict[str, str]],
    timeout: float = DEFAULT_CHECKER_TIMEOUT_SECONDS,
) -> CheckResult:
    """`claimed_items`: the agent's own enumeration, e.g.
    `[{"match": "shipgate/foo.py:12", "disposition": "updated"}, ...]` — each item names
    the specific real match it accounts for (so a real grep hit can be reconciled against
    a specific claimed item, not just compared by count) and a disposition drawn from
    `condition["required_dispositions"]`.

    Fully deterministic (report §2.2 D3): grep really runs, the real match set is
    compared against the claimed set exactly, and any of three failure shapes blocks —
    a real match missing from the claim, a claimed item no real grep found (fabricated
    or stale), or a claimed item with a disposition outside the allowed list."""
    del timeout  # no subprocess here — a plain filesystem walk + regex, like forbidden_pattern_absent
    condition_id = condition["id"]
    pattern = condition["grep_pattern"]
    required_dispositions = set(condition["required_dispositions"])
    regex = re.compile(pattern)

    real_matches: list[str] = []
    for file_path in iter_scanned_files(project_root, ["."]):
        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        # .as_posix(), not str(rel): a bare WindowsPath str() uses backslashes, which
        # would make a match string built on Windows silently unmatchable against a
        # claimed_items list built (or tested) with forward slashes — the same
        # cross-platform trap shipgate/ledger/paths.py already guards against for
        # source_dir, for the identical reason.
        rel = file_path.relative_to(project_root).as_posix()
        for lineno, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                real_matches.append(f"{rel}:{lineno}")

    if not real_matches:
        return CheckResult(
            condition_id,
            Verdict.UNVERIFIED_VACUOUS,
            None,
            reason=(
                f"grep pattern {pattern!r} matched 0 lines under {project_root} — there is "
                "nothing to inventory, so a clean result here confirms nothing was searched"
            ),
            observed=False,
        )

    real_match_set = set(real_matches)
    claimed_match_set = {item["match"] for item in claimed_items}

    missing = sorted(real_match_set - claimed_match_set)
    extra = sorted(claimed_match_set - real_match_set)
    bad_dispositions = [
        item
        for item in claimed_items
        if item["match"] in real_match_set and item.get("disposition") not in required_dispositions
    ]

    if missing or extra or bad_dispositions:
        parts = []
        if missing:
            parts.append(f"{len(missing)} real match(es) not in the claimed inventory: {missing[:5]}")
        if extra:
            parts.append(f"{len(extra)} claimed item(s) no real grep found: {extra[:5]}")
        if bad_dispositions:
            shown = [f"{i['match']} -> {i.get('disposition')!r}" for i in bad_dispositions[:5]]
            parts.append(f"{len(bad_dispositions)} claimed item(s) with a disallowed disposition: {shown}")
        return CheckResult(condition_id, Verdict.CONTRADICTED, None, reason="; ".join(parts), observed=True)

    return CheckResult(
        condition_id,
        Verdict.VERIFIED,
        EvidenceTier.DISK_VERIFIED,
        reason=f"{len(real_matches)} real match(es) for {pattern!r}, all claimed with an allowed disposition",
        observed=True,
    )


# --- runtime_evidence, log-marker variant (task 2.4) ------------------------------------
#
# report §2.2 (D1): "'Complete on disk' ≠ 'verified in runtime' — agent ships code, tests
# pass, agent says 'fix is active' — but the running daemon never reloaded it." Grepping
# SOURCE CODE for a marker string would only prove the marker exists in the file, exactly
# the disk-only claim D1 warns against. This checker instead searches this project's own
# ledger (`<project_root>/.shipgate/ledger.db`) — real events a hook actually recorded
# during a real session — for the marker appearing in a REAL, OBSERVED payload. Only the
# `log_marker` method is built this session (task 2.4's stated scope); `endpoint_probe`
# and `db_query` remain schema-only and raise a specific error, not a silent pass.


def check_runtime_evidence(
    condition: dict[str, Any], project_root: Path, timeout: float = DEFAULT_CHECKER_TIMEOUT_SECONDS
) -> CheckResult:
    del timeout  # no subprocess here — a local sqlite read
    condition_id = condition["id"]
    method = condition["method"]
    detail = condition["detail"]

    if method != "log_marker":
        raise ValueError(
            f"runtime_evidence method {method!r} has no checker implemented yet "
            f"(condition id: {condition_id!r}) — only 'log_marker' is built (task 2.4); "
            "'endpoint_probe' and 'db_query' are schema-only so far"
        )

    db_path = project_root / ".shipgate" / "ledger.db"
    if not db_path.exists():
        return CheckResult(
            condition_id,
            Verdict.UNVERIFIED_VACUOUS,
            None,
            reason=f"no ledger at {db_path} — no hook has ever run in this project; nothing to search",
            observed=False,
        )

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT raw_payload_redacted FROM events WHERE raw_payload_redacted IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return CheckResult(
            condition_id,
            Verdict.UNVERIFIED_VACUOUS,
            None,
            reason=f"ledger at {db_path} exists but has 0 events with a payload — nothing was ever observed",
            observed=False,
        )

    matches = sum(1 for (payload,) in rows if payload and detail in payload)
    if matches > 0:
        return CheckResult(
            condition_id,
            Verdict.VERIFIED,
            EvidenceTier.RUNTIME_VERIFIED,
            reason=f"marker {detail!r} found in {matches} real recorded event(s) in {db_path}",
            observed=True,
        )

    # A real, non-empty search of real recorded events found zero matches -- a
    # confirmed negative, not an absence of evidence. Source code can contain this
    # exact marker string and this still renders CONTRADICTED if it never actually
    # ran (the D1 lie) -- see test_runtime_evidence_marker_in_source_but_never_run_...
    return CheckResult(
        condition_id,
        Verdict.CONTRADICTED,
        None,
        reason=(
            f"marker {detail!r} not found in any of {len(rows)} recorded event(s) in {db_path} — "
            "code may exist on disk but was never observed running"
        ),
        observed=True,
    )


# --- command_succeeds ---------------------------------------------------------------------


def check_command_succeeds(
    condition: dict[str, Any], project_root: Path, timeout: float = DEFAULT_CHECKER_TIMEOUT_SECONDS
) -> CheckResult:
    """`command` runs via `shell=True` — the shipfile is trusted project-owner input,
    the same trust class as a Makefile target or a CI workflow's `run:` line, not
    untrusted remote input; a shell-syntax command (pipes, `&&`) is the whole point of
    letting an author write an arbitrary command here."""
    condition_id = condition["id"]
    command = condition["command"]
    try:
        proc = _run_shell_command_bounded(command, cwd=project_root, timeout=timeout)
    except subprocess.TimeoutExpired:
        return CheckResult(
            condition_id,
            Verdict.UNVERIFIED,
            None,
            reason=(
                f"command did not finish within {timeout}s (command: {command!r}) — "
                "killed before it reported anything; no evidence either way, not a pass"
            ),
            observed=False,
        )
    if proc.returncode == 0:
        return CheckResult(
            condition_id, Verdict.VERIFIED, EvidenceTier.RUNTIME_VERIFIED,
            reason=f"command exited 0 (command: {command!r})", observed=True,
        )
    return CheckResult(
        condition_id, Verdict.CONTRADICTED, None,
        reason=f"command exited {proc.returncode} (command: {command!r}): {proc.stderr[:300]}",
        observed=True,
    )


# --- dispatch ---------------------------------------------------------------------------

#: `inventory_complete` is deliberately excluded — its signature takes an extra
#: `claimed_items` argument the other five don't need (see that function's docstring),
#: so it can't be dispatched uniformly through `run_checker`. Call it directly.
CHECKERS: dict[str, Callable[[dict[str, Any], Path, float], CheckResult]] = {
    "tests_pass": check_tests_pass,
    "file_exists": check_file_exists,
    "forbidden_pattern_absent": check_forbidden_pattern_absent,
    "command_succeeds": check_command_succeeds,
    "runtime_evidence": check_runtime_evidence,
}


def run_checker(
    condition: dict[str, Any], project_root: Path, timeout: float = DEFAULT_CHECKER_TIMEOUT_SECONDS
) -> CheckResult:
    """Dispatches on `condition["type"]`. Raises `ValueError` for a condition type with
    no checker implemented yet (`emission_traced` — no task assigned yet — and the `ears`
    representation, which is never dispatched to a checker at all), and for
    `inventory_complete`, which is implemented but not dispatchable here — see
    `check_inventory_complete`'s docstring and call it directly."""
    cond_type = condition.get("type")
    if cond_type == "inventory_complete":
        raise ValueError(
            "inventory_complete is implemented but cannot be dispatched through "
            "run_checker — it needs a claimed_items argument the other condition types "
            "don't take. Call shipgate.gate.checkers.check_inventory_complete directly."
        )
    checker = CHECKERS.get(cond_type)
    if checker is None:
        raise ValueError(
            f"no deterministic checker implemented yet for condition type {cond_type!r} "
            f"(condition id: {condition.get('id')!r}) — implemented so far: "
            f"{sorted({*CHECKERS, 'inventory_complete'})}"
        )
    return checker(condition, project_root, timeout)


__all__ = [
    "CHECKERS",
    "DEFAULT_CHECKER_TIMEOUT_SECONDS",
    "IGNORED_DIR_NAMES",
    "CheckResult",
    "check_command_succeeds",
    "check_file_exists",
    "check_forbidden_pattern_absent",
    "check_inventory_complete",
    "check_runtime_evidence",
    "check_tests_pass",
    "iter_scanned_files",
    "run_checker",
]

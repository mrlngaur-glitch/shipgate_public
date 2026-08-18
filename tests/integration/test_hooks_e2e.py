"""Task 2.1's actual done-condition, end to end: "hooks fire on a real session and
write ledger rows; no network call from any hook." Every test here either shells out to
the real `python -m shipgate.hooks.X` entrypoint (exactly how `settings.json` invokes
it) or proves the no-network / corpus-untouched guarantees at the level they actually
matter — not mocked, not assumed.
"""

import ast
import json
import os
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from shipgate.ledger.integrity import verify_all_chains

REPO_ROOT = Path(__file__).resolve().parents[2]
_HOOK_MODULES = ["shipgate.hooks.pretooluse", "shipgate.hooks.posttooluse", "shipgate.hooks.stop"]

#: Any of these appearing as an import anywhere under shipgate/hooks/ would make a
#: network call at least *possible* from a hook -- structurally forbidden, not just
#: avoided by convention.
_NETWORK_CAPABLE_MODULES = {
    "socket",
    "http",
    "http.client",
    "urllib",
    "urllib.request",
    "requests",
    "httpx",
    "ftplib",
    "smtplib",
    "ssl",
    "asyncio",
}


def _run_hook(module: str, payload: dict, cwd: Path) -> subprocess.CompletedProcess:
    # encoding="utf-8" explicit: `text=True` alone decodes captured output with this
    # parent process's own locale encoding, which can mojibake a child's correctly
    # UTF-8-encoded stderr on a non-UTF-8-locale machine (Session 011). Explicit
    # encoding keeps these assertions independent of the machine running them.
    return subprocess.run(
        [sys.executable, "-m", module],
        input=json.dumps(payload),
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def _base_payload(cwd: Path, **overrides) -> dict:
    base = {
        "session_id": "e2e-sess-1",
        "cwd": str(cwd),
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "pytest -q"},
        "tool_use_id": "tu-e2e-1",
    }
    base.update(overrides)
    return base


# --- structural: no hooks module can even import something network-capable -------------


def test_no_hook_module_imports_anything_network_capable():
    hooks_dir = REPO_ROOT / "shipgate" / "hooks"
    py_files = list(hooks_dir.glob("*.py"))
    assert len(py_files) >= 4  # __init__, _common, pretooluse, posttooluse, stop

    offending = []
    for path in py_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                if name.split(".")[0] in _NETWORK_CAPABLE_MODULES or name in _NETWORK_CAPABLE_MODULES:
                    offending.append((path.name, name))

    assert offending == [], f"network-capable import(s) found in shipgate/hooks/: {offending}"


def test_no_hook_module_imports_the_dogfood_corpus_path_module():
    """A hook writing to `.shipgate/` under the project cwd doesn't need -- and this
    package must never gain -- any *import* of the corpus-root module `ledger.paths`
    defines for JSONL ingest. Checked by parsing actual import statements (AST), not a
    raw substring search of the file text -- a substring check would also trip on this
    very module's own docstring, which *names* `DEFAULT_CORPUS_ROOT` in prose precisely
    to explain that it is never imported. The claim under test is "never imported,"
    so that's what gets checked, not "never mentioned.\""""
    hooks_dir = REPO_ROOT / "shipgate" / "hooks"
    offending = []
    for path in hooks_dir.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and "ledger.paths" in node.module:
                offending.append((path.name, node.module, [a.name for a in node.names]))
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if "ledger.paths" in alias.name:
                        offending.append((path.name, alias.name, None))
    assert offending == [], f"a hook module imports the corpus-root path module: {offending}"


# --- runtime: a real subprocess run cannot actually open a socket ----------------------


def test_hooks_cannot_open_a_socket_at_runtime(tmp_path, monkeypatch):
    """Belt-and-suspenders on top of the structural check above: even if some future
    edit added a network-capable import, this proves the actual runtime path taken by
    each entrypoint never calls connect(). Patches socket.socket.connect to raise, runs
    all three hooks for real, and requires them to still succeed -- which they can only
    do if connect() was never called."""

    def _raise(*_args, **_kwargs):
        raise AssertionError("a hook attempted to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", _raise)

    from shipgate.hooks import posttooluse, pretooluse, stop

    assert pretooluse.run(json.dumps(_base_payload(tmp_path))) == 0
    assert posttooluse.run(json.dumps(_base_payload(tmp_path, hook_event_name="PostToolUse"))) == 0
    assert stop.run(json.dumps({"session_id": "e2e-sess-1", "cwd": str(tmp_path), "stop_hook_active": False})) == 0

    assert (tmp_path / ".shipgate" / "ledger.db").exists()


# --- runtime: the dogfood corpus is never touched, even if HOME points at a fake one ----


def test_dogfood_corpus_directory_is_never_created_by_a_hook_run(tmp_path, monkeypatch):
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    project_dir = tmp_path / "real-project"
    project_dir.mkdir()

    from shipgate.hooks import pretooluse

    pretooluse.run(json.dumps(_base_payload(project_dir)))

    assert not (fake_home / ".claude").exists()
    assert (project_dir / ".shipgate" / "ledger.db").exists()


# --- real subprocess, real settings.json-style invocation, a realistic combined session -


def test_a_realistic_combined_session_via_real_subprocesses(tmp_path):
    """Not one hook in isolation -- a real sequence: two tool calls (pre+post each),
    then Stop, each invoked as an actual subprocess reading real stdin, exactly as
    settings.json would call it. Verifies the chain across process boundaries, not just
    within one Python process's memory."""
    cwd = tmp_path

    for i, command in enumerate(["python -m pytest", "ruff check ."]):
        pre = _run_hook(
            "shipgate.hooks.pretooluse",
            _base_payload(cwd, tool_use_id=f"tu-{i}", tool_input={"command": command}),
            cwd,
        )
        assert pre.returncode == 0, pre.stderr

        post = _run_hook(
            "shipgate.hooks.posttooluse",
            _base_payload(
                cwd,
                hook_event_name="PostToolUse",
                tool_use_id=f"tu-{i}",
                tool_input={"command": command},
                tool_result={"exit_code": 0, "stdout": "ok", "stderr": ""},
            ),
            cwd,
        )
        assert post.returncode == 0, post.stderr

    stop_result = _run_hook(
        "shipgate.hooks.stop",
        {"session_id": "e2e-sess-1", "cwd": str(cwd), "stop_hook_active": False},
        cwd,
    )
    assert stop_result.returncode == 0, stop_result.stderr

    db_path = cwd / ".shipgate" / "ledger.db"
    assert db_path.exists()
    conn = sqlite3.connect(db_path)
    verify_all_chains(conn)

    record_types = [r[0] for r in conn.execute("SELECT record_type FROM events ORDER BY event_id")]
    assert record_types == [
        "pretooluse_hook",
        "posttooluse_hook",
        "pretooluse_hook",
        "posttooluse_hook",
        "stop_hook",
    ]
    sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    assert sessions == 1  # one session bootstrapped once, reused by every later hook call


# --- task 2.6, real subprocess: Stop actually gates, Gate B conditions 1 and 4 ----------


def _real_shipfile(cwd: Path, command: str, max_retries: int = 3) -> None:
    _real_shipfile_conditions(
        cwd, [{"id": "e2e-condition", "type": "command_succeeds", "command": command}], max_retries=max_retries
    )


def _real_shipfile_conditions(cwd: Path, done_conditions: list[dict], max_retries: int = 3) -> None:
    """General form: any list of done_conditions dicts, serialized with the real YAML
    library rather than hand-built strings -- robust for condition types with nested
    fields (inventory_complete's grep_pattern/required_dispositions, etc.)."""
    shipfile = {
        "task_classes": {
            "feature": {
                "risk_tier": "medium",
                "starting_model_tier": "mid",
                "max_tokens": 100000,
                "gate_strictness": "strict",
            }
        },
        "done_conditions": done_conditions,
        "routing": {},
        "budgets": {},
        "context_policy": {},
        "intent": {"summary": "e2e test shipfile"},
        "gate_policy": {"max_retries": max_retries},
        "session_policy": {},
    }
    (cwd / "shipfile.yaml").write_text(yaml.safe_dump(shipfile, sort_keys=False), encoding="utf-8")


def test_real_stop_subprocess_blocks_a_real_false_completion_gate_b_condition_1(tmp_path):
    """Gate B condition #1, runtime-verified end to end: a real `python -m
    shipgate.hooks.stop` subprocess, invoked exactly as settings.json would, actually
    refuses a stop when the shipfile's own done_conditions aren't met."""
    cwd = tmp_path
    _real_shipfile(cwd, f'"{sys.executable}" -c "import sys; sys.exit(1)"')

    result = _run_hook("shipgate.hooks.stop", {"session_id": "e2e-gate-1", "cwd": str(cwd)}, cwd)
    assert result.returncode == 0  # hooks always exit 0 -- the refusal is in the JSON
    decision = json.loads(result.stdout)
    assert decision["decision"] == "block"
    assert "e2e-condition" in decision["reason"]


def test_real_stop_subprocess_allows_a_real_true_completion(tmp_path):
    cwd = tmp_path
    _real_shipfile(cwd, f'"{sys.executable}" -c "import sys; sys.exit(0)"')

    result = _run_hook("shipgate.hooks.stop", {"session_id": "e2e-gate-2", "cwd": str(cwd)}, cwd)
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_real_stop_subprocess_retry_cap_converges_gate_b_condition_4(tmp_path):
    """Gate B condition #4, runtime-verified end to end: four real, separate `python -m
    shipgate.hooks.stop` subprocess invocations (a persistently-red project, default
    max_retries=3) converge to an honest-red release -- blocked, blocked, blocked,
    released -- with no IllegalTransitionError anywhere across process boundaries."""
    cwd = tmp_path
    _real_shipfile(cwd, f'"{sys.executable}" -c "import sys; sys.exit(1)"')

    blocked = []
    for _ in range(4):
        result = _run_hook("shipgate.hooks.stop", {"session_id": "e2e-gate-3", "cwd": str(cwd)}, cwd)
        assert result.returncode == 0, result.stderr
        blocked.append(bool(result.stdout.strip()) and json.loads(result.stdout)["decision"] == "block")

    assert blocked == [True, True, True, False]

    conn = sqlite3.connect(cwd / ".shipgate" / "ledger.db")
    verify_all_chains(conn)  # the hash chain survives four separate real hook processes
    exhausted_flags = [
        json.loads(r[0])["exhausted"]
        for r in conn.execute(
            "SELECT raw_payload_redacted FROM events WHERE record_type='gate_evaluation' ORDER BY event_id"
        )
    ]
    assert exhausted_flags == [False, False, False, True]


def test_real_stop_subprocess_zero_test_pass_renders_vacuous_gate_b_condition_2(tmp_path):
    """Gate B condition #2, runtime-verified end to end through the real Stop hook
    subprocess: a tests_pass condition pointed at a directory with no tests collects
    zero and renders unverified-vacuous -- never green -- and the gate blocks on it,
    exactly as it would on a real failure."""
    cwd = tmp_path
    (cwd / "empty_tests").mkdir()
    _real_shipfile_conditions(
        cwd,
        [{"id": "no-tests-here", "type": "tests_pass", "command": f'"{sys.executable}" -m pytest empty_tests -q'}],
    )

    result = _run_hook("shipgate.hooks.stop", {"session_id": "e2e-gate-2b", "cwd": str(cwd)}, cwd)
    assert result.returncode == 0, result.stderr
    decision = json.loads(result.stdout)
    assert decision["decision"] == "block"
    assert "no-tests-here" in decision["reason"]

    conn = sqlite3.connect(cwd / ".shipgate" / "ledger.db")
    verdict = conn.execute(
        "SELECT verdict FROM verdicts WHERE claim_id='e2e-gate-2b:no-tests-here'"
    ).fetchone()
    assert verdict == ("unverified-vacuous",)


def test_real_stop_subprocess_finding5_zero_tests_plus_undecodable_conftest_output_still_blocks(tmp_path):
    """Finding 5 (a launch blocker), through the real `Stop`-hook
    subprocess -- the founder's own exact A/B/C control, control C, at the most
    externally faithful layer this project has: a genuine `python -m
    shipgate.hooks.stop` process, real stdin, real stdout, exactly as `settings.json`
    invokes it. A zero-test project whose conftest.py writes a raw UTF-8 byte sequence
    the HOST machine's locale can't decode used to crash `evaluate_gate` with an
    uncaught `AttributeError` deep inside `collect_test_count` -- caught only by this
    module's own outer fail-open `except Exception` (the deliberate boundary for
    genuinely unanticipated errors, see this module's docstring), which released the
    stop with an EMPTY stdout: no `decision` key, silently ALLOWED, no per-condition
    ledger detail. Must now match the sibling zero-tests-no-conftest test immediately
    above, byte for byte in shape: a real `{"decision": "block", ...}`."""
    cwd = tmp_path
    (cwd / "conftest.py").write_text(
        'import sys\nsys.stdout.buffer.write("collection note: \\U0001F40D\\n".encode("utf-8"))\n'
        "sys.stdout.buffer.flush()\n",
        encoding="utf-8",
    )
    _real_shipfile_conditions(
        cwd,
        [{"id": "no-tests-plus-emoji-conftest", "type": "tests_pass", "command": f'"{sys.executable}" -m pytest -q'}],
    )

    result = _run_hook("shipgate.hooks.stop", {"session_id": "e2e-gate-2c", "cwd": str(cwd)}, cwd)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() != "", (
        "used to be EMPTY here -- the exact defect: the gate crashed internally and "
        "fail-open released the stop silently, with no decision key at all"
    )
    decision = json.loads(result.stdout)
    assert decision["decision"] == "block"
    assert "no-tests-plus-emoji-conftest" in decision["reason"]
    assert "collected 0 tests" in decision["reason"]


def test_real_stop_subprocess_incomplete_inventory_blocks_gate_b_condition_3(tmp_path):
    """Gate B condition #3, runtime-verified end to end through the real Stop hook
    subprocess and the claims sidecar bridge: agent lists 3 real matches, grep finds 5
    -> blocks. Not per-checker (task 2.3's own demonstration) -- through evaluate_gate,
    dispatched via a real .shipgate/claims/<id>.json sidecar file, exactly as a future
    claims-extraction pipeline would eventually supply it."""
    cwd = tmp_path
    for i in range(5):
        (cwd / f"mod_{i}.py").write_text("from shipgate.ledger.writer import LedgerWriter\n", encoding="utf-8")
    claims_dir = cwd / ".shipgate" / "claims"
    claims_dir.mkdir(parents=True, exist_ok=True)
    (claims_dir / "inv-e2e.json").write_text(
        json.dumps([{"match": f"mod_{i}.py:1", "disposition": "updated"} for i in range(3)]), encoding="utf-8"
    )
    _real_shipfile_conditions(
        cwd,
        [
            {
                "id": "inv-e2e",
                "type": "inventory_complete",
                "grep_pattern": "from shipgate.ledger.writer import",
                "required_dispositions": ["updated"],
            }
        ],
    )

    result = _run_hook("shipgate.hooks.stop", {"session_id": "e2e-gate-3b", "cwd": str(cwd)}, cwd)
    assert result.returncode == 0, result.stderr
    decision = json.loads(result.stdout)
    assert decision["decision"] == "block"
    assert "inv-e2e" in decision["reason"]

    conn = sqlite3.connect(cwd / ".shipgate" / "ledger.db")
    verdict = conn.execute("SELECT verdict FROM verdicts WHERE claim_id='e2e-gate-3b:inv-e2e'").fetchone()
    assert verdict == ("contradicted",)


# --- Codepage safety (investigated alongside Finding 4's confirmed CLI blocker, Session 011) --
#
# Several of this package's stderr writes (`— skipping, not blocking`) sit outside any
# try/except -- they ARE the handler for a routine condition. This was investigated as a
# candidate second instance of Finding 4's bug shape, then precisely scoped rather than
# assumed identical: a negative-control test (temporarily disabling `shipgate.hooks.
# _common.ensure_utf8_streams`) proved these writes never actually crash on cp437/cp932,
# because `sys.stderr`'s default error handler is CPython's own `'backslashreplace'` --
# independent of platform, independent of this project's own fix. `stop.py`'s one stdout
# write (`json.dumps(...)`) was never at risk either -- ASCII-escaped by construction.
# The tests below confirm this stays true (defense-in-depth, not a proven-crash regression
# guard) using the same forced-codepage mechanism as test_cli_e2e.py, for the same
# "exercise the real code path on any host OS, including this project's own ubuntu-latest
# CI runner" reason documented in that module's docstring.


def _run_hook_with_encoding(module: str, payload: dict, cwd: Path, *, python_io_encoding: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = python_io_encoding
    return subprocess.run(
        [sys.executable, "-m", module],
        input=json.dumps(payload),
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
    )


def test_pretooluse_invalid_input_survives_cp932(tmp_path: Path):
    """The un-guarded `except HookInputError` stderr write -- outside any try/except --
    was the candidate crash path investigated for Finding 4's bug shape. Confirmed safe
    (`sys.stderr`'s built-in `backslashreplace` default) by a negative-control test; this
    pins that the defensive `ensure_utf8_streams()` addition doesn't change the outcome."""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "cp932"
    result = subprocess.run(
        [sys.executable, "-m", "shipgate.hooks.pretooluse"],
        input="not json",
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "UnicodeEncodeError" not in result.stderr
    assert "skipping, not blocking" in result.stderr


def test_posttooluse_invalid_input_survives_cp932(tmp_path: Path):
    result = _run_hook_with_encoding(
        "shipgate.hooks.posttooluse", {"broken": "payload, missing session_id/cwd"}, tmp_path, python_io_encoding="cp932"
    )
    assert result.returncode == 0, result.stderr
    assert "UnicodeEncodeError" not in result.stderr


def test_stop_retry_cap_release_reason_survives_cp437(tmp_path: Path):
    """A real, always-failing condition, real retry-cap exhaustion, and
    `release_reason` (which carries an em dash) written to stderr under cp437 -- the
    Stop hook's own analogue of the CLI scenario the founder found, investigated on the
    same suspicion. Confirmed already-safe (stderr's `backslashreplace` default) by a
    negative-control test rather than assumed; this test pins that the defensive fix
    doesn't regress it."""
    _real_shipfile(tmp_path, "exit 1", max_retries=1)
    payload = {"session_id": "cp437-stop-sess", "cwd": str(tmp_path)}

    first = _run_hook_with_encoding("shipgate.hooks.stop", payload, tmp_path, python_io_encoding="cp437")
    assert first.returncode == 0, first.stderr
    assert "UnicodeEncodeError" not in first.stderr
    decision = json.loads(first.stdout)
    assert decision["decision"] == "block"

    second = _run_hook_with_encoding("shipgate.hooks.stop", payload, tmp_path, python_io_encoding="cp437")
    assert second.returncode == 0, second.stderr
    assert "UnicodeEncodeError" not in second.stderr
    assert "retry cap exhausted" in second.stderr
    assert second.stdout == ""  # exhausted -> allowed, not blocked


# --- Finding 6 / P12: the Stop hook's outer fail-open catch also swallowed
# a broken ledger -- the thing that fails is also the thing that would record it. Real,
# broken filesystem state, not mocked -- the same standard as every prior finding. -------


def _corrupt_ledger_db(cwd: Path) -> None:
    """Scenario 1 of the founder's own repro: ledger.db replaced with garbage bytes."""
    shipgate_dir = cwd / ".shipgate"
    shipgate_dir.mkdir(parents=True, exist_ok=True)
    (shipgate_dir / "ledger.db").write_bytes(b"this is not a sqlite database, just garbage bytes")


def _marker_path(cwd: Path) -> Path:
    return cwd / ".shipgate" / "gate_unavailable.json"


def test_stop_ledger_failure_first_consecutive_attempt_fails_open_but_is_durably_recorded(tmp_path: Path):
    """Finding 6's baseline, unchanged on purpose: a genuinely transient-looking failure
    still gets its one free pass -- fails open exactly as before this fix -- but is now
    durably recorded in a plain sibling marker file the broken ledger can't take down
    with it, rather than vanishing into stderr with nothing left behind."""
    _real_shipfile(tmp_path, "exit 1")
    _corrupt_ledger_db(tmp_path)

    result = _run_hook("shipgate.hooks.stop", {"session_id": "f6-sess-1", "cwd": str(tmp_path)}, tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""  # still allowed -- unchanged baseline
    assert "internal error (non-blocking)" in result.stderr

    marker = json.loads(_marker_path(tmp_path).read_text(encoding="utf-8"))
    assert marker["consecutive_failures"] == 1
    assert "file is not a database" in marker["last_error"]


def test_stop_ledger_failure_second_consecutive_attempt_blocks_as_an_infrastructure_problem(tmp_path: Path):
    """Finding 6's fix: recurring identically past one grace attempt escalates to a real
    block -- the only channel actually visible to the user/agent in the moment -- with a
    reason naming this as ShipGate's own infrastructure problem, not the project's code,
    so an agent reading it does not go looking for a bug in its own work."""
    _real_shipfile(tmp_path, "exit 1")
    _corrupt_ledger_db(tmp_path)

    _run_hook("shipgate.hooks.stop", {"session_id": "f6-sess-2", "cwd": str(tmp_path)}, tmp_path)  # 1st: fails open
    second = _run_hook("shipgate.hooks.stop", {"session_id": "f6-sess-2", "cwd": str(tmp_path)}, tmp_path)

    assert second.returncode == 0, second.stderr
    decision = json.loads(second.stdout)
    assert decision["decision"] == "block"
    assert "infrastructure problem, not your code" in decision["reason"]
    assert "2 consecutive attempts" in decision["reason"]


def test_stop_ledger_failure_counter_resets_after_a_healthy_run_between_failures(tmp_path: Path):
    """A failure that heals and then recurs later is a NEW 1st occurrence, not a
    continuation of an old, already-resolved one -- the marker is cleared the moment the
    ledger opens successfully (see stop.py's module docstring), so this must fail open
    again rather than immediately blocking as if it were consecutive attempt 2."""
    _real_shipfile(tmp_path, "exit 1")
    _corrupt_ledger_db(tmp_path)
    _run_hook("shipgate.hooks.stop", {"session_id": "f6-sess-3", "cwd": str(tmp_path)}, tmp_path)  # fails open, count=1
    assert json.loads(_marker_path(tmp_path).read_text(encoding="utf-8"))["consecutive_failures"] == 1

    (tmp_path / ".shipgate" / "ledger.db").unlink()  # heal: a fresh, healthy db gets created on next open
    healthy = _run_hook("shipgate.hooks.stop", {"session_id": "f6-sess-3", "cwd": str(tmp_path)}, tmp_path)
    assert json.loads(healthy.stdout)["decision"] == "block"  # the real gate condition, not an infra message
    assert not _marker_path(tmp_path).exists()  # cleared

    _corrupt_ledger_db(tmp_path)
    again = _run_hook("shipgate.hooks.stop", {"session_id": "f6-sess-3", "cwd": str(tmp_path)}, tmp_path)
    assert again.stdout.strip() == ""  # 1st occurrence again -- fails open, does not immediately block
    assert json.loads(_marker_path(tmp_path).read_text(encoding="utf-8"))["consecutive_failures"] == 1


def test_stop_ledger_failure_exhausts_past_the_block_cap_instead_of_looping_forever(tmp_path: Path):
    """An infinite enforcement loop is a worse failure than a lie. Blocking forever on
    an unrecoverable, persistently corrupt ledger would be exactly that loop -- this
    mechanism must give up after a small, bounded number of blocks, the same way
    `evaluate_gate`'s own retry cap does, and say so rather than silently reverting to
    Finding 6's original silent-allow behavior."""
    _real_shipfile(tmp_path, "exit 1")
    _corrupt_ledger_db(tmp_path)

    results = [
        _run_hook("shipgate.hooks.stop", {"session_id": "f6-sess-4", "cwd": str(tmp_path)}, tmp_path)
        for _ in range(7)
    ]
    decisions = []
    for r in results:
        if r.stdout.strip():
            decisions.append(json.loads(r.stdout)["decision"])
        else:
            decisions.append(None)
    # attempt 1: fails open (None). attempts 2-6: block. attempt 7: exhausts (None again).
    assert decisions == [None, "block", "block", "block", "block", "block", None]
    assert "giving up blocking" in results[-1].stderr

    marker = json.loads(_marker_path(tmp_path).read_text(encoding="utf-8"))
    assert marker["consecutive_failures"] == 7
    assert marker["exhausted"] is True

    # And it stays given-up, not a one-time lapse back into blocking:
    eighth = _run_hook("shipgate.hooks.stop", {"session_id": "f6-sess-4", "cwd": str(tmp_path)}, tmp_path)
    assert eighth.stdout.strip() == ""


@pytest.mark.skipif(os.name != "nt", reason="ACL-based write-denial reproduction is Windows-specific (icacls)")
def test_stop_ledger_permission_denied_directory_falls_back_to_the_documented_residual_gap(tmp_path: Path):
    """Named, accepted limitation (stop.py's module docstring, P12): if
    `.shipgate/` itself is locked down enough that even the marker file can't be
    written, there is no way to distinguish a 1st from a 5th failure without guessing --
    and guessing at evidence this project doesn't have is exactly what this project
    exists to refuse. Falls back to Finding 6's original behavior for
    this one sub-case only: fails open every time, and says explicitly that it could not
    even record the failure, rather than silently blocking or silently pretending to
    track it."""
    _real_shipfile(tmp_path, "exit 1")
    shipgate_dir = tmp_path / ".shipgate"
    shipgate_dir.mkdir(parents=True, exist_ok=True)
    username = os.environ.get("USERNAME", "")
    subprocess.run(["icacls", str(shipgate_dir), "/deny", f"{username}:(W)"], capture_output=True, check=True)
    try:
        first = _run_hook("shipgate.hooks.stop", {"session_id": "f6-sess-5", "cwd": str(tmp_path)}, tmp_path)
        second = _run_hook("shipgate.hooks.stop", {"session_id": "f6-sess-5", "cwd": str(tmp_path)}, tmp_path)
    finally:
        subprocess.run(["icacls", str(shipgate_dir), "/remove:d", username], capture_output=True, check=False)

    for result in (first, second):
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == ""  # always fails open here -- never blocks, never crashes
        assert "could not even record this failure" in result.stderr
    assert not _marker_path(tmp_path).exists()  # confirmed: genuinely never trackable in this sub-case

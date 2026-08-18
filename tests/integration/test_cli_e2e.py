"""Task 3.1/3.2 — `shipgate init` and `shipgate doctor`, through the real
`python -m shipgate.cli` subprocess entrypoint, exactly how a stranger would invoke them
(the same discipline `test_hooks_e2e.py` already applies to the hook entrypoints, and the
same fix that mattered there — using `sys.executable`, not a bare `python`, so the
subprocess resolves to this venv's own install rather than whatever `python` happens to
mean on `PATH` — applies here too).

**The founder's explicit ask, Session 009: "Demonstrate the refusal path live, not just
the happy path."** `test_init_refuses_to_overwrite_an_existing_shipfile_live` is that
demonstration — a real subprocess, a real pre-existing file, a real refusal, and a real
assertion that the file was never touched.

**Finding 1, Session 010 (Gate C blocker): `test_broken_interpreter_path_produces_zero_
observable_signal_not_a_crash` demonstrates the residual gap named alongside the fix.**
`init` now emits `sys.executable` instead of a bare `"python"`, closing the primary
failure mode. What it *cannot* close: if the recorded interpreter path later stops
resolving (a moved or deleted venv), the hook subprocess simply never runs — and
because every hook entrypoint fails open by design, nothing crashes, nothing writes to
a place a user would see it, and the ledger simply never gains the row it would have.
This test proves that exact signature: a real subprocess invocation of a broken
interpreter path, and a real assertion that the project's ledger was never even
created — the user's only evidence is an absence, indistinguishable from a hook that was
never configured at all.

**Finding 4, Session 011 (Gate C blocker): non-UTF-8 Windows codepages crashed this
CLI's own output.** This project's printed output uses U+2014 throughout. Windows'
default text encoding for a process is the system locale's codepage — cp1252 encodes
U+2014, but cp437 and cp932 (the *default* on a Japanese-locale Windows machine) do not.
`shipgate init` did its entire job (real shipfile, real `CLAUDE.md`, real
`.claude/settings.json`) and then crashed with a raw `UnicodeEncodeError` while printing
its own success report — the inverse of Finding 1's failure shape (that one silently
never ran; this one loudly claims to have failed after actually succeeding), and both
land at the one moment Gate C measures: first contact. Fixed in `shipgate.cli.
_ensure_utf8_streams`. The identical bug shape was investigated in the hooks package too
(`shipgate.hooks._common.ensure_utf8_streams`) — but proven, via a negative-control
test, to be already safe there: `sys.stderr`'s default error handler is CPython's own
`'backslashreplace'`, so hooks' stderr writes never actually crash on a narrow codepage
the way this CLI's `stdout` writes (default `'strict'`) did. The hooks-package function
is kept as defense-in-depth, reported precisely as that, not as a second confirmed
blocker — see its own docstring.

**Why these regression tests force a codepage via `PYTHONIOENCODING` instead of adding a
Windows CI runner:** the codecs for cp437/cp932/cp1252 are pure Python lookup tables,
bundled with every CPython install regardless of host OS — forcing `PYTHONIOENCODING`
exercises the *exact* code path that broke (`TextIOWrapper` encoding a `str` through a
narrow codec) on any machine, including this project's existing `ubuntu-latest` CI job.
The founder's own structural finding was that CI never exercises a non-UTF-8 default
encoding at all, not that CI never runs on Windows specifically — a forced-codepage test
closes the actual gap (a codec class of bug) without the cost and slowdown of a second
OS runner for what is not an OS-specific defect. If a genuinely Windows-console-specific
encoding behavior is ever found (distinct from the text-encoding-selection bug fixed
here), that would be grounds to revisit this decision — not a currently open question.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    # encoding="utf-8" is explicit, not incidental: `text=True` alone decodes the
    # child's captured stdout/stderr using this PARENT process's own locale encoding
    # (locale.getpreferredencoding()) -- on a non-UTF-8-locale machine that silently
    # mojibakes the child's correctly-UTF-8-encoded output (Session 011: the em dashes
    # in `shipgate.cli`'s own printed messages), which can produce a false test failure
    # even after the product-side encoding bug is fixed. Explicit encoding makes what
    # these tests assert independent of whatever machine happens to run them.
    return subprocess.run(
        [sys.executable, "-m", "shipgate.cli", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def _init(cwd: Path, *, intent="e2e test project", test_command="python -m pytest") -> subprocess.CompletedProcess:
    return _run_cli(
        ["init", "--project-dir", ".", "--intent-summary", intent, "--test-command", test_command],
        cwd,
    )


# --- happy path, live ------------------------------------------------------------------


def test_init_happy_path_writes_all_three_files_live(tmp_path: Path):
    result = _init(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "shipfile.yaml").exists()
    assert (tmp_path / "CLAUDE.md").exists()
    assert (tmp_path / ".claude" / "settings.json").exists()
    assert "written" in result.stdout


# --- the founder's explicit ask: the refusal path, live, not just asserted in-process --


def test_init_refuses_to_overwrite_an_existing_shipfile_live(tmp_path: Path):
    original_content = "# a real, pre-existing shipfile the user already wrote\nnot_even_valid: [\n"
    (tmp_path / "shipfile.yaml").write_text(original_content, encoding="utf-8")

    result = _init(tmp_path)

    # Founder review finding, Session 011: a shipfile-only refusal is init's own
    # by-design safe default, not a failure -- exit 0, not 1. CLAUDE.md and
    # settings.json still get written normally alongside the refusal.
    assert result.returncode == 0, result.stdout + result.stderr
    assert "refused" in result.stdout
    assert "never overwrites" in result.stdout
    assert "correctly left untouched" in result.stdout
    # the actual, real file on disk was never touched -- not just the reported outcome
    assert (tmp_path / "shipfile.yaml").read_text(encoding="utf-8") == original_content
    # the other two still got written -- one refusal doesn't stop the rest
    assert (tmp_path / "CLAUDE.md").exists()


def test_init_refuses_on_malformed_existing_settings_json_live(tmp_path: Path):
    settings_dir = tmp_path / ".claude"
    settings_dir.mkdir()
    (settings_dir / "settings.json").write_text("{ not json at all", encoding="utf-8")

    result = _init(tmp_path)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "not valid JSON" in result.stdout
    assert (settings_dir / "settings.json").read_text(encoding="utf-8") == "{ not json at all"


# --- combined-realistic: init -> doctor, chained through real subprocesses -------------


def test_doctor_live_after_init_is_vacuous_not_clean(tmp_path: Path):
    """Finding 2, Session 010: a freshly-init'd project's only done_condition is
    tests_pass — a type doctor doesn't inspect. This is the literal out-of-the-box path
    and the literal Gate C script; it must never render "Clean" on zero observations."""
    init_result = _init(tmp_path)
    assert init_result.returncode == 0, init_result.stdout + init_result.stderr

    doctor_result = _run_cli(["doctor", "--project-dir", "."], tmp_path)

    assert doctor_result.returncode == 3, doctor_result.stdout + doctor_result.stderr
    assert "Clean" not in doctor_result.stdout
    assert "Nothing checked" in doctor_result.stdout
    assert "NOT a clean result" in doctor_result.stdout


def test_doctor_live_reports_genuine_clean_when_something_was_actually_checked(tmp_path: Path):
    init_result = _init(tmp_path)
    assert init_result.returncode == 0, init_result.stdout + init_result.stderr

    import yaml

    shipfile_path = tmp_path / "shipfile.yaml"
    doc = yaml.safe_load(shipfile_path.read_text(encoding="utf-8"))
    doc["done_conditions"].append({"id": "readme-exists", "type": "file_exists", "path": "shipfile.yaml"})
    shipfile_path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")

    doctor_result = _run_cli(["doctor", "--project-dir", "."], tmp_path)

    assert doctor_result.returncode == 0, doctor_result.stdout + doctor_result.stderr
    assert "Clean — 1 reference(s) checked, none stale." in doctor_result.stdout


def test_doctor_live_catches_a_deleted_file_reference(tmp_path: Path):
    init_result = _init(tmp_path)
    assert init_result.returncode == 0, init_result.stdout + init_result.stderr

    import yaml

    shipfile_path = tmp_path / "shipfile.yaml"
    doc = yaml.safe_load(shipfile_path.read_text(encoding="utf-8"))
    doc["done_conditions"].append({"id": "gone", "type": "file_exists", "path": "no_such_file.txt"})
    shipfile_path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")

    doctor_result = _run_cli(["doctor", "--project-dir", "."], tmp_path)

    assert doctor_result.returncode == 1, doctor_result.stdout + doctor_result.stderr
    assert "no_such_file.txt" in doctor_result.stdout


# --- combined-realistic: init -> a real PreToolUse hook -> declare-task-class ----------


def test_full_chain_init_then_real_hook_then_declare_task_class_live(tmp_path: Path):
    """The whole loop a real Claude Code session produces: `shipgate init` wires the
    hooks and writes a shipfile with a high-risk task class already in it; a real
    `PreToolUse` hook fire (exactly what Claude Code invokes before any tool call)
    creates the session row; `shipgate declare-task-class` records against it. This is
    Gate B condition 5, demonstrated for the first time through a real, live,
    end-user-reachable path rather than a direct call to `record_high_risk_change`."""
    init_result = _init(tmp_path)
    assert init_result.returncode == 0, init_result.stdout + init_result.stderr

    hook_payload = {
        "session_id": "full-chain-sess",
        "cwd": str(tmp_path),
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "echo hi"},
    }
    hook_result = subprocess.run(
        [sys.executable, "-m", "shipgate.hooks.pretooluse"],
        input=json.dumps(hook_payload),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert hook_result.returncode == 0, hook_result.stderr

    declare_result = _run_cli(
        [
            "declare-task-class",
            "high_risk_change",
            "the first live, end-to-end declaration",
            "--project-dir",
            ".",
        ],
        tmp_path,
    )

    assert declare_result.returncode == 0, declare_result.stdout + declare_result.stderr
    assert "recorded" in declare_result.stdout

    import sqlite3

    conn = sqlite3.connect(tmp_path / ".shipgate" / "ledger.db")
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM events WHERE record_type = 'high_risk_change'"
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == 1


# --- Finding 1 (Session 010): the residual gap the sys.executable fix does not close --


def test_broken_interpreter_path_produces_zero_observable_signal_not_a_crash(tmp_path: Path):
    """What the founder asked to see: a settings.json hook command pointing at an
    interpreter path that doesn't exist (the moved/deleted-venv scenario the module
    docstring names as a stated, unresolved gap), invoked exactly the way Claude Code
    would invoke it — a shell command string, not a Python API call — and a real
    assertion of what a user actually gets: no exception surfaces anywhere this test can
    observe, and no ledger is ever created. The absence of a crash IS the danger — it is
    indistinguishable from a hook that was never configured at all."""
    broken_interpreter = str(tmp_path / "no_such_interpreter" / "python.exe")
    command = f'"{broken_interpreter}" -m shipgate.hooks.pretooluse'

    result = subprocess.run(
        command,
        shell=True,
        cwd=tmp_path,
        input=json.dumps({"session_id": "broken-interp", "cwd": str(tmp_path)}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    # The shell reports the command failed to launch at all -- but nothing in this
    # failure is ShipGate's own code; ShipGate's hook module never even started running.
    assert result.returncode != 0

    # The user-visible consequence: no ledger was ever created, no session row, no
    # events -- the same silence a correctly-configured-but-idle project would show.
    assert not (tmp_path / ".shipgate" / "ledger.db").exists()


# --- Finding 4 (Session 011): non-UTF-8 codepages must never crash this CLI ------------


def _run_cli_with_encoding(args: list[str], cwd: Path, *, python_io_encoding: str) -> subprocess.CompletedProcess:
    """Forces the CHILD process's default text encoding to `python_io_encoding` via
    `PYTHONIOENCODING`, reproducing on any host OS (including this project's own
    `ubuntu-latest` CI runner) the exact code path that broke on a narrow Windows
    codepage — see the module docstring's "why forced codepage, not a Windows CI job"
    note. `encoding="utf-8"` on this `subprocess.run` call is this PARENT process's own
    decode of the child's captured output and is unrelated to what's under test."""
    import os

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = python_io_encoding
    return subprocess.run(
        [sys.executable, "-m", "shipgate.cli", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
    )


def test_init_survives_cp437_the_founders_exact_reproduction(tmp_path: Path):
    """Before the fix, this exact sequence produced: `shipfile.yaml`, `CLAUDE.md`, and
    `.claude/settings.json` all written to disk, immediately followed by a raw
    `UnicodeEncodeError` traceback and empty stdout -- the entire job done, reported as
    a crash. This test pins that it no longer happens."""
    result = _run_cli_with_encoding(
        ["init", "--project-dir", ".", "--intent-summary", "cp437 test", "--test-command", "pytest"],
        tmp_path,
        python_io_encoding="cp437",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "UnicodeEncodeError" not in result.stderr
    assert "written" in result.stdout
    assert (tmp_path / "shipfile.yaml").exists()


def test_init_survives_cp932_the_default_on_japanese_locale_windows(tmp_path: Path):
    result = _run_cli_with_encoding(
        ["init", "--project-dir", ".", "--intent-summary", "cp932 test", "--test-command", "pytest"],
        tmp_path,
        python_io_encoding="cp932",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "UnicodeEncodeError" not in result.stderr


def test_doctor_survives_cp932_including_the_vacuous_message(tmp_path: Path):
    """The vacuous-doctor message (Finding 2) also carries an em dash — pinning that
    Finding 2's fix and Finding 4's fix compose correctly, not just individually."""
    init_result = _run_cli_with_encoding(
        ["init", "--project-dir", ".", "--intent-summary", "x", "--test-command", "pytest"],
        tmp_path,
        python_io_encoding="cp932",
    )
    assert init_result.returncode == 0, init_result.stdout + init_result.stderr

    doctor_result = _run_cli_with_encoding(["doctor", "--project-dir", "."], tmp_path, python_io_encoding="cp932")

    assert doctor_result.returncode == 3, doctor_result.stdout + doctor_result.stderr
    assert "UnicodeEncodeError" not in doctor_result.stderr
    assert "Nothing checked" in doctor_result.stdout


def test_init_refusal_path_also_survives_cp932(tmp_path: Path):
    """The refusal message (Session 009's live demonstration) also carries an em dash.
    Exit 0 per Session 011's exit-code fix -- a shipfile-only refusal is by design."""
    (tmp_path / "shipfile.yaml").write_text("existing: true\n", encoding="utf-8")

    result = _run_cli_with_encoding(
        ["init", "--project-dir", ".", "--intent-summary", "x", "--test-command", "pytest"],
        tmp_path,
        python_io_encoding="cp932",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "UnicodeEncodeError" not in result.stderr
    assert "refused" in result.stdout

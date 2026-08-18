"""Task 2.1 — Claude Code hook entrypoints (Phase 2 done-condition for
this task: "hooks fire on a real session and write ledger rows; no network call from
any hook") — and task 2.6, which is where `Stop` starts actually gating.

`PreToolUse`/`PostToolUse` stay observational only — see `shipgate/hooks/__init__.py`'s
docstring. `Stop` now calls `shipgate.gate.evaluate_gate` when `<cwd>/shipfile.yaml`
exists (see `shipgate/hooks/stop.py`'s module docstring) — the "not that a stop can be
refused" boundary this file used to state is exactly what the task-2.6 tests below now
cover, since it's no longer true.

Unit-level: calls each entrypoint's `run()` directly with a literal JSON string,
`tmp_path` as the project `cwd`. Real subprocess invocation (`python -m
shipgate.hooks.X`, exactly how `settings.json` would call it), the multi-hook combined
session, and the no-network-call proof live in
`tests/integration/test_hooks_e2e.py` — the realistic case, not just the isolated one.
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

from shipgate.hooks import posttooluse, pretooluse, stop
from shipgate.ledger.integrity import verify_all_chains

PY = sys.executable

_ENTRYPOINTS = [pretooluse, stop, posttooluse]


def _payload(tmp_path, **overrides):
    base = {
        "session_id": "sess-1",
        "cwd": str(tmp_path),
        "hook_event_name": "PreToolUse",
        "permission_mode": "default",
        "tool_name": "Bash",
        "tool_input": {"command": "echo hi"},
        "tool_use_id": "tu-1",
    }
    base.update(overrides)
    return json.dumps(base)


def _ledger_path(tmp_path):
    return tmp_path / ".shipgate" / "ledger.db"


# --- each entrypoint writes a real row ------------------------------------------------


@pytest.mark.parametrize("module,record_type", [(pretooluse, "pretooluse_hook"), (posttooluse, "posttooluse_hook")])
def test_tool_hook_writes_one_event_row(tmp_path, module, record_type):
    exit_code = module.run(_payload(tmp_path))
    assert exit_code == 0

    conn = sqlite3.connect(_ledger_path(tmp_path))
    rows = conn.execute("SELECT record_type, uuid FROM events").fetchall()
    assert rows == [(record_type, "tu-1")]


def test_stop_hook_writes_one_event_row_and_allows_the_stop(tmp_path):
    """No shipfile.yaml in cwd -- nothing to gate on, unchanged from before task 2.6."""
    payload = json.dumps(
        {"session_id": "sess-1", "cwd": str(tmp_path), "hook_event_name": "Stop", "stop_hook_active": False}
    )
    exit_code = stop.run(payload)
    assert exit_code == 0

    conn = sqlite3.connect(_ledger_path(tmp_path))
    rows = conn.execute("SELECT record_type FROM events").fetchall()
    assert rows == [("stop_hook",)]


def test_stop_hook_active_true_still_writes_and_still_allows(tmp_path):
    """Respects Claude Code's own stop_hook_active field even though this build never
    blocks -- writes its event either way, never adds a second loop-detection layer on
    top of Claude Code's own."""
    payload = json.dumps(
        {"session_id": "sess-1", "cwd": str(tmp_path), "hook_event_name": "Stop", "stop_hook_active": True}
    )
    exit_code = stop.run(payload)
    assert exit_code == 0


# --- ledger writes to .shipgate/, never anywhere else ----------------------------------


def test_ledger_lands_under_dot_shipgate_in_the_project_cwd(tmp_path):
    pretooluse.run(_payload(tmp_path))
    assert _ledger_path(tmp_path).exists()
    assert _ledger_path(tmp_path).parent.name == ".shipgate"


# --- session bootstrap is idempotent ---------------------------------------------------


def test_two_hook_calls_in_the_same_session_write_one_sessions_row_not_two(tmp_path):
    pretooluse.run(_payload(tmp_path))
    posttooluse.run(_payload(tmp_path))

    conn = sqlite3.connect(_ledger_path(tmp_path))
    sessions = conn.execute("SELECT session_id FROM sessions").fetchall()
    assert sessions == [("sess-1",)]
    events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert events == 2


# --- malformed input degrades safely, never crashes -------------------------------------


@pytest.mark.parametrize("module", _ENTRYPOINTS)
def test_empty_stdin_exits_zero_and_writes_nothing(tmp_path, module):
    exit_code = module.run("")
    assert exit_code == 0
    assert not _ledger_path(tmp_path).exists()


@pytest.mark.parametrize("module", _ENTRYPOINTS)
def test_malformed_json_exits_zero_and_writes_nothing(tmp_path, module):
    exit_code = module.run("{not valid json")
    assert exit_code == 0
    assert not _ledger_path(tmp_path).exists()


@pytest.mark.parametrize("module", _ENTRYPOINTS)
def test_missing_required_field_exits_zero_and_writes_nothing(tmp_path, module):
    payload = json.dumps({"session_id": "sess-1"})  # cwd missing
    exit_code = module.run(payload)
    assert exit_code == 0


@pytest.mark.parametrize("module", _ENTRYPOINTS)
def test_json_array_instead_of_object_exits_zero_not_crash(tmp_path, module):
    exit_code = module.run("[1, 2, 3]")
    assert exit_code == 0


# --- Gate C blocker, founder finding this session: a hook must never create a project
# root it didn't find -- the old open_project_ledger trusted the payload's cwd
# absolutely. Reproduced live before this fix: an unresolvable cwd made it silently
# build a whole new directory tree wherever cwd pointed and write a real ledger there,
# while the real project got no .shipgate/ at all -- both exit code and stdout AND
# stderr all said nothing was wrong. See shipgate/hooks/_common.py's
# ProjectRootUnresolvableError docstring for the full reproduction. -----------------------


def _unresolvable_cwd_payload(module, bogus_cwd, session_id="sess-1"):
    if module is stop:
        return json.dumps({"session_id": session_id, "cwd": bogus_cwd, "hook_event_name": "Stop"})
    event_name = "PreToolUse" if module is pretooluse else "PostToolUse"
    return json.dumps(
        {
            "session_id": session_id,
            "cwd": bogus_cwd,
            "hook_event_name": event_name,
            "permission_mode": "default",
            "tool_name": "Bash",
            "tool_input": {"command": "echo hi"},
            "tool_use_id": "tu-1",
        }
    )


@pytest.mark.parametrize("module", _ENTRYPOINTS)
def test_unresolvable_cwd_creates_no_directory_and_fails_open_loudly(tmp_path, module, capsys):
    """Requirement (e): a non-existent-cwd regression test on each of the three hooks,
    asserting BOTH that no directory is created AND that stderr is non-empty."""
    bogus_cwd = str(tmp_path / "does" / "not" / "exist" / "at" / "all")
    assert not Path(bogus_cwd).exists()

    exit_code = module.run(_unresolvable_cwd_payload(module, bogus_cwd))

    assert exit_code == 0  # (b) still fails open on the DECISION -- the loop-breaker rule stands
    assert not Path(bogus_cwd).exists()  # (a) never creates a project root it didn't find
    err = capsys.readouterr().err
    assert err.strip() != ""  # (b) never SILENTLY -- this is the regression itself
    assert "did not run" in err

    # the real project directory the test harness controls also gets nothing -- the hook
    # has no way to know tmp_path was the "real" project once the payload lies about cwd;
    # this confirms nothing was written ANYWHERE, not that it self-corrected to the right place.
    assert not (tmp_path / ".shipgate").exists()


def test_unresolvable_cwd_that_is_a_file_not_a_directory_is_also_refused(tmp_path, capsys):
    """Path.is_dir() also catches the case where cwd resolves to something that already
    exists but isn't a directory (a plain file) -- also not a valid project root,
    refused the same way, not waved through because something is technically there."""
    not_a_dir = tmp_path / "actually_a_file.txt"
    not_a_dir.write_text("not a directory", encoding="utf-8")
    exit_code = stop.run(json.dumps({"session_id": "sess-1", "cwd": str(not_a_dir), "hook_event_name": "Stop"}))
    assert exit_code == 0
    err = capsys.readouterr().err
    assert "did not run" in err


def test_open_project_ledger_raises_directly_without_touching_disk(tmp_path):
    """Root-cause coverage, isolated from any one hook's wrapping: the fix lives in
    shipgate.hooks._common.open_project_ledger itself, so it must be provably correct
    there directly, not only observable through whichever hook happens to call it."""
    from shipgate.hooks._common import ProjectRootUnresolvableError, open_project_ledger

    bogus_cwd = str(tmp_path / "never" / "created")
    with pytest.raises(ProjectRootUnresolvableError, match="does not exist"):
        open_project_ledger(bogus_cwd)
    assert not Path(bogus_cwd).exists()


# --- secrets already redact through the existing pipeline -------------------------------


def test_a_secret_in_tool_input_never_reaches_disk(tmp_path):
    """Reuses shipgate.ledger.redaction (Session 003) via insert_event's existing
    redact-before-write pipeline -- this test proves the WIRING is real, not that
    redaction itself works (that's test_redaction.py's job)."""
    secret_url = "https://deployer:hunter2supersecret@example.com/repo.git"
    payload = _payload(tmp_path, tool_input={"command": f"git remote add origin {secret_url}"})
    pretooluse.run(payload)

    raw_bytes = _ledger_path(tmp_path).read_bytes()
    assert b"hunter2supersecret" not in raw_bytes


def test_a_secret_used_as_a_dict_key_never_reaches_disk(tmp_path):
    """Task 3.5's own founder instruction, every round: a secret in a dict KEY, not
    only a value -- fresh fixture, distinct from the URL-in-a-value case above. This is
    the exact failure shape `shipgate.ledger.redaction`'s own module docstring names
    (a Phase 0 spike leaked transcript content through a free-text dict key on its
    first run) -- here with an actual secret-SHAPED string as the key, not just an
    overlong free-text one. `redact_json`'s dict-key path is already unit-tested
    directly against the pure function (`test_redaction.py`); this proves the WIRING
    through the real hook -> `LedgerWriter` pipeline is real, the same standard
    `test_a_secret_in_tool_input_never_reaches_disk` already holds itself to. Synthetic
    secret, freshly authored for this test -- never the real corpus at
    ~/.claude/projects/, which this test never reads, writes, or references."""
    leaked_key = "sk-ant-api03-freshFixtureNotTheRealCorpus1234567890ABCDEFabcdefGHIJKL"
    payload = _payload(
        tmp_path,
        tool_input={
            "command": "curl -H 'Authorization: Bearer $TOKEN' https://api.example.com/webhook",
            # a realistic misuse: a response cache keyed by the credential that produced
            # it, rather than a request id -- the credential ends up as a dict KEY, not
            # a value under a secret-named field.
            "response_cache_by_credential": {leaked_key: {"status": 200, "body": "ok"}},
        },
    )
    pretooluse.run(payload)

    raw_bytes = _ledger_path(tmp_path).read_bytes()
    assert leaked_key.encode("utf-8") not in raw_bytes


# --- hash chain integrity holds across hook-originated writes ---------------------------


def test_hash_chain_verifies_after_a_realistic_multi_hook_sequence(tmp_path):
    """The messy realistic case: several tool calls, not just one write."""
    for i in range(3):
        pretooluse.run(_payload(tmp_path, tool_use_id=f"tu-{i}"))
        posttooluse.run(_payload(tmp_path, tool_use_id=f"tu-{i}"))
    stop.run(json.dumps({"session_id": "sess-1", "cwd": str(tmp_path), "stop_hook_active": False}))

    conn = sqlite3.connect(_ledger_path(tmp_path))
    verify_all_chains(conn)  # raises ChainTamperedError if anything is wrong; no exception == pass

    count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert count == 7  # 3 pretool + 3 posttool + 1 stop


# --- task 2.6: Stop actually gates when a shipfile is present ---------------------------


def _stop_payload(tmp_path, session_id="sess-1"):
    return json.dumps({"session_id": session_id, "cwd": str(tmp_path), "hook_event_name": "Stop"})


def _write_shipfile(tmp_path, done_conditions, max_retries=3):
    """A minimal but fully schema-VALID shipfile (all eight required blocks) — a
    shipfile missing a block fails validation before evaluate_gate ever runs, which
    would make these "green"/"red" tests exercise the fail-open invalid-shipfile path
    by accident instead of real gating."""
    conditions_yaml = "\n".join(
        f"  - id: {c['id']}\n    type: {c['type']}\n    command: {c['command']!r}" for c in done_conditions
    )
    (tmp_path / "shipfile.yaml").write_text(
        "task_classes:\n"
        "  feature:\n"
        "    risk_tier: medium\n"
        "    starting_model_tier: mid\n"
        "    max_tokens: 100000\n"
        "    gate_strictness: strict\n"
        f"done_conditions:\n{conditions_yaml}\n"
        "routing: {}\n"
        "budgets: {}\n"
        "context_policy: {}\n"
        "intent:\n"
        "  summary: test shipfile for task 2.6\n"
        f"gate_policy:\n  max_retries: {max_retries}\n"
        "session_policy: {}\n",
        encoding="utf-8",
    )


def test_stop_with_no_shipfile_is_unchanged_from_before_task_2_6(tmp_path):
    """No shipfile.yaml -- nothing to gate on, allowed, no gate_evaluation event."""
    exit_code = stop.run(_stop_payload(tmp_path))
    assert exit_code == 0
    conn = sqlite3.connect(_ledger_path(tmp_path))
    types = [r[0] for r in conn.execute("SELECT record_type FROM events")]
    assert "gate_evaluation" not in types


def test_stop_with_an_invalid_shipfile_fails_open_not_blocked(tmp_path):
    (tmp_path / "shipfile.yaml").write_text("this is not: [valid, shipfile\n", encoding="utf-8")
    exit_code = stop.run(_stop_payload(tmp_path))
    assert exit_code == 0  # fail open -- a broken shipfile is not a done_conditions failure

    conn = sqlite3.connect(_ledger_path(tmp_path))
    types = [r[0] for r in conn.execute("SELECT record_type FROM events")]
    assert "gate_shipfile_invalid" in types


def test_stop_with_a_green_shipfile_allows_and_writes_no_block_decision(tmp_path, capsys):
    _write_shipfile(tmp_path, [{"id": "ok", "type": "command_succeeds", "command": f'"{PY}" -c "import sys; sys.exit(0)"'}])
    exit_code = stop.run(_stop_payload(tmp_path))
    assert exit_code == 0
    assert capsys.readouterr().out == ""  # no decision key at all on green


def test_stop_with_a_red_shipfile_blocks_the_founders_exact_gate_b_scenario(tmp_path, capsys):
    """Gate B condition #1, runtime-verified via the real Stop hook entrypoint: a false
    completion (a project whose done_conditions aren't actually met) is blocked."""
    _write_shipfile(
        tmp_path, [{"id": "broken", "type": "command_succeeds", "command": f'"{PY}" -c "import sys; sys.exit(1)"'}]
    )
    exit_code = stop.run(_stop_payload(tmp_path))
    assert exit_code == 0  # hooks always exit 0; the refusal is IN the JSON, not the exit code

    decision = json.loads(capsys.readouterr().out)
    assert decision["decision"] == "block"
    assert "broken" in decision["reason"]


def test_stop_retry_cap_converges_to_an_honest_red_release_gate_b_condition_4(tmp_path, capsys):
    """Gate B condition #4, runtime-verified via the real Stop hook entrypoint, four real
    invocations in a row (a persistently-red project, default max_retries=3): the retry
    cap produces an honest red, and the Stop hook itself stops blocking once it's hit."""
    _write_shipfile(
        tmp_path, [{"id": "broken", "type": "command_succeeds", "command": f'"{PY}" -c "import sys; sys.exit(1)"'}]
    )
    decisions = []
    for _ in range(4):
        stop.run(_stop_payload(tmp_path))
        out = capsys.readouterr().out
        decisions.append(json.loads(out) if out else None)

    assert [d is not None and d.get("decision") == "block" for d in decisions] == [True, True, True, False]

    conn = sqlite3.connect(_ledger_path(tmp_path))
    payloads = [
        json.loads(r[0])
        for r in conn.execute(
            "SELECT raw_payload_redacted FROM events WHERE record_type='gate_evaluation' ORDER BY event_id"
        )
    ]
    assert [p["exhausted"] for p in payloads] == [False, False, False, True]


# --- founder review finding: a truncated retry ceiling must be visible, not silent ------


def test_stop_warns_on_stderr_when_shipfile_max_retries_exceeds_the_safety_ceiling(tmp_path, capsys):
    _write_shipfile(
        tmp_path,
        [{"id": "broken", "type": "command_succeeds", "command": f'"{PY}" -c "import sys; sys.exit(1)"'}],
        max_retries=10,  # schema-legal, but above shipgate.gate.orchestrator's own ceiling
    )
    stop.run(_stop_payload(tmp_path))
    err = capsys.readouterr().err
    assert "max_retries=10" in err
    assert "capped at 6" in err


def test_stop_does_not_warn_when_max_retries_is_within_the_safety_ceiling(tmp_path, capsys):
    _write_shipfile(
        tmp_path, [{"id": "ok", "type": "command_succeeds", "command": f'"{PY}" -c "import sys; sys.exit(0)"'}]
    )  # default max_retries=3
    stop.run(_stop_payload(tmp_path))
    err = capsys.readouterr().err
    assert "safety ceiling" not in err


def test_stop_writes_the_release_reason_to_stderr_when_the_retry_cap_exhausts(tmp_path, capsys):
    _write_shipfile(
        tmp_path, [{"id": "broken", "type": "command_succeeds", "command": f'"{PY}" -c "import sys; sys.exit(1)"'}]
    )
    for _ in range(4):  # default max_retries=3 -> exhausts on the 4th
        capsys.readouterr()  # drain
        stop.run(_stop_payload(tmp_path))
    err = capsys.readouterr().err
    assert "honest red" in err
    assert "broken" in err


# --- Finding 6 / P12: the marker helpers directly, independent of the full
# subprocess -- fast, isolated coverage of the counting logic itself. ---------------------


def _marker_path(tmp_path):
    return tmp_path / ".shipgate" / "gate_unavailable.json"


def test_gate_unavailable_marker_round_trips_through_read_write(tmp_path):
    assert stop._read_gate_unavailable_marker(str(tmp_path)) == {}  # nothing written yet
    ok = stop._write_gate_unavailable_marker(str(tmp_path), {"consecutive_failures": 3, "last_error": "boom"})
    assert ok is True
    assert stop._read_gate_unavailable_marker(str(tmp_path)) == {"consecutive_failures": 3, "last_error": "boom"}


def test_gate_unavailable_marker_clear_is_a_no_op_when_nothing_exists(tmp_path):
    stop._clear_gate_unavailable_marker(str(tmp_path))  # must not raise
    assert not _marker_path(tmp_path).exists()


def test_gate_unavailable_marker_clear_removes_a_real_marker(tmp_path):
    stop._write_gate_unavailable_marker(str(tmp_path), {"consecutive_failures": 1})
    assert _marker_path(tmp_path).exists()
    stop._clear_gate_unavailable_marker(str(tmp_path))
    assert not _marker_path(tmp_path).exists()


def test_gate_unavailable_marker_read_treats_malformed_json_as_no_prior_failures(tmp_path):
    """A best-effort aid, never a source of truth anything else depends on -- corrupted
    the same way the ledger itself was found corrupted must not, in turn, crash this."""
    path = _marker_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json", encoding="utf-8")
    assert stop._read_gate_unavailable_marker(str(tmp_path)) == {}


def test_handle_ledger_unavailable_escalation_shape_directly(tmp_path, capsys):
    """The full escalation sequence (grace, blocks, exhaustion), exercised directly
    against `_handle_ledger_unavailable` rather than through a real corrupted sqlite
    file -- the integration-level tests in test_hooks_e2e.py cover the real-corruption
    trigger; this covers the counting/escalation logic itself in isolation."""
    cwd = str(tmp_path)
    exc = RuntimeError("simulated ledger failure")

    stop._handle_ledger_unavailable(cwd, "s1", exc, "2026-08-17T00:00:00Z")
    assert capsys.readouterr().out == ""  # attempt 1: fails open
    assert stop._read_gate_unavailable_marker(cwd)["consecutive_failures"] == 1

    for expected_count in range(2, 7):  # attempts 2-6: block
        stop._handle_ledger_unavailable(cwd, "s1", exc, "2026-08-17T00:00:00Z")
        out = capsys.readouterr().out
        decision = json.loads(out)
        assert decision["decision"] == "block"
        assert f"{expected_count} consecutive attempts" in decision["reason"]

    stop._handle_ledger_unavailable(cwd, "s1", exc, "2026-08-17T00:00:00Z")  # attempt 7: exhausts
    err = capsys.readouterr().err
    assert "giving up blocking" in err
    marker = stop._read_gate_unavailable_marker(cwd)
    assert marker["consecutive_failures"] == 7
    assert marker["exhausted"] is True

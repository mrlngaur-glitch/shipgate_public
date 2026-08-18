"""Task 3.1 — `shipgate.discipline.init.run_init`, the merge-never-overwrite writer.
Every test writes real files to a real `tmp_path` and reads them back — the "does the
file on disk actually say what we claim" standard this project applies everywhere else.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from shipgate.discipline.init import DEFAULT_SETTINGS_JSON_RELPATH, run_init
from shipgate.discipline.templates import CLAUDE_MD_BEGIN_MARKER, CLAUDE_MD_END_MARKER
from shipgate.shipfile import load_shipfile

_INTENT = "Ship the thing"
_TEST_CMD = "python -m pytest"

#: The commands `init` actually emits — Session 010's fix: the absolute interpreter path
#: this test process itself runs under, never the bare string "python" (a Gate C blocker
#: the founder caught: wrong "python" resolution + hooks' own fail-open design meant a
#: dead hook and a working one were indistinguishable to the user).
_EXPECTED_PRETOOLUSE_COMMAND = f"{sys.executable} -m shipgate.hooks.pretooluse"
_EXPECTED_POSTTOOLUSE_COMMAND = f"{sys.executable} -m shipgate.hooks.posttooluse"
_EXPECTED_STOP_COMMAND = f"{sys.executable} -m shipgate.hooks.stop"


# --- fresh directory: everything written ------------------------------------------------


def test_fresh_directory_writes_all_three_files(tmp_path: Path):
    result = run_init(tmp_path, intent_summary=_INTENT, test_command=_TEST_CMD)

    assert result.shipfile.action == "written"
    assert result.claude_md.action == "written"
    assert result.settings_json.action == "written"
    assert not result.any_refused

    assert (tmp_path / "shipfile.yaml").exists()
    assert (tmp_path / "CLAUDE.md").exists()
    assert (tmp_path / DEFAULT_SETTINGS_JSON_RELPATH).exists()


def test_generated_shipfile_actually_parses_and_validates(tmp_path: Path):
    run_init(tmp_path, intent_summary=_INTENT, test_command=_TEST_CMD)
    shipfile = load_shipfile(tmp_path / "shipfile.yaml")
    assert shipfile["intent"]["summary"] == _INTENT
    assert shipfile["done_conditions"][0]["command"] == _TEST_CMD
    assert shipfile["task_classes"]["high_risk_change"]["risk_tier"] == "high"


def test_generated_settings_json_has_all_three_hook_events(tmp_path: Path):
    run_init(tmp_path, intent_summary=_INTENT, test_command=_TEST_CMD)
    data = json.loads((tmp_path / DEFAULT_SETTINGS_JSON_RELPATH).read_text(encoding="utf-8"))
    for event, expected_command in [
        ("PreToolUse", _EXPECTED_PRETOOLUSE_COMMAND),
        ("PostToolUse", _EXPECTED_POSTTOOLUSE_COMMAND),
        ("Stop", _EXPECTED_STOP_COMMAND),
    ]:
        commands = [h["command"] for group in data["hooks"][event] for h in group["hooks"]]
        assert expected_command in commands


def test_generated_settings_json_never_emits_a_bare_python_command(tmp_path: Path):
    """The Gate C blocker, Session 010: a bare "python" resolves against whatever's on
    PATH when Claude Code spawns the hook, which may not be the interpreter ShipGate was
    installed into — and every hook fails open silently, so a dead hook and a working
    one look identical to the user. `init` must emit an absolute interpreter path."""
    run_init(tmp_path, intent_summary=_INTENT, test_command=_TEST_CMD)
    data = json.loads((tmp_path / DEFAULT_SETTINGS_JSON_RELPATH).read_text(encoding="utf-8"))
    for event in ("PreToolUse", "PostToolUse", "Stop"):
        for group in data["hooks"][event]:
            for handler in group["hooks"]:
                assert handler["command"] != f"python -m shipgate.hooks.{event.lower()}"
                assert Path(handler["command"].rsplit(" -m ", 1)[0]).is_absolute()


def test_generated_claude_md_carries_the_task_class_declaration_instruction(tmp_path: Path):
    run_init(tmp_path, intent_summary=_INTENT, test_command=_TEST_CMD)
    text = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert "shipgate declare-task-class" in text
    assert CLAUDE_MD_BEGIN_MARKER in text
    assert CLAUDE_MD_END_MARKER in text


# --- shipfile.yaml: always refuse if one already exists, the founder's exact ask --------


def test_shipfile_already_present_is_always_refused_never_overwritten(tmp_path: Path):
    (tmp_path / "shipfile.yaml").write_text("this is not even valid yaml: [", encoding="utf-8")

    result = run_init(tmp_path, intent_summary=_INTENT, test_command=_TEST_CMD)

    assert result.shipfile.action == "refused"
    assert "already exists" in result.shipfile.reason
    # untouched -- proves this isn't a "refused but wrote anyway" bug
    assert (tmp_path / "shipfile.yaml").read_text(encoding="utf-8") == "this is not even valid yaml: ["


def test_shipfile_refusal_does_not_block_the_other_two_files(tmp_path: Path):
    (tmp_path / "shipfile.yaml").write_text("existing: true\n", encoding="utf-8")

    result = run_init(tmp_path, intent_summary=_INTENT, test_command=_TEST_CMD)

    assert result.shipfile.action == "refused"
    assert result.claude_md.action == "written"
    assert result.settings_json.action == "written"
    assert result.any_refused
    # Session 011: a shipfile-only refusal is by design, not a problem -- distinct from
    # any_refused (the raw fact) and asserted separately so the two never drift apart.
    assert not result.needs_user_attention


def test_needs_user_attention_true_when_claude_md_merge_genuinely_fails(tmp_path: Path):
    (tmp_path / "CLAUDE.md").write_text(
        f"{CLAUDE_MD_BEGIN_MARKER}\nno end marker\n", encoding="utf-8"
    )

    result = run_init(tmp_path, intent_summary=_INTENT, test_command=_TEST_CMD)

    assert result.claude_md.action == "refused"
    assert result.needs_user_attention


def test_needs_user_attention_true_when_settings_json_merge_genuinely_fails(tmp_path: Path):
    settings_path = tmp_path / DEFAULT_SETTINGS_JSON_RELPATH
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text("{ not valid json", encoding="utf-8")

    result = run_init(tmp_path, intent_summary=_INTENT, test_command=_TEST_CMD)

    assert result.settings_json.action == "refused"
    assert result.needs_user_attention


# --- CLAUDE.md: append-only merge, idempotent, never touches existing content -----------


def test_claude_md_absent_writes_a_fresh_file(tmp_path: Path):
    result = run_init(tmp_path, intent_summary=_INTENT, test_command=_TEST_CMD)
    assert result.claude_md.action == "written"


def test_claude_md_present_without_markers_appends_never_replaces(tmp_path: Path):
    original = "# My Project\n\nSome instructions the user wrote themselves.\n"
    (tmp_path / "CLAUDE.md").write_text(original, encoding="utf-8")

    result = run_init(tmp_path, intent_summary=_INTENT, test_command=_TEST_CMD)

    assert result.claude_md.action == "merged"
    text = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert text.startswith(original.rstrip("\n"))
    assert CLAUDE_MD_BEGIN_MARKER in text
    assert "Some instructions the user wrote themselves." in text


def test_claude_md_rerun_is_idempotent(tmp_path: Path):
    run_init(tmp_path, intent_summary=_INTENT, test_command=_TEST_CMD)
    first_text = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")

    # Re-run against the SAME project (shipfile.yaml now exists, so it refuses -- but
    # CLAUDE.md's own merge logic runs independently and should report "unchanged").
    result = run_init(tmp_path, intent_summary=_INTENT, test_command=_TEST_CMD)

    assert result.claude_md.action == "unchanged"
    assert (tmp_path / "CLAUDE.md").read_text(encoding="utf-8") == first_text


def test_claude_md_broken_marker_pair_is_refused_not_guessed(tmp_path: Path):
    (tmp_path / "CLAUDE.md").write_text(
        f"# Project\n\n{CLAUDE_MD_BEGIN_MARKER}\nhand-edited, no end marker\n",
        encoding="utf-8",
    )

    result = run_init(tmp_path, intent_summary=_INTENT, test_command=_TEST_CMD)

    assert result.claude_md.action == "refused"
    assert "marker" in result.claude_md.reason
    # untouched
    assert "hand-edited, no end marker" in (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")


def test_claude_md_markers_in_wrong_order_is_refused(tmp_path: Path):
    (tmp_path / "CLAUDE.md").write_text(
        f"{CLAUDE_MD_END_MARKER}\nsomething\n{CLAUDE_MD_BEGIN_MARKER}\n",
        encoding="utf-8",
    )

    result = run_init(tmp_path, intent_summary=_INTENT, test_command=_TEST_CMD)

    assert result.claude_md.action == "refused"


# --- .claude/settings.json: additive JSON merge, refuses only when unsafe ---------------


def test_settings_json_absent_writes_a_fresh_file(tmp_path: Path):
    result = run_init(tmp_path, intent_summary=_INTENT, test_command=_TEST_CMD)
    assert result.settings_json.action == "written"


def test_settings_json_preserves_unrelated_keys_and_unrelated_hooks(tmp_path: Path):
    settings_path = tmp_path / DEFAULT_SETTINGS_JSON_RELPATH
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps(
            {
                "model": "opus",
                "hooks": {
                    "PreToolUse": [
                        {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo mine"}]}
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    result = run_init(tmp_path, intent_summary=_INTENT, test_command=_TEST_CMD)

    assert result.settings_json.action == "merged"
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    assert data["model"] == "opus"
    pretool_commands = [h["command"] for group in data["hooks"]["PreToolUse"] for h in group["hooks"]]
    assert "echo mine" in pretool_commands
    assert _EXPECTED_PRETOOLUSE_COMMAND in pretool_commands


def test_settings_json_rerun_is_idempotent_no_duplicate_entries(tmp_path: Path):
    run_init(tmp_path, intent_summary=_INTENT, test_command=_TEST_CMD)
    settings_path = tmp_path / DEFAULT_SETTINGS_JSON_RELPATH
    first_data = json.loads(settings_path.read_text(encoding="utf-8"))

    (tmp_path / "shipfile.yaml").unlink()  # let shipfile write again too, irrelevant here
    result = run_init(tmp_path, intent_summary=_INTENT, test_command=_TEST_CMD)

    assert result.settings_json.action == "unchanged"
    second_data = json.loads(settings_path.read_text(encoding="utf-8"))
    assert second_data == first_data
    stop_commands = [h["command"] for group in second_data["hooks"]["Stop"] for h in group["hooks"]]
    assert stop_commands.count(_EXPECTED_STOP_COMMAND) == 1


def test_settings_json_malformed_existing_json_is_refused_not_guessed(tmp_path: Path):
    settings_path = tmp_path / DEFAULT_SETTINGS_JSON_RELPATH
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text("{ not valid json", encoding="utf-8")

    result = run_init(tmp_path, intent_summary=_INTENT, test_command=_TEST_CMD)

    assert result.settings_json.action == "refused"
    assert "not valid JSON" in result.settings_json.reason
    assert settings_path.read_text(encoding="utf-8") == "{ not valid json"


def test_settings_json_non_object_top_level_is_refused(tmp_path: Path):
    settings_path = tmp_path / DEFAULT_SETTINGS_JSON_RELPATH
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text("[1, 2, 3]", encoding="utf-8")

    result = run_init(tmp_path, intent_summary=_INTENT, test_command=_TEST_CMD)

    assert result.settings_json.action == "refused"


def test_settings_json_non_array_hook_event_is_refused(tmp_path: Path):
    settings_path = tmp_path / DEFAULT_SETTINGS_JSON_RELPATH
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({"hooks": {"Stop": "not-an-array"}}), encoding="utf-8")

    result = run_init(tmp_path, intent_summary=_INTENT, test_command=_TEST_CMD)

    assert result.settings_json.action == "refused"
    assert "hooks.Stop" in result.settings_json.reason


# --- Finding 1 (Session 010): stale/pre-fix interpreter commands are updated in place --


def test_settings_json_upgrades_a_pre_fix_bare_python_command_in_place(tmp_path: Path):
    """A settings.json written by a pre-Session-010 build of `init` (bare "python",
    the Gate C blocker) is recognized as ShipGate's own entry by command *suffix* and
    corrected in place on the next `init` run — not silently duplicated, not silently
    left broken."""
    settings_path = tmp_path / DEFAULT_SETTINGS_JSON_RELPATH
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [
                        {"matcher": "*", "hooks": [{"type": "command", "command": "python -m shipgate.hooks.stop"}]}
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    result = run_init(tmp_path, intent_summary=_INTENT, test_command=_TEST_CMD)

    assert result.settings_json.action == "merged"
    assert "updated" in result.settings_json.reason
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    stop_handlers = [h for group in data["hooks"]["Stop"] for h in group["hooks"]]
    assert len(stop_handlers) == 1  # corrected in place, not duplicated
    assert stop_handlers[0]["command"] == _EXPECTED_STOP_COMMAND


def test_settings_json_upgrades_a_stale_absolute_interpreter_path_in_place(tmp_path: Path):
    """A moved/deleted venv scenario: an absolute path from a *different* interpreter
    than the one running `init` now. Still recognized as ShipGate's own entry (by
    suffix) and corrected in place — simply re-running `shipgate init` self-heals this,
    per the module docstring's stated residual-gap note."""
    settings_path = tmp_path / DEFAULT_SETTINGS_JSON_RELPATH
    settings_path.parent.mkdir(parents=True)
    stale_command = r"C:\old\deleted\venv\Scripts\python.exe -m shipgate.hooks.pretooluse"
    settings_path.write_text(
        json.dumps(
            {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": stale_command}]}]}}
        ),
        encoding="utf-8",
    )

    result = run_init(tmp_path, intent_summary=_INTENT, test_command=_TEST_CMD)

    assert result.settings_json.action == "merged"
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    pretool_handlers = [h for group in data["hooks"]["PreToolUse"] for h in group["hooks"]]
    assert len(pretool_handlers) == 1
    assert pretool_handlers[0]["command"] == _EXPECTED_PRETOOLUSE_COMMAND


def test_settings_json_does_not_touch_an_unrelated_hook_with_a_similar_looking_command(tmp_path: Path):
    """A user's own, unrelated command that happens to also invoke "-m shipgate.hooks.
    stop" (contrived, but the suffix-match logic must not be fooled by substring
    presence outside the actual command) is not what this guards against directly --
    what it guards is that ShipGate never touches a command that doesn't end with its
    own suffix. This test pins that boundary."""
    settings_path = tmp_path / DEFAULT_SETTINGS_JSON_RELPATH
    settings_path.parent.mkdir(parents=True)
    unrelated_command = "python -m shipgate.hooks.stop --extra-flag-that-changes-the-suffix"
    settings_path.write_text(
        json.dumps(
            {"hooks": {"Stop": [{"matcher": "*", "hooks": [{"type": "command", "command": unrelated_command}]}]}}
        ),
        encoding="utf-8",
    )

    result = run_init(tmp_path, intent_summary=_INTENT, test_command=_TEST_CMD)

    assert result.settings_json.action == "merged"
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    stop_handlers = [h["command"] for group in data["hooks"]["Stop"] for h in group["hooks"]]
    assert unrelated_command in stop_handlers  # untouched
    assert _EXPECTED_STOP_COMMAND in stop_handlers  # ShipGate's own added alongside it
    assert len(stop_handlers) == 2

"""`shipgate init` (task 3.1) — the Discipline Generator's
minimal-interview version. Writes `shipfile.yaml`, appends to (or creates) `CLAUDE.md`,
and merges into (or creates) `.claude/settings.json`.

**Founder review finding, resolved before this module was written (Session 009 plan
review), not discovered as a bug afterward:** `shipgate init` is the first time ShipGate
writes files a user didn't ask for one by one, into a project that may already have a
`CLAUDE.md` and a `.claude/settings.json` with the user's own hooks and permissions in
them. Clobbering either is destructive and hard to reverse, and
breaking someone's existing hook config is, in the founder's own framing, "the
reputational equivalent of a false block." **The rule this module is built around: never
overwrite, never silently replace — merge additively, or refuse and say exactly what was
found and what would have been written.**

Per-file policy, and why each is different:

- **`shipfile.yaml` — always refuse if one already exists.** A shipfile is a frozen
  public interface (Gate A); regenerating over a real one is a decision for the
  project's own author, never a default `init` takes for them. No flag bypasses this —
  delete or rename the existing file yourself to get a fresh one.
- **`CLAUDE.md` — write whole if absent; otherwise append/replace only ShipGate's own
  delimited block, never touch anything outside it.** Re-running `init` is idempotent:
  it replaces the content between `<!-- SHIPGATE:BEGIN -->` / `<!-- SHIPGATE:END -->`
  with the current template, and nothing else in the file moves. If those markers exist
  in a broken shape (only one of the pair, wrong order, or more than one pair — someone
  hand-edited around them) this refuses rather than guessing where ShipGate's block
  should go.
- **`.claude/settings.json` — additive JSON merge, with in-place self-repair.**
  `hooks.<Event>` arrays gain ShipGate's own matcher-group entry if it isn't already
  present (Claude Code hook arrays support multiple independent handlers per event —
  adding one is never a conflict with an existing, unrelated hook; see the primary
  source cited in `templates.py`). ShipGate's own handler is recognized by its command's
  *suffix* (`templates.hook_command_suffix`), not the full string — so if the command
  points at a stale interpreter path (a moved venv, or an entry from before Session 010
  fixed the bare `"python"` Gate C blocker below), it's corrected **in place**, never
  silently duplicated and never silently left broken. Every other top-level key, and
  every other unrelated hook entry, is preserved byte-for-byte. Refuses only when the
  merge genuinely can't be done safely: the existing file isn't valid JSON, isn't a JSON
  object, or has a `hooks.<Event>` value that isn't the array shape Claude Code's own
  schema expects.

Every outcome — written, merged, unchanged (idempotent re-run), or refused — is returned
as a `FileOutcome` with a `reason` string. Nothing here is a silent success or a silent
no-op; the CLI layer (`shipgate/cli.py`) prints every one of them.

Zero Claude-Code-specific *behavior* (no live process talked to); this package is not
one of the four core-purity-restricted packages (`ledger`/`gate`/`verdicts`/`shipfile`),
so it's free to know about `.claude/settings.json`'s shape the way `shipgate/hooks/`
already knows about Claude Code's hook stdin shape.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .templates import (
    CLAUDE_MD_BEGIN_MARKER,
    CLAUDE_MD_END_MARKER,
    build_hooks_fragment,
    hook_command_suffix,
    render_shipfile,
    wrapped_claude_md_block,
)

DEFAULT_SHIPFILE_FILENAME = "shipfile.yaml"
DEFAULT_CLAUDE_MD_FILENAME = "CLAUDE.md"
DEFAULT_SETTINGS_JSON_RELPATH = Path(".claude") / "settings.json"

_HOOK_EVENTS = ("PreToolUse", "PostToolUse", "Stop")
#: Kept in sync with `templates.build_hooks_fragment`'s own event->module wiring by
#: construction — both are the three entrypoints task 2.1 built, which are frozen.
_HOOK_EVENT_MODULES: dict[str, str] = {
    "PreToolUse": "pretooluse",
    "PostToolUse": "posttooluse",
    "Stop": "stop",
}

Action = Literal["written", "merged", "unchanged", "refused"]


@dataclass(frozen=True)
class FileOutcome:
    """`reason` is always populated — the standing rule (`shipgate.gate.orchestrator`
    design decision 3, restated by the founder for every task built since): if the code
    knows something the user would want to know, the return value carries it."""

    path: str
    action: Action
    reason: str


@dataclass(frozen=True)
class InitResult:
    shipfile: FileOutcome
    claude_md: FileOutcome
    settings_json: FileOutcome

    @property
    def any_refused(self) -> bool:
        """Any of the three files was refused, for any reason — the raw fact. See
        `needs_user_attention` for the subset that's actually worth a non-zero exit."""
        return any(o.action == "refused" for o in (self.shipfile, self.claude_md, self.settings_json))

    @property
    def needs_user_attention(self) -> bool:
        """**Founder review finding, Session 011:** `shipfile.yaml` being refused
        because one already exists is `init`'s own by-design, safe default — not a
        problem the user needs to fix, and the single most common reason `init` gets
        re-run at all: to repair `.claude/settings.json`/`CLAUDE.md` (e.g. after a venv
        moved, Finding 1) without ever touching a real shipfile. Reporting that the same
        way as a genuinely broken `CLAUDE.md` marker pair or a malformed `settings.json`
        — which DO need the user's attention — turned an ordinary, successful self-heal
        into a reported failure. `True` only when `claude_md` or `settings_json` was
        refused: the two files whose only refusal reasons are real problems (malformed
        JSON, broken markers, an unexpected shape), never "a file already exists, as
        designed." This assumes `_write_shipfile`'s only refusal reason stays "already
        exists" — stated explicitly so a future second shipfile-refusal reason doesn't
        silently fall through this exemption unnoticed."""
        return self.claude_md.action == "refused" or self.settings_json.action == "refused"


def _write_shipfile(project_root: Path, *, intent_summary: str, test_command: str) -> FileOutcome:
    path = project_root / DEFAULT_SHIPFILE_FILENAME
    if path.exists():
        return FileOutcome(
            path=str(path),
            action="refused",
            reason=(
                f"a shipfile already exists at {path} — shipgate init never overwrites or "
                "merges a shipfile (it's a frozen, structurally significant contract, not a "
                "template). Delete or rename it yourself if you want a freshly generated one."
            ),
        )
    content = render_shipfile(intent_summary=intent_summary, test_command=test_command)
    path.write_text(content, encoding="utf-8")
    return FileOutcome(path=str(path), action="written", reason=f"wrote a new shipfile to {path}")


def _merge_claude_md(project_root: Path) -> FileOutcome:
    path = project_root / DEFAULT_CLAUDE_MD_FILENAME
    block = wrapped_claude_md_block()

    if not path.exists():
        path.write_text(block + "\n", encoding="utf-8")
        return FileOutcome(path=str(path), action="written", reason=f"wrote a new {path.name}")

    text = path.read_text(encoding="utf-8")
    begin_count = text.count(CLAUDE_MD_BEGIN_MARKER)
    end_count = text.count(CLAUDE_MD_END_MARKER)

    if begin_count == 0 and end_count == 0:
        separator = "\n\n" if text and not text.endswith("\n\n") else ""
        path.write_text(text + separator + block + "\n", encoding="utf-8")
        return FileOutcome(
            path=str(path),
            action="merged",
            reason=f"appended ShipGate's block to the existing {path.name} — nothing else in the file was touched",
        )

    if begin_count != 1 or end_count != 1:
        return FileOutcome(
            path=str(path),
            action="refused",
            reason=(
                f"{path.name} has {begin_count} {CLAUDE_MD_BEGIN_MARKER!r} marker(s) and "
                f"{end_count} {CLAUDE_MD_END_MARKER!r} marker(s) — expected exactly one of each. "
                "Refusing to guess how to update it; remove the markers by hand and re-run "
                "shipgate init to get a clean block, or edit the existing block directly."
            ),
        )

    begin_idx = text.index(CLAUDE_MD_BEGIN_MARKER)
    end_idx = text.index(CLAUDE_MD_END_MARKER) + len(CLAUDE_MD_END_MARKER)
    if end_idx <= begin_idx:
        return FileOutcome(
            path=str(path),
            action="refused",
            reason=(
                f"{path.name}'s {CLAUDE_MD_END_MARKER!r} marker appears before its "
                f"{CLAUDE_MD_BEGIN_MARKER!r} marker — refusing to guess how to update it; "
                "fix the marker order by hand and re-run shipgate init."
            ),
        )

    existing_block = text[begin_idx:end_idx]
    if existing_block == block:
        return FileOutcome(
            path=str(path),
            action="unchanged",
            reason=f"{path.name}'s ShipGate block already matches this version — nothing to update",
        )

    new_text = text[:begin_idx] + block + text[end_idx:]
    path.write_text(new_text, encoding="utf-8")
    return FileOutcome(
        path=str(path),
        action="merged",
        reason=f"replaced ShipGate's own block in {path.name} in place — everything outside the markers was untouched",
    )


def _merge_settings_json(project_root: Path) -> FileOutcome:
    path = project_root / DEFAULT_SETTINGS_JSON_RELPATH
    fragment = build_hooks_fragment()

    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(fragment, indent=2) + "\n", encoding="utf-8")
        return FileOutcome(path=str(path), action="written", reason=f"wrote a new {path}")

    raw_text = path.read_text(encoding="utf-8")
    try:
        existing = json.loads(raw_text) if raw_text.strip() else {}
    except json.JSONDecodeError as exc:
        return FileOutcome(
            path=str(path),
            action="refused",
            reason=(
                f"{path} is not valid JSON ({exc}) — refusing to guess how to merge into it. "
                "Fix the file (or move it aside) and re-run shipgate init."
            ),
        )
    if not isinstance(existing, dict):
        return FileOutcome(
            path=str(path),
            action="refused",
            reason=(
                f"{path}'s top level is a {type(existing).__name__}, not a JSON object — "
                "refusing to guess how to merge ShipGate's hooks into it."
            ),
        )

    hooks_block = existing.setdefault("hooks", {})
    if not isinstance(hooks_block, dict):
        return FileOutcome(
            path=str(path),
            action="refused",
            reason=(
                f"{path}'s existing \"hooks\" key is a {type(hooks_block).__name__}, not an "
                "object — refusing to guess how to merge ShipGate's hooks into it."
            ),
        )

    added: list[str] = []
    updated: list[str] = []
    for event in _HOOK_EVENTS:
        wanted_entry = fragment["hooks"][event][0]
        wanted_command = wanted_entry["hooks"][0]["command"]
        module = _HOOK_EVENT_MODULES[event]
        suffix = hook_command_suffix(module)
        groups = hooks_block.setdefault(event, [])
        if not isinstance(groups, list):
            return FileOutcome(
                path=str(path),
                action="refused",
                reason=(
                    f"{path}'s existing \"hooks.{event}\" value is a {type(groups).__name__}, "
                    "not an array — refusing to guess how to merge ShipGate's own hook into it."
                ),
            )

        # Find ShipGate's own handler for this event, if one already exists — matched by
        # the interpreter-independent command *suffix*, not the full command string, so
        # a stale interpreter path (a moved venv, or an entry from a pre-Session-010
        # build that emitted the bare string "python") is recognized as ShipGate's own
        # and updated in place, never silently duplicated or silently left broken.
        own_handler: dict | None = None
        for group in groups:
            if not isinstance(group, dict):
                continue
            for handler in group.get("hooks", []):
                if isinstance(handler, dict) and isinstance(handler.get("command"), str) and handler[
                    "command"
                ].endswith(suffix):
                    own_handler = handler
                    break
            if own_handler is not None:
                break

        if own_handler is None:
            groups.append(wanted_entry)
            added.append(event)
        elif own_handler.get("command") != wanted_command:
            own_handler["command"] = wanted_command
            updated.append(event)

    if not added and not updated:
        return FileOutcome(
            path=str(path),
            action="unchanged",
            reason=f"{path} already has all of ShipGate's hook entries pointing at the current interpreter — nothing to change",
        )

    path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    parts = []
    if added:
        parts.append(f"added {', '.join(added)}")
    if updated:
        parts.append(
            f"updated {', '.join(updated)} to the current interpreter path "
            "(a stale or pre-fix command was found and corrected in place)"
        )
    return FileOutcome(
        path=str(path),
        action="merged",
        reason=(
            f"{'; '.join(parts)} in {path} — every other key and every other hook entry "
            "was left exactly as found"
        ),
    )


def run_init(project_root: Path, *, intent_summary: str, test_command: str) -> InitResult:
    """The one entrypoint `shipgate/cli.py`'s `init` command calls. Each of the three
    files is handled independently — a refusal on one (e.g. an existing shipfile) does
    not stop the other two from being written or merged; `InitResult.any_refused` tells
    the caller whether to exit non-zero."""
    return InitResult(
        shipfile=_write_shipfile(project_root, intent_summary=intent_summary, test_command=test_command),
        claude_md=_merge_claude_md(project_root),
        settings_json=_merge_settings_json(project_root),
    )


__all__ = [
    "DEFAULT_CLAUDE_MD_FILENAME",
    "DEFAULT_SETTINGS_JSON_RELPATH",
    "DEFAULT_SHIPFILE_FILENAME",
    "FileOutcome",
    "InitResult",
    "run_init",
]

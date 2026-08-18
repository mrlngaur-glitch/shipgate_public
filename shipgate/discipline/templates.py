"""Templates emitted by `shipgate init` (task 3.1) — the
"minimal interview" version of the report's Discipline Generator. The **full**
Discipline Generator (a richer interview producing a fuller shipfile, a charter, and a
signed hook catalog) is explicitly out of scope here — targeted for F3. This module
produces three things:

1. A minimal, valid shipfile (`render_shipfile`) — enough `done_conditions` to gate on
   something real (the interview's own test command) plus a `high_risk_change` task
   class so `shipgate declare-task-class` has something to point at immediately, without
   the user hand-writing a `task_classes` block first.
2. A `CLAUDE.md` block (`CLAUDE_MD_BLOCK`) instructing the agent to self-declare a task
   class before high-risk work — Gate B condition 5's wiring: the agent declares,
   ShipGate doesn't infer.
3. The `.claude/settings.json` hooks fragment (`build_hooks_fragment`) wiring
   `PreToolUse`/`PostToolUse`/`Stop` to this project's own hook entrypoints.

**Settings.json structure verified against the primary source before writing this**
(not assumed from memory — the same discipline already applied to the
600s hook-timeout and 8-block-override figures): https://code.claude.com/docs/en/hooks.md,
"Full JSON Structure Example". Quoted shape: `{"hooks": {"<Event>": [{"matcher": "*",
"hooks": [{"type": "command", "command": "..."}]}]}}`. `matcher: "*"` is used for all
three events, matching that example's own Stop entry (the doc separately notes Stop
"has no matcher support" but its own worked example still includes one, so this follows
the literal example rather than the prose aside).

**Founder review finding, fixed this session (Session 010), not left as a stated
assumption: a bare `"python"` command was a Gate C blocker.** The earlier version of
this module emitted `"command": "python -m shipgate.hooks.<name>"`, relying on `python`
resolving to the right interpreter on `PATH` when Claude Code spawns the hook subprocess.
Every hook entrypoint catches its own exceptions and exits 0 by design (`shipgate/
hooks/__init__.py`'s own fail-open contract) — so a wrong `python` doesn't crash
visibly; it silently imports nothing, writes no session row, writes no events, and the
Stop hook can never gate anything. **The user's only evidence ShipGate is working is
the absence of a problem, which is exactly what a dead hook also looks like** — the same
failure shape as this project's own Session 006 `ModuleNotFoundError` packaging bug,
moved from this machine (where it was caught) to a user's (where nobody is positioned to
diagnose it). Fixed: `build_hooks_fragment` now emits `sys.executable` — the absolute
path to the interpreter `shipgate init` is actually running under — never the bare
string `"python"`.

**Stated residual gap, not silently assumed covered by the fix:** the emitted path is
correct at the moment `init` runs. If the venv is later moved, deleted, or recreated at
a different path, that absolute path silently stops resolving and the hook fails open
with zero visible signal — the identical failure shape, one layer up. `init.py`'s merge
logic (see its module docstring) detects and *updates* its own hook entry in place on
every re-run — so simply re-running `shipgate init` after moving a venv self-heals this
— but nothing currently detects the stale state *between* those two events. Parked as a
`shipgate doctor` enhancement, parked, rather than built here or silently
left unstated.

Zero Claude-Code-specific imports in the sense of *behavior* (this module never talks to
a live Claude Code process) — it does emit Claude-Code-specific *config text*, which is
exactly `shipgate/discipline/`'s job per report §13.1's layout; this package is not one
of the four core-purity-restricted packages (`ledger`/`gate`/`verdicts`/`shipfile`).
"""

from __future__ import annotations

import sys

import yaml

#: Delimits ShipGate's own block inside a user's `CLAUDE.md` so `shipgate init` can be
#: re-run without ever touching anything the user wrote themselves outside these lines —
#: see `shipgate/discipline/init.py`'s merge logic and its module docstring.
CLAUDE_MD_BEGIN_MARKER = "<!-- SHIPGATE:BEGIN -->"
CLAUDE_MD_END_MARKER = "<!-- SHIPGATE:END -->"

CLAUDE_MD_BLOCK = """## ShipGate — the independent completion gate

This project is gated by ShipGate. `shipfile.yaml` declares what "done" means; ShipGate's
`Stop` hook checks it against real evidence before a session is allowed to end — it does
not trust a self-reported "done."

**Before starting work in a task class whose `risk_tier` is `high`** (see this project's
`shipfile.yaml`, `task_classes` block — `high_risk_change` is the one `shipgate init`
created for you; rename it or add more to match this project), declare it first:

    shipgate declare-task-class <task_class> "<one-line description of the change>"

This records the change against this session's blast-radius budget
(`session_policy.max_high_risk_changes_per_session`). The N+1th high-risk change in one
session is refused pending a logged override:

    shipgate declare-task-class <task_class> "<description>" --override-reason "<why>"

**This is a self-declaration, not a detector.** ShipGate has no way to see an undeclared
high-risk change — its own reports always state the declared count as *self-declared
only*, never as a verified clean pass, so zero declarations reads as "nothing was
declared," never as "nothing risky happened."

Never edit `shipfile.yaml`'s `task_classes`, `done_conditions`, `gate_policy`, or
`session_policy` blocks to make a failing gate pass. Fix the underlying work instead."""


def wrapped_claude_md_block() -> str:
    """`CLAUDE_MD_BLOCK` between its markers — the exact text `init.py` writes fresh or
    replaces in place on re-run. A function, not a module constant, so the markers and
    the block body can never drift out of sync with each other."""
    return f"{CLAUDE_MD_BEGIN_MARKER}\n{CLAUDE_MD_BLOCK}\n{CLAUDE_MD_END_MARKER}"


def hook_command_suffix(module: str) -> str:
    """The trailing, interpreter-independent part of a ShipGate hook command —
    `"-m shipgate.hooks.<module>"`. `init.py`'s merge logic matches on this suffix (not
    the full command string) to recognize its own entry regardless of which interpreter
    path wrote it — including an entry written by the pre-Session-010 version of this
    module, which emitted the bare string `"python"` instead of an absolute path. That
    means re-running `shipgate init` after a venv moves, or after upgrading past the
    Session 010 fix, replaces the stale command in place rather than silently
    duplicating it or silently leaving it broken."""
    return f"-m shipgate.hooks.{module}"


def build_hooks_fragment(*, python_executable: str | None = None) -> dict:
    """The `hooks` fragment `init.py` merges into `.claude/settings.json`. One matcher
    group per event, each with exactly one handler — ShipGate's own. Never includes
    anything beyond the `hooks` key; `init.py`'s merge is what protects every other key
    an existing `settings.json` might already have (`model`, `permissions`, other
    hooks, ...).

    `python_executable` defaults to `sys.executable` — the absolute path to the
    interpreter this process is actually running under, never the bare string
    `"python"` (Session 010 fix; see this module's own docstring, "Founder review
    finding"). The parameter exists so a caller (or a test) can supply a different
    interpreter path explicitly rather than this function silently reading global
    process state whenever that's not what's wanted."""
    python_executable = python_executable if python_executable is not None else sys.executable

    def _entry(module: str) -> dict:
        return {
            "matcher": "*",
            "hooks": [{"type": "command", "command": f"{python_executable} {hook_command_suffix(module)}"}],
        }

    return {
        "hooks": {
            "PreToolUse": [_entry("pretooluse")],
            "PostToolUse": [_entry("posttooluse")],
            "Stop": [_entry("stop")],
        }
    }


_SHIPFILE_HEADER = """# Generated by `shipgate init` -- the minimal interview, not the full Discipline
# Generator (a richer interview producing a fuller shipfile, a charter, and a signed
# hook catalog -- a fast-follow release, not built yet). Edit this file directly to
# add more done_conditions, task classes, or policy; see the ShipGate repo's
# docs/shipfile_worked_example.yaml for a fuller worked example of every block.
"""


def render_shipfile(*, intent_summary: str, test_command: str) -> str:
    """Builds the whole document via `yaml.safe_dump` — not hand-formatted string
    interpolation — specifically so arbitrary interview answers (a colon, a quote, a
    leading dash) can never produce a shipfile with broken YAML syntax. The static
    header comment is prepended as plain text since it isn't part of the data and
    `yaml.safe_dump` would strip it anyway."""
    doc = {
        "shipfile_version": "0.1",
        "task_classes": {
            "feature": {
                "risk_tier": "medium",
                "starting_model_tier": "mid",
                "max_tokens": 100000,
                "gate_strictness": "strict",
            },
            "high_risk_change": {
                "risk_tier": "high",
                "starting_model_tier": "frontier",
                "max_tokens": 200000,
                "gate_strictness": "strict",
            },
        },
        "done_conditions": [
            {
                "id": "tests-pass",
                "type": "tests_pass",
                "command": test_command,
                "description": "The test suite given to `shipgate init` actually runs and passes.",
            },
        ],
        "routing": {},
        "budgets": {},
        "context_policy": {},
        "intent": {"summary": intent_summary},
        "gate_policy": {"max_retries": 3},
        "session_policy": {"max_high_risk_changes_per_session": 3},
    }
    return _SHIPFILE_HEADER + yaml.safe_dump(doc, sort_keys=False, default_flow_style=False)


__all__ = [
    "CLAUDE_MD_BEGIN_MARKER",
    "CLAUDE_MD_BLOCK",
    "CLAUDE_MD_END_MARKER",
    "build_hooks_fragment",
    "hook_command_suffix",
    "render_shipfile",
    "wrapped_claude_md_block",
]

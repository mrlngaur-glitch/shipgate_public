# ShipGate

**The independent completion gate for AI coding agents — the agent doesn't get to grade its own homework.**

> **Status: pre-launch, Phase 3 substantially built.** The gate, the append-only ledger, the
> hooks that write to it, and the CLI below (`init` / `status` / `report` / `doctor` /
> `declare-task-class`) are real, tested (393 tests, shown running below), and this project
> gates its own repository with them — see `shipfile.yaml` at the repo root. What is **not**
> true yet, stated plainly rather than implied: this repository is private, not public; a
> GitHub remote exists and three real pushes have triggered CI, but every one failed to
> start (zero jobs, no logs — account-level, unrelated to this codebase, see `SECURITY.md`),
> so CI has still never executed a single step against it; there is no PyPI package, no
> tagged release, no signed artifact, no `shipgate analyze` command (so no dollar-cost
> figures), and no published latency benchmark. Install from source, as shown below — that
> is the only way to run this today.

ShipGate converts rough plain-English requests into machine-checkable contracts, and refuses to
accept an agent's work until its claims are verified against recorded evidence **by a party that
is not the agent**.

## Quickstart

Requires Python 3.12. There is no package on PyPI yet — install from a clone.

**Windows (PowerShell) — runtime-verified this session, output below:**

```powershell
git clone <this-repo-url>
cd shipgate
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.lock
.venv\Scripts\python.exe -m pip install -e . --no-deps
```

**macOS / Linux (bash) — the standard equivalent of the same steps. Not independently run on
either OS by this project: there is no CI history yet (`SECURITY.md`) and no macOS/Linux dev
machine behind this repository today. If one of these breaks, that is new information — say so,
don't assume it works because the commands look right:**

```bash
git clone <this-repo-url>
cd shipgate
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install --require-hashes -r requirements.lock
pip install -e . --no-deps
```

Both forms are the same three steps CI runs (`.github/workflows/ci.yml`, "Install
(pinned, hash-verified)") — matched at the level of *what happens* (upgrade pip, install the
hash-verified pinned set, install this package editable with no dependency resolution), not at
the level of literal shell text: CI installs into a bare runner Python with no venv step at all,
and the Windows/macOS forms above differ from each other in the interpreter invocation and the
venv's internal path (`Scripts\` vs. `bin/`). `--require-hashes` refuses to install anything
whose downloaded file doesn't match a hash already recorded in `requirements.lock` — see that
file's own header for exactly which platforms are hash-covered versus which have actually been
run, and `SECURITY.md` for the same distinction stated in full. Do **not** use
`pip install -e ".[dev]"` as a single command — it resolves dependency versions against live PyPI
ranges instead of the pinned, hashed lock file, and has been observed to drift (`Pygments`
2.21.0 resolved against this lock file's pinned 2.20.0, 2026-08-17).

Then run the suite, the same way CI does:

```
pytest
lint-imports        # core purity: no harness-specific imports in the gate/ledger core
ruff check .
```

**Real output, this session, fresh venv, a genuine re-materialization of the exact 82-file
published set (not the working tree) in a clean temp directory — not hand-edited when the
suite grew since the last paste (Windows; command as shown above):**

```
$ .venv\Scripts\python.exe -m pytest -q
........................................................................ [ 18%]
........................................................................ [ 36%]
........................................................................ [ 54%]
........................................................................ [ 73%]
........................................................................ [ 91%]
.................................                                        [100%]
393 passed in 39.97s

$ .venv\Scripts\lint-imports.exe
=============
Import Linter
=============


---------
Contracts
---------

Analyzed 41 files, 58 dependencies.
-----------------------------------

Core is harness-agnostic (no Claude Code imports in
ledger/gate/verdicts/shipfile) KEPT

Contracts: 1 kept, 0 broken.
```

## See it work

`shipgate init` is the first command a real user runs — a minimal interview that writes
`shipfile.yaml` (your project's own machine-checkable done-conditions), a `CLAUDE.md` your coding
agent reads, and `.claude/settings.json` wiring the hooks below into Claude Code. Never
overwrites an existing file — merges additively, or refuses and says exactly what it found.

```
$ shipgate init --project-dir . --intent-summary "..." --test-command "pytest -q"
[+] shipfile.yaml: written — wrote a new shipfile to <project>
[+] CLAUDE.md: written — wrote a new CLAUDE.md
[+] .claude/settings.json: written — wrote a new <project>/.claude/settings.json

Done. Review the generated files, then start your agent session normally.
```

From there, `shipgate` is not something you run by hand — the hooks `init` just wired fire on
their own, on every tool call and at the end of every Claude Code session, and write to a local,
append-only, hash-chained ledger (`.shipgate/ledger.db`, git-ignored). What follows is that real
mechanism, driven directly rather than through a live Claude Code session (which a README can't
reproduce) — the exact three hook entrypoints Claude Code invokes
(`python -m shipgate.hooks.pretooluse` / `.posttooluse` / `.stop`), fed the same JSON shape on
stdin Claude Code feeds them, run as real subprocesses against a fresh demo project with one
`tests_pass` done-condition (`pytest -q` against one real passing test). Anyone can reproduce
this exactly — the commands are the ones above, run in order.

`shipgate status` — quick, plain, closer to `git status`:

```
$ shipgate status --project-dir .
[verified] tests-pass: 1 passed, 1 collected (command: 'pytest -q')

GATE: GREEN
```

`shipgate report` — the full, screenshot-able Ship Report:

```
$ shipgate report --project-dir .
┌─────────────────────────────────────────────────────────────────────────────┐
│                                 GATE: GREEN                                 │
└─────────────────────────────────────────────────────────────────────────────┘

                                    Claims
┌───────────────────────┬──────────┬──────────────────┬───────────────────────┐
│ Claim                 │ Verdict  │ Tier             │ Reason                │
├───────────────────────┼──────────┼──────────────────┼───────────────────────┤
│ The test suite given  │ verified │ runtime-verified │ 1 passed, 1 collected │
│ to `shipgate init`    │          │                  │ (command: 'pytest     │
│ actually runs and     │          │                  │ -q')                  │
│ passes.               │          │                  │                       │
└───────────────────────┴──────────┴──────────────────┴───────────────────────┘

Blast radius: 0 high-risk change(s) self-declared this session (self-declared
only — ShipGate cannot detect an undeclared one).
Tokens this session: 0 in / 0 out / 0 cache-read (dollar cost requires shipgate
analyze's price tables, not yet built).

Ledger receipt (verdicts): #1 (029062d5f84e…) through #1 (029062d5f84e…) — this
project's entire claim history.
--verify: VERIFIED — entire ledger hash chain (events, claims, verdicts)
intact; the embedded verdicts range matches the ledger exactly, unchanged
```

`--verify` (on by default) independently recomputes and checks the whole ledger's hash chain
before rendering the banner above it — a tampered row renders `GATE: INCONSISTENT — do not trust
this report` and names the disagreeing claim, not a silently vouched-for green (the taxonomy
behind `verified`/`unverified`/`unverified-vacuous`/the other four verdict classes is explained
in `docs/verdicts_explainer.md`).

The token line above reads zero because this demo drove the hooks directly rather than through a
real, billed Claude Code session — a real session's real token counts populate that line the same
way. The dollar-cost gap next to it is real, not a placeholder: `shipgate analyze`'s price tables
don't exist yet, so the report shows a true number (tokens) and says plainly what it can't yet
compute, rather than inventing a figure or dropping the line silently.

## What's built, what isn't — every claim above, one evidence class each

| # | Claim | Evidence class | Basis |
|---|---|---|---|
| 1 | The Windows install (three `pip` commands above) works end-to-end | `runtime-verified` | Run in a genuinely fresh venv this session; `pytest`/`lint-imports` output pasted above, unedited |
| 2 | The macOS/Linux install works the same way | `disk-verified` | Standard `venv`/`pip` syntax for those platforms; not independently run — no CI history yet, no macOS/Linux machine behind this repo |
| 3 | 393 tests pass | `runtime-verified` | Pasted above, this session |
| 4 | `shipgate init` writes `shipfile.yaml` / `CLAUDE.md` / `.claude/settings.json`, never overwrites | `runtime-verified` | Real run, this session, pasted above; the never-overwrite behavior is separately tested (`tests/`) |
| 5 | The hooks write real ledger rows via the same entrypoints Claude Code invokes | `runtime-verified` | Real subprocess run of all three hook modules this session, JSON on stdin, feeding the `status`/`report` output above |
| 6 | `shipgate report` renders a verdict per claim, a blast-radius line, a token line, and a self-verifying ledger receipt | `runtime-verified` | Pasted above, unedited, this session |
| 7 | No hook makes a network call | `runtime-verified` locally / untested in CI | `tests/integration/test_hooks_e2e.py` passes locally; CI has never run — see `SECURITY.md` |
| 8 | Dependencies are pinned and hash-verified for the dev/CI install | `runtime-verified` (Windows) / `disk-verified` (other hash-covered platforms) | `requirements.lock`'s own header states exactly which platforms are hash-covered vs. tested; `SECURITY.md` states the same split for the published-package install path |
| 9 | This project gates its own repository under the rules it ships | `disk-verified` | `shipfile.yaml` at the repo root, wired live into this repo's own (git-ignored) `.claude/settings.json` |
| 10 | A published, CI-measured <10ms p99 hook-latency benchmark exists | **Does not exist** | Named as a gap, not implied or omitted |
| 11 | A dollar-cost figure appears on the Ship Report | **Does not exist** | `shipgate analyze`'s price tables aren't built; the report states this inline (see above) rather than inventing a number |
| 12 | This project has run in CI, has a tagged release, or is on PyPI | **Does not exist yet** | Pre-launch; install from source only, as shown above |

## Repository map

| Path | What it is |
|---|---|
| `shipgate/` | The core package — ledger, gate, verdict taxonomy, checkers, hooks, CLI |
| `tests/` | 393 tests (unit + integration) — the real evidence behind every `runtime-verified` row above |
| `reporters/` | Per-test-runner reporters (`pytest` today; the vacuous-pass detection `tests_pass` relies on) |
| `docs/verdicts_explainer.md` | The 7-class verdict taxonomy, plain-language, frozen since Gate A |
| `docs/shipfile_worked_example.yaml` (+ `.md`) | A fuller worked `shipfile.yaml` than `shipgate init` generates |
| `docs/ledger_schema_design.md` | How the append-only, hash-chained ledger is structured |
| `docs/jsonl_format_notes.md` | Notes on the Claude Code transcript format the hooks read |
| `shipfile.yaml` | This repo's own shipfile — ShipGate gates itself under it |
| `SECURITY.md` | Threat model, what's enforced today and how to verify it yourself, known limitations stated rather than hidden |

## Security

See `SECURITY.md` — how to report a vulnerability, what's actually enforced today versus
disk-verified-but-CI-unconfirmed, and the known limitations this project states about itself
rather than leaves for someone else to find.

## Licence

Apache-2.0. See `LICENSE`.

Built by Laxmi Narayan, FRM — after 100 days auditing an AI coding agent's output on a
long project, and finding it graded its own homework.

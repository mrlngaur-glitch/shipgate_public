"""Claude Code hook entrypoints (task 2.1, Phase 2).

**Scope of this session's build, stated explicitly so nothing here is overclaimed:**
these three entrypoints (`PreToolUse`, `PostToolUse`, `Stop`) fire on a real Claude Code
session and write ledger rows. **None of them blocks anything yet.** The report's own
Week-2 breakdown builds hooks, then checkers, then the retry-cap loop-breaker and session
blast-radius counter, and only once those exist does the Stop hook have anything to
evaluate and a legal way to refuse a stop. Wiring `Stop` up to actually gate — reading
`shipgate.gate`'s checker results and returning `{"decision": "block", "reason": ...}` —
is task 2.6/2.8/2.9's job, not this one's. Claiming otherwise here would be exactly the
kind of `disk-verified`-presented-as-`runtime-verified` overclaim this project forbids of itself.

**This is the one package under `shipgate/` allowed to know about Claude Code.** The
`import-linter` core-purity contract (`pyproject.toml`) forbids `shipgate.ledger`,
`shipgate.gate`, `shipgate.verdicts`, and `shipgate.shipfile` from importing
`shipgate.hooks` — the dependency arrow points one way: hooks call into the core, the
core never calls into hooks. This package is adapter #1, not the architecture.

**Hard rules enforced by construction, not by discipline alone:**

- **No network call from any hook, ever** (task 2.1's own done-condition).
  Every module in this package imports only the Python standard library plus
  `shipgate.ledger` (sqlite3 + filesystem, no sockets). There is nothing here *capable*
  of a network call — verified structurally and at runtime in
  `tests/integration/test_hooks_e2e.py`.
- **Hooks write to `.shipgate/` under the project's own `cwd`, never to
  `~/.claude/projects/`.** The dogfood corpus stays read-only under all circumstances,
  including hook writes — this package never imports `shipgate.ledger.paths` or
  references the corpus root at all; it has no way to reach it.
- **A hook must never crash or hang the user's Claude Code session.** Every entrypoint
  catches its own failures, writes a note to stderr, and exits 0 — a broken hook
  degrading to "observed nothing this time" is a far smaller failure than a broken hook
  breaking someone's actual work. (This posture is specific to *this session's*
  non-blocking scope; once `Stop` needs to legally refuse a stop, fail-open vs.
  fail-closed on an internal hook error becomes its own explicit decision, not
  inherited silently from this one — flagged for that task, not decided here.)
"""

from __future__ import annotations

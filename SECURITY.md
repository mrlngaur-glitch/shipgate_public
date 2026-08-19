# Security Policy

ShipGate is a completion gate for AI coding agents: it runs inside a developer's own
project, reads their code, and writes a local ledger. This project's own standing rule
is the one this file exists to make enforceable: **one supply-chain incident kills
this product permanently.** Everything below is either already built and tested in this
repository, or explicitly marked as not yet built — nothing here is aspirational.

## Reporting a vulnerability

Please report security issues privately rather than opening a public GitHub issue. Open
a [GitHub Security Advisory](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
on this repository — that is the primary channel. **If private vulnerability reporting is
not yet enabled on this repository**, open a public issue asking for a private channel to
be opened — with no vulnerability details in it — and we will follow up privately from
there; this is the stated fallback, not something to guess at. Once a private channel is
open, include a minimal reproduction if possible. We aim to acknowledge a report within 5
business days of a private channel being established.

There is no maintainer email address for this project. Reports sent any other way than
the two channels above will not be monitored.

Do not include real secrets, credentials, or production data in a report — a synthetic,
minimal reproduction is preferred and is usually sufficient to demonstrate the issue.

## Supported versions

ShipGate is pre-1.0 (`0.0.1.dev0`). Until a 1.0 release, only the latest commit on `main`
is supported — there is no backport policy yet. This section will be replaced with a
version table at the first tagged release.

## What's already enforced, and how to verify it yourself

**This note is retired, as it said it would be, the first time CI actually ran.** CI's
first real run ([32182623079](https://github.com/mrlngaur-glitch/shipgate_public/actions/runs/32182623079))
executed every step in `.github/workflows/ci.yml` on a real Ubuntu runner and passed;
its second real run ([32221241397](https://github.com/mrlngaur-glitch/shipgate_public/actions/runs/32221241397))
passed against a raised `MIN_TESTS` floor (397) and confirmed the suite still collects
and passes at that count; its third real run ([32288583970](https://github.com/mrlngaur-glitch/shipgate_public/actions/runs/32288583970))
passed against a further-raised `MIN_TESTS` floor (425), at the same commit as this
project's own last local Windows run, closing the local-vs-CI divergence the earlier two
runs still had. Three earlier pushes had failed to start at all (zero jobs, no
logs) — the cause was an account-level billing lock on the GitHub account this
repository is hosted under, unrelated to this file, `ci.yml`, or any code in this
repository; it has since been resolved, and is why this note existed in the first
place. Every "CI-enforced" / "on every build" claim below is now
re-confirmed against those three real runs, not left as the disk-verified-only status this
note used to require:

- **No network calls from hooks** — both tests named just below ran in CI and passed on
  all three runs.
- **Secrets never reach the ledger** — the full `tests/unit/test_redaction.py` and
  `tests/unit/test_hooks.py` suites (including the dict-key regression) ran in CI and
  passed on all three runs; they are ordinary members of the 425 collected at the latest
  run, not a separate CI step.
- **The ledger is append-only, structurally** — `tests/unit/test_ledger.py`'s
  trigger-refusal tests (`test_update_is_refused_by_trigger_on_every_table`,
  `test_delete_is_refused_by_trigger_on_every_table`) and its hash-chain tests
  (`test_fresh_ledger_chains_verify_clean` and the four `test_tamper_*` cases, one per
  table plus an earlier-row-tampered case) ran in CI and passed on all three runs, same
  basis as above.
- **Dependencies are pinned, hashed, and audited** — the dev/CI set
  (`requirements.lock`) installed with `--require-hashes` on a real Ubuntu runner and
  succeeded on all three runs (closing the "manylinux x86_64... will be tested the first
  time CI actually runs" gap named lower in this section); `pip-audit` ran in CI's own
  "Audit dependencies" step, against a separately hash-pinned audit environment
  (`requirements-audit.lock`), and reported no known vulnerabilities on all three runs —
  this is now a continuous, CI-run check, not only the 2026-08-17 manual pass named below.
  The two GitHub Actions supply-chain pins (`actions/checkout`, `actions/setup-python`)
  are exercised by definition on every CI run, including these three.
- **Core purity is enforced by a real check** — `import-linter`'s "Core purity contract"
  step ran in CI and passed (1 kept, 0 broken) on all three runs, not only locally.

The remaining gap this note used to cover — **macOS and the other hash-covered-but-
untested platforms are still not run anywhere, CI included** (`ubuntu-latest` is CI's
only runner) — is real and unchanged by CI going green; it is stated in
`requirements.lock`'s own header and in `README.md`'s own evidence table, not implied
away here.

**No network calls from hooks, structurally, not by convention.** `PreToolUse`,
`PostToolUse`, and `Stop` — the three hooks Claude Code invokes inside a live coding
session — import nothing network-capable. This is a real test in this repository's own
suite, not a promise (see the evidence note above for its current CI status):
`tests/integration/test_hooks_e2e.py::test_no_hook_module_imports_anything_network_capable`
walks every module under `shipgate/hooks/` and fails the build if any of them import
`socket`, `http`, `urllib`, `requests`, `ssl`, or similar. A second test
(`test_hooks_cannot_open_a_socket_at_runtime`) proves the same thing behaviorally, not
just by import.

**Secrets never reach the ledger, in a value or in a key.** Every payload a hook writes
passes through `shipgate/ledger/redaction.py` before it is hashed or stored — vendor
API-key shapes, PEM private keys, basic-auth URLs, secret-*named* fields regardless of
their value's shape, and — since a Phase 0 spike leaked transcript content this exact
way — a secret used as a dict **key**, not only a value. This has been adversarially
re-opened once already, after founder review found three
confirmed leaks the first version's done-condition never asked it to look for; all three
were fixed at the root, not patched with more shapes, and the widened requirement
("secrets must never reach disk by shape or by field name, verified adversarially") is
a binding decision. The redaction module's own docstring
carries the full incident history. Both the pure redaction function
(`tests/unit/test_redaction.py`) and the real hook → ledger write pipeline
(`tests/unit/test_hooks.py::test_a_secret_in_tool_input_never_reaches_disk` and
`test_a_secret_used_as_a_dict_key_never_reaches_disk`) are tested — the second class of
test exists specifically because a redaction function proven correct in isolation and a
redaction function actually wired into every write path are different claims.

**The ledger is append-only, structurally.** Every one of the five ledger tables has a
`BEFORE UPDATE` and `BEFORE DELETE` trigger that refuses the statement outright — a
correction can only be a new, superseding row, never an edit to history. This is a SQLite
enforcement layer, not just a rule the writer code follows (`shipgate/ledger/schema.py`).
On top of that, every chained table (`events`, `claims`, `verdicts`) is hash-chained;
`shipgate.ledger.integrity.verify_chain` recomputes and checks every row's hash and can
name the exact row if the chain is broken by an out-of-band edit (a raw file write, or a
connection with the triggers disabled). `shipgate report --verify` exposes this to a
user directly — see `shipgate/report/data.py`'s `verify_receipt`.

**Dependencies are pinned, hashed, and audited — but three different sets, and only one is
audited today.** This project resolves dependencies three different ways, and they are not
the same set:

- **The dev/CI environment** — what this project's own tests, lint, and `pip-audit` run
  against — is `requirements.lock`: every runtime and dev dependency pinned to an exact
  version *and* a sha256 hash. Installed with
  `pip install --require-hashes -r requirements.lock`, which refuses to install anything
  whose hash doesn't match — not just a version check, an artifact-substitution check: if
  a pinned version's file on PyPI were ever silently replaced, this install fails loudly
  instead of installing it. This is what `pip-audit` actually audits.
  **Hash-covered and tested-on are two different claims, kept separate** — an earlier
  version of this file's own reasoning conflated them, the same conflation of "pinned"
  and "audited" this whole section exists to avoid: every entry in `requirements.lock` carries
  a hash for Windows amd64, manylinux x86_64, manylinux aarch64, macOS arm64, macOS x86_64,
  and the sdist as a universal fallback — a hash is a fingerprint permitted to install, not
  a claim that platform works. What this project has actually *run* the suite on:
  Windows amd64, in this project's own dev venv, and now manylinux x86_64 too — CI's
  first three real runs ([32182623079](https://github.com/mrlngaur-glitch/shipgate_public/actions/runs/32182623079),
  [32221241397](https://github.com/mrlngaur-glitch/shipgate_public/actions/runs/32221241397),
  [32288583970](https://github.com/mrlngaur-glitch/shipgate_public/actions/runs/32288583970))
  installed and ran the suite on a real Ubuntu (`manylinux`) runner. manylinux aarch64,
  macOS arm64, and macOS x86_64 are still installable but unverified here — see
  `requirements.lock`'s own header for the exact commands used to generate each hash and
  the same claim stated in full.
- **A user installing the published ShipGate package** does not go through
  `requirements.lock` at all — `pip install shipgate` resolves against the version
  *ranges* declared in `pyproject.toml`'s `dependencies` (e.g. `rich>=13.9,<16`), and gets
  whatever version satisfies that range at install time. **This set is not what
  `pip-audit` checks**, and previously (until 2026-08-17) this file said the opposite —
  corrected once that was found. The ranges carry upper bounds
  (added the same pass) so a user's resolver can't drift onto an untested major version
  (or, for the two still-pre-1.0 dependencies, an untested minor) — see the comment above
  `dependencies = [...]` in `pyproject.toml` for the per-package reasoning — but an
  in-range version a user actually resolves to has not been individually audited the way
  the pinned dev/CI set has.
- **The GitHub Actions that execute CI itself** — `actions/checkout` and
  `actions/setup-python`, in `.github/workflows/ci.yml` — are a third supply-chain surface,
  distinct from either dependency set above: they run with the repository's own
  `GITHUB_TOKEN` before any Python dependency is ever installed. **Until 2026-08-18 these
  were unpinned, referenced only by mutable tag (`@v4`, `@v5`)** — a tag that can be
  re-pointed by the action's maintainer (or anyone who compromises that maintainer's
  account) to a different commit at any time, which would make every future CI run on this
  repository execute unreviewed code holding write-scoped repo credentials. This was found
  by founder review, not by CI and not by `pip-audit` — neither tool inspects workflow
  files. Fixed by pinning both to full 40-character commit SHAs, resolved against GitHub's
  own tag API (not guessed or copied from another repository), with the human-readable
  version kept as a trailing comment: `actions/checkout@11d5960a326750d5838078e36cf38b85af677262  # v4.4.0`
  and `actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065  # v5.6.0`. A pinned SHA
  cannot be silently re-pointed the way a tag can; bumping to a newer version is now a
  visible diff, not an invisible drift. This set has no hash-verification analog (GitHub
  Actions has no equivalent of `--require-hashes`) — a SHA pin is the strongest guarantee
  available for it.

`pip-audit` is wired into `.github/workflows/ci.yml` ("Audit dependencies") to run against
`requirements.lock` — the pinned, hashed, dev/CI set — on every build, and now has: CI's
first two real runs both executed this step and both reported **no known
vulnerabilities**. A manual `pip-audit -r requirements.lock` pass on **2026-08-17**,
before CI ever ran, reported the same result against that same pinned set. Either way,
this is a point-in-time result, not a permanent guarantee — it goes stale the moment a
new CVE is published against a pinned version; it is now a check CI actually re-runs on
every build, not only a manual snapshot. Runtime dependencies are kept deliberately
minimal and each addition is a stated decision, not a convenience — see `pyproject.toml`'s
own comment above `dependencies = [...]`.

**Core purity is enforced by a real check, not just documented.** `shipgate/ledger/`,
`shipgate/gate/`, `shipgate/verdicts/`, and `shipgate/shipfile/` are contractually
forbidden from importing anything Claude-Code-specific — checked by `import-linter`,
wired to run on every build (`.github/workflows/ci.yml`, "Core purity contract"; see the
evidence note above for its current CI status). This matters for security as much as
for portability: the core that reads your code and writes your ledger has no dependency
on, and therefore no attack surface shared with, any specific coding-agent harness.

## Known limitations, stated rather than hidden

- **The gate trusts the shipfile absolutely.** An agent that edits `shipfile.yaml`
  mid-session to relax a `done_conditions` entry gets a real, honestly-computed green
  verdict against the relaxed condition — the gate has no integrity check on the
  shipfile itself yet. Founder-reviewed and consciously parked to **F1**, alongside
  ShipGate's own `.shipgate/` state files (a dated decision extends this to cover
  `.shipgate/gate_unavailable.json` and any future sibling state file for the identical
  reason). This is a known, recorded gap, not an oversight — raised here so a security
  reviewer finds it stated, not discovered.
- **`shipgate init`'s hook wiring trusts `sys.executable` at generation time.** If a
  project's virtualenv is later moved or recreated at a different path, the recorded
  interpreter path can stop resolving; `shipgate doctor` does not yet check
  `.claude/settings.json` for this (targeted at F1 alongside the merge
  gate). `shipgate init` self-heals this on any re-run in the meantime.
- **A dollar cost figure is not yet computable.** `shipgate report` shows real token
  counts but not a price, because `shipgate analyze`'s price tables (task 1.6) are not
  yet built — stated inline in the report itself, not silently omitted or invented.

## Reporting scope

In scope: anything that would let a secret reach the ledger unredacted, let a hook make
a network call, let the ledger's append-only or hash-chain guarantees be defeated
through ShipGate's own code (not an admin with raw filesystem access — that threat model
is out of scope, same as for any local-first tool), or let a malicious shipfile/project
achieve remote code execution beyond what the project owner's own `done_conditions`
commands already do (a shipfile is trusted project-owner input, the same trust class as
a Makefile — see `shipgate/gate/checkers.py`'s own module docstring).

Out of scope: the known limitations listed above (already tracked, not silently
accepted); social-engineering reports; denial-of-service reports against a purely
local-first tool with no server component.

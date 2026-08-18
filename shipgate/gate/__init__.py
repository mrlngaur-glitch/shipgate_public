"""The deterministic completion gate (Phase 2) — checkers that turn a
`done_conditions` entry plus real project state into a verdict, never trusting an
agent's own word for it; an orchestrator (task 2.6) that ties them to the ledger and
decides whether `Stop` should block, with flake quarantine built in (task 2.7); and a
session blast-radius counter (task 2.8) that's deliberately separate from both.

Public API so far (tasks 2.2–2.4, 2.6–2.8 — `tests_pass`, `file_exists`,
`forbidden_pattern_absent`, `command_succeeds`, `inventory_complete`, `runtime_evidence`
(`log_marker` method only), each with built-in vacuous-pass detection where that concept
applies — see `checkers.py`'s module docstring for which do and don't, and why):

- `CheckResult` — one checker's verdict, evidence tier, reason, and whether it actually
  observed something, for one `done_conditions` entry.
- `run_checker` — dispatches a condition dict to its checker by `type`, for the five
  checkers whose signature takes only `(condition, project_root, timeout)`.
- `check_inventory_complete` — called directly, not through `run_checker`: it needs an
  extra `claimed_items` argument (the agent's own enumeration) the other checkers don't.
  `evaluate_gate` (below) can now supply that argument itself, from a claims sidecar
  file, when one exists — see `orchestrator.py`'s design decision 6.
- `CHECKERS` — the type -> checker function table for the five uniformly-dispatchable
  checkers.
- `iter_scanned_files`, `IGNORED_DIR_NAMES` — the shared file-walk `forbidden_pattern_
  absent`, `inventory_complete`, and `compute_project_fingerprint` (below) all use.
- `evaluate_gate` — task 2.6, the retry-cap loop-breaker (plus task 2.7's flake
  quarantine, folded in): runs every dispatchable `done_conditions` entry for a session,
  records claims/verdicts, quarantines a flip on unchanged code as `QUARANTINED_FLAKY`
  (advisory, never hard-block), and decides whether `Stop` should block, release green,
  or release exhausted (honest red, no loop). See `orchestrator.py`'s module docstring
  for the full set of load-bearing design decisions.
- `GateEvaluation`, `ConditionOutcome` — `evaluate_gate`'s return shape.
- `compute_project_fingerprint` — the content fingerprint flake detection compares
  across consecutive attempts.
- `DEFAULT_SHIPFILE_FILENAME` — where `shipgate.hooks.stop` looks for a project's
  shipfile. `SIDECAR_CLAIMS_DIRNAME` — where an `inventory_complete` condition's
  `claimed_items` can be supplied, per condition id.
- `record_high_risk_change`, `BlastRadiusResult` — task 2.8, the session blast-radius
  counter. Deliberately **not** called from `evaluate_gate`/`Stop` — see
  `blast_radius.py`'s module docstring for why.

**Not yet implemented**: `runtime_evidence`'s `endpoint_probe`/`db_query` methods and the
`emission_traced` condition type have no assigned task yet; automatic detection of which
task class a real-world change belongs to (`blast_radius.py`'s stated gap); automatic
extraction of `inventory_complete`'s `claimed_items` from a transcript (task 1.5
territory — the sidecar file only removes the "nothing can supply it" blocker, it
doesn't build the extractor). `run_checker` raises a specific, honest error for any
condition type not yet dispatchable — never a silent pass; `evaluate_gate` excludes any
condition type (or advisory-only outcome) it can't use to decide green/red, for the same
reason.

Zero Claude-Code-specific imports — core-purity contract, `pyproject.toml`. This package
may depend on `shipgate.ledger`, `shipgate.verdicts`, `shipgate.shipfile`, and the
sibling `reporters/` package; none of them may depend on `shipgate.hooks` (enforced by
`import-linter`, `pyproject.toml`).
"""

from __future__ import annotations

from .blast_radius import BlastRadiusResult, record_high_risk_change
from .checkers import (
    CHECKERS,
    DEFAULT_CHECKER_TIMEOUT_SECONDS,
    IGNORED_DIR_NAMES,
    CheckResult,
    check_command_succeeds,
    check_file_exists,
    check_forbidden_pattern_absent,
    check_inventory_complete,
    check_runtime_evidence,
    check_tests_pass,
    iter_scanned_files,
    run_checker,
)
from .orchestrator import (
    DEFAULT_SHIPFILE_FILENAME,
    SIDECAR_CLAIMS_DIRNAME,
    ConditionOutcome,
    GateEvaluation,
    compute_project_fingerprint,
    evaluate_gate,
)

__all__ = [
    "CHECKERS",
    "DEFAULT_CHECKER_TIMEOUT_SECONDS",
    "DEFAULT_SHIPFILE_FILENAME",
    "IGNORED_DIR_NAMES",
    "SIDECAR_CLAIMS_DIRNAME",
    "BlastRadiusResult",
    "CheckResult",
    "ConditionOutcome",
    "GateEvaluation",
    "check_command_succeeds",
    "check_file_exists",
    "check_forbidden_pattern_absent",
    "check_inventory_complete",
    "check_runtime_evidence",
    "check_tests_pass",
    "compute_project_fingerprint",
    "evaluate_gate",
    "iter_scanned_files",
    "record_high_risk_change",
    "run_checker",
]

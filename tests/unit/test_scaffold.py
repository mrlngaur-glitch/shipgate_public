"""Phase 0 scaffold tests.

These verify the build environment itself, not product behaviour — no product code
exists yet, by design (report §13.2 starts at the ledger schema, and review finding
R3 places the read-only JSONL spike before it).

Their real job is to make the CI anti-vacuous assertion meaningful from commit one:
a suite that collects zero tests must fail the build (review finding R7).
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_python_is_312():
    """Report §5.3 fixes Python 3.12 — GEPA/DSPy are Python, and the founder's
    environment is proven on it. A different minor version is a silent drift."""
    assert sys.version_info[:2] == (3, 12), f"expected Python 3.12, got {sys.version_info[:2]}"


def test_repo_layout_matches_report_section_13_1():
    """Report §13.1 fixes the directory layout ('create exactly this').
    Drift here is cheap to prevent and annoying to unwind later."""
    required = [
        "shipgate/ledger",
        "shipgate/shipfile",
        "shipgate/charter",
        "shipgate/gate",
        "shipgate/verdicts",
        "shipgate/hooks",
        "shipgate/permissions",
        "shipgate/discipline",
        "shipgate/analyze",
        "shipgate/doctor",
        "shipgate/router",
        "shipgate/context",
        "shipgate/evolve",
        "reporters",
        "integrations/speckit-extension",
        "integrations/github-action",
        "integrations/omniroute-middleware",
        "dashboard",
        "benchmarks",
        "docs",
    ]
    missing = [d for d in required if not (REPO_ROOT / d).is_dir()]
    assert not missing, f"missing directories required by report §13.1: {missing}"


def test_control_documents_present():
    """Blocker 14 (Session 025): this test used to assert five internal planning
    documents that never enter the public repository — a real assertion that would
    fail on the first public CI run, and whose failure output would print every one
    of those private filenames into a public log. Rewritten to assert the documents a
    stranger actually receives: the
    ones every real clone of this repository must have for a stranger to be able to
    install it, understand what it does, verify its security posture, and see the
    contract it gates itself under. Kept as a real, dispatchable assertion — not a
    `skipif` or a conditional pass, which would check nothing and render the exact
    vacuous pass this product exists to catch."""
    required = [
        "README.md",                          # install/quickstart, the first thing a stranger reads
        "LICENSE",                             # required for a real OSS release
        "SECURITY.md",                         # vulnerability reporting, dependency posture
        "shipfile.yaml",                       # this repo's own live gate contract
        "docs/shipfile_worked_example.yaml",   # the fuller worked example shipfile.yaml itself points to
    ]
    missing = [f for f in required if not (REPO_ROOT / f).is_file()]
    assert not missing, f"missing control documents: {missing}"

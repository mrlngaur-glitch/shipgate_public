"""tests/unit/test_archive_boundary.py

Guards the repo's never-publish boundary. `.gitattributes`' export-ignore list is
the actual mechanism (P31); this test does NOT trust that file blindly -- it holds
its own, independent record of what was ruled never-publish and checks the real
`git archive HEAD` output against it. That distinction matters: this session found
that an unescaped space in a pattern ("Revenue forecast.txt") silently split into
pattern "Revenue" + bogus attributes, so the line was present in `.gitattributes`
and did nothing -- caught only by re-running the real archive, not by the file
existing. A test that re-implemented the same trust ("does .gitattributes say
export-ignore?") would not have caught that. This one re-derives the answer from
`git archive`'s actual bytes.

Two layers:

1. Closed-world enumeration. Every top-level tracked path, and every path directly
   under `docs/` (the one mixed directory holding both published and never-published
   material), must appear in a PUBLISHED or NEVER_PUBLISH set below. An unrecognized
   new entry fails LOUD rather than defaulting to "shipped" -- this is what makes a
   future eleventh never-publish document surface as a red test, not a leak.
2. The real archive. `git archive HEAD` is actually invoked as a subprocess and
   parsed with `tarfile` -- first asserted non-empty and containing known-published
   files (the vacuous-pass guard: an archive command that silently failed or
   returned nothing must not make every absence assertion below pass for free),
   then asserted to contain zero members under any NEVER_PUBLISH path.
"""

from __future__ import annotations

import io
import subprocess
import tarfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Every path ruled never-publish this session (P31) -- a plain literal list,
# independent of .gitattributes, because this is the canonical ruling the
# mechanism is supposed to implement, not a re-check of the mechanism's own say-so.
NEVER_PUBLISH_TOP_LEVEL = {
    "Revenue forecast.txt",
    "final_ShipGate_Master_Report_v4_FINAL.md",
    "CLAUDE.md",
    "PHASE_PLAN.md",
    "SESSION_LOG.md",
    "PARKING_LOT.md",
    "ANALYST_DISCIPLINE_CHARTER.md",
    "Archive",  # directory -- prefix-matched in the archive check below
}

# Every other top-level path this repo tracks, ruled publish this session (P31).
PUBLISHED_TOP_LEVEL = {
    ".gitattributes",
    ".github",
    ".gitignore",
    "LICENSE",
    "README.md",
    "SECURITY.md",
    "benchmarks",
    "dashboard",
    "docs",
    "integrations",
    "pyproject.toml",
    "reporters",
    "requirements-audit.lock",
    "requirements.lock",
    "shipfile.yaml",
    "shipgate",
    "tests",
}

# docs/ is the one mixed directory -- its direct children get the same closed-world
# treatment, since a new docs/<internal-doc>.md is exactly where the last two
# never-publish documents (REPORT_REVIEW, analyst_briefs) actually lived.
NEVER_PUBLISH_DOCS_CHILDREN = {
    "REPORT_REVIEW_2026-08-15.md",
    "analyst_briefs",  # directory -- prefix-matched in the archive check below
}
PUBLISHED_DOCS_CHILDREN = {
    "jsonl_format_notes.md",
    "ledger_schema_design.md",
    "shipfile_worked_example.md",
    "shipfile_worked_example.yaml",
    "spikes",
    "verdicts_explainer.md",
}

NEVER_PUBLISH = {f"docs/{p}" for p in NEVER_PUBLISH_DOCS_CHILDREN} | NEVER_PUBLISH_TOP_LEVEL


def _tracked_names(pathspec: str | None = None) -> set[str]:
    cmd = ["git", "ls-tree", "--name-only", "HEAD"]
    if pathspec:
        cmd.append(pathspec)
    out = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, check=True)
    names = {line for line in out.stdout.splitlines() if line}
    assert names, f"git ls-tree {pathspec or ''} returned nothing -- vacuous, not a real check"
    return names


def test_every_top_level_tracked_path_is_ruled():
    """Closed-world check: an unrecognized new top-level path fails loud, not silently."""
    tracked = _tracked_names()
    known = PUBLISHED_TOP_LEVEL | NEVER_PUBLISH_TOP_LEVEL
    unrecognized = tracked - known
    assert not unrecognized, (
        f"New top-level tracked path(s) with no publish/never-publish ruling: "
        f"{sorted(unrecognized)} -- rule on each explicitly (README/.gitattributes/"
        f"this test) before it ships by default in a bare `git archive HEAD`."
    )


def test_every_docs_child_is_ruled():
    """docs/ is the one mixed directory -- guard its direct children the same way."""
    # `git ls-tree ... docs/` returns paths prefixed with "docs/" (unlike the
    # no-pathspec top-level call, which returns bare names) -- strip it so this
    # compares like-for-like against the bare child names below.
    tracked = {name.removeprefix("docs/") for name in _tracked_names("docs/")}
    known = PUBLISHED_DOCS_CHILDREN | NEVER_PUBLISH_DOCS_CHILDREN
    unrecognized = tracked - known
    assert not unrecognized, (
        f"New docs/ path(s) with no publish/never-publish ruling: {sorted(unrecognized)} "
        f"-- this is exactly where the last two never-publish documents lived; "
        f"rule on each explicitly before it ships."
    )


@pytest.fixture(scope="module")
def archive_members() -> list[str]:
    result = subprocess.run(
        ["git", "archive", "HEAD"], cwd=REPO_ROOT, capture_output=True, check=True,
    )
    with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:") as tar:
        return [m.name for m in tar.getmembers()]


def test_archive_is_real_not_vacuous(archive_members):
    """The vacuous-pass guard, checked first: an archive command that silently
    failed or returned nothing must not make every absence assertion below pass
    for free. A check that observed nothing never renders green."""
    assert archive_members, (
        "git archive HEAD produced zero members -- cannot prove absence of "
        "anything from an empty result"
    )
    assert "README.md" in archive_members, (
        "known-published file missing from the archive -- archive command is not "
        "behaving as expected, absence assertions below would be meaningless"
    )
    assert "shipgate/cli.py" in archive_members, (
        "known-published file missing from the archive -- archive command is not "
        "behaving as expected, absence assertions below would be meaningless"
    )


def test_archive_ships_no_never_publish_path(archive_members):
    assert archive_members and "README.md" in archive_members, (
        "archive appears empty/broken -- see test_archive_is_real_not_vacuous; "
        "refusing to assert absence against a vacuous result"
    )
    leaked = [
        member
        for never_publish_path in NEVER_PUBLISH
        for member in archive_members
        if member == never_publish_path or member.startswith(never_publish_path + "/")
    ]
    assert not leaked, (
        f"never-publish path(s) present in `git archive HEAD` output: {sorted(leaked)} "
        f"-- .gitattributes' export-ignore is not actually excluding these; this is "
        f"the exact shape of bug this session found once already (an unescaped space "
        f"silently defeating a pattern) -- do not assume the fix from the file's "
        f"presence, re-run this test."
    )

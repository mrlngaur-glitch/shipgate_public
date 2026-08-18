"""The configured corpus root (Session 002 open question 4, decided by the founder:
"one configurable root, not a settings framework").

`sessions.source_dir` is stored relative to this root so the ledger file itself is
portable and never bakes in the founder's local absolute directory structure. Deliberately
a single path, not a settings/config framework — Task 1.5 (ingest) is what will actually
call this; nothing about ingest itself is built this session.

Zero Claude-Code-specific imports — core-purity contract, `pyproject.toml`.
"""

from __future__ import annotations

from pathlib import Path

#: The default corpus root — `~/.claude/projects/`, matching the report's spec and the
#: layout confirmed by the Phase 0 spike (`docs/jsonl_format_notes.md`). Callers that
#: ingest from elsewhere (tests, a future multi-root setup) pass their own root
#: explicitly to `relative_source_dir` rather than relying on this default.
DEFAULT_CORPUS_ROOT = Path.home() / ".claude" / "projects"


def relative_source_dir(absolute_dir: Path, corpus_root: Path = DEFAULT_CORPUS_ROOT) -> str:
    """`absolute_dir`, expressed relative to `corpus_root`, POSIX-style, for storage in
    `sessions.source_dir`. Raises `ValueError` if `absolute_dir` isn't under
    `corpus_root` — a session directory outside the configured root is a configuration
    error to surface, not to silently accept."""
    return absolute_dir.resolve().relative_to(corpus_root.resolve()).as_posix()

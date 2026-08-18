"""The Discipline Generator (task 3.1) — `shipgate init`'s
minimal interview, writing `shipfile.yaml` / `CLAUDE.md` / `.claude/settings.json`. See
`init.py`'s module docstring for the merge-never-overwrite design, and `templates.py`
for the emitted content and its sources.

**Full Discipline Generator (richer interview, charter generation, signed hook catalog)
is a fast-follow release, not this package's current scope.**
"""

from .init import (
    DEFAULT_CLAUDE_MD_FILENAME,
    DEFAULT_SETTINGS_JSON_RELPATH,
    DEFAULT_SHIPFILE_FILENAME,
    FileOutcome,
    InitResult,
    run_init,
)
from .session import NoSessionRecordedError, current_session_id, open_project_ledger, utc_now_iso

__all__ = [
    "DEFAULT_CLAUDE_MD_FILENAME",
    "DEFAULT_SETTINGS_JSON_RELPATH",
    "DEFAULT_SHIPFILE_FILENAME",
    "FileOutcome",
    "InitResult",
    "NoSessionRecordedError",
    "current_session_id",
    "open_project_ledger",
    "run_init",
    "utc_now_iso",
]

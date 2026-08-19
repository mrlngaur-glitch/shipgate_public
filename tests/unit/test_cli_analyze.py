"""`shipgate analyze` at the CLI layer — exit codes and `--roots` wiring.
`shipgate.analyze.data`'s own module owns the data-gathering tests
(`tests/unit/test_analyze.py`); this file is the thin CLI wrapper only.
"""

from __future__ import annotations

from typer.testing import CliRunner

from shipgate.cli import app
from shipgate.ledger.writer import LedgerWriter

runner = CliRunner()


def _make_project(root, slug: str):
    ledger_dir = root / slug / ".shipgate"
    ledger_dir.mkdir(parents=True)
    return ledger_dir / "ledger.db"


def test_analyze_exits_3_when_nothing_found_anywhere(tmp_path):
    result = runner.invoke(app, ["analyze", "--roots", str(tmp_path)])
    assert result.exit_code == 3
    assert "OBSERVED NOTHING" in result.stdout


def test_analyze_exits_0_when_at_least_one_ledger_reads_and_verifies(tmp_path):
    db_path = _make_project(tmp_path, "proj-a")
    LedgerWriter(db_path).close()

    result = runner.invoke(app, ["analyze", "--roots", str(tmp_path)])
    assert result.exit_code == 0
    assert "proj-a" in result.stdout


def test_analyze_exits_1_when_a_ledger_is_unreadable(tmp_path):
    garbage_path = _make_project(tmp_path, "proj-bad")
    garbage_path.write_bytes(b"not a database")

    result = runner.invoke(app, ["analyze", "--roots", str(tmp_path)])
    assert result.exit_code == 1
    assert "UNREADABLE" in result.stdout


def test_analyze_accepts_repeated_roots_option(tmp_path):
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    db_a = _make_project(root_a, "proj-a")
    LedgerWriter(db_a).close()
    db_b = _make_project(root_b, "proj-b")
    LedgerWriter(db_b).close()

    result = runner.invoke(app, ["analyze", "--roots", str(root_a), "--roots", str(root_b)])
    assert result.exit_code == 0
    assert "proj-a" in result.stdout
    assert "proj-b" in result.stdout


def test_analyze_never_touches_a_project_ledger_never_creates_shipgate_dir(tmp_path):
    """A minimal, CLI-level echo of the read-only proof — invoking the real command
    must never create `.shipgate/` anywhere except the project directories that
    already had one before the run."""
    db_path = _make_project(tmp_path, "proj-a")
    LedgerWriter(db_path).close()
    before = db_path.stat().st_mtime_ns

    runner.invoke(app, ["analyze", "--roots", str(tmp_path)])

    assert db_path.stat().st_mtime_ns == before
    assert not (tmp_path / ".shipgate").exists()

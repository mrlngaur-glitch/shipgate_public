"""Task 3.2 — `shipgate.doctor.check.run_doctor`, shipfile staleness detection. Every
`Shipfile` here is built directly via `Shipfile(raw={...})` (same convention as
`test_blast_radius.py`) rather than round-tripped through a written-to-disk YAML file —
deliberately, so a test's own `grep_pattern` string never appears anywhere on disk and
can't accidentally self-match the way a live demo script already documented happening
once (a pattern written into `shipfile.yaml` matched its own line).
"""

from __future__ import annotations

from pathlib import Path

from shipgate.doctor.check import run_doctor
from shipgate.shipfile import Shipfile


def _condition(**kwargs) -> dict:
    return kwargs


def _shipfile(*conditions: dict) -> Shipfile:
    return Shipfile(raw={"done_conditions": list(conditions)})


# --- file_exists -------------------------------------------------------------------


def test_file_exists_condition_pointing_at_a_real_file_is_clean(tmp_path: Path):
    (tmp_path / "real.txt").write_text("hi", encoding="utf-8")
    shipfile = _shipfile(_condition(id="c1", type="file_exists", path="real.txt"))

    report = run_doctor(shipfile, tmp_path)

    assert report.is_clean
    assert report.checked_condition_ids == ("c1",)


def test_file_exists_condition_pointing_at_a_deleted_file_is_stale(tmp_path: Path):
    shipfile = _shipfile(_condition(id="c1", type="file_exists", path="deleted.txt"))

    report = run_doctor(shipfile, tmp_path)

    assert not report.is_clean
    assert len(report.stale) == 1
    assert report.stale[0].condition_id == "c1"
    assert report.stale[0].field == "path"
    assert "deleted.txt" in report.stale[0].reason


# --- forbidden_pattern_absent --------------------------------------------------------


def test_forbidden_pattern_absent_with_existing_scope_paths_is_clean(tmp_path: Path):
    (tmp_path / "shipgate").mkdir()
    shipfile = _shipfile(
        _condition(id="c2", type="forbidden_pattern_absent", pattern="TODO", paths=["shipgate"])
    )

    report = run_doctor(shipfile, tmp_path)

    assert report.is_clean


def test_forbidden_pattern_absent_with_a_deleted_scope_path_is_stale(tmp_path: Path):
    shipfile = _shipfile(
        _condition(id="c2", type="forbidden_pattern_absent", pattern="TODO", paths=["never_existed/"])
    )

    report = run_doctor(shipfile, tmp_path)

    assert not report.is_clean
    assert report.stale[0].field == "paths"


def test_forbidden_pattern_absent_with_no_paths_field_is_clean_nothing_to_check(tmp_path: Path):
    shipfile = _shipfile(_condition(id="c2", type="forbidden_pattern_absent", pattern="TODO"))

    report = run_doctor(shipfile, tmp_path)

    assert report.is_clean
    assert report.checked_condition_ids == ("c2",)


# --- inventory_complete --------------------------------------------------------------


def test_inventory_complete_with_a_real_match_is_clean(tmp_path: Path):
    (tmp_path / "mod.py").write_text("from shipgate.ledger.writer import LedgerWriter\n", encoding="utf-8")
    shipfile = _shipfile(
        _condition(
            id="c3",
            type="inventory_complete",
            grep_pattern="from shipgate.ledger.writer import",
            required_dispositions=["updated"],
        )
    )

    report = run_doctor(shipfile, tmp_path)

    assert report.is_clean


def test_inventory_complete_with_zero_matches_is_stale(tmp_path: Path):
    (tmp_path / "mod.py").write_text("nothing relevant here\n", encoding="utf-8")
    shipfile = _shipfile(
        _condition(
            id="c3",
            type="inventory_complete",
            grep_pattern="a_pattern_that_appears_nowhere_in_this_fixture",
            required_dispositions=["updated"],
        )
    )

    report = run_doctor(shipfile, tmp_path)

    assert not report.is_clean
    assert report.stale[0].field == "grep_pattern"
    assert "zero locations" in report.stale[0].reason


# --- honesty about what's not checked -------------------------------------------------


def test_unchecked_condition_types_are_named_not_silently_skipped(tmp_path: Path):
    shipfile = _shipfile(
        _condition(id="c4", type="tests_pass", command="pytest"),
        _condition(id="c5", type="command_succeeds", command="ruff check ."),
        _condition(id="c6", type="runtime_evidence", method="log_marker", detail="x"),
        _condition(id="c7", type="emission_traced", marker="X"),
        _condition(id="c8", ears="WHEN x THE SYSTEM SHALL y"),  # EARS, no type field
    )

    report = run_doctor(shipfile, tmp_path)

    assert report.checked_condition_ids == ()
    assert set(report.skipped_condition_types) == {
        "tests_pass",
        "command_succeeds",
        "runtime_evidence",
        "emission_traced",
    }
    # EARS entries have no `type` at all -- not even counted as "skipped", since there's
    # no field to have skipped checking in the first place.
    # Nothing checked, nothing found stale -- but per the Session 010 fix this is the
    # VACUOUS state, not a clean one: is_clean is False when nothing was checked at all.
    assert not report.stale
    assert report.is_vacuous
    assert not report.is_clean


# --- combined-realistic: several conditions, only some stale --------------------------


def test_combined_realistic_shipfile_some_clean_some_stale_some_unchecked(tmp_path: Path):
    (tmp_path / "keep.txt").write_text("present", encoding="utf-8")
    shipfile = _shipfile(
        _condition(id="ok", type="file_exists", path="keep.txt"),
        _condition(id="gone", type="file_exists", path="gone.txt"),
        _condition(id="cmd", type="tests_pass", command="pytest"),
    )

    report = run_doctor(shipfile, tmp_path)

    assert not report.is_clean
    assert not report.is_vacuous  # two conditions WERE checked, one just happens to be stale
    assert len(report.stale) == 1
    assert report.stale[0].condition_id == "gone"
    assert report.checked_condition_ids == ("ok", "gone")
    assert report.skipped_condition_types == ("tests_pass",)


# --- Finding 2 (Session 010): is_vacuous vs is_clean, the exact distinction the founder ---
# --- flagged as collapsed into a single misleading "Clean" -------------------------------


def test_is_vacuous_true_and_is_clean_false_when_nothing_is_checkable(tmp_path: Path):
    """The exact shape a freshly `shipgate init`-generated shipfile produces: its only
    done_condition is tests_pass, which doctor doesn't check."""
    shipfile = _shipfile(_condition(id="only", type="tests_pass", command="pytest"))

    report = run_doctor(shipfile, tmp_path)

    assert report.is_vacuous
    assert not report.is_clean
    assert not report.stale  # not stale either -- there's simply nothing to compare


def test_is_vacuous_false_and_is_clean_true_when_something_real_was_checked(tmp_path: Path):
    (tmp_path / "real.txt").write_text("x", encoding="utf-8")
    shipfile = _shipfile(_condition(id="c1", type="file_exists", path="real.txt"))

    report = run_doctor(shipfile, tmp_path)

    assert not report.is_vacuous
    assert report.is_clean


def test_is_vacuous_true_for_a_shipfile_with_no_done_conditions_at_all(tmp_path: Path):
    shipfile = _shipfile()

    report = run_doctor(shipfile, tmp_path)

    assert report.is_vacuous
    assert not report.is_clean

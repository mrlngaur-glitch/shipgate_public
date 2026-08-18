"""`shipgate doctor` (task 3.2) — shipfile staleness detection.
See `check.py`'s module docstring for exactly what "stale" means and which condition
types are checked."""

from .check import DoctorReport, StaleReference, run_doctor

__all__ = ["DoctorReport", "StaleReference", "run_doctor"]

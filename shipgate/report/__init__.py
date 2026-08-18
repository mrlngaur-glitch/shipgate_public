"""Ship Report v1 (task 3.4, Phase 3) — the screenshot-able artifact
(report §8.1). `data.py` gathers plain facts from the ledger; `render.py` turns them
into `rich` terminal output. See each module's own docstring for the full reasoning.
"""

from __future__ import annotations

from .data import (
    ClaimRow,
    GateUnavailableInfo,
    LedgerReceipt,
    ShipReportData,
    VerifyResult,
    find_green_inconsistency,
    gather_ledger_receipt,
    gather_report_data,
    read_gate_unavailable_marker,
    verify_receipt,
)
from .render import render_ship_report

__all__ = [
    "ClaimRow",
    "GateUnavailableInfo",
    "LedgerReceipt",
    "ShipReportData",
    "VerifyResult",
    "find_green_inconsistency",
    "gather_ledger_receipt",
    "gather_report_data",
    "read_gate_unavailable_marker",
    "render_ship_report",
    "verify_receipt",
]

"""Shipfile v0.1 parsing — YAML text in, a validated `Shipfile` out, or a specific
refusal (Phase 1 brief done-condition: "each malformed variant produces a specific
error naming the offending block").

Zero Claude-Code-specific imports — core-purity contract, `pyproject.toml`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .validator import validate_shipfile

#: Defaults applied when the field is present-but-omitted inside a *present* block, or
#: when the whole block is present but empty. A missing block is still a validation
#: error (`REQUIRED_BLOCKS`) — defaults only fill in what's optional *within* a block
#: that does exist. Values chosen match `schema.py`'s `const`/`default` annotations
#: exactly (report §13.2's stated defaults for `gate_policy` and `session_policy`).
_GATE_POLICY_DEFAULTS = {
    "max_retries": 3,
    "on_retries_exhausted": "honest_red_report",
    "flake_policy": "quarantine",
}
_SESSION_POLICY_DEFAULTS = {
    "max_high_risk_changes_per_session": 3,
    "on_exceeded": "require_logged_override",
}


class ShipfileSyntaxError(ValueError):
    """The text isn't parseable YAML at all — distinct from a well-formed-YAML,
    invalid-shipfile document (`ShipfileValidationError`)."""


class ShipfileValidationError(ValueError):
    """The YAML parsed, but the shipfile it describes is not well-formed. Carries every
    specific error found (not just the first), each naming the block it came from."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        message = "shipfile is invalid:\n" + "\n".join(f"  - {e}" for e in errors)
        super().__init__(message)


@dataclass(frozen=True)
class Shipfile:
    """A validated shipfile: the eight blocks, with `gate_policy`/`session_policy`
    defaults filled in. `raw` is the normalized mapping — callers needing a specific
    block read `shipfile.raw["task_classes"]` etc.; no accessor methods are added ahead
    of a real consumer needing them (Phase 2's checkers, `shipgate.gate`)."""

    raw: dict[str, Any]
    source_path: str | None = None

    def __getitem__(self, block: str) -> Any:
        return self.raw[block]


def _apply_defaults(data: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(data)
    normalized["gate_policy"] = {**_GATE_POLICY_DEFAULTS, **data.get("gate_policy", {})}
    normalized["session_policy"] = {**_SESSION_POLICY_DEFAULTS, **data.get("session_policy", {})}
    return normalized


def parse_shipfile(text: str, *, source_path: str | None = None) -> Shipfile:
    """Parse and validate shipfile YAML text. Raises `ShipfileSyntaxError` on
    unparseable YAML, `ShipfileValidationError` (one entry per specific problem, each
    naming its block) on a well-formed-YAML-but-invalid-shipfile document."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ShipfileSyntaxError(f"shipfile is not valid YAML: {exc}") from exc

    errors = validate_shipfile(data)
    if errors:
        raise ShipfileValidationError(errors)

    return Shipfile(raw=_apply_defaults(data), source_path=source_path)


def load_shipfile(path: str | Path) -> Shipfile:
    """Read and parse a shipfile from disk."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    return parse_shipfile(text, source_path=str(path))

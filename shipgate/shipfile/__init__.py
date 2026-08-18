"""Shipfile v0.1 (Task 1.4) — a public interface, frozen at Gate A alongside the
verdict taxonomy.

Public API:

- `Shipfile`, `parse_shipfile`, `load_shipfile` — parse validated shipfile YAML.
- `ShipfileSyntaxError` — the text isn't YAML at all.
- `ShipfileValidationError` — well-formed YAML, invalid shipfile; carries one specific
  error per problem, each naming the block it came from.
- `validate_shipfile` — the lower-level function returning error strings directly,
  without raising, for callers that want to inspect all problems at once.
- `REQUIRED_BLOCKS`, `CONDITION_TYPE_SCHEMAS` — the frozen vocabulary: the eight blocks
  and the seven done-condition types.
- `SUPPORTED_SHIPFILE_VERSION` — the highest `shipfile_version` this build accepts.
  Absent `shipfile_version` in a file means "0.1".

**No checker exists in this package.** Validating a shipfile here proves it is
well-formed; it proves nothing about whether any condition is true of a real project —
that is Phase 2 (`shipgate.gate`).

Zero Claude-Code-specific imports — core-purity contract, `pyproject.toml`.
"""

from .parser import (
    Shipfile,
    ShipfileSyntaxError,
    ShipfileValidationError,
    load_shipfile,
    parse_shipfile,
)
from .schema import CONDITION_TYPE_SCHEMAS, REQUIRED_BLOCKS, SUPPORTED_SHIPFILE_VERSION
from .validator import validate_shipfile

__all__ = [
    "CONDITION_TYPE_SCHEMAS",
    "REQUIRED_BLOCKS",
    "SUPPORTED_SHIPFILE_VERSION",
    "Shipfile",
    "ShipfileSyntaxError",
    "ShipfileValidationError",
    "load_shipfile",
    "parse_shipfile",
    "validate_shipfile",
]

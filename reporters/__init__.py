"""Reporter plugins that feed the ledger (report §13.1: "reporters/ — pytest (first),
vitest -> ledger, with flake detection + collected-count"). Pytest first; other
languages' runners arrive later, one at a time, when a real project needs one.

Deliberately a top-level package, sibling to `shipgate/`, not `shipgate.reporters` —
matching the report's own layout. Harness-agnostic like the `shipgate` core packages
(no Claude-Code-specific imports), but not gated by the `import-linter` core-purity
contract since that contract's `source_modules` list is scoped to packages under
`shipgate/` (`pyproject.toml`).
"""

from __future__ import annotations

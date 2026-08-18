"""Shipfile v0.1 validation — produces a *specific* error naming the offending block
for every malformed variant (Phase 1 brief done-condition), not a generic dump.

Structural validation only. This module never executes a condition against a real
project — that's Phase 2's checkers. It answers exactly one question: is this shipfile
*well-formed*?

Zero Claude-Code-specific imports — core-purity contract, `pyproject.toml`.
"""

from __future__ import annotations

import re

from jsonschema import Draft202012Validator

from .schema import (
    BLOCK_SCHEMAS,
    CONDITION_TYPE_SCHEMAS,
    EARS_CONDITION_SCHEMA,
    REQUIRED_BLOCKS,
    SHIPFILE_VERSION_PATTERN,
    SUPPORTED_SHIPFILE_VERSION,
    VERIFY_AFTER_PATTERN,
)

_VERIFY_AFTER_RE = re.compile(VERIFY_AFTER_PATTERN)
_SHIPFILE_VERSION_RE = re.compile(SHIPFILE_VERSION_PATTERN)

#: Top-level keys recognized alongside the eight required blocks. `shipfile_version` is
#: optional and is *not* one of the eight blocks (it has no block schema of its own —
#: see `_shipfile_version_format`), so it has to be named here or it would fall into
#: the generic "not a recognized shipfile v0.1 block" bucket, which is the exact wrong
#: error this field exists to prevent.
_KNOWN_TOP_LEVEL_KEYS = frozenset({*REQUIRED_BLOCKS, "shipfile_version"})


def _format_jsonschema_errors(prefix: str, instance: object, schema: dict) -> list[str]:
    """Run `instance` against `schema`, returning one specific string per error, each
    prefixed with `prefix` plus the exact sub-path jsonschema reports — e.g.
    `"$.task_classes.refactor.risk_tier: 'extreme' is not one of ['low', 'medium', 'high']"`.
    """
    validator = Draft202012Validator(schema)
    errors = []
    for err in sorted(validator.iter_errors(instance), key=lambda e: list(e.path)):
        sub_path = "".join(f"[{p!r}]" if isinstance(p, str) else f"[{p}]" for p in err.path)
        errors.append(f"{prefix}{sub_path}: {err.message}")
    return errors


def _validate_verify_after(path: str, entry: dict) -> list[str]:
    verify_after = entry.get("verify_after")
    if verify_after is not None and not _VERIFY_AFTER_RE.match(str(verify_after)):
        return [
            (
                f"{path}.verify_after: {verify_after!r} is not a recognized duration "
                "(expected a number followed by m/h/d/w, e.g. '24h', '7d')"
            )
        ]
    return []


def _validate_done_condition_entry(index: int, entry: object) -> list[str]:
    """Custom per-entry dispatch, not one `oneOf`/`anyOf` schema across the seven
    checker types plus the EARS shape. A combined schema would make jsonschema report
    the unhelpful "not valid under any of the given schemas" on a malformed entry;
    dispatching on the entry's own shape first means every error is already scoped to
    exactly one condition type's schema.

    **Session 004 Gate A fix #3:** every entry must be an object with an `id` — a bare
    EARS string is no longer accepted (`done_conditions` is homogeneous now, so every
    entry is addressable by `claims.shipfile_condition_ref`). Dispatch is on whichever
    of `type` (one of the seven checker types) or `ears` the object carries — never
    both, never neither.
    """
    path = f"$.done_conditions[{index}]"

    if not isinstance(entry, dict):
        return [
            (
                f"{path}: must be an object with an 'id' and either 'type' (one of the "
                f"seven checker types) or 'ears' (an EARS-style condition string) — got "
                f"{type(entry).__name__}. Bare EARS-style strings are no longer accepted "
                "as of Gate A; wrap it as {id: '...', ears: '<the EARS sentence>'}"
            )
        ]

    has_type = "type" in entry
    has_ears = "ears" in entry

    if has_type and has_ears:
        return [f"{path}: an entry may carry 'type' or 'ears', never both"]

    if has_ears:
        errors = _format_jsonschema_errors(path, entry, EARS_CONDITION_SCHEMA)
        return errors + _validate_verify_after(path, entry)

    if has_type:
        cond_type = entry.get("type")
        if cond_type not in CONDITION_TYPE_SCHEMAS:
            known = ", ".join(sorted(CONDITION_TYPE_SCHEMAS))
            return [
                (
                    f"{path}.type: {cond_type!r} is not a recognized condition type — "
                    f"must be one of: {known}"
                )
            ]
        errors = _format_jsonschema_errors(path, entry, CONDITION_TYPE_SCHEMAS[cond_type])
        return errors + _validate_verify_after(path, entry)

    return [
        (
            f"{path}: must carry either 'type' (one of the seven checker types) or "
            "'ears' (an EARS-style condition string) — neither is present"
        )
    ]


def _parse_shipfile_version(version: str) -> tuple[int, int]:
    major, minor = version.split(".", 1)
    return (int(major), int(minor))


def _shipfile_version_upgrade_error(version: str) -> str | None:
    """The one error this field exists to get right forever. `version` here is already
    known to be a well-formed `<major>.<minor>` string — see `_shipfile_version_format`.
    Returns the "upgrade ShipGate" error, or `None` if this build supports `version`."""
    if _parse_shipfile_version(version) > _parse_shipfile_version(SUPPORTED_SHIPFILE_VERSION):
        return (
            f"$.shipfile_version: this file declares shipfile_version {version!r}, but "
            f"this build of ShipGate only supports up to {SUPPORTED_SHIPFILE_VERSION!r}. "
            "Upgrade ShipGate to a version that supports this shipfile format."
        )
    return None


def _shipfile_version_format(data: dict) -> tuple[str | None, list[str]]:
    """`shipfile_version` is optional; absent means "0.1" (see `schema.py`), so this
    returns `(SUPPORTED_SHIPFILE_VERSION, [])` when the field isn't present at all —
    every shipfile written before this field existed stays valid unchanged.

    Returns `(parsed_version, format_errors)`. `parsed_version` is `None` whenever the
    field couldn't be read as a real version at all (wrong type, or a string that isn't
    `<major>.<minor>`) — the caller cannot ask "is this newer than supported?" about a
    value that isn't a version, so a malformed field never triggers the short-circuit
    below; it's reported as one ordinary error alongside everything else.

    **The most common mistake with this field, caught here specifically:** YAML parses
    an *unquoted* `0.1` as the float `0.1`, not the text `"0.1"` — the exact same trap
    `docker-compose`'s `version:` key is famous for. That case gets its own message
    telling the author to quote it, rather than the generic "not a recognized version
    string" message, which — before this fix — echoed the same-looking value back as
    its own suggested fix and left the real problem (missing quotes) unstated.
    """
    if "shipfile_version" not in data:
        return SUPPORTED_SHIPFILE_VERSION, []

    version = data["shipfile_version"]

    if not isinstance(version, str):
        return None, [
            (
                f"$.shipfile_version: {version!r} is not a string — quote it, e.g. "
                'shipfile_version: "0.1" (YAML parses an unquoted value like 0.1 as a '
                "number, not text, the same trap docker-compose's version: key has)"
            )
        ]

    if not _SHIPFILE_VERSION_RE.match(version):
        return None, [
            (
                f"$.shipfile_version: {version!r} is not a recognized version string "
                "(expected '<major>.<minor>', e.g. '0.1')"
            )
        ]

    return version, []


def _validate_done_condition_ids_are_unique(conditions: list) -> list[str]:
    """Every id must be unique, or `claims.shipfile_condition_ref` can't unambiguously
    point at one — the exact addressability requirement the homogeneous-object shape
    exists to satisfy (Session 004 Gate A fix #3)."""
    seen: dict[str, list[int]] = {}
    for i, entry in enumerate(conditions):
        if isinstance(entry, dict) and isinstance(entry.get("id"), str):
            seen.setdefault(entry["id"], []).append(i)

    errors = []
    for cond_id, indices in seen.items():
        if len(indices) > 1:
            errors.append(
                f"$.done_conditions: id {cond_id!r} is used by more than one entry "
                f"(indices {indices}) — every id must be unique so it can be pointed "
                "at unambiguously"
            )
    return errors


def validate_shipfile(data: object) -> list[str]:
    """Validate a parsed (but not yet defaulted) shipfile mapping. Returns a list of
    specific error strings — empty means valid. Never raises; `parser.py` is what
    turns a non-empty result into `ShipfileValidationError`."""
    if not isinstance(data, dict):
        return [f"$: shipfile must be a YAML mapping at the top level, got {type(data).__name__}"]

    # --- shipfile_version short-circuit (founder finding, Session 004 close) ---------
    #
    # Runs before every other check, including "required block is missing" and "not a
    # recognized block". A parser that doesn't understand a file's declared version has
    # no standing to say anything about that file's *content* — "block X isn't
    # recognized" is a claim about a format this build has no knowledge of. A realistic
    # v0.2 file (new version + a new block + a widened bound + a new condition type)
    # used to come back as four errors, with the wrong-blame "not a recognized block"
    # error listed first — exactly the confusing outcome this field exists to prevent.
    # The only honest statement an old parser can make about a too-new file is "I can't
    # evaluate this" — so that's the *only* thing it says, once, and every other check
    # in this function is skipped entirely.
    #
    # A malformed `shipfile_version` (wrong type, unparseable string) does NOT
    # short-circuit — there's no "newer than supported" claim to make about a value
    # that isn't a real version, so it's reported as one ordinary error and everything
    # else in the file still gets checked normally.
    version, version_format_errors = _shipfile_version_format(data)
    if version is not None:
        upgrade_error = _shipfile_version_upgrade_error(version)
        if upgrade_error is not None:
            return [upgrade_error]

    errors: list[str] = list(version_format_errors)

    missing = [b for b in REQUIRED_BLOCKS if b not in data]
    for block in missing:
        errors.append(f"$.{block}: required block is missing")

    unknown = [k for k in data if k not in _KNOWN_TOP_LEVEL_KEYS]
    for block in unknown:
        errors.append(f"$.{block}: not a recognized shipfile v0.1 block")

    for block, schema in BLOCK_SCHEMAS.items():
        if block in data:
            errors.extend(_format_jsonschema_errors(f"$.{block}", data[block], schema))

    if "done_conditions" in data:
        conditions = data["done_conditions"]
        if not isinstance(conditions, list):
            errors.append(
                f"$.done_conditions: must be a list, got {type(conditions).__name__}"
            )
        else:
            for i, entry in enumerate(conditions):
                errors.extend(_validate_done_condition_entry(i, entry))
            errors.extend(_validate_done_condition_ids_are_unique(conditions))

    return errors

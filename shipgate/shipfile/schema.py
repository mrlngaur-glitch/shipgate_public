"""Shipfile v0.1 — the schema fragments (Task 1.4; report §13.2 step 2, Phase 1 row 1.4).

The shipfile is the project's declared contract: what "done" means, per task class, in
a form a checker can evaluate (Phase 2) instead of an agent asserting it. This module
defines the shape only — **no checker exists yet** (Phase 1 brief: "do not build a
checker; checkers are Phase 2"). Validating a shipfile here proves it is *well-formed*,
never that any of its conditions are *true* of a real project.

Eight required blocks (report §13.2 step 2): `task_classes`, `done_conditions`,
`routing`, `budgets`, `context_policy`, `intent`, `gate_policy`, `session_policy`.

**Gate A, verdict half: SIGNED OFF and FROZEN** (founder, Session 004). This half —
the shipfile — was sent back with three required changes, all applied in this revision:

1. **Model tiers, not model names** (report §16.1, the anti-obsolescence doctrine):
   `starting_model_tier` and `routing.default_model_tier`/`escalate_to` are now
   constrained to `frontier | mid | cheap | local` — see `MODEL_TIERS`. The tier→model
   mapping lives outside this frozen interface, in `shipgate/router/model_tiers.yaml`
   (data only; the router *engine* is F4 and is not built by this file's existence).
2. **The gate laws now have bounded numbers, not just locked names** — see the
   `gate_policy` / `session_policy` section below for the reasoning behind the specific
   bounds chosen.
3. **`done_conditions` is homogeneous**: every entry is an object with a required `id`,
   so every condition — including an EARS-style one — is addressable by
   `claims.shipfile_condition_ref` and unambiguous to any CI rule parsing the file. A
   bare EARS string is no longer a valid entry; EARS text is now the `ears` field on an
   object (see `EARS_CONDITION_SCHEMA`).

Each block is validated **independently**, against its own small schema fragment, not
one monolithic schema for the whole file. This is a deliberate choice, not a jsonschema
default: a single combined schema using `oneOf`/`anyOf` across blocks produces
jsonschema's well-known "not valid under any of the given schemas" non-answer on
failure — useless for the Phase 1 brief's actual done-condition ("each malformed
variant produces a *specific* error naming the offending block"). Validating block by
block means every error is already scoped to the block that produced it before any
message gets formatted.

Zero Claude-Code-specific imports — core-purity contract, `pyproject.toml`.
"""

from __future__ import annotations

# --- shipfile_version ---------------------------------------------------------------
#
# Gate A, final item (founder, Session 004 close). The format has been called "v0.1" in
# code, docs, and every error message since Task 1.4 — but no file ever declared it, and
# the field was REFUSED as an unrecognized block if a user wrote it anyway. Combined
# with strict unknown-block rejection, a future v0.2 file handed to *this* build fails
# with "$.shipfile_version: not a recognized shipfile v0.1 block" — blaming the
# version field's own name instead of naming the real gap. That specific wrong error is
# uniquely un-fixable later: once a v0.1 parser is out in the world rejecting this field,
# it can never be added to v0.2 without breaking every deployed v0.1 parser it might
# meet. It ships now, while there is still only one version in the wild, or the format
# can never gain a version marker without a breaking change to itself.
#
# `shipfile_version` is OPTIONAL. Absent means "0.1" — every shipfile already written
# before this field existed (including every fixture and worked example in this repo)
# stays valid with zero changes required.

#: The only shipfile schema version this build of ShipGate understands validating.
SUPPORTED_SHIPFILE_VERSION: str = "0.1"

#: `shipfile_version` must be `<major>.<minor>`, both non-negative integers — the
#: simplest shape that supports ordered comparison; nothing consumes this value yet
#: beyond that comparison, so a richer grammar (pre-release tags, etc.) is deferred
#: until something real needs it.
SHIPFILE_VERSION_PATTERN = r"^[0-9]+\.[0-9]+$"

#: The eight required top-level blocks, in report order.
REQUIRED_BLOCKS: tuple[str, ...] = (
    "task_classes",
    "done_conditions",
    "routing",
    "budgets",
    "context_policy",
    "intent",
    "gate_policy",
    "session_policy",
)

# --- model tiers -------------------------------------------------------------------
#
# report §16.1: "Routing references tiers (frontier / mid / cheap / local), never
# model names. A new model is a config-table row, not code." — one of the four
# tripwire responses and the anti-obsolescence doctrine, now enforced at the schema
# level everywhere the shipfile names a model target. The mapping from tier to actual
# model lives in `shipgate/router/model_tiers.yaml`, deliberately outside this frozen
# interface, so a new model release is a data-row change there, never a shipfile
# schema change (and never a change to any shipfile a user has already written).

MODEL_TIERS: tuple[str, ...] = ("frontier", "mid", "cheap", "local")

# --- task_classes --------------------------------------------------------------

#: report §13.2: "task_classes (each with risk_tier, starting_model_tier, max_tokens,
#: gate_strictness)". `risk_tier` and `gate_strictness` enums are chosen to match
#: vocabulary already used elsewhere in this project ("high-risk
#: change" language; report §6.3's advisory-first / strict distinction) rather than
#: invented fresh — one vocabulary, not two. `starting_model_tier` is constrained to
#: `MODEL_TIERS` (Session 004 Gate A fix #1) — previously an unconstrained string,
#: which meant `'opus'`, `'gpt-4o'`, and `'banana'` all validated equally.
TASK_CLASSES_SCHEMA = {
    "type": "object",
    "minProperties": 1,
    "additionalProperties": {
        "type": "object",
        "properties": {
            "risk_tier": {"enum": ["low", "medium", "high"]},
            "starting_model_tier": {"enum": list(MODEL_TIERS)},
            "max_tokens": {"type": "integer", "minimum": 1},
            "gate_strictness": {"enum": ["advisory", "strict"]},
        },
        "required": ["risk_tier", "starting_model_tier", "max_tokens", "gate_strictness"],
        "additionalProperties": False,
    },
}

# --- routing -----------------------------------------------------------------------
#
# report gives no full field-level spec for `routing` (it belongs to the M2/Ship 3
# policy engine, not built yet) — but it *does* give one explicit, load-bearing rule
# that applies regardless of when the engine lands (§16.1): any field that names a
# routing target names a *tier*, never a model. `default_model_tier` and an
# `escalation` list's `escalate_to` are the two fields this shipfile format currently
# has for that, so both are constrained now. Everything else about `routing` stays
# open (`additionalProperties: True`) — the smaller, safer commitment
# until the real engine defines the rest.

ROUTING_SCHEMA = {
    "type": "object",
    "properties": {
        "default_model_tier": {"enum": list(MODEL_TIERS)},
        "escalation": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "condition": {"type": "string", "minLength": 1},
                    "escalate_to": {"enum": list(MODEL_TIERS)},
                },
                "required": ["condition", "escalate_to"],
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": True,
}

# --- budgets / context_policy -------------------------------------------------------
# Neither has a field-level spec in the report (budget engine and cache shaping are
# both later releases) — v0.1 validates only that each is present and is a mapping.
# Over-specifying fields for machinery that doesn't exist yet risks freezing the wrong
# shape and needing a breaking change once the real engine lands; a loose block now,
# tightened later, is the smaller and safer commitment.
#
# Note for the worked example / docs, not a schema constraint: `budgets` fields like
# `max_cost_per_session_usd` are compared against a *derived* cost estimate from the
# price table (Task 1.6), never an observed one — see `docs/jsonl_format_notes.md` Q2
# and `docs/shipfile_worked_example.md`. The schema can't and shouldn't encode that;
# it's a documentation obligation, not a validation rule.

_OPEN_OBJECT_SCHEMA = {"type": "object"}

BUDGETS_SCHEMA = _OPEN_OBJECT_SCHEMA
CONTEXT_POLICY_SCHEMA = _OPEN_OBJECT_SCHEMA

# --- intent ----------------------------------------------------------------------
# The one required field is a plain-language anchor of what the project/task is for —
# the natural-language target the F2 Intent Mirror will eventually read back against a
# broad or frustrated request. No machinery yet; the anchor has to exist first.

INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "minLength": 1},
    },
    "required": ["summary"],
}

# --- gate_policy / session_policy -------------------------------------------------
# report §13.2: "gate_policy (max_retries default 3, on_retries_exhausted:
# honest_red_report, flake_policy: quarantine), session_policy
# (max_high_risk_changes_per_session default 3, on_exceeded: require_logged_override)".
#
# `on_retries_exhausted`, `flake_policy`, and `on_exceeded` are locked to their one
# legal value with `const`, not left as free-form strings a shipfile author could set
# to anything. This is a deliberate schema-level enforcement of this project's own rule
# ("loop-breaker and flake quarantine are law"): a shipfile literally cannot configure
# away these three invariants. The `default` lets an author omit the field entirely
# and get the lawful value for free.
#
# **Session 004 Gate A fix #2 — the numbers are now bounded too.** Locking the law's
# *name* while leaving its *number* unbounded (`max_retries: 999999` validated before
# this revision) defeats the law just as completely as leaving the name open would —
# report §13.3: "an infinite enforcement loop is a worse failure than a lie." Both
# `maximum` values below are chosen and argued, not picked round:
#
# - `maximum: 10` on both fields — roughly 3x the stated default of 3. That's real
#   headroom for a genuinely flaky environment (`max_retries`) or a project with an
#   unusually high but still *stated* risk tolerance (`max_high_risk_changes_per_
#   session`), while staying an order of magnitude below anything that reads as
#   "effectively unbounded." The property both fields protect — a loop or a session
#   that stays reviewable by a human — breaks down long before three-figure
#   territory, so the bound has to sit much closer to the default than to "no bound."
# - `minimum: 0` on **both** fields, and both are a stated decision, not an accident
#   of an unconstrained lower bound:
#   - `max_retries: 0` — "never retry, fail straight to an honest red report on the
#     first failure." A deliberate, legitimate configuration for a project at
#     declared go-live under `gate_strictness: strict` (report §6.3's "strictness
#     tightens at declared go-live") that wants zero tolerance, not a design
#     accident. Founder's own lean, adopted explicitly here.
#   - `max_high_risk_changes_per_session: 0` — analyst's extension of the identical
#     reasoning, logged per the founder's own pattern of inviting this kind of call:
#     "always require a logged override, even for the first high-risk change" is the
#     same kind of legitimate ultra-strict posture as `max_retries: 0`, and leaving
#     this one field asymmetric (minimum 1) with no stated reason would be exactly
#     the kind of unexplained inconsistency the rest of this file argues against.

GATE_POLICY_SCHEMA = {
    "type": "object",
    "properties": {
        "max_retries": {"type": "integer", "minimum": 0, "maximum": 10, "default": 3},
        "on_retries_exhausted": {"const": "honest_red_report", "default": "honest_red_report"},
        "flake_policy": {"const": "quarantine", "default": "quarantine"},
    },
    "additionalProperties": False,
}

SESSION_POLICY_SCHEMA = {
    "type": "object",
    "properties": {
        "max_high_risk_changes_per_session": {
            "type": "integer",
            "minimum": 0,
            "maximum": 10,
            "default": 3,
        },
        "on_exceeded": {"const": "require_logged_override", "default": "require_logged_override"},
    },
    "additionalProperties": False,
}

#: Maps every non-`done_conditions` block to the schema it's validated against.
BLOCK_SCHEMAS: dict[str, dict] = {
    "task_classes": TASK_CLASSES_SCHEMA,
    "routing": ROUTING_SCHEMA,
    "budgets": BUDGETS_SCHEMA,
    "context_policy": CONTEXT_POLICY_SCHEMA,
    "intent": INTENT_SCHEMA,
    "gate_policy": GATE_POLICY_SCHEMA,
    "session_policy": SESSION_POLICY_SCHEMA,
}

# --- done_conditions: the seven checker types, plus the EARS representation --------
#
# **Session 004 Gate A fix #3.** Every `done_conditions` entry is now an object with a
# required `id` — a bare EARS string is no longer valid on its own. Founder decision:
# "every done-condition is an object with an id; EARS text becomes a field on that
# object rather than a bare string." Report §13.2's "EARS-style condition strings
# allowed" is satisfied by `EARS_CONDITION_SCHEMA.ears`, not by a second array-item
# shape — `done_conditions` is homogeneous now: always a list of objects, always
# addressable by `id` (so `claims.shipfile_condition_ref` can point at any of them,
# EARS-style or checker-typed alike), never a mixed array of objects and raw strings
# every CI rule parsing the file would otherwise have to special-case.
#
# `type` is fixed per-schema via `const` so a hand-edited YAML file can't spell a
# condition as `"tests_pas"` and have it silently validate against the wrong schema —
# each type-schema only accepts its own exact type string.

_DURATION_FIELDS = {
    # format is checked separately by a regex (see validator.py) — jsonschema's
    # "format" keyword isn't guaranteed to be enforced without an optional extra
    # dependency, so the check is done explicitly rather than relying on that.
    "verify_after": {"type": "string", "minLength": 1},
}

_COMMON_CONDITION_PROPERTIES = {
    "id": {"type": "string", "minLength": 1},
    "description": {"type": "string"},
    "task_class": {"type": "string"},
    **_DURATION_FIELDS,
}


def _condition_schema(type_name: str, extra_properties: dict, required_extra: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "type": {"const": type_name},
            **_COMMON_CONDITION_PROPERTIES,
            **extra_properties,
        },
        "required": ["id", "type", *required_extra],
        "additionalProperties": False,
    }


CONDITION_TYPE_SCHEMAS: dict[str, dict] = {
    "tests_pass": _condition_schema(
        "tests_pass",
        {"command": {"type": "string", "minLength": 1}},
        ["command"],
    ),
    "file_exists": _condition_schema(
        "file_exists",
        {"path": {"type": "string", "minLength": 1}},
        ["path"],
    ),
    "forbidden_pattern_absent": _condition_schema(
        "forbidden_pattern_absent",
        {
            "pattern": {"type": "string", "minLength": 1},
            "paths": {"type": "array", "items": {"type": "string"}},
        },
        ["pattern"],
    ),
    "command_succeeds": _condition_schema(
        "command_succeeds",
        {"command": {"type": "string", "minLength": 1}},
        ["command"],
    ),
    "emission_traced": _condition_schema(
        "emission_traced",
        {"marker": {"type": "string", "minLength": 1}},
        ["marker"],
    ),
    "runtime_evidence": _condition_schema(
        "runtime_evidence",
        {
            "method": {"enum": ["log_marker", "endpoint_probe", "db_query"]},
            "detail": {"type": "string", "minLength": 1},
        },
        ["method", "detail"],
    ),
    "inventory_complete": _condition_schema(
        "inventory_complete",
        {
            "grep_pattern": {"type": "string", "minLength": 1},
            "required_dispositions": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
            },
        },
        ["grep_pattern", "required_dispositions"],
    ),
}

#: The EARS-style representation (report §13.2: "EARS-style condition strings
#: allowed"). Not one of the seven checker types — no `type` field at all, since it
#: isn't dispatched to a checker; `validator.py` distinguishes this shape from a
#: checker-typed entry by the presence of `ears` instead of `type`.
EARS_CONDITION_SCHEMA = {
    "type": "object",
    "properties": {
        "ears": {"type": "string", "minLength": 1},
        **_COMMON_CONDITION_PROPERTIES,
    },
    "required": ["id", "ears"],
    "additionalProperties": False,
}

#: `verify_after` accepts a plain integer count of a unit — e.g. `24h`, `7d`, `30m`.
#: Deliberately simple (no full ISO-8601 duration grammar) since nothing consumes this
#: value yet; the F2 recheck producer is free to demand a richer grammar later without
#: this being a breaking change to the shipfile format itself (the field stays a
#: string either way).
VERIFY_AFTER_PATTERN = r"^[0-9]+(m|h|d|w)$"

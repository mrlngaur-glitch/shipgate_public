"""Task 1.4 — shipfile v0.1 tests (Phase 1 brief done-condition):

    "Valid shipfile parses; each malformed variant produces a specific error naming
    the offending block, not a generic one — runtime-verified"

No checker is tested here, and none exists — Phase 1 brief: "do not build a checker;
checkers are Phase 2." Every test proves the shipfile is well-formed or specifically
not; none claims anything about a real project.

**Session 004 — Gate A review, shipfile half sent back with three required changes,**
all reflected in the fixture and tests below: model *tiers* not model names;
`gate_policy`/`session_policy` numbers bounded, not just their names locked;
`done_conditions` entries are homogeneous objects with an `id` (EARS text is now a
field, not a bare string).
"""

from pathlib import Path

import pytest
import yaml

from shipgate.shipfile import (
    CONDITION_TYPE_SCHEMAS,
    REQUIRED_BLOCKS,
    ShipfileSyntaxError,
    ShipfileValidationError,
    load_shipfile,
    parse_shipfile,
)
from shipgate.shipfile.schema import MODEL_TIERS, SUPPORTED_SHIPFILE_VERSION

REPO_ROOT = Path(__file__).resolve().parents[2]

VALID_SHIPFILE_YAML = """
task_classes:
  refactor:
    risk_tier: medium
    starting_model_tier: mid
    max_tokens: 100000
    gate_strictness: strict
  hotfix:
    risk_tier: high
    starting_model_tier: frontier
    max_tokens: 50000
    gate_strictness: strict

done_conditions:
  - id: tests-pass
    type: tests_pass
    command: pytest -q
  - id: file-exists
    type: file_exists
    path: shipgate/cli.py
  - id: no-todo
    type: forbidden_pattern_absent
    pattern: "TODO"
    paths: ["shipgate/"]
  - id: cmd-ok
    type: command_succeeds
    command: ruff check .
  - id: emitted
    type: emission_traced
    marker: SHIPGATE_STARTED
  - id: runtime-ev
    type: runtime_evidence
    method: log_marker
    detail: "log line X fired"
    verify_after: "24h"
  - id: inv-complete
    type: inventory_complete
    grep_pattern: "def old_func"
    required_dispositions: ["removed", "migrated"]
  - id: ears-example
    ears: "WHEN the user submits the form THE SYSTEM SHALL validate all required fields"

routing:
  default_model_tier: mid
  escalation:
    - condition: hotfix
      escalate_to: frontier
budgets:
  max_tokens_per_session: 500000
context_policy:
  cache_ttl_preference: 1h
intent:
  summary: Ship a working login page
gate_policy: {}
session_policy: {}
"""


def _mutated(yaml_text: str, old: str, new: str) -> str:
    assert old in yaml_text, f"fixture no longer contains {old!r} -- test is stale"
    return yaml_text.replace(old, new)


# --- the valid case ----------------------------------------------------------------


def test_valid_shipfile_with_all_eight_blocks_and_all_seven_condition_types_parses():
    sf = parse_shipfile(VALID_SHIPFILE_YAML)
    assert set(REQUIRED_BLOCKS) <= set(sf.raw.keys())
    condition_types = {c["type"] for c in sf.raw["done_conditions"] if "type" in c}
    assert condition_types == set(CONDITION_TYPE_SCHEMAS)


def test_ears_style_condition_is_an_addressable_object_not_a_bare_string():
    sf = parse_shipfile(VALID_SHIPFILE_YAML)
    ears_entries = [c for c in sf.raw["done_conditions"] if "ears" in c]
    assert len(ears_entries) == 1
    assert ears_entries[0]["id"] == "ears-example"
    assert ears_entries[0]["ears"].startswith("WHEN")
    assert "type" not in ears_entries[0]


def test_every_done_condition_entry_is_a_dict_with_an_id():
    """The homogeneity Session 004 required: no bare strings anywhere in the array."""
    sf = parse_shipfile(VALID_SHIPFILE_YAML)
    for entry in sf.raw["done_conditions"]:
        assert isinstance(entry, dict)
        assert isinstance(entry.get("id"), str) and entry["id"]


def test_gate_policy_and_session_policy_defaults_are_filled_in_when_omitted():
    sf = parse_shipfile(VALID_SHIPFILE_YAML)
    assert sf.raw["gate_policy"] == {
        "max_retries": 3,
        "on_retries_exhausted": "honest_red_report",
        "flake_policy": "quarantine",
    }
    assert sf.raw["session_policy"] == {
        "max_high_risk_changes_per_session": 3,
        "on_exceeded": "require_logged_override",
    }


def test_explicit_max_retries_overrides_the_default_but_the_law_fields_stay_locked():
    text = _mutated(VALID_SHIPFILE_YAML, "gate_policy: {}", "gate_policy: {max_retries: 5}")
    sf = parse_shipfile(text)
    assert sf.raw["gate_policy"]["max_retries"] == 5
    assert sf.raw["gate_policy"]["on_retries_exhausted"] == "honest_red_report"


# --- malformed YAML syntax ----------------------------------------------------------


def test_malformed_yaml_syntax_raises_shipfile_syntax_error_not_validation_error():
    with pytest.raises(ShipfileSyntaxError):
        parse_shipfile("task_classes: [unterminated")


def test_yaml_that_parses_to_a_list_not_a_mapping_is_a_specific_validation_error():
    with pytest.raises(ShipfileValidationError) as exc_info:
        parse_shipfile("- just\n- a\n- list\n")
    assert any("must be a YAML mapping" in e for e in exc_info.value.errors)


# --- missing / unknown blocks --------------------------------------------------------


@pytest.mark.parametrize("block", REQUIRED_BLOCKS)
def test_each_missing_required_block_produces_a_specific_error_naming_that_block(block):
    data = yaml.safe_load(VALID_SHIPFILE_YAML)
    del data[block]
    text = yaml.safe_dump(data)
    with pytest.raises(ShipfileValidationError) as exc_info:
        parse_shipfile(text)
    assert any(f"${'.'}{block}: required block is missing" in e for e in exc_info.value.errors)


def test_unknown_top_level_block_is_a_specific_error_naming_it():
    text = VALID_SHIPFILE_YAML + "\nnonsense_block: true\n"
    with pytest.raises(ShipfileValidationError) as exc_info:
        parse_shipfile(text)
    assert any("$.nonsense_block: not a recognized" in e for e in exc_info.value.errors)


# --- Gate A final item: shipfile_version --------------------------------------------
#
# "Both directions": a supported version parses; an unsupported future version gives
# the upgrade error, not a block error. `VALID_SHIPFILE_YAML` itself carries no
# `shipfile_version` at all, so every other test in this file already exercises the
# "absent means 0.1" default -- the tests below are the explicit, positive-control
# proof of that plus the two directions named above.


def test_shipfile_version_absent_is_accepted_as_the_supported_default():
    """Every shipfile written before this field existed has no `shipfile_version` key
    at all -- `VALID_SHIPFILE_YAML` is exactly such a file. It must keep parsing with
    zero changes required."""
    assert "shipfile_version" not in yaml.safe_load(VALID_SHIPFILE_YAML)
    sf = parse_shipfile(VALID_SHIPFILE_YAML)
    assert "shipfile_version" not in sf.raw or sf.raw["shipfile_version"] == SUPPORTED_SHIPFILE_VERSION


def test_shipfile_version_matching_the_supported_version_parses():
    text = f"shipfile_version: {SUPPORTED_SHIPFILE_VERSION!r}\n" + VALID_SHIPFILE_YAML
    sf = parse_shipfile(text)
    assert sf.raw["shipfile_version"] == SUPPORTED_SHIPFILE_VERSION


def test_shipfile_version_older_than_supported_is_accepted():
    text = "shipfile_version: '0.0'\n" + VALID_SHIPFILE_YAML
    sf = parse_shipfile(text)
    assert sf.raw["shipfile_version"] == "0.0"


def test_shipfile_version_newer_than_supported_gives_the_upgrade_error_not_a_block_error():
    """The one error this field exists to get right: NOT '$.shipfile_version: not a
    recognized shipfile v0.1 block' (that would blame the field's own name), but a
    specific 'upgrade ShipGate' message naming both the file's version and the
    supported one -- and that error is the ONLY one returned (founder finding: an old
    parser has no standing to say anything about a format it doesn't know)."""
    text = "shipfile_version: '99.0'\n" + VALID_SHIPFILE_YAML
    with pytest.raises(ShipfileValidationError) as exc_info:
        parse_shipfile(text)
    errors = exc_info.value.errors
    assert len(errors) == 1
    assert "Upgrade ShipGate" in errors[0] and "99.0" in errors[0] and SUPPORTED_SHIPFILE_VERSION in errors[0]


def test_shipfile_version_newer_than_supported_short_circuits_even_when_the_rest_of_the_file_has_real_errors():
    """The case that will actually occur: a realistic v0.2 file, not a clean v0.1 file
    with only the version bumped. Built with a new block (`evidence_policy`), a
    gate_policy value above today's bound, and a done_conditions entry using a
    condition type that doesn't exist yet -- exactly what a real v0.2 file looks like.
    Before the fix, this produced FOUR errors with the wrong-blame block error first;
    a v0.1 parser has no standing to evaluate any of that content, so the fix must
    produce exactly one error: the upgrade message, and nothing else."""
    data = yaml.safe_load(VALID_SHIPFILE_YAML)
    data["shipfile_version"] = "0.2"
    data["evidence_policy"] = {"require_attestation": True}  # a block this build has never heard of
    data["gate_policy"] = {"max_retries": 25}  # above today's bound -- would be its own error
    data["done_conditions"].append(
        {"id": "attested", "type": "attestation_signed", "signature": "abc"}  # a type that doesn't exist yet
    )
    text = yaml.safe_dump(data)

    with pytest.raises(ShipfileValidationError) as exc_info:
        parse_shipfile(text)
    errors = exc_info.value.errors
    assert len(errors) == 1
    assert "Upgrade ShipGate" in errors[0] and "0.2" in errors[0]


def test_shipfile_version_malformed_string_is_a_specific_error():
    text = "shipfile_version: banana\n" + VALID_SHIPFILE_YAML
    with pytest.raises(ShipfileValidationError) as exc_info:
        parse_shipfile(text)
    assert any("$.shipfile_version" in e and "banana" in e for e in exc_info.value.errors)


def test_shipfile_version_wrong_type_is_a_specific_error():
    text = "shipfile_version: 1\n" + VALID_SHIPFILE_YAML
    with pytest.raises(ShipfileValidationError) as exc_info:
        parse_shipfile(text)
    assert any("$.shipfile_version" in e for e in exc_info.value.errors)


def test_shipfile_version_unquoted_decimal_gives_a_quoting_hint_not_a_confusing_echo():
    """Founder finding: `shipfile_version: 0.1` (unquoted) is parsed by YAML as the
    FLOAT 0.1, not the string "0.1" -- the exact trap docker-compose's `version:` key
    is known for. The old message rejected the value and then displayed what looked
    like the identical value back as the 'fix', explaining nothing. The new message
    must say to quote it."""
    text = "shipfile_version: 0.1\n" + VALID_SHIPFILE_YAML
    parsed = yaml.safe_load(text)
    assert isinstance(parsed["shipfile_version"], float), "fixture no longer reproduces the YAML float trap"

    with pytest.raises(ShipfileValidationError) as exc_info:
        parse_shipfile(text)
    errors = exc_info.value.errors
    assert any("$.shipfile_version" in e and "quote it" in e for e in errors)


def test_shipfile_version_malformed_value_does_not_short_circuit_the_rest_of_the_file():
    """The short-circuit is deliberately narrow: it only fires when the version is
    well-formed AND too new. A malformed version (this build can't even tell if it's
    newer) must NOT swallow unrelated real errors elsewhere in the same file."""
    text = "shipfile_version: banana\n" + VALID_SHIPFILE_YAML + "\nnonsense_block: true\n"
    with pytest.raises(ShipfileValidationError) as exc_info:
        parse_shipfile(text)
    errors = exc_info.value.errors
    assert any("$.shipfile_version" in e and "banana" in e for e in errors)
    assert any("$.nonsense_block: not a recognized" in e for e in errors)
    assert len(errors) >= 2


def test_the_gate_a_worked_example_declares_its_shipfile_version():
    sf = load_shipfile(REPO_ROOT / "docs" / "shipfile_worked_example.yaml")
    assert sf.raw["shipfile_version"] == SUPPORTED_SHIPFILE_VERSION


# --- malformed task_classes ----------------------------------------------------------


def test_invalid_risk_tier_enum_value_is_a_specific_error_naming_task_classes():
    text = _mutated(VALID_SHIPFILE_YAML, "risk_tier: medium", "risk_tier: extreme")
    with pytest.raises(ShipfileValidationError) as exc_info:
        parse_shipfile(text)
    assert any("task_classes" in e and "risk_tier" in e for e in exc_info.value.errors)


def test_task_classes_entry_missing_a_required_field_is_a_specific_error():
    text = _mutated(VALID_SHIPFILE_YAML, "    max_tokens: 100000\n", "")
    with pytest.raises(ShipfileValidationError) as exc_info:
        parse_shipfile(text)
    assert any("task_classes" in e and "max_tokens" in e for e in exc_info.value.errors)


# --- Session 004 fix #1: model tiers, never model names -----------------------------


def test_task_classes_starting_model_tier_rejects_a_model_name():
    text = _mutated(VALID_SHIPFILE_YAML, "starting_model_tier: mid", "starting_model_tier: claude-sonnet-5")
    with pytest.raises(ShipfileValidationError) as exc_info:
        parse_shipfile(text)
    errors = exc_info.value.errors
    assert any("task_classes" in e and "starting_model_tier" in e for e in errors)
    assert any(str(sorted(MODEL_TIERS)) in e or "frontier" in e for e in errors)


def test_task_classes_starting_model_tier_accepts_every_legal_tier():
    for tier in MODEL_TIERS:
        text = _mutated(VALID_SHIPFILE_YAML, "starting_model_tier: mid", f"starting_model_tier: {tier}")
        sf = parse_shipfile(text)
        assert sf.raw["task_classes"]["refactor"]["starting_model_tier"] == tier


def test_routing_default_model_tier_rejects_a_model_name():
    text = _mutated(VALID_SHIPFILE_YAML, "default_model_tier: mid", "default_model_tier: gpt-4o")
    with pytest.raises(ShipfileValidationError) as exc_info:
        parse_shipfile(text)
    assert any("routing" in e and "default_model_tier" in e for e in exc_info.value.errors)


def test_routing_escalation_escalate_to_rejects_a_model_name():
    text = _mutated(VALID_SHIPFILE_YAML, "escalate_to: frontier", "escalate_to: opus")
    with pytest.raises(ShipfileValidationError) as exc_info:
        parse_shipfile(text)
    assert any("routing" in e and "escalate_to" in e for e in exc_info.value.errors)


def test_model_tiers_config_table_exists_and_matches_the_frozen_enum():
    """The tier -> model mapping lives outside the frozen shipfile interface
    (report §16.1 anti-obsolescence doctrine) but its tier *names* must still match
    MODEL_TIERS exactly, or the two would silently drift apart."""
    tiers_path = REPO_ROOT / "shipgate" / "router" / "model_tiers.yaml"
    data = yaml.safe_load(tiers_path.read_text(encoding="utf-8"))
    assert set(data["tiers"].keys()) == set(MODEL_TIERS)


# --- Session 004 fix #2: gate_policy / session_policy numbers are bounded -----------


def test_max_retries_above_the_bound_is_a_specific_error():
    text = _mutated(VALID_SHIPFILE_YAML, "gate_policy: {}", "gate_policy: {max_retries: 999999}")
    with pytest.raises(ShipfileValidationError) as exc_info:
        parse_shipfile(text)
    assert any("gate_policy" in e and "max_retries" in e for e in exc_info.value.errors)


def test_max_high_risk_changes_above_the_bound_is_a_specific_error():
    text = _mutated(
        VALID_SHIPFILE_YAML, "session_policy: {}", "session_policy: {max_high_risk_changes_per_session: 99999}"
    )
    with pytest.raises(ShipfileValidationError) as exc_info:
        parse_shipfile(text)
    assert any(
        "session_policy" in e and "max_high_risk_changes_per_session" in e for e in exc_info.value.errors
    )


def test_max_retries_zero_is_intentionally_legal():
    """Founder decision: 'never retry, fail straight to an honest red' is a deliberate
    strict-mode configuration, not an accident of an unconstrained lower bound."""
    text = _mutated(VALID_SHIPFILE_YAML, "gate_policy: {}", "gate_policy: {max_retries: 0}")
    sf = parse_shipfile(text)
    assert sf.raw["gate_policy"]["max_retries"] == 0


def test_max_high_risk_changes_zero_is_intentionally_legal():
    """Analyst's extension of the same reasoning (logged in schema.py): 'always
    require a logged override' is the same kind of legitimate ultra-strict posture."""
    text = _mutated(
        VALID_SHIPFILE_YAML, "session_policy: {}", "session_policy: {max_high_risk_changes_per_session: 0}"
    )
    sf = parse_shipfile(text)
    assert sf.raw["session_policy"]["max_high_risk_changes_per_session"] == 0


def test_max_retries_negative_is_still_a_specific_error():
    text = _mutated(VALID_SHIPFILE_YAML, "gate_policy: {}", "gate_policy: {max_retries: -1}")
    with pytest.raises(ShipfileValidationError) as exc_info:
        parse_shipfile(text)
    assert any("gate_policy" in e and "max_retries" in e for e in exc_info.value.errors)


def test_max_retries_at_the_bound_is_legal():
    text = _mutated(VALID_SHIPFILE_YAML, "gate_policy: {}", "gate_policy: {max_retries: 10}")
    sf = parse_shipfile(text)
    assert sf.raw["gate_policy"]["max_retries"] == 10


# --- malformed done_conditions -------------------------------------------------------


def test_unrecognized_condition_type_is_a_specific_error_listing_the_real_seven():
    text = _mutated(VALID_SHIPFILE_YAML, "type: tests_pass", "type: test_pas")
    with pytest.raises(ShipfileValidationError) as exc_info:
        parse_shipfile(text)
    errors = exc_info.value.errors
    assert any("done_conditions[0].type" in e and "test_pas" in e for e in errors)
    assert any("tests_pass" in e for e in errors)  # the correct type is named as a hint


def test_condition_missing_its_required_type_specific_field_is_a_specific_error():
    """`tests_pass` requires `command`; omit it and the error must name both the
    condition's index and the missing field, not a generic 'invalid entry'."""
    text = _mutated(
        VALID_SHIPFILE_YAML,
        "  - id: tests-pass\n    type: tests_pass\n    command: pytest -q\n",
        "  - id: tests-pass\n    type: tests_pass\n",
    )
    with pytest.raises(ShipfileValidationError) as exc_info:
        parse_shipfile(text)
    assert any("done_conditions[0]" in e and "command" in e for e in exc_info.value.errors)


def test_bare_ears_string_is_no_longer_accepted_and_the_error_names_the_new_form():
    """Session 004 fix #3: a bare string was legal before this session; it must now be
    refused with an error that tells the author how to fix it (wrap as an object)."""
    text = _mutated(
        VALID_SHIPFILE_YAML,
        '  - id: ears-example\n    ears: "WHEN the user submits the form THE SYSTEM SHALL validate all required fields"',
        '  - "WHEN the user submits the form THE SYSTEM SHALL validate all required fields"',
    )
    with pytest.raises(ShipfileValidationError) as exc_info:
        parse_shipfile(text)
    errors = exc_info.value.errors
    assert any("done_conditions[7]" in e and "no longer accepted" in e for e in errors)
    assert any("ears" in e for e in errors)


def test_done_condition_entry_that_is_not_an_object_at_all_is_a_specific_error():
    text = _mutated(
        VALID_SHIPFILE_YAML,
        '  - id: ears-example\n    ears: "WHEN the user submits the form THE SYSTEM SHALL validate all required fields"',
        "  - 42",
    )
    with pytest.raises(ShipfileValidationError) as exc_info:
        parse_shipfile(text)
    assert any("must be an object" in e for e in exc_info.value.errors)


def test_empty_ears_field_is_a_specific_error():
    text = _mutated(
        VALID_SHIPFILE_YAML,
        'ears: "WHEN the user submits the form THE SYSTEM SHALL validate all required fields"',
        'ears: ""',
    )
    with pytest.raises(ShipfileValidationError) as exc_info:
        parse_shipfile(text)
    assert any("done_conditions[7]" in e and "ears" in e for e in exc_info.value.errors)


def test_entry_with_both_type_and_ears_is_a_specific_error():
    text = _mutated(
        VALID_SHIPFILE_YAML,
        '  - id: ears-example\n    ears: "WHEN the user submits the form THE SYSTEM SHALL validate all required fields"',
        '  - id: ears-example\n    type: tests_pass\n    command: pytest\n    ears: "also EARS"',
    )
    with pytest.raises(ShipfileValidationError) as exc_info:
        parse_shipfile(text)
    assert any("never both" in e for e in exc_info.value.errors)


def test_entry_with_neither_type_nor_ears_is_a_specific_error():
    text = _mutated(
        VALID_SHIPFILE_YAML,
        '  - id: ears-example\n    ears: "WHEN the user submits the form THE SYSTEM SHALL validate all required fields"',
        "  - id: mystery-condition\n    description: no type, no ears",
    )
    with pytest.raises(ShipfileValidationError) as exc_info:
        parse_shipfile(text)
    assert any("neither is present" in e for e in exc_info.value.errors)


def test_duplicate_done_condition_id_is_a_specific_error():
    text = _mutated(VALID_SHIPFILE_YAML, "id: cmd-ok", "id: tests-pass")
    with pytest.raises(ShipfileValidationError) as exc_info:
        parse_shipfile(text)
    errors = exc_info.value.errors
    assert any("tests-pass" in e and "more than one entry" in e for e in errors)


def test_done_conditions_that_is_not_a_list_is_a_specific_error():
    data = yaml.safe_load(VALID_SHIPFILE_YAML)
    data["done_conditions"] = "not a list"
    text = yaml.safe_dump(data)
    with pytest.raises(ShipfileValidationError) as exc_info:
        parse_shipfile(text)
    assert any("done_conditions: must be a list" in e for e in exc_info.value.errors)


def test_malformed_verify_after_is_a_specific_error():
    text = _mutated(VALID_SHIPFILE_YAML, 'verify_after: "24h"', 'verify_after: "banana"')
    with pytest.raises(ShipfileValidationError) as exc_info:
        parse_shipfile(text)
    assert any("verify_after" in e and "banana" in e for e in exc_info.value.errors)


def test_inventory_complete_missing_required_dispositions_is_a_specific_error():
    text = _mutated(
        VALID_SHIPFILE_YAML,
        '    required_dispositions: ["removed", "migrated"]',
        "",
    )
    with pytest.raises(ShipfileValidationError) as exc_info:
        parse_shipfile(text)
    assert any(
        "done_conditions" in e and "required_dispositions" in e for e in exc_info.value.errors
    )


def test_runtime_evidence_invalid_method_enum_is_a_specific_error():
    text = _mutated(VALID_SHIPFILE_YAML, "method: log_marker", "method: carrier_pigeon")
    with pytest.raises(ShipfileValidationError) as exc_info:
        parse_shipfile(text)
    assert any("done_conditions" in e and "method" in e for e in exc_info.value.errors)


# --- gate_policy / session_policy law fields can't be configured away --------------


def test_gate_policy_on_retries_exhausted_cannot_be_overridden_to_anything_else():
    """This project's own standing rule: 'loop-breaker and flake quarantine are law.'
    A shipfile must not be able to configure this away."""
    text = _mutated(VALID_SHIPFILE_YAML, "gate_policy: {}", "gate_policy: {on_retries_exhausted: retry_forever}")
    with pytest.raises(ShipfileValidationError) as exc_info:
        parse_shipfile(text)
    assert any("gate_policy" in e and "on_retries_exhausted" in e for e in exc_info.value.errors)


def test_gate_policy_flake_policy_cannot_be_overridden_to_anything_else():
    text = _mutated(VALID_SHIPFILE_YAML, "gate_policy: {}", "gate_policy: {flake_policy: ignore}")
    with pytest.raises(ShipfileValidationError) as exc_info:
        parse_shipfile(text)
    assert any("gate_policy" in e and "flake_policy" in e for e in exc_info.value.errors)


def test_session_policy_on_exceeded_cannot_be_overridden_to_anything_else():
    text = _mutated(VALID_SHIPFILE_YAML, "session_policy: {}", "session_policy: {on_exceeded: allow_silently}")
    with pytest.raises(ShipfileValidationError) as exc_info:
        parse_shipfile(text)
    assert any("session_policy" in e and "on_exceeded" in e for e in exc_info.value.errors)


def test_unknown_field_in_gate_policy_is_rejected():
    text = _mutated(VALID_SHIPFILE_YAML, "gate_policy: {}", "gate_policy: {made_up_field: 1}")
    with pytest.raises(ShipfileValidationError) as exc_info:
        parse_shipfile(text)
    assert any("gate_policy" in e for e in exc_info.value.errors)


# --- intent -------------------------------------------------------------------------


def test_intent_missing_summary_is_a_specific_error():
    text = _mutated(VALID_SHIPFILE_YAML, "intent:\n  summary: Ship a working login page", "intent: {}")
    with pytest.raises(ShipfileValidationError) as exc_info:
        parse_shipfile(text)
    assert any("intent" in e and "summary" in e for e in exc_info.value.errors)


# --- multiple errors reported together, each specific ------------------------------


def test_multiple_malformed_blocks_each_produce_their_own_named_error():
    text = _mutated(VALID_SHIPFILE_YAML, "risk_tier: medium", "risk_tier: extreme")
    text = _mutated(text, "gate_policy: {}", "gate_policy: {flake_policy: ignore}")
    with pytest.raises(ShipfileValidationError) as exc_info:
        parse_shipfile(text)
    errors = exc_info.value.errors
    assert any("task_classes" in e for e in errors)
    assert any("gate_policy" in e for e in errors)
    assert len(errors) >= 2


# --- the Gate A worked example, loaded from disk ------------------------------------


def test_the_gate_a_worked_example_shipfile_parses():
    """docs/shipfile_worked_example.yaml is what gets presented at Gate A -- it must
    actually parse, not just look plausible."""
    sf = load_shipfile(REPO_ROOT / "docs" / "shipfile_worked_example.yaml")
    assert set(REQUIRED_BLOCKS) <= set(sf.raw.keys())


def test_the_gate_a_worked_example_covers_all_seven_condition_types():
    sf = load_shipfile(REPO_ROOT / "docs" / "shipfile_worked_example.yaml")
    condition_types = {c["type"] for c in sf.raw["done_conditions"] if "type" in c}
    assert condition_types == set(CONDITION_TYPE_SCHEMAS)


def test_the_gate_a_worked_example_includes_an_addressable_ears_condition():
    sf = load_shipfile(REPO_ROOT / "docs" / "shipfile_worked_example.yaml")
    ears_entries = [c for c in sf.raw["done_conditions"] if "ears" in c]
    assert len(ears_entries) == 1
    assert ears_entries[0]["id"]


def test_the_gate_a_worked_example_uses_tiers_not_model_names_everywhere():
    sf = load_shipfile(REPO_ROOT / "docs" / "shipfile_worked_example.yaml")
    for task_class in sf.raw["task_classes"].values():
        assert task_class["starting_model_tier"] in MODEL_TIERS
    assert sf.raw["routing"]["default_model_tier"] in MODEL_TIERS
    for rule in sf.raw["routing"].get("escalation", []):
        assert rule["escalate_to"] in MODEL_TIERS


def test_the_gate_a_worked_example_done_condition_ids_are_all_unique():
    sf = load_shipfile(REPO_ROOT / "docs" / "shipfile_worked_example.yaml")
    ids = [c["id"] for c in sf.raw["done_conditions"]]
    assert len(ids) == len(set(ids))

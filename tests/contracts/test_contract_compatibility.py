import copy
import json

import pytest

from shared.contract_compatibility import MAJOR, MINOR, PATCH, classify, compare


def contract(schema, number="1.2.3", changelog="Baseline"):
    return {
        "VERSION": number,
        "CHANGELOG.md": changelog,
        "sample.schema.json": json.dumps(schema),
    }


def test_optional_field_requires_minor_version():
    before = {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}
    after = copy.deepcopy(before)
    after["properties"]["note"] = {"type": "string"}
    with pytest.raises(ValueError, match="minor"):
        compare(contract(before), contract(after, "1.2.4", "Added note"))
    assert compare(contract(before), contract(after, "1.3.0", "Added note"))[0] == MINOR


def test_new_required_field_requires_major_version():
    before = {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}
    after = copy.deepcopy(before)
    after["properties"]["version"] = {"type": "string"}
    after["required"].append("version")
    with pytest.raises(ValueError, match="major"):
        compare(contract(before), contract(after, "1.3.0", "Required version"))
    assert compare(contract(before), contract(after, "2.0.0", "Required version"))[0] == MAJOR


def test_new_codes_are_minor_but_removed_codes_are_major():
    before = {"type": "string", "enum": ["Ready", "Blocked"]}
    after = {"type": "string", "enum": ["Ready", "Blocked", "Future"]}
    assert classify(before, after)[0] == MINOR
    assert classify(after, before)[0] == MAJOR


def test_removing_optional_field_is_breaking():
    before = {"type": "object", "properties": {"note": {"type": "string"}}}
    after = {"type": "object", "properties": {}}
    assert classify(before, after)[0] == MAJOR


def test_descriptions_and_examples_only_need_patch():
    before = {"type": "string", "description": "Old"}
    after = {"type": "string", "description": "Clearer", "examples": ["demo"]}
    assert compare(contract(before), contract(after, "1.2.4", "Clarified"))[0] == PATCH


def test_changelog_is_required_even_with_correct_version():
    with pytest.raises(ValueError, match="CHANGELOG"):
        compare(
            contract({"type": "string", "enum": ["Ready"]}),
            contract({"type": "string", "enum": ["Ready", "Future"]}, "1.3.0"),
        )


def test_new_event_schema_is_minor_and_removal_is_major():
    before = contract({"type": "object"})
    after = {**before, "VERSION": "1.3.0", "CHANGELOG.md": "New event", "new.schema.json": "{}"}
    assert compare(before, after)[0] == MINOR
    removed = {key: value for key, value in after.items() if key != "new.schema.json"}
    removed.update(VERSION="2.0.0", **{"CHANGELOG.md": "Removed event"})
    assert compare(after, removed)[0] == MAJOR


def test_unchanged_contract_needs_no_version_bump():
    original = contract({"type": "string"})
    assert compare(original, original) == (0, [])


def test_version_cannot_regress_even_without_schema_changes():
    with pytest.raises(ValueError, match="cannot decrease"):
        compare(contract({"type": "string"}), contract({"type": "string"}, "1.2.2"))


@pytest.mark.parametrize("name", ["description", "summary", "enum", "required"])
def test_field_names_are_not_mistaken_for_schema_keywords(name):
    before = {"type": "object", "properties": {name: {"type": "string"}}}
    after = {"type": "object", "properties": {name: {"type": "number"}}}
    assert classify(before, after)[0] == MAJOR

import json
import shutil
from pathlib import Path

import pytest
from jsonschema import ValidationError

from shared.contract_validation import load_validators, validate_examples

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"


def example(name):
    return json.loads((CONTRACTS / "examples" / name).read_text(encoding="utf-8"))


def test_declared_examples_validate():
    validate_examples(CONTRACTS)


@pytest.mark.parametrize("field", ["caseId", "eventId", "schemaVersion", "data"])
def test_envelope_rejects_missing_required_fields(field):
    payload = example("photo-uploaded.json")
    del payload[field]
    with pytest.raises(ValidationError):
        load_validators(CONTRACTS)["events/event-envelope.schema.json"].validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("caseId", "not-a-uuid"),
        ("eventId", "not-a-uuid"),
        ("occurredAt", "2026-10-27T09:00:00"),
        ("schemaVersion", "01.0.0"),
        ("data", []),
    ],
)
def test_envelope_rejects_invalid_values(field, value):
    payload = example("photo-uploaded.json")
    payload[field] = value
    with pytest.raises(ValidationError):
        load_validators(CONTRACTS)["events/event-envelope.schema.json"].validate(payload)


def test_nullable_case_and_future_fields_are_allowed():
    payload = example("pre-case-upload.json")
    payload["futureField"] = {"new": True}
    load_validators(CONTRACTS)["events/event-envelope.schema.json"].validate(payload)


def test_ready_signal_cannot_contain_unmet_conditions():
    payload = example("readiness-ready.json")
    payload["readinessMissing"] = ["PL-0042-01"]
    with pytest.raises(ValidationError):
        load_validators(CONTRACTS)["activator/readiness-signal.schema.json"].validate(payload)


def test_not_ready_is_not_an_activator_transition_notification():
    payload = example("readiness-ready.json")
    payload["readiness"] = "NotReady"
    with pytest.raises(ValidationError):
        load_validators(CONTRACTS)["activator/readiness-signal.schema.json"].validate(payload)


@pytest.fixture
def copied_contracts(tmp_path):
    root = tmp_path / "contracts"
    shutil.copytree(CONTRACTS, root)
    return root


def test_manifest_cannot_omit_an_example(copied_contracts):
    (copied_contracts / "examples" / "undeclared.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="every example"):
        validate_examples(copied_contracts)


def test_manifest_cannot_escape_contract_root(copied_contracts):
    manifest = [{"schema": "events/event-envelope.schema.json", "example": "../outside.json"}]
    (copied_contracts / "examples" / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="escapes"):
        validate_examples(copied_contracts)


def test_duplicate_schema_ids_are_rejected(copied_contracts):
    shutil.copyfile(
        copied_contracts / "events" / "event-envelope.schema.json",
        copied_contracts / "events" / "duplicate.schema.json",
    )
    with pytest.raises(ValueError, match="Duplicate schema ID"):
        validate_examples(copied_contracts)

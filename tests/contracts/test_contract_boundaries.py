import copy
import json
from pathlib import Path
from urllib.parse import urldefrag

import pytest
from jsonschema import ValidationError
from pydantic import ValidationError as ModelValidationError

from shared.contract_checks import validate_supporting_files
from shared.contract_reading import read_model
from shared.contract_validation import (
    example_payload,
    load_schemas,
    load_validators,
    select_validator,
)
from shared.contracts import models

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
MANIFEST = json.loads((CONTRACTS / "examples" / "manifest.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def validators():
    return load_validators(CONTRACTS)


@pytest.mark.parametrize("entry", MANIFEST, ids=lambda entry: entry["schema"])
def test_generated_python_models_accept_contract_examples(entry):
    schema, pointer = urldefrag(entry["schema"])
    name = pointer.rsplit("/", 1)[-1] if pointer else load_schemas(CONTRACTS)[schema]["title"]
    getattr(models, name).model_validate(example_payload(CONTRACTS, entry["example"]))


@pytest.mark.parametrize(
    ("name", "schema", "field"),
    [
        ("PhotoUploaded", "photo-uploaded", "photoId"),
        ("ArtifactUploaded", "artifact-uploaded", "blobUrl"),
        ("ValidationResultCreated", "validation-result-created", "confidence"),
        ("ValidationResultReviewed", "validation-result-reviewed", "decisionEntryId"),
        ("WorkOrderChanged", "work-order-changed", "version"),
        ("TicketChanged", "ticket-changed", "blocking"),
        ("MilestoneChanged", "milestone-changed", "milestoneType"),
    ],
)
def test_event_specific_payload_is_required(validators, name, schema, field):
    payload = example_payload(CONTRACTS, f"examples/events.json#/{name}")
    del payload["data"][field]
    with pytest.raises(ValidationError):
        validators[f"events/{schema}.schema.json"].validate(payload)


@pytest.mark.parametrize("both", [True, False])
def test_validation_requires_exactly_one_target(validators, both):
    payload = example_payload(CONTRACTS, "examples/shared-objects.json#/ValidationResult")
    if both:
        payload["artifactId"] = "ART-0042-RFDS"
    else:
        del payload["photoId"]
    with pytest.raises(ValidationError):
        select_validator(validators, "models.schema.json#/$defs/ValidationResult").validate(payload)


def test_document_validation_has_no_photo_target(validators):
    payload = example_payload(CONTRACTS, "examples/shared-objects.json#/ValidationResult")
    del payload["photoId"]
    payload.update(artifactId="ART-0042-RFDS", checklistItemCode=None)
    select_validator(validators, "models.schema.json#/$defs/ValidationResult").validate(payload)
    models.ValidationResult.model_validate(payload)


def test_producer_rejects_unknown_code_but_reader_logs_and_ignores_extra_fields(validators, caplog):
    payload = example_payload(CONTRACTS, "examples/shared-objects.json#/ValidationResult")
    payload.update(reviewStatus="FutureReview", futureMetadata={"ignored": True})
    schema = "models.schema.json#/$defs/ValidationResult"
    with pytest.raises(ValidationError):
        select_validator(validators, schema).validate(payload)
    result = read_model(CONTRACTS, schema, models.ValidationResult, payload).model_dump(mode="json")
    assert result["reviewStatus"] == "FutureReview"
    assert "futureMetadata" not in result
    assert "Unknown contract code" in caplog.text
    assert "FutureReview" in caplog.text


def test_tolerant_reader_still_rejects_invalid_confidence():
    payload = example_payload(CONTRACTS, "examples/shared-objects.json#/ValidationResult")
    payload.update(reviewStatus="FutureReview", confidence=1.5)
    with pytest.raises(ValidationError):
        read_model(
            CONTRACTS,
            "models.schema.json#/$defs/ValidationResult",
            models.ValidationResult,
            payload,
        )


def test_action_generation_preserves_required_extension_fields():
    payload = example_payload(CONTRACTS, "examples/actions.json#/CreateReworkRequest")
    del payload["reason"]
    with pytest.raises(ModelValidationError):
        models.CreateReworkRequest.model_validate(payload)


def test_openapi_and_external_mapping_are_structurally_valid():
    validate_supporting_files(CONTRACTS)


def test_rework_cannot_lose_its_case(validators):
    payload = example_payload(CONTRACTS, "examples/shared-objects.json#/WorkOrder")
    payload["caseId"] = None
    with pytest.raises(ValidationError):
        select_validator(validators, "models.schema.json#/$defs/WorkOrder").validate(payload)


def test_photo_upload_requires_capture_and_replacement_metadata(validators):
    payload = example_payload(CONTRACTS, "examples/actions.json#/UploadMetadata")
    del payload["capturedAt"]
    with pytest.raises(ValidationError):
        select_validator(validators, "apim/actions.schema.json#/$defs/UploadMetadata").validate(
            payload
        )


def test_action_requires_approval_reference(validators):
    payload = example_payload(CONTRACTS, "examples/actions.json#/CreateReworkRequest")
    missing_approval = copy.deepcopy(payload)
    del missing_approval["approvalEntryId"]
    with pytest.raises(ValidationError):
        select_validator(
            validators, "apim/actions.schema.json#/$defs/CreateReworkRequest"
        ).validate(missing_approval)

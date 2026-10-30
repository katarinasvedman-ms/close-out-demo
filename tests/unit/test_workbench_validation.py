import base64
import copy
import hashlib
import json
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from agents import workbench_validation as validation
from agents.blob_sources import BlobSources
from agents.definitions import definition_hash
from shared.data_agent_stub import DataAgentStub
from shared.headless import digest, stable_id
from shared.seed import load_seed

CONTRACTS = validation.ROOT / "contracts"
CONTAINER = "https://example.blob.core.windows.net/inputs"
BODY = {"kind": "prompt", "model": "existing-model", "instructions": "publicationContext"}


@pytest.fixture
def registered():
    facts = DataAgentStub(
        load_seed(validation.ROOT / "seed" / "seed.json", CONTRACTS), {"SE-STO-0042"}
    ).site_facts("SE-STO-0042", "initial-blocked")
    photo = copy.deepcopy(next(row for row in facts["photos"] if row["photoId"] == "PHOTO-20"))
    rfds = copy.deepcopy(next(row for row in facts["artifacts"] if row["artifactType"] == "rfds"))
    photo["blobUrl"], rfds["blobUrl"] = f"{CONTAINER}/photo.png", f"{CONTAINER}/rfds.pdf"
    text = (
        f"SYNTHETIC TEST INPUT\n{rfds['artifactId']}\nSite: SE-STO-0042\n"
        f"Version: {rfds['version']}\nSector B azimuth 120 degrees\n"
    ).encode()
    files = {"photo.png": b"actual registered image bytes", "rfds.pdf": b"pdf", "rfds.txt": text}

    def reference(path, media_type):
        return {
            "url": f"{CONTAINER}/{path}",
            "etag": '"pinned-etag"',
            "sha256": hashlib.sha256(files[path]).hexdigest(),
            "mediaType": media_type,
        }

    manifest = {
        "schemaVersion": "0.2.0",
        "runId": str(uuid4()),
        "batchId": str(uuid4()),
        "caseId": facts["caseId"],
        "siteId": "SE-STO-0042",
        "program": facts["project"]["programCode"],
        "registeredAt": "2026-10-29T09:00:00Z",
        "facts": facts,
        "members": [
            {
                "evidenceId": "PHOTO-20",
                "kind": "photo",
                "metadata": photo,
                "blob": reference("photo.png", "image/png"),
            },
            {
                "evidenceId": rfds["artifactId"],
                "kind": "document",
                "metadata": rfds,
                "blob": reference("rfds.pdf", "application/pdf"),
                "text": reference("rfds.txt", "text/plain"),
            },
        ],
    }
    settings = validation.ValidationSettings(
        endpoint="https://example.services.ai.azure.com/api/projects/test",
        storageUrl="https://example.blob.core.windows.net",
        agent={
            "name": "existing-validator",
            "version": "6",
            "definitionHash": definition_hash(BODY),
        },
        manifest=manifest,
        evidenceId="PHOTO-20",
    )
    source = BlobSources(manifest, CONTRACTS, CONTAINER, lambda path, *_: files[path])
    return SimpleNamespace(settings=settings, source=source, files=files)


@pytest.fixture
def sdk(monkeypatch, registered):
    credential, blobs, project, client = [MagicMock() for _ in range(4)]
    for instance in (credential, blobs, project, client):
        instance.__enter__.return_value = instance
    classes = {
        "azure.ai.projects": ("AIProjectClient", project),
        "azure.identity": ("AzureCliCredential", credential),
        "azure.storage.blob": ("BlobServiceClient", blobs),
    }
    for name in ("azure", "azure.ai", "azure.storage", *classes):
        module = ModuleType(name)
        module.__path__ = []
        if name in classes:
            attribute, instance = classes[name]
            setattr(module, attribute, MagicMock(return_value=instance))
        monkeypatch.setitem(sys.modules, name, module)
    project.get_openai_client.return_value = client
    project.agents.get_version.return_value.definition.as_dict.return_value = BODY
    client.responses.create.return_value.id = "resp-test"
    monkeypatch.setattr(validation, "sources", lambda *_: registered.source)
    monkeypatch.setattr(
        validation,
        "publication_receipt",
        lambda *_: {"status": "NotConfirmed", "eventIds": [], "error": None},
    )
    return SimpleNamespace(client=client, project=project, blobs=blobs)


def request(settings, identifier=None):
    return validation.ValidationRequest(
        siteId=settings.manifest["siteId"],
        evidenceId=settings.evidenceId,
        publicationId=identifier or str(uuid4()),
    )


def assessment():
    return {
        "evidenceId": "PHOTO-20",
        "compliant": False,
        "confidence": 0.9,
        "findings": ["Observed sector B azimuth 127 degrees; RFDS plans 120 degrees."],
        "rfdsReference": "STO42-rfds v1",
        "observedAzimuthDegrees": 127.0,
    }


def completed():
    return {
        "id": "resp-test",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": json.dumps({"assessments": [assessment()]})},
                ],
            }
        ],
    }


def test_exact_registered_bytes_and_context_without_seeded_outcomes(registered):
    identifier = str(uuid4())
    content = validation.validation_input(registered.settings, registered.source, identifier)[0][
        "content"
    ]
    body = json.loads(content[0]["text"])
    assert body["publicationContext"] == {
        "sourceRunId": registered.settings.manifest["runId"],
        "sourceBatchId": registered.settings.manifest["batchId"],
        "publicationId": identifier,
    }
    assert set(body) == {"evidence", "publicationContext"}
    packet = body["evidence"][0]
    assert set(packet) == {"evidenceId", "kind", "metadata", "checklist", "rfds"}
    assert packet["metadata"] == registered.settings.manifest["members"][0]["metadata"]
    assert packet["checklist"][0]["rfdsCheck"] is True
    assert "Sector B azimuth 120 degrees" in packet["rfds"]["text"]
    assert "input_image" == content[2]["type"]
    assert (
        base64.b64decode(content[2]["image_url"].split(",", 1)[1]) == registered.files["photo.png"]
    )


def test_changed_photo_bytes_fail_before_inference(registered, sdk, tmp_path):
    registered.files["photo.png"] = b"changed bytes"
    runner = validation.ValidationRunner(registered.settings, tmp_path)
    state = runner.start(request(registered.settings))
    assert state["status"] == "Failed"
    assert "hash" in state["error"]
    sdk.client.responses.create.assert_not_called()


def test_submissions_are_durable_idempotent_and_one_active_target(registered, sdk, tmp_path):
    runner = validation.ValidationRunner(registered.settings, tmp_path)
    first = request(registered.settings)
    state = runner.start(first)
    assert state["status"] == "Running" and state["responseId"] == "resp-test"
    restarted = validation.ValidationRunner(registered.settings, tmp_path)
    assert restarted.start(first) == state
    assert restarted.start(request(registered.settings)) == state
    assert restarted.info()["latest"] == state
    sdk.client.responses.create.assert_called_once()
    arguments = sdk.client.responses.create.call_args.kwargs
    assert arguments["background"] is True and arguments["store"] is True
    assert arguments["extra_body"]["agent_reference"] == {
        "type": "agent_reference",
        "name": "existing-validator",
        "version": "6",
    }
    sdk.project.get_openai_client.assert_called_with(timeout=45, max_retries=0)


def test_unknown_initial_post_is_not_automatically_repeated(registered, sdk, tmp_path):
    sdk.client.responses.create.side_effect = TimeoutError("Unknown remote outcome")
    runner = validation.ValidationRunner(registered.settings, tmp_path)
    state = runner.start(request(registered.settings))
    assert state["status"] == "Unknown" and state["responseId"] is None
    assert "Do not retry inference" in state["error"]
    assert runner.start(request(registered.settings)) == state
    sdk.client.responses.create.assert_called_once()


def test_failure_after_response_id_does_not_allow_another_inference(registered, sdk, tmp_path):
    sdk.client.__exit__.side_effect = RuntimeError("Client cleanup failed")
    runner = validation.ValidationRunner(registered.settings, tmp_path)
    state = runner.start(request(registered.settings))
    assert state["status"] == "Running" and state["responseId"] == "resp-test"
    assert runner.start(request(registered.settings)) == state
    sdk.client.responses.create.assert_called_once()


def test_scope_and_pinned_definition_fail_before_inference(registered, sdk, tmp_path):
    runner = validation.ValidationRunner(registered.settings, tmp_path)
    outside = request(registered.settings)
    outside.siteId = "SE-GOT-0017"
    with pytest.raises(ValueError, match="scope"):
        runner.start(outside)
    sdk.project.agents.get_version.return_value.definition.as_dict.return_value = {}
    assert runner.start(request(registered.settings))["status"] == "Failed"
    sdk.client.responses.create.assert_not_called()


def test_poll_retrieves_same_response_and_keeps_publication_separate(registered, sdk, tmp_path):
    runner = validation.ValidationRunner(registered.settings, tmp_path)
    state = runner.start(request(registered.settings))
    raw = completed()
    sdk.client.responses.retrieve.return_value = SimpleNamespace(
        id="resp-test", status="completed", model_dump=lambda **_: raw
    )
    state = runner.poll(state["publicationId"])
    assert state["status"] == "Completed" and state["assessment"] == assessment()
    assert state["publication"]["status"] == "NotConfirmed"
    assert json.loads((tmp_path / "resp-test.json").read_text()) == raw
    runner.poll(state["publicationId"])
    sdk.client.responses.create.assert_called_once()
    sdk.client.responses.retrieve.assert_called_once_with("resp-test")


def test_poll_transport_failure_preserves_response_for_status_retry(registered, sdk, tmp_path):
    runner = validation.ValidationRunner(registered.settings, tmp_path)
    state = runner.start(request(registered.settings))
    sdk.client.responses.retrieve.side_effect = TimeoutError("Read failed")
    with pytest.raises(TimeoutError):
        runner.poll(state["publicationId"])
    assert runner.get(state["publicationId"]) == state
    sdk.client.responses.create.assert_called_once()


def test_publication_read_failure_can_be_rechecked_without_inference(
    registered, sdk, tmp_path, monkeypatch
):
    runner = validation.ValidationRunner(registered.settings, tmp_path)
    state = runner.start(request(registered.settings))
    sdk.client.responses.retrieve.return_value = SimpleNamespace(
        id="resp-test", status="completed", model_dump=lambda **_: completed()
    )
    receipt = MagicMock(side_effect=TimeoutError("Ledger read failed"))
    monkeypatch.setattr(validation, "publication_receipt", receipt)
    assert runner.poll(state["publicationId"])["publication"]["status"] == "Unknown"
    receipt.side_effect = None
    receipt.return_value = {
        "status": "Published",
        "eventIds": ["one", "two", "three"],
        "error": None,
    }
    assert runner.poll(state["publicationId"])["publication"]["status"] == "Published"
    sdk.client.responses.retrieve.assert_called_once()
    sdk.client.responses.create.assert_called_once()


@pytest.mark.parametrize("change", ["wrong-evidence", "empty-findings", "extra", "failed"])
def test_malformed_assessment_is_not_a_completed_validation(change):
    raw = completed()
    row = assessment()
    if change == "wrong-evidence":
        row["evidenceId"] = "PHOTO-19"
    elif change == "empty-findings":
        row["findings"] = []
    elif change == "extra":
        row["approval"] = True
    else:
        raw["status"] = "failed"
    raw["output"][0]["content"][0]["text"] = json.dumps({"assessments": [row]})
    with pytest.raises(ValueError):
        validation.assessment_result(raw, "PHOTO-20")


def test_publisher_ledger_requires_matching_provenance_and_all_events(registered, monkeypatch):
    settings = registered.settings
    identifier = str(uuid4())
    batch = stable_id(identifier, "assessment-publication")
    events = [
        stable_id(identifier, batch, "PHOTO-20", name)
        for name in ("PhotoUploaded", "ValidationResultCreated")
    ] + [stable_id(identifier, batch, "completed")]
    state = {
        "status": "Completed",
        "published": events,
        "result": {
            "runId": identifier,
            "batchId": batch,
            "siteId": settings.manifest["siteId"],
            "caseId": settings.manifest["caseId"],
            "provenance": {
                "sourceRunId": settings.manifest["runId"],
                "sourceBatchId": settings.manifest["batchId"],
                "sourceManifestSha256": digest(settings.manifest),
                "assessment": assessment(),
            },
        },
    }
    store = MagicMock()
    store.read.return_value = state, "etag"
    monkeypatch.setattr(validation, "BlobStore", lambda _: store)
    result = validation.publication_receipt(settings, identifier, MagicMock(), assessment())
    assert result == {"status": "Published", "eventIds": events, "error": None}
    store.read.assert_called_with(f"validation/{identifier}/{batch}.json")
    state["published"] = events[:1]
    with pytest.raises(ValueError, match="three events"):
        validation.publication_receipt(settings, identifier, MagicMock(), assessment())
    state["status"] = "Failed"
    assert (
        validation.publication_receipt(settings, identifier, MagicMock(), assessment())["status"]
        == "NotConfirmed"
    )
    state["result"]["provenance"]["sourceManifestSha256"] = "different"
    with pytest.raises(ValueError, match="registration"):
        validation.publication_receipt(settings, identifier, MagicMock(), assessment())


def test_changed_registration_is_rejected(registered, monkeypatch):
    store = MagicMock()
    store.read.return_value = {**registered.settings.manifest, "program": "changed"}, "etag"
    monkeypatch.setattr(validation, "BlobStore", lambda _: store)
    with pytest.raises(ValueError, match="manifest changed"):
        validation.sources(registered.settings, MagicMock())


def test_interrupted_submission_is_not_replayed(registered, sdk, tmp_path):
    runner = validation.ValidationRunner(registered.settings, tmp_path)
    state = runner.start(request(registered.settings))
    state.update(status="Starting", responseId=None, createdAt=0)
    runner.save(state)
    assert runner.poll(state["publicationId"])["status"] == "Unknown"
    assert runner.start(request(registered.settings))["status"] == "Unknown"
    sdk.client.responses.create.assert_called_once()

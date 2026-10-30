import asyncio
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from jsonschema import ValidationError

from agents.blob_sources import BlobSources, azure_downloader
from agents.definitions import FoundrySettings
from agents.flow import (
    EventstreamPublisher,
    emit_fixture_event,
    eventstream_client,
    prepare_batch,
    storage_endpoint,
    upload_batch,
)
from agents.flow_coordinator import SnapshotSources, coordinate, snapshot_reader
from agents.flow_projection import project_run
from agents.flow_publish import openapi_document, publish_assessment
from shared.data_agent_stub import DataAgentStub
from shared.headless import (
    Busy,
    Conflict,
    Ledger,
    accepted_document_status,
    batch_events,
    validate_batch,
    validate_callback,
)
from shared.seed import load_seed

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "contracts"
CONTAINER = "https://example.blob.core.windows.net/inputs"


def test_coordinator_workflow_calls_foundry_directly_and_polls_same_response():
    workflow = json.loads((ROOT / "src/logicapps/coordinator/workflow.json").read_text())
    actions = workflow["definition"]["actions"]
    start = actions["Start_agent_response"]
    assert start["inputs"]["body"]["background"] is True
    assert start["inputs"]["body"]["agent_reference"]["type"] == "agent_reference"
    assert start["inputs"]["authentication"]["audience"] == "https://ai.azure.com"
    assert start["inputs"]["retryPolicy"] == {"type": "none"}
    prompt = start["inputs"]["body"]["input"][0]["content"][0]["text"]
    for field in ("siteId", "caseId", "program", "runId", "factsRevision"):
        assert f"['{field}']" in prompt
    poll = actions["Wait_for_agent_completion"]
    assert poll["operationOptions"] == "FailWhenLimitsReached"
    assert poll["limit"]["timeout"] == "PT30M"
    assert all(
        status in poll["expression"]
        for status in ("completed", "failed", "cancelled", "incomplete")
    )
    get = poll["actions"]["Get_agent_response"]["inputs"]
    assert get["method"] == "GET"
    assert "body('Start_agent_response')?['id']" in get["uri"]
    assert "FLOW_ADAPTER" not in json.dumps(workflow)
    assert "kusto" not in json.dumps(workflow).lower()


def test_coordinator_workflow_saves_only_completed_raw_response_with_run_scoped_name():
    workflow = json.loads((ROOT / "src/logicapps/coordinator/workflow.json").read_text())
    actions = workflow["definition"]["actions"]
    save = actions["Save_completed_agent_response"]
    assert save["runAfter"] == {"Wait_for_agent_completion": ["Succeeded"]}
    assert save["expression"] == {
        "equals": ["@variables('Agent_response')?['status']", "completed"]
    }
    blob = save["actions"]["Save_response_to_Blob"]["inputs"]
    assert blob["method"] == "PUT"
    assert blob["body"] == "@variables('Agent_response')"
    assert blob["headers"]["x-ms-blob-type"] == "BlockBlob"
    assert blob["authentication"]["audience"] == "https://storage.azure.com/"
    for part in ("results/coordinator/", "runId", "caseId", "transitionId", "workflow().run.name"):
        assert part in blob["uri"]
    assert save["else"]["actions"]["Agent_did_not_complete"]["inputs"]["runStatus"] == "Failed"


class Store:
    def __init__(self):
        self.rows = {}

    def read(self, key):
        return copy.deepcopy(self.rows.get(key, (None, None)))

    def write(self, key, value, etag):
        _, current = self.read(key)
        if current != etag:
            raise Busy("Concurrent modification")
        version = str(uuid4())
        self.rows[key] = copy.deepcopy(value), version
        return version


@pytest.fixture
def batch():
    facts = DataAgentStub(load_seed(ROOT / "seed" / "seed.json", CONTRACTS), {"SE-STO-0042"})
    facts = facts.site_facts("SE-STO-0042", "initial-blocked")
    row = copy.deepcopy(next(photo for photo in facts["photos"] if photo["photoId"] == "PHOTO-19"))
    row["blobUrl"] = f"{CONTAINER}/photo.png"
    return {
        "schemaVersion": "0.2.0",
        "runId": str(uuid4()),
        "batchId": str(uuid4()),
        "caseId": facts["caseId"],
        "siteId": facts["site"]["siteId"],
        "program": facts["project"]["programCode"],
        "registeredAt": "2026-10-29T09:00:00Z",
        "facts": facts,
        "members": [
            {
                "evidenceId": row["photoId"],
                "kind": "photo",
                "metadata": row,
                "blob": {
                    "url": row["blobUrl"],
                    "etag": '"expected-etag"',
                    "sha256": hashlib.sha256(b"actual image bytes").hexdigest(),
                    "mediaType": "image/png",
                },
            }
        ],
    }


@pytest.fixture
def sources(batch):
    return BlobSources(batch, CONTRACTS, CONTAINER, lambda *_: b"actual image bytes")


@pytest.fixture
def settings():
    binding = {"name": "validation", "version": "2", "definitionHash": "synthetic-test"}
    return FoundrySettings(
        endpoint="https://example.services.ai.azure.com/api/projects/test",
        deployment="test",
        model="gpt-4o",
        modelVersion="2024-11-20",
        validation=binding,
        closeout={**binding, "name": "coordinator"},
    )


def receipt(batch):
    return {
        "responseId": "actual-service-response-example",
        "completedAt": "2026-10-29T09:01:00Z",
        "modes": {"inference": "live"},
        "result": [
            {
                "validationResultId": "temporary-id",
                "photoId": "PHOTO-19",
                "checklistItemCode": "grounding",
                "compliant": False,
                "confidence": 0.9,
                "findings": ["The grounding label is missing"],
                "rfdsArtifactId": None,
                "rfdsReference": None,
                "reviewStatus": "Auto",
                "validatedBy": "validation",
                "model": "gpt-4o",
                "modelVersion": "2024-11-20",
                "validatedAt": "2026-10-29T09:01:00Z",
                "caseId": batch["caseId"],
            }
        ],
    }


@pytest.fixture
def publication(batch, monkeypatch):
    ledger = Ledger(Store())
    ledger.register(f"registrations/{batch['runId']}/{batch['batchId']}/manifest.json", batch)
    monkeypatch.setattr(
        "agents.flow_publish.azure_downloader", lambda _: lambda *_: b"actual image bytes"
    )
    return {
        "body": {
            "sourceRunId": batch["runId"],
            "sourceBatchId": batch["batchId"],
            "publicationId": str(uuid4()),
            "assessment": {
                "evidenceId": "PHOTO-19",
                "compliant": False,
                "confidence": 0.9,
                "findings": ["The grounding identifier label is missing"],
                "rfdsReference": None,
                "observedAzimuthDegrees": None,
            },
        },
        "ledger": ledger,
        "results": Ledger(Store()),
        "inputs": SimpleNamespace(url=CONTAINER),
    }


def publish_test(value, settings, publish):
    return publish_assessment(
        value["body"],
        settings,
        CONTRACTS,
        value["inputs"],
        value["ledger"],
        value["results"],
        publish,
        "test-project-identity",
    )


def test_tool_publication_is_idempotent_and_preserves_submitted_provenance(publication, settings):
    events = []
    result = publish_test(publication, settings, events.append)
    assert publish_test(publication, settings, events.append) == result
    assert len(events) == 3
    receipt, _ = publication["results"].store.read(result["resultKey"])
    assert receipt["result"][0]["model"] == "not-attested"
    assert receipt["provenance"]["modelResponseIndependentlyVerified"] is False
    manifest, _ = publication["ledger"].store.read(
        f"registrations/{result['runId']}/{result['batchId']}/manifest.json"
    )
    projected = project_run([manifest], events, CONTRACTS)
    assert projected["facts"]["validationSources"][result["validationResultId"]] == (
        "agent-submitted-assessment"
    )
    assert projected["runId"] != publication["body"]["sourceRunId"]
    publication["body"]["assessment"]["confidence"] = 0.8
    with pytest.raises(Conflict):
        publish_test(publication, settings, events.append)


def test_tool_publication_recovers_partial_delivery_without_changing_event_ids(
    publication, settings
):
    events = []

    def failing(event):
        if events:
            raise RuntimeError("Eventstream unavailable")
        events.append(copy.deepcopy(event))

    with pytest.raises(RuntimeError, match="Eventstream"):
        publish_test(publication, settings, failing)
    result = publish_test(publication, settings, events.append)
    assert len(events) == len(set(event["eventId"] for event in events)) == 3
    assert result["status"] == "Completed"


@pytest.mark.parametrize("change", ["unknown-photo", "source-run-reuse", "fabricated-context"])
def test_tool_publication_rejects_unregistered_or_changed_scope(publication, settings, change):
    if change == "unknown-photo":
        publication["body"]["assessment"]["evidenceId"] = "PHOTO-OTHER-SITE"
    elif change == "source-run-reuse":
        publication["body"]["publicationId"] = publication["body"]["sourceRunId"]
    else:
        publication["body"]["metadata"] = {"siteId": "arbitrary-site"}
    events = []
    with pytest.raises(ValueError):
        publish_test(publication, settings, events.append)
    assert events == []


def test_publisher_openapi_has_valid_scoped_operation_and_resolvable_request_schema(publication):
    from jsonschema import Draft202012Validator
    from openapi_spec_validator import validate

    document = openapi_document("https://example.azurewebsites.net")
    validate(document)
    operation = document["paths"]["/api/publish-validation-result"]["post"]
    assert operation["operationId"] == "publish_validation_result"
    validator = Draft202012Validator(
        {
            "$ref": "#/components/schemas/PublishAssessment",
            "components": document["components"],
        }
    )
    validator.validate(publication["body"])
    del publication["body"]["assessment"]["findings"]
    with pytest.raises(ValidationError):
        validator.validate(publication["body"])
    with pytest.raises(ValueError):
        openapi_document("https://example.azurewebsites.net/api?sig=secret")


def test_project_publisher_cannot_invoke_existing_or_anonymous_routes(monkeypatch):
    func = pytest.importorskip("azure.functions")
    from flow_functions.function_app import (
        coordinator_draft,
        publish_validation_result,
        validation_batch,
    )

    monkeypatch.setenv("FLOW_ALLOWED_CALLER_OIDS", "logic-app-only")
    monkeypatch.setenv("FLOW_PUBLISHER_CALLER_OIDS", "foundry-project-only")
    request = func.HttpRequest(
        method="POST",
        url="https://example.azurewebsites.net",
        headers={"x-ms-client-principal-id": "foundry-project-only"},
        body=b"{}",
    )
    assert asyncio.run(validation_batch(request)).status_code == 403
    assert asyncio.run(coordinator_draft(request)).status_code == 403
    request = func.HttpRequest(
        method="POST",
        url="https://example.azurewebsites.net",
        headers={"x-ms-client-principal-id": "logic-app-only"},
        body=b"{}",
    )
    assert publish_validation_result(request).status_code == 403


def test_blob_sources_attach_downloaded_bytes_and_pinned_identity(sources):
    packets, attachments = sources.validation_inputs(["PHOTO-19"])
    assert packets[0]["evidenceId"] == "PHOTO-19"
    assert attachments[0][1] == b"actual image bytes"
    assert attachments[0][3]["etag"] == '"expected-etag"'
    assert "validations" not in packets[0]
    with pytest.raises(PermissionError, match="scope"):
        sources.validation_inputs(["another-site-photo"])


@pytest.mark.parametrize(
    "change", ["site", "baseline-site", "duplicate", "url", "traversal", "hash"]
)
def test_blob_sources_reject_scope_identity_and_hash_changes(batch, change):
    if change == "site":
        batch["members"][0]["metadata"]["siteId"] = "SE-GOT-0017"
    elif change == "baseline-site":
        batch["facts"]["tickets"][0]["siteId"] = "SE-GOT-0017"
    elif change == "duplicate":
        batch["members"].append(copy.deepcopy(batch["members"][0]))
    elif change in {"url", "traversal"}:
        url = (
            "https://other.blob.core.windows.net/inputs/photo.png"
            if change == "url"
            else f"{CONTAINER}/%2e%2e/private.png"
        )
        batch["members"][0]["blob"]["url"] = url
        batch["members"][0]["metadata"]["blobUrl"] = url
    else:
        batch["members"][0]["blob"]["sha256"] = "0" * 64
    with pytest.raises((ValueError, PermissionError)):
        sources = BlobSources(batch, CONTRACTS, CONTAINER, lambda *_: b"actual image bytes")
        sources.validation_inputs(["PHOTO-19"])


def test_blob_download_uses_if_match_and_explicit_version():
    calls = []

    class Blob:
        def get_blob_properties(self, **kwargs):
            calls.append(kwargs)
            return type("Properties", (), {"size": 3})()

        def download_blob(self, **kwargs):
            calls.append(kwargs)
            return type("Download", (), {"readall": lambda self: b"png"})()

    class Container:
        def get_blob_client(self, path, **kwargs):
            assert path == "photo.png"
            assert kwargs == {"version_id": "pinned-version"}
            return Blob()

    downloader = azure_downloader(Container())
    assert downloader("photo.png", {"etag": "etag", "versionId": "pinned-version"}, 10) == b"png"
    assert all(call["etag"] == "etag" for call in calls)
    assert all(str(call["match_condition"]) == "MatchConditions.IfNotModified" for call in calls)


def test_registration_cannot_be_silently_overwritten(batch):
    ledger = Ledger(Store())
    ledger.register("registration", batch)
    ledger.register("registration", batch)
    batch["siteId"] = "other"
    with pytest.raises(Conflict, match="immutable"):
        ledger.register("registration", batch)


def test_claims_reject_concurrency_and_allow_expired_recovery():
    now = [100]
    ledger = Ledger(Store(), clock=lambda: now[0])
    first, _ = ledger.claim("work", {"version": 1}, seconds=10)
    with pytest.raises(Busy):
        ledger.claim("work", {"version": 1})
    now[0] = 111
    second, _ = ledger.claim("work", {"version": 1})
    assert second.state["owner"] != first.state["owner"]
    with pytest.raises(Busy):
        first.save()
    with pytest.raises(Busy):
        first.finish("Failed")
    second.finish("Completed")
    assert ledger.claim("work", {"version": 1})[0] is None
    with pytest.raises(Conflict):
        ledger.claim("work", {"version": 2})


def test_outbox_recovers_partial_delivery_without_repeating_inference(sources, settings):
    ledger = Ledger(Store())
    inference_calls, delivered = [], []

    async def infer(*args):
        inference_calls.append(args)
        return receipt(sources.manifest)

    def publish(event):
        if len(delivered) == 1:
            raise ConnectionError("Fabric unavailable")
        delivered.append(event)

    with pytest.raises(ConnectionError):
        asyncio.run(validate_batch(sources, settings, ledger, infer, publish))
    state = asyncio.run(validate_batch(sources, settings, ledger, infer, delivered.append))
    assert state["status"] == "Completed"
    assert len(inference_calls) == 1
    assert len(delivered) == 3
    assert delivered[-1]["eventType"] == "EvidenceBatchCompleted"
    assert delivered[-1]["data"]["eventIds"] == [event["eventId"] for event in delivered[:-1]]
    asyncio.run(validate_batch(sources, settings, ledger, infer, delivered.append))
    assert len(delivered) == 3
    assert (
        state["result"]["result"][0]["validationResultId"]
        == delivered[1]["data"]["validationResultId"]
    )


def test_failed_inference_is_not_a_completed_empty_ready_draft(sources, settings):
    store = Store()

    async def fail(*_):
        raise RuntimeError("Model error")

    with pytest.raises(RuntimeError, match="Model error"):
        asyncio.run(validate_batch(sources, settings, Ledger(store), fail, lambda _: None))
    state = next(iter(store.rows.values()))[0]
    assert state["status"] == "Failed"
    assert state["result"] is None
    assert state["outbox"] == []
    assert state["history"][-1]["code"] == "InferenceFailed"


def test_batch_events_are_stable_and_require_all_inference_results(sources):
    first = batch_events(sources.manifest, receipt(sources.manifest), sources.validators)
    second = batch_events(sources.manifest, receipt(sources.manifest), sources.validators)
    assert first == second
    missing = receipt(sources.manifest)
    missing["result"] = []
    with pytest.raises(ValueError, match="every batch member"):
        batch_events(sources.manifest, missing, sources.validators)


def test_callback_requires_matching_identity_and_empty_ready_gaps(sources):
    value = json.loads((CONTRACTS / "examples" / "headless.json").read_text())["callback"]
    key = validate_callback(value, sources.validators)
    assert value["data"]["transitionId"] in key
    value["data"]["signal"]["caseId"] = str(uuid4())
    with pytest.raises(ValueError, match="identities"):
        validate_callback(value, sources.validators)
    value["data"]["signal"]["caseId"] = value["caseId"]
    value["data"]["signal"]["readiness"] = "Ready"
    with pytest.raises(ValidationError):
        validate_callback(value, sources.validators)


@pytest.mark.parametrize(
    ("compliant", "review", "confidence", "expected"),
    [
        (True, "NeedsReview", 0.99, "Submitted"),
        (True, "Overridden", 0.99, "Submitted"),
        (True, "Auto", 0.74, "Submitted"),
        (True, "Auto", 0.75, "Validated"),
        (True, "Confirmed", 0.2, "Validated"),
        (False, "Auto", 0.9, "Rejected"),
    ],
)
def test_document_projection_preserves_review_and_threshold(
    compliant, review, confidence, expected
):
    assert (
        accepted_document_status(
            {"compliant": compliant, "reviewStatus": review, "confidence": confidence}, 0.75
        )
        == expected
    )


def test_eventstream_uses_contract_event_identity_and_case_partition(batch):
    class Client:
        def create_batch(self, **kwargs):
            assert kwargs == {"partition_key": batch["caseId"], "timeout": 30}
            self.messages = []
            return self

        def add(self, event):
            self.messages.append(event)

        def send_batch(self, events, **kwargs):
            assert events is self
            assert kwargs == {"timeout": 30}

    client = Client()
    value = {"eventId": str(uuid4()), "caseId": batch["caseId"], "data": {"example": True}}
    EventstreamPublisher(client)(value)
    assert client.messages[0].message_id == value["eventId"]
    assert json.loads(client.messages[0].body_as_str()) == value


def test_fixture_controls_require_explicit_supported_events_and_reuse_receipts(batch, sources):
    events = json.loads((CONTRACTS / "examples" / "events.json").read_text())
    event = events["TicketChanged"]
    ledger, published = Ledger(Store()), []
    run, operation = batch["runId"], str(uuid4())
    emit_fixture_event(event, run, operation, ledger, CONTRACTS, published.append)
    emit_fixture_event(event, run, operation, ledger, CONTRACTS, published.append)
    assert len(published) == 1
    assert published[0]["fixtureOnly"] is True
    event["data"]["description"] = "Changed payload"
    with pytest.raises(Conflict):
        emit_fixture_event(event, run, operation, ledger, CONTRACTS, published.append)
    event["eventType"] = "ApprovalGranted"
    with pytest.raises(ValueError, match="Only explicit"):
        emit_fixture_event(event, run, operation, ledger, CONTRACTS, published.append)


def test_prepared_upload_registers_before_bytes_and_seals_after_all_inputs(batch, monkeypatch):
    from azure.core.exceptions import ResourceExistsError

    import agents.flow as flow

    records = {}
    for photo in batch["facts"]["photos"]:
        records["Photo", photo["photoId"]] = photo
    for artifact in batch["facts"]["artifacts"]:
        records["AcceptanceArtifact", artifact["artifactId"]] = artifact

    class LocalSources:
        def __init__(self, *_):
            self.records = records

        def read_asset(self, row, *, text=False):
            value = (
                f"{row['artifactId']}\nSite: {row['siteId']}\nVersion: {row['version']}\n"
                "Sector B azimuth 120 degrees\n"
                if text
                else f"actual attachment: {row.get('photoId', row.get('artifactId'))}"
            )
            return value.encode(), {}

    monkeypatch.setattr(flow, "Sources", LocalSources)
    store = Store()
    prefix = f"{batch['runId']}/{batch['batchId']}"

    class InputBlob:
        def __init__(self, path):
            self.path = path

        def upload_blob(self, data, **kwargs):
            assert store.read(f"registrations/{prefix}/intent.json")[0] is not None
            if self.path == f"{prefix}/_batch.ready":
                assert store.read(f"registrations/{prefix}/manifest.json")[0] is not None
            if self.path in inputs:
                raise ResourceExistsError("Already uploaded")
            inputs[self.path] = data

        def get_blob_properties(self):
            return type("Properties", (), {"etag": "etag", "version_id": "version"})()

        def download_blob(self, **kwargs):
            return type("Download", (), {"readall": lambda _: inputs[self.path]})()

    class Container:
        def get_blob_client(self, path):
            return InputBlob(path)

    inputs = {}
    ledger = Ledger(store)
    for _ in range(2):
        manifest, uploads = prepare_batch(
            batch["facts"],
            Path("."),
            CONTRACTS,
            CONTAINER,
            ["PHOTO-20"],
            batch["runId"],
            batch["batchId"],
        )
        assert {member["evidenceId"] for member in manifest["members"]} == {
            "PHOTO-20",
            next(
                a["artifactId"] for a in batch["facts"]["artifacts"] if a["artifactType"] == "rfds"
            ),
        }
        result = upload_batch(manifest, uploads, Container(), ledger, CONTRACTS)
        assert result["status"] == "Uploaded-not-yet-validated"
        sealed = store.read(f"registrations/{prefix}/manifest.json")[0]
        assert all(member["blob"]["versionId"] == "version" for member in sealed["members"])
        BlobSources(
            sealed,
            CONTRACTS,
            CONTAINER,
            lambda path, ref, limit: inputs[path],
        ).validation_inputs(["PHOTO-20"])
    assert len(inputs) == 4  # PNG, RFDS PDF/text, then the batch marker.


def callback_for(snapshot):
    callback = json.loads((CONTRACTS / "examples" / "headless.json").read_text())["callback"]
    callback.update(caseId=snapshot["caseId"], siteId=snapshot["siteId"])
    callback["data"].update(
        {key: snapshot[key] for key in ("runId", "program", "factsRevision", "sourceRef")}
    )
    callback["data"]["signal"] = {
        "caseId": snapshot["caseId"],
        "siteId": snapshot["siteId"],
        "readiness": snapshot["facts"]["readiness"],
        "readinessMissing": snapshot["facts"]["readinessMissing"],
        "evaluatedAt": snapshot["evaluatedAt"],
    }
    return callback


def test_fabric_projection_waits_for_the_whole_batch_and_deduplicates(sources):
    events = batch_events(sources.manifest, receipt(sources.manifest), sources.validators)
    assert project_run([sources.manifest], events[1:], CONTRACTS) is None
    snapshot = project_run([sources.manifest], list(reversed(events)), CONTRACTS)
    duplicated = project_run([sources.manifest], [*events, *events], CONTRACTS)
    assert duplicated == snapshot
    assert snapshot["facts"]["readinessMissing"] == ["CHK-A-AZ-B", "CHK-A-GROUNDING", "PL-0042-01"]
    assert (
        snapshot["facts"]["validationSources"][events[1]["data"]["validationResultId"]]
        == "live-foundry-inference"
    )
    assert "seed" in snapshot["facts"]["validationSources"].values()
    changed = copy.deepcopy(events[0])
    changed["data"]["latitude"] = 0
    with pytest.raises(ValueError, match="eventId"):
        project_run([sources.manifest], [*events, changed], CONTRACTS)


def test_fabric_projection_rejects_mismatched_hash_and_cross_run_events(sources):
    events = batch_events(sources.manifest, receipt(sources.manifest), sources.validators)
    events[0]["inputSha256"] = "0" * 64
    with pytest.raises(ValueError, match="pinned member"):
        project_run([sources.manifest], events, CONTRACTS)
    events[0]["runId"] = str(uuid4())
    with pytest.raises(ValueError, match="identity"):
        project_run([sources.manifest], events, CONTRACTS)


@pytest.mark.parametrize(
    ("confidence", "review", "missing"),
    [(0.74, "Auto", True), (0.99, "NeedsReview", True), (0.75, "Auto", False)],
)
def test_fabric_projection_uses_actual_review_and_program_threshold(
    sources, confidence, review, missing
):
    value = receipt(sources.manifest)
    value["result"][0].update(compliant=True, confidence=confidence, reviewStatus=review)
    events = batch_events(sources.manifest, value, sources.validators)
    snapshot = project_run([sources.manifest], events, CONTRACTS)
    assert ("CHK-A-GROUNDING" in snapshot["facts"]["readinessMissing"]) == missing


def test_fabric_projection_keeps_construction_precedence(sources):
    for milestone in sources.manifest["facts"]["milestones"]:
        if milestone["milestoneType"] == "ConstructionComplete":
            milestone.update(status="Planned", actualDate=None)
    events = batch_events(sources.manifest, receipt(sources.manifest), sources.validators)
    snapshot = project_run([sources.manifest], events, CONTRACTS)
    assert snapshot["facts"]["readiness"] == "NotReady"


def test_native_snapshot_sources_reject_stale_or_tampered_facts(sources):
    events = batch_events(sources.manifest, receipt(sources.manifest), sources.validators)
    snapshot = project_run([sources.manifest], events, CONTRACTS)
    callback = callback_for(snapshot)
    native = SnapshotSources(snapshot, callback, CONTRACTS)
    assert native.facts["mode"] == "fabric-live-test"
    with pytest.raises(RuntimeError, match="native document"):
        native.documents()
    changed = copy.deepcopy(snapshot)
    changed["facts"]["validations"][0]["confidence"] = 0.1
    with pytest.raises(ValueError, match="immutable facts"):
        SnapshotSources(changed, callback, CONTRACTS)
    callback["data"]["factsRevision"] = "0" * 64
    with pytest.raises(ValueError, match="Stale"):
        SnapshotSources(snapshot, callback, CONTRACTS)


def test_native_snapshot_accepts_reformatted_timestamp_but_not_different_instant(sources):
    events = batch_events(sources.manifest, receipt(sources.manifest), sources.validators)
    snapshot = project_run([sources.manifest], events, CONTRACTS)
    snapshot["evaluatedAt"] = "2026-10-29T10:51:17.445875+00:00"
    callback = callback_for(snapshot)
    callback["data"]["signal"]["evaluatedAt"] = "2026-10-29T10:51:17.4458750Z"
    SnapshotSources(snapshot, callback, CONTRACTS)
    callback["data"]["signal"]["evaluatedAt"] = "2026-10-29T10:51:18.4458750Z"
    with pytest.raises(ValueError, match="callback state disagree"):
        SnapshotSources(snapshot, callback, CONTRACTS)


def test_coordinator_recovery_reuses_saved_inference_and_requires_native_receipts(
    sources, settings
):
    from types import SimpleNamespace

    events = batch_events(sources.manifest, receipt(sources.manifest), sources.validators)
    snapshot = project_run([sources.manifest], events, CONTRACTS)
    callback = callback_for(snapshot)
    configured = SimpleNamespace(closeout=settings.closeout, fabric=True, knowledge=True)
    calls = []

    async def infer(*_):
        calls.append(True)
        return {
            "responseId": "service-response",
            "fabricRetrieval": [{"callId": "fabric"}],
            "knowledgeRetrieval": [{"callId": "iq"}],
            "result": {"gaps": []},
        }

    class FailingOnce(Store):
        def write(self, *args):
            if not self.rows:
                self.rows["failure"] = ({}, "sentinel")
                raise ConnectionError("Result storage temporarily unavailable")
            return super().write(*args)

    ledger, results = Ledger(Store()), Ledger(FailingOnce())

    def read(_):
        return snapshot

    with pytest.raises(ConnectionError):
        asyncio.run(coordinate(callback, configured, CONTRACTS, ledger, results, read, infer))
    completed = asyncio.run(
        coordinate(callback, configured, CONTRACTS, ledger, results, read, infer)
    )
    assert completed["status"] == "Completed"
    assert len(calls) == 1
    asyncio.run(coordinate(callback, configured, CONTRACTS, ledger, results, read, infer))
    assert len(calls) == 1


def test_coordinator_discards_inference_when_fabric_revision_changes(sources, settings):
    from types import SimpleNamespace

    events = batch_events(sources.manifest, receipt(sources.manifest), sources.validators)
    snapshot = project_run([sources.manifest], events, CONTRACTS)
    callback = callback_for(snapshot)
    changed = copy.deepcopy(snapshot)

    async def infer(*_):
        changed["factsRevision"] = "0" * 64
        return {
            "responseId": "service-response",
            "fabricRetrieval": [{}],
            "knowledgeRetrieval": [{}],
            "result": {"gaps": []},
        }

    ledger, result_store = Ledger(Store()), Store()
    with pytest.raises(ValueError, match="Stale"):
        asyncio.run(
            coordinate(
                callback,
                SimpleNamespace(closeout=settings.closeout, fabric=True, knowledge=True),
                CONTRACTS,
                ledger,
                Ledger(result_store),
                lambda _: changed,
                infer,
            )
        )
    assert not result_store.rows
    assert next(iter(ledger.store.rows.values()))[0]["result"] is None


@pytest.mark.parametrize(
    "url",
    [
        "https://example.net",
        "https://user@example.blob.core.windows.net",
        "https://example.blob.core.windows.net?sig=secret",
        "https://example.blob.core.windows.net/inputs",
        "http://example.blob.core.windows.net",
        "https://example.blob.core.windows.net.evil.test",
    ],
)
def test_operator_endpoint_rejects_ambiguous_or_foreign_targets(url):
    with pytest.raises(ValueError, match="exact HTTPS"):
        storage_endpoint(url)


def test_eventstream_endpoint_rejects_injected_protocol_fields():
    with pytest.raises(ValueError, match="exact Eventstream"):
        eventstream_client("name.servicebus.windows.net?other=target", "es_test", None)
    with pytest.raises(ValueError, match="exact Eventstream"):
        eventstream_client("name.servicebus.windows.net", "../different", None)


@pytest.mark.parametrize("encoded", [True, False])
def test_snapshot_reader_queries_only_current_run_and_has_no_static_fallback(
    sources, monkeypatch, encoded
):
    from types import SimpleNamespace

    import httpx

    import agents.flow_coordinator as coordinator

    events = batch_events(sources.manifest, receipt(sources.manifest), sources.validators)
    snapshot = project_run([sources.manifest], events, CONTRACTS)
    callback = callback_for(snapshot)
    values = [json.dumps(snapshot) if encoded else snapshot]

    class Credential:
        def get_token(self, scope):
            assert scope == "https://api.kusto.windows.net/.default"
            return SimpleNamespace(token="synthetic-unit-test")

    def handle(request):
        query = json.loads(request.content)
        assert query["db"] == "database"
        assert "CloseoutFlowFactsV02" in query["csl"]
        assert snapshot["runId"] in query["csl"]
        assert snapshot["caseId"] in query["csl"]
        assert "factsRevision ==" not in query["csl"]
        assert "ContractFacts" not in query["csl"]
        assert "sort by evaluatedAt desc, projectedAt desc" in query["csl"]
        return httpx.Response(
            200,
            json={
                "Tables": [
                    {
                        "Columns": [{"ColumnName": "snapshot"}],
                        "Rows": [[value] for value in values],
                    }
                ]
            },
        )

    client_type = httpx.Client
    monkeypatch.setattr(
        coordinator.httpx,
        "Client",
        lambda **kwargs: client_type(transport=httpx.MockTransport(handle), **kwargs),
    )
    read = snapshot_reader(
        SimpleNamespace(
            kustoEndpoint="https://test.kusto.fabric.microsoft.com", databaseId="database"
        ),
        Credential(),
    )
    assert read(callback) == snapshot
    values.clear()
    with pytest.raises(ValueError, match="no seeded fallback"):
        read(callback)


def test_projection_rejects_a_cyclic_replacement_chain(sources):
    sources.manifest["members"][0]["metadata"]["replacesPhotoId"] = "PHOTO-19"
    events = batch_events(sources.manifest, receipt(sources.manifest), sources.validators)
    with pytest.raises(ValueError, match="cycle"):
        project_run([sources.manifest], events, CONTRACTS)


def test_projection_replacements_stay_blocked_until_explicit_fixture_closure(sources):
    baseline = sources.manifest
    facts = DataAgentStub(load_seed(ROOT / "seed" / "seed.json", CONTRACTS), {"SE-STO-0042"})
    corrected = facts.site_facts("SE-STO-0042", "replacements-reviewed-rework-open")
    replacement = copy.deepcopy(baseline)
    replacement.update(
        batchId=str(uuid4()), registeredAt="2026-10-29T10:00:00Z", facts=corrected, members=[]
    )
    results = []
    for identifier in ("PHOTO-19b", "PHOTO-20b"):
        photo = copy.deepcopy(next(p for p in corrected["photos"] if p["photoId"] == identifier))
        photo["blobUrl"] = f"{CONTAINER}/{identifier}.png"
        reference = {**baseline["members"][0]["blob"], "url": photo["blobUrl"]}
        replacement["members"].append(
            {"evidenceId": identifier, "kind": "photo", "metadata": photo, "blob": reference}
        )
        result = copy.deepcopy(
            next(v for v in corrected["validations"] if v.get("photoId") == identifier)
        )
        result["validatedAt"] = "2026-10-29T10:01:00Z"
        results.append(result)
    initial = batch_events(baseline, receipt(baseline), sources.validators)
    newer = batch_events(
        replacement,
        {"result": results, "completedAt": "2026-10-29T10:01:00Z"},
        sources.validators,
    )

    def control(record, event_type, when):
        return {
            "schemaVersion": "0.2.0",
            "eventType": event_type,
            "eventId": str(uuid4()),
            "caseId": baseline["caseId"],
            "siteId": baseline["siteId"],
            "occurredAt": when,
            "producer": "azure.operator.synthetic-fixture-control",
            "runId": baseline["runId"],
            "fixtureOnly": True,
            "data": copy.deepcopy(record),
        }

    ticket = next(t for t in corrected["tickets"] if t["ticketId"] == "PL-0042-01")
    rework = next(w for w in corrected["workOrders"] if w["workOrderId"] == "RW-0042-01")
    controls = [
        control(ticket, "TicketChanged", "2026-10-29T10:02:00Z"),
        control(rework, "WorkOrderChanged", "2026-10-29T10:03:00Z"),
    ]
    events = [*initial, *newer, *controls]
    blocked = project_run([baseline, replacement], list(reversed(events)), CONTRACTS)
    assert blocked["facts"]["readiness"] == "Blocked"
    assert blocked["facts"]["readinessMissing"] == ["RW-0042-01"]
    assert not any(
        p["isCurrent"]
        for p in blocked["facts"]["photos"]
        if p["photoId"] in {"PHOTO-19", "PHOTO-20"}
    )
    rework = {**rework, "status": "Closed"}
    events.append(control(rework, "WorkOrderChanged", "2026-10-29T10:04:00Z"))
    ready = project_run([baseline, replacement], events, CONTRACTS)
    assert ready["facts"]["readiness"] == "Ready"
    assert ready["facts"]["readinessMissing"] == []
    assert ready["factsRevision"] != blocked["factsRevision"]


@pytest.mark.parametrize("route", ["validation_batch", "coordinator_draft"])
def test_function_adapter_rejects_untrusted_principal_before_dependencies(monkeypatch, route):
    func = pytest.importorskip("azure.functions")
    from flow_functions import function_app

    monkeypatch.setenv("FLOW_ALLOWED_CALLER_OIDS", "authorized-logic-app")
    request = func.HttpRequest(
        method="POST",
        url="https://adapter.test",
        body=b"{}",
        headers={"x-ms-client-principal-id": "different-principal"},
    )
    response = asyncio.run(getattr(function_app, route)(request))
    assert response.status_code == 403


@pytest.mark.parametrize("mismatched", [False, True])
def test_external_ingress_setup_reuses_only_matching_feed_and_never_saves_keys(
    monkeypatch, tmp_path, mismatched
):
    from types import SimpleNamespace

    import httpx

    import agents.flow_setup as setup
    from agents.definitions import FabricSettings

    fabric = FabricSettings(
        workspaceId=str(uuid4()),
        dataAgentId=str(uuid4()),
        databaseId=str(uuid4()),
        kustoEndpoint="https://test.kusto.fabric.microsoft.com",
        connectionName="test-connection",
        toolName="DataAgent_test",
    )
    definition = setup.ingress_definition(fabric)
    if mismatched:
        import base64

        topology = json.loads(base64.b64decode(definition["parts"][0]["payload"]))
        topology["destinations"][0]["properties"]["tableName"] = "UnrelatedTable"
        definition["parts"][0]["payload"] = base64.b64encode(json.dumps(topology).encode()).decode()

    def handle(request):
        if str(request.url).endswith("/eventstreams"):
            return httpx.Response(
                200, json={"value": [{"displayName": setup.FEED, "id": "existing-test-feed"}]}
            )
        assert not mismatched
        assert str(request.url).endswith("/connection")
        return httpx.Response(
            200,
            json={
                "fullyQualifiedNamespace": "test.servicebus.windows.net",
                "eventHubName": "test",
                "accessKeys": {"primaryKey": "must-not-appear-in-receipts"},
            },
        )

    class Remote:
        def __init__(self, *_):
            self.client = httpx.Client(transport=httpx.MockTransport(handle))

        def kql(self, query, *, write=False):
            assert write
            assert query.startswith(".create table CloseoutFlow")
            return []

        def fabric(self, path, body):
            assert path.endswith("/getDefinition")  # Never rewrite an existing feed.
            return {"definition": definition}

    class Credential:
        def get_token(self, _):
            return SimpleNamespace(token="synthetic-unit-test")

    monkeypatch.setattr(setup, "FabricSetup", Remote)
    if mismatched:
        with pytest.raises(ValueError, match="not overwritten"):
            setup.prepare(SimpleNamespace(fabric=fabric), Credential(), tmp_path)
        assert not (tmp_path / "fabric-ingress.json").exists()
    else:
        receipt = setup.prepare(SimpleNamespace(fabric=fabric), Credential(), tmp_path)
        assert receipt["eventstreamId"] == "existing-test-feed"
        assert "must-not-appear" not in (tmp_path / "fabric-ingress.json").read_text()

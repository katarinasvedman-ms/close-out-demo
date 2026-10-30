import ast
import base64
import copy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from agents.fabric_flow_activator import (
    activator_entities,
    dispatch_pipeline_definition,
    pipeline_definition,
)
from agents.fabric_flow_automation import notebook_definition
from agents.fabric_flow_runtime import (
    NotebookTransport,
    persist_snapshot,
    restore_upload_metadata,
    run_projection,
    validate_target,
)
from shared.contract_validation import load_validators
from shared.data_agent_stub import DataAgentStub
from shared.headless import batch_events, digest
from shared.seed import load_seed

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "contracts"
CONFIG = {
    "workspaceId": "00000000-0000-4000-8000-000000000001",
    "databaseId": "00000000-0000-4000-8000-000000000002",
    "kustoEndpoint": "https://example.kusto.fabric.microsoft.com",
    "storageEndpoint": "https://example.blob.core.windows.net",
}


@pytest.fixture
def evidence():
    example = json.loads((CONTRACTS / "examples" / "headless.json").read_text())
    manifest = example["batch"]
    facts = DataAgentStub(load_seed(ROOT / "seed" / "seed.json", CONTRACTS), {"SE-STO-0042"})
    manifest["facts"] = facts.site_facts("SE-STO-0042", "initial-blocked")
    validation = copy.deepcopy(
        next(row for row in manifest["facts"]["validations"] if row.get("photoId") == "PHOTO-19")
    )
    receipt = {"result": [validation], "completedAt": "2026-10-29T09:01:00Z"}
    return manifest, batch_events(manifest, receipt, load_validators(CONTRACTS))


class FabricMemory:
    def __init__(self, manifest, events):
        self.manifest = manifest
        self.events = events
        self.snapshots = []
        self.writes = []

    def manifests(self):
        return [self.manifest]

    def kql(self, query, *, write=False):
        if write:

            def value(node):
                if isinstance(node, ast.Constant):
                    return node.value
                if isinstance(node, ast.Name):
                    return {"true": True, "false": False, "null": None}[node.id]
                if isinstance(node, ast.List):
                    return [value(item) for item in node.elts]
                if isinstance(node, ast.Dict):
                    return {}
                if isinstance(node, ast.Call):
                    args = [value(item) for item in node.args]
                    if node.func.id == "dynamic":
                        return args[0]
                    if node.func.id == "pack_array":
                        return args
                    if node.func.id == "bag_pack":
                        return dict(zip(args[::2], args[1::2], strict=True))
                raise AssertionError(f"Unexpected test expression: {ast.dump(node)}")

            snapshot = value(ast.parse(query.split("print snapshot=", 1)[1], mode="eval").body)
            self.snapshots.append(snapshot)
            self.writes.append(query)
            return []
        if query.startswith("CloseoutFlowEventsV02"):
            return [{"event": event} for event in self.events]
        revision = query.split("factsRevision) == '", 1)[1].split("'")[0]
        return [
            {"snapshot": snapshot}
            for snapshot in self.snapshots
            if snapshot["factsRevision"] == revision
        ]


def test_full_registration_transport_projects_and_verifies_immutable_revision(evidence):
    manifest, events = evidence
    transport = FabricMemory(manifest, events)
    receipt = run_projection(transport, CONTRACTS, bundle_sha256="fixture")
    assert receipt["runs"][0]["status"] == "Appended"
    snapshot = transport.snapshots[0]
    assert snapshot["factsRevision"] == digest(
        {"facts": snapshot["facts"], "eventIds": snapshot["eventIds"]}
    )
    assert snapshot["facts"]["readiness"] == "Blocked"
    assert snapshot["projection"]["manifestTransport"] == "private-blob-platform-token"
    assert "ingestIfNotExists" in transport.writes[0]
    assert "bag_pack(" in transport.writes[0]
    assert "capturedAt" in transport.writes[0]
    assert (
        run_projection(transport, CONTRACTS, bundle_sha256="fixture")["runs"][0]["status"]
        == "AlreadyPresent"
    )
    assert len(transport.writes) == 1
    changed = copy.deepcopy(snapshot)
    changed["facts"]["readiness"] = "Ready"
    with pytest.raises(ValueError, match="digest"):
        persist_snapshot(transport, changed)


def test_incomplete_event_barrier_never_writes_readiness(evidence):
    manifest, events = evidence
    transport = FabricMemory(manifest, events[1:])
    result = run_projection(transport, CONTRACTS, bundle_sha256="fixture")
    assert result["runs"] == [{"runId": manifest["runId"], "status": "AwaitingCompleteBatch"}]
    assert transport.writes == []


def test_later_unfinished_registration_does_not_hide_completed_history(evidence):
    manifest, events = evidence
    later = copy.deepcopy(manifest)
    later.update(
        batchId="00000000-0000-4000-8000-000000000999",
        registeredAt="2026-10-29T09:02:00Z",
    )
    transport = FabricMemory(manifest, events)
    transport.manifests = lambda: [manifest, later]
    result = run_projection(transport, CONTRACTS, bundle_sha256="fixture")
    assert result["runs"][0]["status"] == "Appended"
    assert transport.snapshots[0]["eventIds"] == sorted(event["eventId"] for event in events)


def test_eventhouse_date_coercion_is_restored_only_after_semantic_match(evidence):
    manifest, events = evidence
    events = copy.deepcopy(events)
    expected = copy.deepcopy(events)
    events[0]["data"]["capturedAt"] = "2026-10-29T08:00:00.0000000Z"
    assert restore_upload_metadata([manifest], events) == expected
    events[0]["data"]["capturedAt"] = "2026-10-29T08:00:01.0000000Z"
    with pytest.raises(ValueError, match="pinned Blob"):
        restore_upload_metadata([manifest], events)


def test_ingestion_acknowledgement_without_readback_is_not_success(evidence):
    manifest, events = evidence
    transport = FabricMemory(manifest, events)
    real = transport.kql
    transport.kql = lambda query, **kwargs: (
        real(query, **kwargs) if query.startswith("CloseoutFlowEventsV02") else []
    )
    with pytest.raises(RuntimeError, match="readable immutable"):
        run_projection(transport, CONTRACTS, bundle_sha256="fixture")


def test_manifest_listing_checks_path_identity_and_uses_platform_storage_token(evidence):
    manifest, _ = evidence
    name = f"registrations/{manifest['runId']}/{manifest['batchId']}/manifest.json"
    page = (
        f"<EnumerationResults><Blobs><Blob><Name>{name}</Name></Blob></Blobs></EnumerationResults>"
    )
    listing = Mock(content=page.encode())
    blob = Mock()
    blob.json.return_value = manifest
    session = Mock()
    session.get.side_effect = [listing, blob]
    token = Mock(return_value="platform-token")
    transport = NotebookTransport(CONFIG, token, session)
    assert transport.manifests() == [manifest]
    token.assert_called_with("storage")
    assert session.get.call_args.kwargs["allow_redirects"] is False
    assert session.get.call_args.args[0].endswith("/ledger/" + name)
    manifest["batchId"] = "00000000-0000-4000-8000-000000000999"
    session.get.side_effect = [listing, blob]
    with pytest.raises(ValueError, match="path disagrees"):
        transport.manifests()


def test_notebook_bundles_original_projection_and_schemas_without_tokens():
    definition, bundle_hash = notebook_definition(ROOT, CONFIG)
    notebook = json.loads(base64.b64decode(definition["parts"][0]["payload"]))
    source = "".join(notebook["cells"][0]["source"])
    compile(source, "fabric-notebook", "exec")
    assert bundle_hash in source
    assert "shared/readiness.py" in source
    assert "contracts/events/evidence-batch-completed.schema.json" in source
    assert "AzureCliCredential" not in source
    assert "DefaultAzureCredential" not in source


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://example.blob.core.windows.net",
        "https://example.blob.core.windows.net?sig=secret",
        "https://example.blob.core.windows.net/other-container",
        "https://example.blob.core.windows.net.attacker.example",
    ],
)
def test_fabric_transport_rejects_credentials_and_unscoped_endpoints(endpoint):
    with pytest.raises(ValueError, match="credential-free"):
        validate_target({**CONFIG, "storageEndpoint": endpoint})


def test_staged_rules_scope_run_and_case_and_detect_becomes_not_steady_state():
    entities = activator_entities(
        CONFIG,
        "00000000-0000-4000-8000-000000000003",
        start_time="2026-10-29T00:00:00Z",
    )
    by_name = {entity["payload"]["name"]: entity for entity in entities}
    split = json.loads(by_name["case-events"]["payload"]["definition"]["instance"])
    mappings = [
        row for step in split["steps"] for row in step["rows"] if row["kind"] == "FieldIdMapping"
    ]
    assert mappings[0]["arguments"][0]["value"] == "objectId"
    for state in ("Blocked", "Ready"):
        rule = by_name["becomes-" + state]["payload"]
        assert rule["definition"]["settings"]["shouldRun"] is False
        assert rule["parentObject"] == by_name["readiness"]["payload"]["parentObject"]
        template = json.loads(rule["definition"]["instance"])
        detect = next(step for step in template["steps"] if step["name"] == "ScalarDetectStep")
        assert detect["rows"][0]["kind"] == "TextChanges"
        assert detect["rows"][0]["arguments"] == [
            {"name": "op", "type": "string", "value": "ChangesTo"},
            {"name": "value", "type": "string", "value": state},
        ]
        binding = next(step for step in template["steps"] if step["name"] == "ActStep")["rows"]
        assert len(binding) == 1
        parameters = next(
            item["values"] for item in binding[0]["arguments"] if item["name"] == "parameters"
        )
        values = {
            item["arguments"][0]["value"]: item["arguments"][2]["values"][0] for item in parameters
        }
        assert values["runId"]["arguments"][0]["value"] == by_name["runId"]["uniqueIdentifier"]
        assert (
            values["factsRevision"]["arguments"][0]["value"]
            == by_name["factsRevision"]["uniqueIdentifier"]
        )
    pipeline = json.loads(base64.b64decode(pipeline_definition()["parts"][0]["payload"]))
    assert pipeline["properties"]["activities"][0]["type"] == "Fail"
    assert "url" not in json.dumps(pipeline)


def test_enabled_dispatch_excludes_history_and_leaves_ready_rule_stopped():
    start = "2026-10-29T13:00:00Z"
    entities = activator_entities(
        CONFIG, "00000000-0000-4000-8000-000000000003", start_time=start, enabled=True
    )
    by_name = {entity["payload"]["name"]: entity["payload"] for entity in entities}
    source = by_name["Immutable Fabric facts/readiness revisions"]
    assert source["runSettings"]["isStopped"] is False
    assert f"| where timestamp >= datetime({start})" in source["query"]["queryString"]
    assert by_name["becomes-Blocked"]["definition"]["settings"]["shouldRun"] is True
    assert by_name["becomes-Ready"]["definition"]["settings"]["shouldRun"] is False


def test_authenticated_dispatch_uses_stable_snapshot_identity_and_json_encoding():
    definition = dispatch_pipeline_definition("00000000-0000-4000-8000-000000000003")
    pipeline = json.loads(base64.b64decode(definition["parts"][0]["payload"]))
    activity = pipeline["properties"]["activities"][0]
    body = activity["typeProperties"]["body"]["value"]
    assert "setProperty(" in body
    assert "json(string(pipeline().parameters.readinessMissing))" in body
    assert "'occurredAt', pipeline().parameters.evaluatedAt" in body
    assert "substring(pipeline().parameters.factsRevision" in body
    assert "utcNow(" not in body
    assert "guid(" not in body
    assert "sig=" not in activity["typeProperties"]["relativeUrl"]
    assert activity["policy"]["secureInput"] is True
    assert activity["policy"]["secureOutput"] is True
    with pytest.raises(ValueError):
        dispatch_pipeline_definition("https://example.test?sig=secret")

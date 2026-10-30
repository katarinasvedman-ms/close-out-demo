import copy
import json
from pathlib import Path

import pytest
from jsonschema import ValidationError

from agents.coordinator_oauth import OAuthClient
from agents.coordinator_workflow import (
    PARSE_SECURE,
    SECURE,
    delegated_workflow,
    isolated_probe,
    readable_workflow,
)

ROOT = Path(__file__).resolve().parents[2]
CLIENT = OAuthClient(
    tenantId="00000000-0000-4000-8000-000000000001",
    clientId="00000000-0000-4000-8000-000000000002",
    objectId="00000000-0000-4000-8000-000000000003",
)
SECRET = "https://demo-vault.vault.azure.net/secrets/coordinator-user"


def source():
    return json.loads((ROOT / "src" / "logicapps" / "coordinator" / "workflow.json").read_text())


def test_live_fabric_question_requests_scoped_latest_facts_not_a_second_draft():
    prompt = (ROOT / "src" / "agents" / "prompts" / "closeout-fabric.txt").read_text()
    for field in ("siteId", "caseId", "program", "runId", "factsRevision"):
        assert f"<{field}>" in prompt
    assert "NOT a gap\nanalysis or recommendations" in prompt
    assert "greatest validatedAt" in prompt
    assert "Never shorten this to a request for Fabric to draft the analysis" in prompt


def test_delegation_changes_only_coordinator_authentication_and_serializes_token_rotation():
    original = source()
    before = copy.deepcopy(original)
    result = delegated_workflow(original, CLIENT, SECRET, "15")
    assert original == before
    actions = result["definition"]["actions"]
    assert actions["Acknowledge_durable_workflow_run"]["operationOptions"] == "Asynchronous"
    start = actions["Start_agent_response"]
    poll = actions["Wait_for_agent_completion"]["actions"]["Get_agent_response"]
    assert start["inputs"]["body"]["agent_reference"]["version"] == "15"
    assert start["inputs"]["headers"]["Authorization"] == poll["inputs"]["headers"]["Authorization"]
    assert "authentication" not in start["inputs"]
    assert "authentication" not in poll["inputs"]
    assert start["runAfter"] == {"Persist_coordinator_credential": ["Succeeded"]}
    assert start["inputs"]["retryPolicy"] == {"type": "none"}
    assert actions["Persist_coordinator_credential"]["runAfter"] == {
        "Parse_coordinator_access": ["Succeeded"]
    }
    for name in (
        "Get_coordinator_credential",
        "Refresh_coordinator_access",
        "Persist_coordinator_credential",
        "Start_agent_response",
    ):
        assert actions[name]["runtimeConfiguration"] == SECURE
    for name in ("Parse_coordinator_credential", "Parse_coordinator_access"):
        assert actions[name]["runtimeConfiguration"] == PARSE_SECURE
    assert poll["runtimeConfiguration"] == SECURE
    assert (
        actions["Save_completed_agent_response"]
        == original["definition"]["actions"]["Save_completed_agent_response"]
    )
    assert all(
        trigger["runtimeConfiguration"]["concurrency"]["runs"] == 1
        for trigger in result["definition"]["triggers"].values()
    )
    schema = actions["Parse_coordinator_credential"]["inputs"]["schema"]
    assert schema["properties"]["clientId"]["enum"] == [CLIENT.clientId]
    assert schema["properties"]["tenantId"]["enum"] == [CLIENT.tenantId]
    access = actions["Parse_coordinator_access"]["inputs"]["schema"]
    assert access["properties"]["expires_in"]["minimum"] > 30 * 60


@pytest.mark.parametrize(
    ("uri", "version"),
    [
        ("https://attacker.example/secrets/token", "15"),
        (SECRET + "/unexpected-version", "15"),
        (SECRET, "latest"),
    ],
)
def test_delegation_rejects_nonvault_credentials_or_unpinned_versions(uri, version):
    with pytest.raises(ValueError):
        delegated_workflow(source(), CLIENT, uri, version)


def test_delegation_refuses_a_timeout_exceeding_the_validated_token_lifetime():
    original = source()
    original["definition"]["actions"]["Wait_for_agent_completion"]["limit"]["timeout"] = "PT2H"
    with pytest.raises(ValueError, match="token lifetime"):
        delegated_workflow(original, CLIENT, SECRET, "15")


def test_probe_validates_real_callback_shape_and_isolates_result_blobs():
    event = json.loads((ROOT / "contracts" / "examples" / "headless.json").read_text())["callback"]
    original = delegated_workflow(source(), CLIENT, SECRET, "15")
    result = isolated_probe(original, event, "2026-10-30T15:00:00Z")
    actions = result["definition"]["actions"]
    assert actions["Probe_event"]["inputs"] == event
    assert "Acknowledge_durable_workflow_run" not in actions
    assert actions["Get_coordinator_credential"]["runAfter"] == {
        "Claim_isolated_probe": ["Succeeded"]
    }
    assert actions["Claim_isolated_probe"]["inputs"]["headers"]["If-None-Match"] == "*"
    assert actions["Claim_isolated_probe"]["inputs"]["retryPolicy"] == {"type": "none"}
    assert "triggerBody()" not in json.dumps(result)
    assert "results/coordinator-oauth-probe/" in json.dumps(result)
    assert "results/coordinator/" not in json.dumps(result)
    assert len(result["definition"]["triggers"]) == 1
    event["data"]["signal"]["siteId"] = "OTHER-SITE"
    with pytest.raises(ValueError):
        isolated_probe(original, event, "2026-10-30T15:00:00Z")
    del event["data"]["factsRevision"]
    with pytest.raises(ValidationError):
        isolated_probe(original, event, "2026-10-30T15:00:00Z")


@pytest.mark.parametrize("start", ["2026-10-30T15:00:00", "2026-10-30T15:00:00+01:00"])
def test_probe_requires_explicit_utc(start):
    event = json.loads((ROOT / "contracts" / "examples" / "headless.json").read_text())["callback"]
    with pytest.raises(ValueError, match="UTC"):
        isolated_probe(delegated_workflow(source(), CLIENT, SECRET, "17"), event, start)


def test_readable_workflow_keeps_raw_response_and_delegated_auth_before_guarded_markdown_write():
    original = delegated_workflow(source(), CLIENT, SECRET, "17")
    before = copy.deepcopy(original)
    result = readable_workflow(original, "18")
    assert original == before
    actions = result["definition"]["actions"]
    start = actions["Start_agent_response"]
    assert start["inputs"]["body"]["agent_reference"]["version"] == "18"
    assert start["inputs"]["retryPolicy"] == {"type": "none"}
    assert start["runAfter"] == {"Persist_coordinator_credential": ["Succeeded"]}
    assert (
        actions["Persist_coordinator_credential"]
        == before["definition"]["actions"]["Persist_coordinator_credential"]
    )
    completed = actions["Save_completed_agent_response"]["actions"]
    assert (
        completed["Save_response_to_Blob"]
        == before["definition"]["actions"]["Save_completed_agent_response"]["actions"][
            "Save_response_to_Blob"
        ]
    )
    verified = completed["Validate_brief_sources"]
    assert "Brief_failed_tools" in verified["expression"]
    for field in ("sourceRef", "caseId", "siteId", "program"):
        assert field in verified["expression"]
    checks = verified["actions"]
    assert checks["Brief_missing_conditions"]["inputs"]["from"].endswith("['readinessMissing']")
    document = checks["Validate_brief_document"]
    assert "equals(length(body('Brief_text')), 1)" in document["expression"]
    assert "equals(length(body('Brief_missing_conditions')), 0)" in document["expression"]
    assert document["else"]["actions"]["InvalidBrief"]["inputs"]["runStatus"] == "Failed"
    put = document["actions"]["Save_readable_brief"]["inputs"]
    assert "'.md'" in put["uri"] and "'.json'" not in put["uri"]
    assert put["body"] == "@first(body('Brief_text'))?['text']"
    assert put["headers"]["Content-Type"] == "text/markdown; charset=utf-8"
    assert put["headers"]["If-None-Match"] == "*"
    assert put["headers"]["x-ms-meta-agentversion"] == "18"
    assert put["headers"]["x-ms-meta-briefversion"] == "1"
    assert put["authentication"]["type"] == "ManagedServiceIdentity"
    for field in (
        "siteid",
        "caseid",
        "runid",
        "workflowrunid",
        "readiness",
        "responseid",
        "program",
        "sourceref",
        "factsrevision",
    ):
        assert f"x-ms-meta-{field}" in put["headers"]
    for version in ("latest", "0", ""):
        with pytest.raises(ValueError, match="explicit"):
            readable_workflow(original, version)

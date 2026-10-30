"""Render an opt-in delegated coordinator workflow without changing the source workflow."""

import argparse
import copy
import json
import re
from pathlib import Path

from agents.coordinator_oauth import OWNER, SCOPE, OAuthClient
from shared.contract_validation import load_validators
from shared.local_demo import write_json

SECURE = {"secureData": {"properties": ["inputs", "outputs"]}}
# ParseJson secures its parsing output through inputs; explicit secure outputs is invalid.
PARSE_SECURE = {"secureData": {"properties": ["inputs"]}}
VAULT_AUTH = {"type": "ManagedServiceIdentity", "audience": "https://vault.azure.net"}


def delegated_workflow(
    source: dict,
    client: OAuthClient,
    credential_uri: str,
    version: str,
) -> dict:
    if not re.fullmatch(
        r"https://[a-z0-9-]+\.vault\.azure\.net/secrets/[a-zA-Z0-9-]+", credential_uri
    ) or not re.fullmatch(r"[1-9][0-9]*", version):
        raise ValueError("Select an explicit Key Vault secret URI and a published agent version")
    workflow = copy.deepcopy(source)
    actions = workflow["definition"]["actions"]
    actions["Acknowledge_durable_workflow_run"]["operationOptions"] = "Asynchronous"
    if actions["Wait_for_agent_completion"]["limit"]["timeout"] != "PT30M":
        raise ValueError("Review delegated token lifetime if the workflow timeout changes")
    actions["Get_coordinator_credential"] = {
        "type": "Http",
        "inputs": {
            "method": "GET",
            "uri": f"{credential_uri}?api-version=7.4",
            "authentication": VAULT_AUTH,
        },
        "runtimeConfiguration": SECURE,
        "runAfter": {"Acknowledge_durable_workflow_run": ["Succeeded"]},
    }
    actions["Parse_coordinator_credential"] = {
        "type": "ParseJson",
        "inputs": {
            "content": "@json(body('Get_coordinator_credential')?['value'])",
            "schema": {
                "type": "object",
                "required": ["tenantId", "clientId", "refreshToken"],
                "additionalProperties": False,
                "properties": {
                    "tenantId": {"type": "string", "enum": [client.tenantId]},
                    "clientId": {"type": "string", "enum": [client.clientId]},
                    "refreshToken": {"type": "string", "minLength": 1},
                },
            },
        },
        "runtimeConfiguration": PARSE_SECURE,
        "runAfter": {"Get_coordinator_credential": ["Succeeded"]},
    }
    actions["Refresh_coordinator_access"] = {
        "type": "Http",
        "inputs": {
            "method": "POST",
            "uri": f"https://login.microsoftonline.com/{client.tenantId}/oauth2/v2.0/token",
            "headers": {"Content-Type": "application/x-www-form-urlencoded"},
            "body": (
                "@concat('client_id=', "
                "uriComponent(body('Parse_coordinator_credential')?['clientId']), "
                "'&grant_type=refresh_token&refresh_token=', "
                "uriComponent(body('Parse_coordinator_credential')?['refreshToken']), "
                f"'&scope=', uriComponent('{SCOPE} offline_access'))"
            ),
            "retryPolicy": {"type": "none"},
        },
        "runtimeConfiguration": SECURE,
        "runAfter": {"Parse_coordinator_credential": ["Succeeded"]},
    }
    actions["Parse_coordinator_access"] = {
        "type": "ParseJson",
        "inputs": {
            "content": "@body('Refresh_coordinator_access')",
            "schema": {
                "type": "object",
                "required": ["access_token", "refresh_token", "token_type", "expires_in"],
                "properties": {
                    "access_token": {"type": "string", "minLength": 1},
                    "refresh_token": {"type": "string", "minLength": 1},
                    "token_type": {"type": "string", "enum": ["Bearer"]},
                    "expires_in": {"type": "integer", "minimum": 1860},
                },
            },
        },
        "runtimeConfiguration": PARSE_SECURE,
        "runAfter": {"Refresh_coordinator_access": ["Succeeded"]},
    }
    actions["Persist_coordinator_credential"] = {
        "type": "Http",
        "inputs": {
            "method": "PUT",
            "uri": f"{credential_uri}?api-version=7.4",
            "authentication": VAULT_AUTH,
            "headers": {"Content-Type": "application/json"},
            "body": {
                "value": (
                    "@string(setProperty(body('Parse_coordinator_credential'), "
                    "'refreshToken', body('Parse_coordinator_access')?['refresh_token']))"
                ),
                "contentType": "application/json",
                "attributes": {"enabled": True},
                "tags": {"component": OWNER, "client-id": client.clientId},
            },
        },
        "runtimeConfiguration": SECURE,
        "runAfter": {"Parse_coordinator_access": ["Succeeded"]},
    }
    authorization = "@concat('Bearer ', body('Parse_coordinator_access')?['access_token'])"
    start = actions["Start_agent_response"]
    start["inputs"].pop("authentication")
    start["inputs"]["headers"]["Authorization"] = authorization
    start["inputs"]["body"]["agent_reference"]["version"] = version
    start["runAfter"] = {"Persist_coordinator_credential": ["Succeeded"]}
    poll = actions["Wait_for_agent_completion"]["actions"]["Get_agent_response"]
    poll["inputs"].pop("authentication")
    poll["inputs"]["headers"] = {"Authorization": authorization}
    for trigger in workflow["definition"]["triggers"].values():
        trigger["runtimeConfiguration"] = {"concurrency": {"runs": 1}}
    return workflow


def isolated_probe(workflow: dict, event: dict, start_time: str) -> dict:
    from datetime import datetime, timedelta

    from shared.headless import validate_callback

    validate_callback(event, load_validators(Path("contracts")))
    instant = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
    if instant.utcoffset() != timedelta(0):
        raise ValueError("Use an explicit UTC probe start time")
    result = copy.deepcopy(workflow)
    result["definition"]["triggers"] = {
        "Run_isolated_delegated_probe": {
            "type": "Recurrence",
            "recurrence": {"frequency": "Minute", "interval": 1, "startTime": start_time},
            "runtimeConfiguration": {"concurrency": {"runs": 1}},
        }
    }
    actions = result["definition"]["actions"]
    del actions["Acknowledge_durable_workflow_run"]
    actions["Probe_event"] = {"type": "Compose", "inputs": event, "runAfter": {}}
    actions["Claim_isolated_probe"] = {
        "type": "Http",
        "inputs": {
            "method": "PUT",
            "uri": (
                "@concat(appsetting('FLOW_STORAGE_URL'), "
                "'results/coordinator-oauth-probe/claims/', "
                "uriComponent(outputs('Probe_event')?['data']?['transitionId']), '.json')"
            ),
            "headers": {
                "Content-Type": "application/json",
                "x-ms-blob-type": "BlockBlob",
                "x-ms-version": "2023-11-03",
                "If-None-Match": "*",
            },
            "body": {"workflowRunId": "@workflow().run.name"},
            "authentication": {
                "type": "ManagedServiceIdentity",
                "audience": "https://storage.azure.com/",
            },
            "retryPolicy": {"type": "none"},
        },
        "runAfter": {"Probe_event": ["Succeeded"]},
    }
    actions["Get_coordinator_credential"]["runAfter"] = {"Claim_isolated_probe": ["Succeeded"]}
    encoded = json.dumps(result).replace("triggerBody()", "outputs('Probe_event')")
    return json.loads(encoded.replace("results/coordinator/", "results/coordinator-oauth-probe/"))


def readable_workflow(source: dict, version: str) -> dict:
    if not re.fullmatch(r"[1-9][0-9]*", version):
        raise ValueError("Select an explicit readable coordinator version")
    result = copy.deepcopy(source)
    actions = result["definition"]["actions"]
    start = actions["Start_agent_response"]["inputs"]["body"]
    start["agent_reference"]["version"] = version
    start["max_output_tokens"] = 7000
    start["input"][0]["content"][0]["text"] = (
        "@concat('Prepare the human-readable close-out review brief for site ', "
        "triggerBody()?['siteId'], ', case ', triggerBody()?['caseId'], "
        "', program ', triggerBody()?['data']?['program'], "
        "', exact live-test runId ', triggerBody()?['data']?['runId'], "
        "', factsRevision ', triggerBody()?['data']?['factsRevision'], "
        "'. Require the exact immutable sourceRef ', triggerBody()?['data']?['sourceRef'], "
        "'. Retrieve both required sources. Write the final Markdown brief, not JSON.')"
    )
    completed = actions["Save_completed_agent_response"]["actions"]
    raw = completed["Save_response_to_Blob"]

    def query(name: str, where: str) -> None:
        completed[name] = {
            "type": "Query",
            "inputs": {"from": "@variables('Agent_response')?['output']", "where": where},
            "runAfter": {"Save_response_to_Blob": ["Succeeded"]},
        }

    query(
        "Brief_messages",
        "@and(equals(item()?['type'], 'message'), equals(item()?['role'], 'assistant'))",
    )
    for label, name in (
        ("fabric-facts", "Brief_fabric_receipts"),
        ("documents", "Brief_document_receipts"),
    ):
        query(
            name,
            "@and(equals(item()?['type'], 'mcp_call'), "
            f"equals(item()?['server_label'], '{label}'), empty(item()?['error']), "
            "not(equals(item()?['status'], 'failed')), not(empty(item()?['output'])))",
        )
    query(
        "Brief_failed_tools",
        "@and(equals(item()?['type'], 'mcp_call'), "
        "or(not(empty(item()?['error'])), equals(item()?['status'], 'failed')))",
    )

    def failure(code: str, message: str) -> dict:
        return {
            "actions": {
                code: {
                    "type": "Terminate",
                    "inputs": {
                        "runStatus": "Failed",
                        "runError": {"code": code, "message": message},
                    },
                    "runAfter": {},
                }
            }
        }

    text = "first(body('Brief_text'))?['text']"
    put = copy.deepcopy(raw)
    put["inputs"]["uri"] = raw["inputs"]["uri"].replace("'.json'", "'.md'")
    put["inputs"]["body"] = f"@{text}"
    put["inputs"]["headers"].update(
        {
            "Content-Type": "text/markdown; charset=utf-8",
            "If-None-Match": "*",
            "x-ms-meta-siteid": "@triggerBody()?['siteId']",
            "x-ms-meta-program": "@triggerBody()?['data']?['program']",
            "x-ms-meta-readiness": "@triggerBody()?['data']?['signal']?['readiness']",
            "x-ms-meta-responseid": "@variables('Agent_response')?['id']",
            "x-ms-meta-agentversion": version,
            "x-ms-meta-briefversion": "1",
            "x-ms-meta-sourceref": "@triggerBody()?['data']?['sourceRef']",
            "x-ms-meta-factsrevision": "@triggerBody()?['data']?['factsRevision']",
        }
    )
    put["inputs"]["retryPolicy"] = {"type": "none"}
    put["runAfter"] = {}
    fabric_output = "string(last(body('Brief_fabric_receipts'))?['output'])"
    completed["Validate_brief_sources"] = {
        "type": "If",
        "expression": (
            "@and(equals(length(body('Brief_messages')), 1), "
            "greater(length(body('Brief_fabric_receipts')), 0), "
            "greater(length(body('Brief_document_receipts')), 0), "
            "equals(length(body('Brief_failed_tools')), 0), "
            f"contains({fabric_output}, triggerBody()?['data']?['sourceRef']), "
            f"contains({fabric_output}, triggerBody()?['caseId']), "
            f"contains({fabric_output}, triggerBody()?['siteId']), "
            f"contains({fabric_output}, triggerBody()?['data']?['program']))"
        ),
        "actions": {
            "Brief_text": {
                "type": "Query",
                "inputs": {
                    "from": "@first(body('Brief_messages'))?['content']",
                    "where": "@equals(item()?['type'], 'output_text')",
                },
                "runAfter": {},
            },
            "Brief_missing_conditions": {
                "type": "Query",
                "inputs": {
                    "from": "@triggerBody()?['data']?['signal']?['readinessMissing']",
                    "where": f"@not(contains(string({text}), item()))",
                },
                "runAfter": {"Brief_text": ["Succeeded"]},
            },
            "Validate_brief_document": {
                "type": "If",
                "expression": (
                    "@and(equals(length(body('Brief_text')), 1), "
                    "equals(length(body('Brief_missing_conditions')), 0), "
                    f"startsWith(trim(string({text})), '# Close-out review'), "
                    f"contains(string({text}), triggerBody()?['siteId']), "
                    f"contains(string({text}), '## What needs attention'), "
                    f"contains(string({text}), '## Recommended next steps'), "
                    f"contains(string({text}), '## Evidence and open questions'), "
                    f"contains(string({text}), '## Approval boundary'), "
                    f"greater(length(string({text})), 200), "
                    f"lessOrEquals(length(string({text})), 64000))"
                ),
                "actions": {"Save_readable_brief": put},
                "else": failure(
                    "InvalidBrief",
                    "No readable brief was published: document shape or blocker coverage "
                    "is invalid. The raw response is retained.",
                ),
                "runAfter": {"Brief_missing_conditions": ["Succeeded"]},
            },
        },
        "else": failure(
            "UnverifiedBriefSources",
            "No readable brief was published: required retrieval or immutable scope "
            "could not be verified. The raw response is retained.",
        ),
        "runAfter": {
            name: ["Succeeded"]
            for name in (
                "Brief_messages",
                "Brief_fabric_receipts",
                "Brief_document_receipts",
                "Brief_failed_tools",
            )
        },
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--client-config", type=Path, required=True)
    parser.add_argument("--credential-uri", required=True)
    parser.add_argument("--agent-version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--probe-input", type=Path)
    parser.add_argument("--probe-start-time")
    parser.add_argument("--readable-brief", action="store_true")
    args = parser.parse_args()
    if bool(args.probe_input) != bool(args.probe_start_time):
        parser.error("--probe-input and --probe-start-time must be supplied together")
    client = OAuthClient.model_validate_json(args.client_config.read_text(encoding="utf-8"))
    workflow = delegated_workflow(
        json.loads(args.source.read_text(encoding="utf-8")),
        client,
        args.credential_uri,
        args.agent_version,
    )
    if args.readable_brief:
        workflow = readable_workflow(workflow, args.agent_version)
    if args.probe_input:
        workflow = isolated_probe(
            workflow,
            json.loads(args.probe_input.read_text(encoding="utf-8")),
            args.probe_start_time,
        )
    write_json(args.output, workflow)


if __name__ == "__main__":
    main()

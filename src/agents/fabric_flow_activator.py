"""Publish isolated, run/case-scoped Activator rules and authenticated dispatch.

Template grammar: Microsoft Learn reflex-definition and consume-events-activator-rest-api,
plus Microsoft's skills-for-fabric authoring/{kql-source,rule-conditions,action-types}.md.
Identity/SplitEvent shape: microsoft/terraform-provider-fabric Activator test fixture.
"""

import argparse
import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import httpx

from agents.fabric_flow_runtime import validate_target
from agents.fabric_setup import FabricSetup
from shared.headless import stable_id
from shared.local_demo import write_json

ACTIVATOR_NAME = "closeout-headless-v02-readiness"
PIPELINE_NAME = "closeout-headless-v02-dispatch"
OWNER = "close-out-demo v0.2.0 isolated stopped readiness dispatch; callback identity unverified"
FIELDS = (
    "runId",
    "caseId",
    "siteId",
    "program",
    "factsRevision",
    "readiness",
    "readinessMissing",
    "evaluatedAt",
    "sourceRef",
)


def source_query():
    return """
declare query_parameters(startTime:datetime, endTime:datetime);
CloseoutFlowFactsV02
| extend timestamp=todatetime(snapshot.projectedAt),
         runId=tostring(snapshot.runId), caseId=tostring(snapshot.caseId),
         siteId=tostring(snapshot.siteId), program=tostring(snapshot.program),
         factsRevision=tostring(snapshot.factsRevision),
         readiness=tostring(snapshot.facts.readiness),
         readinessMissing=tostring(snapshot.facts.readinessMissing),
         evaluatedAt=tostring(snapshot.evaluatedAt), sourceRef=tostring(snapshot.sourceRef)
| where timestamp between (startTime .. endTime)
| summarize take_any(*) by runId, caseId, siteId, program, factsRevision
| extend objectId=strcat(runId, "|", caseId, "|", siteId, "|", program)
| project timestamp, objectId, runId, caseId, siteId, program, factsRevision,
          readiness, readinessMissing, evaluatedAt, sourceRef
| order by timestamp asc, factsRevision asc
""".strip()


def encoded_definition(path, value):
    return {
        "parts": [
            {
                "path": path,
                "payloadType": "InlineBase64",
                "payload": base64.b64encode(json.dumps(value).encode()).decode(),
            }
        ]
    }


def pipeline_definition():
    return encoded_definition(
        "pipeline-content.json",
        {
            "properties": {
                "parameters": {field: {"type": "string"} for field in FIELDS},
                "activities": [
                    {
                        "name": "CallbackDisabledUntilIdentityVerified",
                        "type": "Fail",
                        "dependsOn": [],
                        "typeProperties": {
                            "message": "Authenticated Fabric callback identity is not verified",
                            "errorCode": "CallbackIdentityUnverified",
                        },
                    }
                ],
            }
        },
    )


def dispatch_pipeline_definition(connection_id):
    connection_id = str(UUID(connection_id))

    def parameter(name):
        return f"pipeline().parameters.{name}"

    def object_value(fields):
        expression = "json('{}')"
        for name, value in fields.items():
            expression = f"setProperty({expression}, '{name}', {value})"
        return expression

    revision = parameter("factsRevision")
    transition = (
        "concat("
        + ", '-', ".join(
            f"substring({revision}, {start}, {length})"
            for start, length in ((0, 8), (8, 4), (12, 4), (16, 4), (20, 12))
        )
        + ")"
    )
    signal = object_value(
        {
            **{field: parameter(field) for field in ("caseId", "siteId", "readiness")},
            "readinessMissing": f"json(string({parameter('readinessMissing')}))",
            "evaluatedAt": parameter("evaluatedAt"),
        }
    )
    data = object_value(
        {
            **{
                field: parameter(field)
                for field in ("runId", "program", "factsRevision", "sourceRef")
            },
            "transitionId": transition,
            "signal": signal,
        }
    )
    body = object_value(
        {
            "schemaVersion": "'0.2.0'",
            "eventType": "'HeadlessReadiness'",
            "eventId": transition,
            "caseId": parameter("caseId"),
            "siteId": parameter("siteId"),
            "occurredAt": parameter("evaluatedAt"),
            "producer": "'fabric.activator.synthetic-test'",
            "data": data,
        }
    )
    return encoded_definition(
        "pipeline-content.json",
        {
            "properties": {
                "parameters": {field: {"type": "string"} for field in FIELDS},
                "activities": [
                    {
                        "name": "DispatchReadinessToLogicApps",
                        "type": "WebActivity",
                        "dependsOn": [],
                        "policy": {
                            "timeout": "0.00:02:00",
                            "retry": 2,
                            "retryIntervalInSeconds": 30,
                            "secureInput": True,
                            "secureOutput": True,
                        },
                        "typeProperties": {
                            "method": "POST",
                            "relativeUrl": (
                                "/api/coordinator/triggers/Fabric_readiness_transition/invoke"
                                "?api-version=2020-05-01-preview"
                            ),
                            "headers": {"Content-Type": "application/json"},
                            "body": {"type": "Expression", "value": "@" + body},
                            "turnOffAsync": True,
                        },
                        "externalReferences": {"connection": connection_id},
                    }
                ],
            },
        },
    )


def activator_entities(config, pipeline_id, *, start_time, enabled=False):
    validate_target(config)
    workspace = str(UUID(config["workspaceId"]))
    UUID(pipeline_id)
    entities = []

    def identifier(name):
        return stable_id(workspace, ACTIVATOR_NAME, name)

    def argument(name, value, kind="string"):
        return {"name": name, "type": kind, "value": value}

    def reference(name, kind, target, *, value_type="complex"):
        return {
            "name": name,
            "kind": kind,
            "type": value_type,
            "arguments": [argument("entityId", identifier(target))],
        }

    def row(name, kind, *arguments):
        return {"name": name, "kind": kind, "arguments": list(arguments)}

    def step(name, *rows, suffix=""):
        return {"name": name, "id": identifier(name + suffix), "rows": list(rows)}

    def entity(name, kind, payload):
        entities.append({"uniqueIdentifier": identifier(name), "type": kind, "payload": payload})

    def view(name, view_type, template=None, steps=None, *, object_scoped=False, stopped=False):
        definition = {"type": view_type}
        if template:
            definition["instance"] = json.dumps(
                {
                    "templateId": template,
                    "templateVersion": "1.2.4",
                    "steps": steps,
                }
            )
        if view_type == "Rule":
            definition["settings"] = {
                "shouldRun": not stopped,
                "shouldApplyRuleOnUpdate": False,
            }
        payload = {
            "name": name,
            "parentContainer": {"targetUniqueIdentifier": identifier("container")},
            "definition": definition,
        }
        if object_scoped:
            payload["parentObject"] = {"targetUniqueIdentifier": identifier("RunCase")}
        entity(name, "timeSeriesView-v1", payload)

    entity("container", "container-v1", {"name": "Headless test readiness", "type": "kqlQueries"})
    entity(
        "source",
        "kqlSource-v1",
        {
            "name": "Immutable Fabric facts/readiness revisions",
            "parentContainer": {"targetUniqueIdentifier": identifier("container")},
            "runSettings": {"executionIntervalInSeconds": 60, "isStopped": not enabled},
            "query": {
                "queryString": source_query()
                + (f"\n| where timestamp >= datetime({start_time})" if enabled else "")
            },
            "eventhouseItem": {
                "itemId": config["databaseId"],
                "workspaceId": workspace,
                "itemType": "KustoDatabase",
            },
            "metadata": {
                "workspaceId": workspace,
                "measureName": "",
                "querySetId": "",
                "queryId": "",
            },
            "eventTimeSettings": {
                "timeFieldName": "timestamp",
                "ingestionDelayInSeconds": 5,
                "timeZone": "UTC",
            },
            "queryParameters": [
                {"name": "startTime", "type": "DURATION_START", "value": start_time},
                {"name": "endTime", "type": "DURATION_END", "value": datetime.now(UTC).isoformat()},
            ],
        },
    )
    view(
        "events",
        "Event",
        "SourceEvent",
        [
            step(
                "SourceEventStep",
                row(
                    "SourceSelector", "SourceReference", argument("entityId", identifier("source"))
                ),
            )
        ],
    )
    view("RunCase", "Object")
    view(
        "objectId",
        "Attribute",
        "IdentityPartAttribute",
        [
            step(
                "IdPartStep",
                row(
                    "TypeAssertion", "TypeAssertion", argument("op", "Text"), argument("format", "")
                ),
            )
        ],
        object_scoped=True,
    )
    view(
        "identity",
        "Attribute",
        "IdentityTupleAttribute",
        [
            step(
                "IdStructureStep",
                row("IdPart", "IdPart", reference("idPart", "AttributeReference", "objectId")),
            )
        ],
        object_scoped=True,
    )
    view(
        "case-events",
        "Event",
        "SplitEvent",
        [
            step(
                "SplitEventStep",
                row("EventSelector", "Event", reference("event", "EventReference", "events")),
                row(
                    "FieldIdMapping",
                    "FieldIdMapping",
                    argument("fieldName", "objectId"),
                    reference("idPart", "AttributeReference", "objectId"),
                ),
                row(
                    "SplitEventOptions",
                    "EventOptions",
                    argument("isAuthoritative", True, "boolean"),
                ),
            )
        ],
        object_scoped=True,
    )
    for field in FIELDS:
        view(
            field,
            "Attribute",
            "BasicEventAttribute",
            [
                step(
                    "EventSelectStep",
                    row(
                        "EventSelector",
                        "Event",
                        reference("event", "EventReference", "case-events"),
                    ),
                    row("EventFieldSelector", "EventField", argument("fieldName", field)),
                    suffix=field,
                ),
                step(
                    "EventComputeStep",
                    row(
                        "TypeAssertion",
                        "TypeAssertion",
                        argument("op", "Text"),
                        argument("format", ""),
                    ),
                    suffix=field,
                ),
            ],
            object_scoped=True,
        )
    action = {
        "name": "Authenticated callback pipeline" if enabled else "Disabled callback pipeline",
        "fabricItem": {"itemId": pipeline_id, "workspaceId": workspace, "itemType": "Pipeline"},
        "jobType": "Pipeline",
        "parentContainer": {"targetUniqueIdentifier": identifier("container")},
    }
    entity("dispatch", "fabricItemAction-v1", action)
    for state in ("Blocked", "Ready"):
        parameters = []
        for field in FIELDS:
            dynamic = reference("", "AttributeReference", field, value_type="complexReference")
            dynamic.pop("name")
            parameters.append(
                {
                    "kind": "FabricItemParameter",
                    "type": "complex",
                    "arguments": [
                        argument("parameterName", field),
                        argument("parameterType", "String"),
                        {"name": "parameterValue", "type": "complexArray", "values": [dynamic]},
                    ],
                }
            )
        binding = row(
            "FabricItemBinding",
            "FabricItemInvocation",
            argument("workspaceId", workspace),
            argument("itemId", pipeline_id),
            argument("itemType", "Pipeline"),
            argument("jobType", "Pipeline"),
            argument("fabricJobConnectionDocumentId", identifier("dispatch")),
            {"name": "additionalInformation", "type": "array", "values": []},
            {"name": "parameters", "type": "array", "values": parameters},
        )
        view(
            "becomes-" + state,
            "Rule",
            "AttributeTrigger",
            [
                step(
                    "ScalarSelectStep",
                    row(
                        "AttributeSelector",
                        "Attribute",
                        reference("attribute", "AttributeReference", "readiness"),
                    ),
                    suffix=state,
                ),
                step(
                    "ScalarDetectStep",
                    row(
                        "TextChanges",
                        "TextChanges",
                        argument("op", "ChangesTo"),
                        argument("value", state),
                    ),
                    row("OccurrenceOption", "EachTime"),
                    suffix=state,
                ),
                step("ActStep", binding, suffix=state),
            ],
            object_scoped=True,
            stopped=not enabled or state != "Blocked",
        )
    return entities


def stage(config, credential, output, *, connection_id=None, enabled=False):
    if enabled and not connection_id:
        raise ValueError("Enabling dispatch requires an explicit authenticated connection")
    setup = FabricSetup(credential, config, output)
    workspace = config["workspaceId"]
    base = f"https://api.fabric.microsoft.com/v1/workspaces/{workspace}"
    receipt_path = output / "fabric-activator-staging.json"
    previous = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
    headers = {
        "Authorization": "Bearer "
        + credential.get_token("https://api.fabric.microsoft.com/.default").token
    }
    try:
        query = source_query().replace(
            "declare query_parameters(startTime:datetime, endTime:datetime);",
            "let startTime=ago(1d); let endTime=now();",
        )
        observed = setup.kql(query)
        if not observed:
            raise ValueError("No actual immutable Fabric snapshots exist for the Activator source")
        write_json(output / "fabric-activator-source-verification.json", observed)
        response = setup.client.get(base + "/items", headers=headers)
        response.raise_for_status()
        inventory = response.json()
        if inventory.get("continuationToken"):
            raise ValueError("Unexpected paginated inventory; not risking ambiguous item names")

        def publish(name, item_type, definition, receipt_key):
            matches = [item for item in inventory["value"] if item["displayName"] == name]
            if len(matches) > 1:
                raise ValueError("Ambiguous isolated automation item")
            if matches:
                item = matches[0]
                if (
                    previous.get(receipt_key) != item["id"]
                    or item["type"] != item_type
                    or item.get("description") != OWNER
                ):
                    raise ValueError("Refusing to overwrite an unowned automation item")
                backup = setup.fabric(
                    f"workspaces/{workspace}/items/{item['id']}/getDefinition", {}
                )
                stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
                write_json(output / f"{name}-before-{stamp}.json", backup)
                setup.fabric(
                    f"workspaces/{workspace}/items/{item['id']}/updateDefinition",
                    {"definition": definition},
                )
            else:
                item = setup.fabric(
                    f"workspaces/{workspace}/items",
                    {
                        "displayName": name,
                        "description": OWNER,
                        "type": item_type,
                        "definition": definition,
                    },
                )
                if not item.get("id"):
                    response = setup.client.get(base + "/items", headers=headers)
                    response.raise_for_status()
                    item = next(x for x in response.json()["value"] if x["displayName"] == name)
            previous[receipt_key] = item["id"]
            write_json(receipt_path, previous)
            readback = setup.fabric(f"workspaces/{workspace}/items/{item['id']}/getDefinition", {})
            write_json(output / f"{name}-readback.json", readback)
            return item["id"], readback

        expected_pipeline = (
            dispatch_pipeline_definition(connection_id) if connection_id else pipeline_definition()
        )
        pipeline_id, pipeline_readback = publish(
            PIPELINE_NAME, "DataPipeline", expected_pipeline, "pipelineId"
        )
        pipeline = next(
            json.loads(base64.b64decode(part["payload"]))
            for part in pipeline_readback["definition"]["parts"]
            if part["path"] == "pipeline-content.json"
        )
        expected_content = json.loads(base64.b64decode(expected_pipeline["parts"][0]["payload"]))
        if pipeline["properties"] != expected_content["properties"]:
            raise ValueError("Dispatch pipeline failed readback")
        start_time = previous.get("enabledFrom") or datetime.now(UTC).isoformat()
        entities = activator_entities(
            config,
            pipeline_id,
            start_time=start_time
            if enabled
            else (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            enabled=enabled,
        )
        write_json(output / "fabric-activator-entities-request.json", entities)
        activator_id, readback = publish(
            ACTIVATOR_NAME,
            "Reflex",
            encoded_definition("ReflexEntities.json", entities),
            "activatorId",
        )
        stored = next(
            json.loads(base64.b64decode(part["payload"]))
            for part in readback["definition"]["parts"]
            if part["path"] == "ReflexEntities.json"
        )
        rules = [
            entity
            for entity in stored
            if entity["payload"].get("definition", {}).get("type") == "Rule"
        ]
        if len(rules) != 2 or any(
            rule["payload"]["definition"]["settings"]["shouldRun"]
            != (enabled and rule["payload"]["name"] == "becomes-Blocked")
            for rule in rules
        ):
            raise ValueError("Activator rule state failed readback")
        actual = {entity["uniqueIdentifier"]: entity for entity in stored}
        for expected in entities:
            persisted = actual[expected["uniqueIdentifier"]]
            if expected["type"] == "timeSeriesView-v1":
                if persisted["payload"]["definition"] != expected["payload"]["definition"]:
                    raise ValueError("Activator template did not persist unchanged")
            elif expected["type"] in {"fabricItemAction-v1", "kqlSource-v1"}:
                payload = dict(persisted["payload"])
                if expected["type"] == "kqlSource-v1":
                    # The service readback formats duration seed timestamps as US local text.
                    parameters = payload["queryParameters"]
                    for actual_parameter, expected_parameter in zip(
                        parameters, expected["payload"]["queryParameters"], strict=True
                    ):
                        observed_time = datetime.strptime(
                            actual_parameter["value"], "%m/%d/%Y %H:%M:%S"
                        )
                        expected_time = datetime.fromisoformat(expected_parameter["value"]).replace(
                            tzinfo=None, microsecond=0
                        )
                        if observed_time != expected_time or any(
                            actual_parameter[key] != expected_parameter[key]
                            for key in ("name", "type")
                        ):
                            raise ValueError("Activator source duration seeds failed readback")
                    payload["queryParameters"] = expected["payload"]["queryParameters"]
                if payload != expected["payload"]:
                    raise ValueError("Activator source/action binding did not persist unchanged")
        result = {
            "workspaceId": workspace,
            "activatorId": activator_id,
            "pipelineId": pipeline_id,
            "ruleState": "Blocked-enabled" if enabled else "Stopped",
            "callbackState": "Authenticated-trigger-unverified"
            if connection_id
            else ("Blocked-IdentityUnverified"),
            "runtimeVerified": False,
            "sourceTable": "CloseoutFlowFactsV02",
            "parameters": list(FIELDS),
            **({"enabledFrom": start_time} if enabled else {}),
        }
        write_json(receipt_path, result)
        return result
    except httpx.HTTPStatusError as error:
        failure = {
            "status": error.response.status_code,
            "url": str(error.request.url),
            "error": error.response.text,
        }
        write_json(output / "fabric-activator-publication-error.json", failure)
        raise RuntimeError(json.dumps(failure)) from error
    finally:
        setup.client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--storage-endpoint", required=True)
    parser.add_argument("--apply", action="store_true", required=True)
    parser.add_argument("--callback-connection", type=UUID)
    parser.add_argument("--enable", action="store_true")
    args = parser.parse_args()
    config = json.loads(args.config.read_text("utf-8-sig"))
    config = {**config.get("fabric", config), "storageEndpoint": args.storage_endpoint}
    from azure.identity import AzureCliCredential

    with AzureCliCredential(process_timeout=60) as credential:
        print(
            json.dumps(
                stage(
                    config,
                    credential,
                    args.output,
                    connection_id=str(args.callback_connection)
                    if args.callback_connection
                    else None,
                    enabled=args.enable,
                ),
                indent=2,
            )
        )


if __name__ == "__main__":
    main()

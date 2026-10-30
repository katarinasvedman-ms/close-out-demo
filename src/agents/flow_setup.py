"""Opt-in isolated external Fabric ingress; never portal/bootstrap provisioning."""

import argparse
import base64
import copy
import json
import time
from pathlib import Path

from agents.definitions import FoundrySettings
from agents.fabric_setup import FabricSetup
from shared.headless import stable_id
from shared.local_demo import write_json

EVENTS_TABLE = "CloseoutFlowEventsV02"
FACTS_TABLE = "CloseoutFlowFactsV02"
MAPPING = "CloseoutFlowEventsV02Json"
FEED = "closeout-headless-v02"


def ingress_definition(settings):
    source_id = stable_id(settings.workspaceId, FEED, "source")
    topology = {
        "sources": [
            {
                "id": source_id,
                "name": "headless-evidence",
                "type": "CustomEndpoint",
                "properties": {},
            }
        ],
        "destinations": [
            {
                "id": stable_id(settings.workspaceId, FEED, "destination"),
                "name": "headless-eventhouse",
                "type": "Eventhouse",
                "properties": {
                    "dataIngestionMode": "DirectIngestion",
                    "workspaceId": settings.workspaceId,
                    "itemId": settings.databaseId,
                    "tableName": EVENTS_TABLE,
                    "connectionName": "headless-v02-ingest",
                    "mappingRuleName": MAPPING,
                },
                "inputNodes": [{"name": "headless-stream"}],
            }
        ],
        "streams": [
            {
                "id": stable_id(settings.workspaceId, FEED, "stream"),
                "name": "headless-stream",
                "type": "DefaultStream",
                "properties": {},
                "inputNodes": [{"name": "headless-evidence"}],
            }
        ],
        "operators": [],
        "compatibilityLevel": "1.1",
    }
    return {
        "format": "eventstream",
        "parts": [
            {
                "path": path,
                "payloadType": "InlineBase64",
                "payload": base64.b64encode(json.dumps(value).encode()).decode(),
            }
            for path, value in {
                "eventstream.json": topology,
                "eventstreamProperties.json": {
                    "retentionTimeInDays": 1,
                    "eventThroughputLevel": "Low",
                },
            }.items()
        ],
    }


def prepare(settings, credential, output):
    fabric = settings.fabric
    if fabric is None:
        raise ValueError("An explicit existing Fabric test target is required")
    setup = FabricSetup(credential, fabric.model_dump(), output)
    base = f"https://api.fabric.microsoft.com/v1/workspaces/{fabric.workspaceId}"
    headers = {
        "Authorization": "Bearer "
        + credential.get_token("https://api.fabric.microsoft.com/.default").token
    }
    try:
        for table, column in ((EVENTS_TABLE, "event"), (FACTS_TABLE, "snapshot")):
            setup.kql(f".create table {table} ({column}:dynamic)", write=True)
        mapping = json.dumps(
            [{"column": "event", "datatype": "dynamic", "Properties": {"Path": "$"}}]
        )
        setup.kql(
            f".create table {EVENTS_TABLE} ingestion json mapping '{MAPPING}' '{mapping}'",
            write=True,
        )

        def find_feed():
            matches, continuation = [], base + "/eventstreams"
            while continuation:
                response = setup.client.get(continuation, headers=headers)
                response.raise_for_status()
                body = response.json()
                matches.extend(item for item in body["value"] if item["displayName"] == FEED)
                continuation = body.get("continuationUri")
                if continuation and not continuation.startswith(base + "/eventstreams?"):
                    raise ValueError("Unexpected Eventstream pagination target")
            if len(matches) > 1:
                raise ValueError("Ambiguous isolated Eventstream name")
            return matches[0] if matches else None

        feed = find_feed()
        if feed is None:
            setup.fabric(
                f"workspaces/{fabric.workspaceId}/eventstreams",
                {
                    "displayName": FEED,
                    "description": "Isolated v0.2.0 headless synthetic evidence events",
                    "definition": ingress_definition(fabric),
                },
            )
            feed = find_feed()
        if feed is None:
            raise RuntimeError("New Eventstream was not visible after creation")
        definition = setup.fabric(
            f"workspaces/{fabric.workspaceId}/eventstreams/{feed['id']}/getDefinition", {}
        )
        topology = next(
            json.loads(base64.b64decode(part["payload"]))
            for part in definition["definition"]["parts"]
            if part["path"] == "eventstream.json"
        )
        expected = ingress_definition(fabric)["parts"][0]
        if topology != json.loads(base64.b64decode(expected["payload"])):
            write_json(output / "existing-headless-definition.json", definition)
            raise ValueError("Existing isolated topology differs; backed up, not overwritten")
        source_id = topology["sources"][0]["id"]
        connection_url = f"{base}/eventstreams/{feed['id']}/sources/{source_id}/connection"
        for attempt in range(20):
            response = setup.client.get(connection_url, headers=headers)
            if response.status_code == 404 and attempt < 19:
                time.sleep(6)
                continue
            response.raise_for_status()
            connection = response.json()
            break
        # Connection responses also carry keys. Never serialize or print those credentials.
        receipt = {
            "workspaceId": fabric.workspaceId,
            "databaseId": fabric.databaseId,
            "eventstreamId": feed["id"],
            "sourceId": source_id,
            "namespace": connection["fullyQualifiedNamespace"],
            "entity": connection["eventHubName"],
            "eventsTable": EVENTS_TABLE,
            "factsTable": FACTS_TABLE,
            "automaticProjection": False,
            "activatorConfigured": False,
        }
        write_json(output / "fabric-ingress.json", receipt)
        return copy.deepcopy(receipt)
    finally:
        setup.client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--apply",
        action="store_true",
        required=True,
        help="Authorize creating only the isolated v0.2.0 test tables/feed",
    )
    args = parser.parse_args()
    from azure.identity import AzureCliCredential

    settings = FoundrySettings.model_validate_json(args.config.read_text(encoding="utf-8-sig"))
    with AzureCliCredential() as credential:
        print(json.dumps(prepare(settings, credential, args.output), indent=2))


if __name__ == "__main__":
    main()

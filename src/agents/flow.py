"""Explicit synthetic batch upload/status and fixture-event controls; never deploys Fabric."""

import argparse
import copy
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import quote, urlsplit
from uuid import UUID, uuid4

from agents.sources import Sources
from shared.contract_validation import load_validators
from shared.data_agent_stub import DataAgentStub
from shared.headless import BlobStore, Conflict, Ledger, digest, stable_id, timestamp
from shared.seed import load_seed


class EventstreamPublisher:
    def __init__(self, client):
        self.client = client

    def __call__(self, event: dict):
        from azure.eventhub import EventData

        message = EventData(json.dumps(event, separators=(",", ":"), allow_nan=False))
        message.content_type = "application/json"
        message.message_id = event["eventId"]
        batch = self.client.create_batch(partition_key=event["caseId"], timeout=30)
        batch.add(message)
        self.client.send_batch(batch, timeout=30)


def storage_endpoint(value: str) -> str:
    if not re.fullmatch(r"https://[a-z0-9]{3,24}\.blob\.core\.windows\.net/?", value):
        raise ValueError("An exact HTTPS Azure Blob account endpoint is required")
    return value.rstrip("/")


def eventstream_client(namespace: str, eventhub: str, credential):
    from azure.eventhub import EventHubProducerClient

    if not re.fullmatch(
        r"[a-z0-9][a-z0-9-]*\.servicebus\.windows\.net", namespace
    ) or not re.fullmatch(r"[a-zA-Z0-9_.-]+", eventhub):
        raise ValueError("An exact Eventstream Event Hubs protocol namespace/entity is required")
    return EventHubProducerClient(
        fully_qualified_namespace=namespace,
        eventhub_name=eventhub,
        credential=credential,
        retry_total=3,
        retry_backoff_factor=1,
        auth_timeout=30,
        socket_timeout=30,
    )


def prepare_batch(
    facts: dict,
    assets: Path,
    contracts: Path,
    container_url: str,
    evidence_ids: list[str],
    run_id: str,
    batch_id: str,
) -> tuple[dict, list[tuple]]:
    run_id, batch_id = str(UUID(run_id)), str(UUID(batch_id))
    if not evidence_ids or len(set(evidence_ids)) != len(evidence_ids):
        raise ValueError("Select distinct evidence IDs")
    sources = Sources(facts, assets, contracts)
    selected = list(evidence_ids)
    if any(sources.records.get(("Photo", identifier), {}).get("sector") for identifier in selected):
        rfds = [row["artifactId"] for row in facts["artifacts"] if row["artifactType"] == "rfds"]
        if len(rfds) != 1:
            raise ValueError("Photo validation requires one unambiguous registered RFDS")
        selected.extend(identifier for identifier in rfds if identifier not in selected)
    manifest = {
        "schemaVersion": "0.2.0",
        "runId": run_id,
        "batchId": batch_id,
        "caseId": facts["caseId"],
        "siteId": facts["site"]["siteId"],
        "program": facts["project"]["programCode"],
        "registeredAt": timestamp(),
        "facts": copy.deepcopy(facts),
        "members": [],
    }
    uploads = []
    for identifier in selected:
        photo = sources.records.get(("Photo", identifier))
        artifact = sources.records.get(("AcceptanceArtifact", identifier))
        original = photo or artifact
        if original is None:
            raise PermissionError("Evidence is outside the selected synthetic site")
        data, _ = sources.read_asset(original)
        name = f"{run_id}/{batch_id}/{quote(identifier, safe='')}"
        row = copy.deepcopy(original)
        row["blobUrl"] = f"{container_url.rstrip('/')}/{name}.{'png' if photo else 'pdf'}"
        member = {
            "evidenceId": identifier,
            "kind": "photo" if photo else "document",
            "metadata": row,
            "blob": {
                "url": row["blobUrl"],
                "sha256": hashlib.sha256(data).hexdigest(),
                "mediaType": "image/png" if photo else "application/pdf",
            },
        }
        uploads.append((member["blob"], data))
        if artifact:
            text, _ = sources.read_asset(original, text=True)
            member["text"] = {
                "url": f"{container_url.rstrip('/')}/{name}.txt",
                "sha256": hashlib.sha256(text).hexdigest(),
                "mediaType": "text/plain",
            }
            uploads.append((member["text"], text))
        manifest["members"].append(member)
    return manifest, uploads


def upload_batch(manifest: dict, uploads: list[tuple], inputs, ledger: Ledger, contracts: Path):
    from azure.core.exceptions import ResourceExistsError
    from azure.storage.blob import ContentSettings

    prefix = f"{manifest['runId']}/{manifest['batchId']}"
    intent_key = f"registrations/{prefix}/intent.json"
    previous, _ = ledger.store.read(intent_key)
    if previous:
        manifest["registeredAt"] = previous["registeredAt"]
    ledger.register(intent_key, manifest)
    for reference, data in uploads:
        path = urlsplit(reference["url"]).path.split("/", 2)[2]
        # get_blob_client expects the decoded Blob name, not a quoted URL path.
        from urllib.parse import unquote

        blob = inputs.get_blob_client(unquote(path))
        try:
            blob.upload_blob(
                data,
                overwrite=False,
                content_settings=ContentSettings(content_type=reference["mediaType"]),
            )
        except ResourceExistsError:
            pass
        properties = blob.get_blob_properties()
        from azure.core import MatchConditions

        actual = blob.download_blob(
            etag=properties.etag, match_condition=MatchConditions.IfNotModified
        ).readall()
        if hashlib.sha256(actual).hexdigest() != reference["sha256"]:
            raise Conflict("An existing batch input has different content; use a new batch")
        reference["etag"] = properties.etag
        if properties.version_id:
            reference["versionId"] = properties.version_id
    load_validators(contracts)["uploads/evidence-batch.schema.json"].validate(manifest)
    ledger.register(f"registrations/{prefix}/manifest.json", manifest)
    marker = inputs.get_blob_client(f"{prefix}/_batch.ready")
    data = json.dumps({"manifestSha256": digest(manifest)}).encode()
    try:
        marker.upload_blob(
            data, overwrite=False, content_settings=ContentSettings(content_type="application/json")
        )
    except ResourceExistsError:
        if marker.download_blob().readall() != data:
            raise Conflict(
                "The existing completion marker belongs to another registration"
            ) from None
    return {
        "runId": manifest["runId"],
        "batchId": manifest["batchId"],
        "manifestSha256": digest(manifest),
        "status": "Uploaded-not-yet-validated",
    }


def emit_fixture_event(
    payload: dict, run_id: str, operation_id: str, ledger: Ledger, contracts: Path, publish
):
    allowed = {
        "WorkOrderChanged": "work-order-changed",
        "TicketChanged": "ticket-changed",
        "MilestoneChanged": "milestone-changed",
    }
    if payload["eventType"] not in allowed:
        raise ValueError("Only explicit synthetic work-order/ticket/milestone changes are allowed")
    event = {
        **copy.deepcopy(payload),
        "schemaVersion": "0.2.0",
        "eventId": stable_id(str(UUID(run_id)), str(UUID(operation_id))),
        "runId": str(UUID(run_id)),
        "producer": "azure.operator.synthetic-fixture-control",
        "fixtureOnly": True,
    }
    if event["data"].get("siteId") != event["siteId"] or (
        "caseId" in event["data"] and event["data"]["caseId"] != event["caseId"]
    ):
        raise ValueError("Fixture event envelope and record identities disagree")
    validators = load_validators(contracts)
    validators[f"events/{allowed[event['eventType']]}.schema.json"].validate(event)
    claim, state = ledger.claim(f"controls/{run_id}/{operation_id}.json", event)
    if claim is not None:
        try:
            publish(event)
            state["published"] = [event["eventId"]]
            state["result"] = {"eventId": event["eventId"], "fixtureOnly": True}
            claim.finish("Completed")
        except Exception:
            claim.finish("Failed", "DeliveryFailed")
            raise
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storage-url", required=True)
    parser.add_argument("--input-container", default="inputs")
    parser.add_argument("--ledger-container", default="ledger")
    parser.add_argument("--contracts", type=Path, default=Path("contracts"))
    commands = parser.add_subparsers(dest="command", required=True)
    upload = commands.add_parser("upload")
    upload.add_argument("--site", required=True)
    upload.add_argument("--scenario", default="initial-blocked")
    upload.add_argument("--evidence", action="append", required=True)
    upload.add_argument("--assets", type=Path, default=Path(".local") / "demo")
    upload.add_argument("--seed", type=Path, default=Path("seed/seed.json"))
    upload.add_argument("--run-id", default=None)
    upload.add_argument("--batch-id", default=None)
    status = commands.add_parser("status")
    status.add_argument("--run-id", required=True, type=UUID)
    status.add_argument("--batch-id", required=True, type=UUID)
    emit = commands.add_parser("emit-fixture")
    emit.add_argument("--event", type=Path, required=True)
    emit.add_argument("--run-id", type=UUID, required=True)
    emit.add_argument("--operation-id", type=UUID, required=True)
    emit.add_argument("--namespace", required=True)
    emit.add_argument("--eventhub", required=True)
    args = parser.parse_args()
    from azure.identity import AzureCliCredential
    from azure.storage.blob import BlobServiceClient

    with (
        AzureCliCredential() as credential,
        BlobServiceClient(storage_endpoint(args.storage_url), credential=credential) as storage,
    ):
        ledger = Ledger(BlobStore(storage.get_container_client(args.ledger_container)))
        if args.command == "status":
            result, _ = ledger.store.read(f"validation/{args.run_id}/{args.batch_id}.json")
            result = result or {"status": "Not-validated"}
        elif args.command == "emit-fixture":
            with eventstream_client(args.namespace, args.eventhub, credential) as client:
                result = emit_fixture_event(
                    json.loads(args.event.read_text(encoding="utf-8-sig")),
                    str(args.run_id),
                    str(args.operation_id),
                    ledger,
                    args.contracts,
                    EventstreamPublisher(client),
                )
        else:
            facts = DataAgentStub(load_seed(args.seed, args.contracts), {args.site}).site_facts(
                args.site, args.scenario
            )
            inputs = storage.get_container_client(args.input_container)
            manifest, uploads = prepare_batch(
                facts,
                args.assets,
                args.contracts,
                inputs.url,
                args.evidence,
                args.run_id or str(uuid4()),
                args.batch_id or str(uuid4()),
            )
            result = upload_batch(manifest, uploads, inputs, ledger, args.contracts)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

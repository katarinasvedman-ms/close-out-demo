"""Synthetic headless transport identity, batch events and optimistic operational receipts."""

import copy
import hashlib
import json
import time
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid4, uuid5


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def stable_id(*values: str) -> str:
    return str(uuid5(NAMESPACE_URL, canonical(values).decode()))


class Busy(RuntimeError):
    pass


class Conflict(ValueError):
    pass


class BlobStore:
    """Conditional writes never replace a different registration or another worker's claim."""

    def __init__(self, container):
        self.container = container

    def read(self, key: str):
        from azure.core.exceptions import ResourceNotFoundError

        try:
            download = self.container.get_blob_client(key).download_blob()
            return json.loads(download.readall()), download.properties.etag
        except ResourceNotFoundError:
            return None, None

    def write(self, key: str, value: dict, etag: str | None):
        from azure.core import MatchConditions
        from azure.core.exceptions import ResourceExistsError, ResourceModifiedError

        blob = self.container.get_blob_client(key)
        try:
            result = blob.upload_blob(
                canonical(value),
                overwrite=etag is not None,
                **(
                    {"etag": etag, "match_condition": MatchConditions.IfNotModified}
                    if etag is not None
                    else {}
                ),
            )
        except (ResourceExistsError, ResourceModifiedError) as error:
            raise Busy("Operational receipt was concurrently modified") from error
        return result["etag"]


class Ledger:
    def __init__(self, store, clock=time.time):
        self.store = store
        self.clock = clock

    def register(self, key: str, manifest: dict):
        previous, etag = self.store.read(key)
        if previous is not None:
            if canonical(previous) != canonical(manifest):
                raise Conflict("An existing registration is immutable; use a new batch/run")
            return
        self.store.write(key, manifest, etag)

    def claim(self, key: str, request: dict, *, seconds: int = 600):
        state, etag = self.store.read(key)
        fingerprint = digest(request)
        if state and state["requestSha256"] != fingerprint:
            raise Conflict("A work key was reused with changed input or agent binding")
        if state and state["status"] == "Completed":
            return None, state
        now = self.clock()
        if state and state["claimUntil"] > now:
            raise Busy("Work is already claimed")
        state = (
            copy.deepcopy(state)
            if state
            else {
                "requestSha256": fingerprint,
                "result": None,
                "outbox": [],
                "published": [],
                "history": [],
            }
        )
        state.update(status="Running", owner=uuid4().hex, claimUntil=now + seconds)
        state["history"].append({"at": now, "outcome": "Claimed", "owner": state["owner"]})
        etag = self.store.write(key, state, etag)
        return Claim(self, key, state, etag, seconds), state


class Claim:
    def __init__(self, ledger: Ledger, key: str, state: dict, etag: str, seconds: int):
        self.ledger, self.key, self.state, self.etag = ledger, key, state, etag
        self.seconds = seconds

    def save(self):
        if self.state["claimUntil"] <= self.ledger.clock():
            raise Busy("Worker claim expired; do not overwrite a newer worker")
        self.state["claimUntil"] = self.ledger.clock() + self.seconds
        self.etag = self.ledger.store.write(self.key, self.state, self.etag)

    def finish(self, status: str, code: str | None = None):
        if self.state["claimUntil"] <= self.ledger.clock():
            raise Busy("Worker claim expired; do not overwrite a newer worker")
        self.state["status"] = status
        self.state["history"].append({"at": self.ledger.clock(), "outcome": status, "code": code})
        self.state["claimUntil"] = 0
        self.etag = self.ledger.store.write(self.key, self.state, self.etag)


def batch_events(
    manifest: dict,
    receipt: dict,
    validators: dict,
    *,
    validation_source="live-foundry-inference",
) -> list[dict]:
    members = {member["evidenceId"]: member for member in manifest["members"]}
    results = receipt["result"]
    identifiers = [row.get("photoId", row.get("artifactId")) for row in results]
    if len(identifiers) != len(set(identifiers)) or set(identifiers) != set(members):
        raise ValueError("Inference did not return every batch member exactly once")
    events = []
    now = receipt["completedAt"]
    for result in results:
        identifier = result.get("photoId", result.get("artifactId"))
        member = members[identifier]
        result["validationResultId"] = "AI-" + stable_id(
            manifest["runId"], manifest["batchId"], identifier, "validation"
        )
        if result["caseId"] != manifest["caseId"]:
            raise ValueError("Validation result belongs to another case")
        for event_type, data, schema in (
            (
                "PhotoUploaded" if member["kind"] == "photo" else "ArtifactUploaded",
                member["metadata"],
                "photo-uploaded" if member["kind"] == "photo" else "artifact-uploaded",
            ),
            ("ValidationResultCreated", result, "validation-result-created"),
        ):
            event = {
                "schemaVersion": "0.2.0",
                "eventType": event_type,
                "eventId": stable_id(
                    manifest["runId"], manifest["batchId"], identifier, event_type
                ),
                "caseId": manifest["caseId"],
                "siteId": manifest["siteId"],
                "occurredAt": now,
                "producer": "azure.validation-tool.synthetic-test"
                if validation_source == "agent-submitted-assessment"
                else "azure.validation.synthetic-test",
                "runId": manifest["runId"],
                "batchId": manifest["batchId"],
                "program": manifest["program"],
                "validationSource": validation_source,
                "inputSha256": member["blob"]["sha256"],
                "data": data,
            }
            validators[f"events/{schema}.schema.json"].validate(event)
            events.append(event)
    completion = {
        **{key: events[0][key] for key in events[0] if key != "inputSha256"},
        "eventType": "EvidenceBatchCompleted",
        "eventId": stable_id(manifest["runId"], manifest["batchId"], "completed"),
        "data": {
            "runId": manifest["runId"],
            "batchId": manifest["batchId"],
            "program": manifest["program"],
            "manifestSha256": digest(manifest),
            "eventIds": [event["eventId"] for event in events],
            "validationSource": validation_source,
        },
    }
    validators["events/evidence-batch-completed.schema.json"].validate(completion)
    return [*events, completion]


async def validate_batch(sources, settings, ledger: Ledger, infer, publish):
    manifest = sources.manifest
    key = f"validation/{manifest['runId']}/{manifest['batchId']}.json"
    claim, state = ledger.claim(
        key, {"manifest": manifest, "agent": settings.validation.model_dump()}
    )
    if claim is None:
        return state
    try:
        if state["result"] is None:
            receipt = await infer("validation", settings, sources, list(sources.members))
            if receipt.get("modes", {}).get("inference") != "live" or not receipt.get("responseId"):
                raise ValueError("A real Foundry inference receipt is required")
            state["outbox"] = batch_events(manifest, receipt, sources.validators)
            state["result"] = receipt
            claim.save()
        for event in state["outbox"]:
            if event["eventId"] not in state["published"]:
                claim.save()
                publish(event)
                state["published"].append(event["eventId"])
                claim.save()
        claim.finish("Completed")
    except Exception:
        # A delivery retry uses the saved inference; a stale worker may not write any failure.
        claim.finish("Failed", "InferenceFailed" if state["result"] is None else "DeliveryFailed")
        raise
    return state


def validate_callback(payload: dict, validators: dict):
    validators["messages/headless-readiness.schema.json"].validate(payload)
    data = payload["data"]
    if (payload["caseId"], payload["siteId"]) != (
        data["signal"]["caseId"],
        data["signal"]["siteId"],
    ):
        raise ValueError("Callback envelope and readiness identities disagree")
    return (
        f"coordinator/{data['runId']}/{payload['caseId']}/"
        f"{data['signal']['readiness']}/{data['transitionId']}.json"
    )


def accepted_document_status(result: dict, threshold: float) -> str:
    if result["reviewStatus"] == "NeedsReview" or result["reviewStatus"] == "Overridden":
        return "Submitted"
    accepted = result["reviewStatus"] == "Confirmed" or (
        result["reviewStatus"] == "Auto" and result["confidence"] >= threshold
    )
    if not accepted:
        return "Submitted"
    return "Validated" if result["compliant"] else "Rejected"


def timestamp() -> str:
    return datetime.now(UTC).isoformat()

"""Pure projection helper for an external Fabric test notebook, not an Azure readiness API.

The caller must persist the resulting immutable row in Fabric before publishing
its readiness observation. Calling this locally is only an offline conformance test.
"""

import copy
from datetime import datetime

from shared.contract_validation import load_validators, select_validator
from shared.headless import accepted_document_status, digest
from shared.readiness import evaluate, latest_result
from shared.seed import KEYS, Snapshot

FIELDS = {
    "CustomerProject": "project",
    "Site": "site",
    "ArtifactRequirement": "requirements",
    "ChecklistItem": "checklist",
    "AcceptanceArtifact": "artifacts",
    "Photo": "photos",
    "ValidationResult": "validations",
    "Ticket": "tickets",
    "WorkOrder": "workOrders",
    "Milestone": "milestones",
}
SCHEMAS = {
    "PhotoUploaded": ("photo-uploaded", "Photo"),
    "ArtifactUploaded": ("artifact-uploaded", "AcceptanceArtifact"),
    "ValidationResultCreated": ("validation-result-created", "ValidationResult"),
    "TicketChanged": ("ticket-changed", "Ticket"),
    "WorkOrderChanged": ("work-order-changed", "WorkOrder"),
    "MilestoneChanged": ("milestone-changed", "Milestone"),
    "EvidenceBatchCompleted": ("evidence-batch-completed", None),
}


def event_time(event):
    return datetime.fromisoformat(event["occurredAt"].replace("Z", "+00:00"))


def project_run(manifests: list[dict], events: list[dict], contracts) -> dict | None:
    if not manifests:
        raise ValueError("An explicit synthetic baseline registration is required")
    validators = load_validators(contracts)
    manifests = sorted(
        manifests,
        key=lambda item: (
            datetime.fromisoformat(item["registeredAt"].replace("Z", "+00:00")),
            item["batchId"],
        ),
    )
    identity = tuple(manifests[0][key] for key in ("runId", "caseId", "siteId", "program"))
    batches = {}
    for manifest in manifests:
        validators["uploads/evidence-batch.schema.json"].validate(manifest)
        if tuple(manifest[key] for key in ("runId", "caseId", "siteId", "program")) != identity:
            raise ValueError("Never mix test runs, cases, sites or programs")
        if manifest["batchId"] in batches:
            raise ValueError("Duplicate batch registration")
        batches[manifest["batchId"]] = manifest
    unique = {}
    for event in events:
        if event["eventId"] in unique and digest(event) != digest(unique[event["eventId"]]):
            raise ValueError("An eventId was reused with a different payload")
        if event.get("eventType") not in SCHEMAS:
            raise ValueError("Unknown event in the isolated test feed")
        if (event.get("runId"), event["caseId"], event["siteId"]) != identity[:3]:
            raise ValueError("Event identity disagrees with the test run")
        validators[f"events/{SCHEMAS[event['eventType']][0]}.schema.json"].validate(event)
        unique[event["eventId"]] = copy.deepcopy(event)
    complete = {}
    for event in unique.values():
        if event["eventType"] != "EvidenceBatchCompleted":
            continue
        data = event["data"]
        manifest = batches.get(data["batchId"])
        if (
            manifest is None
            or data["runId"] != identity[0]
            or data["program"] != identity[3]
            or data["manifestSha256"] != digest(manifest)
        ):
            raise ValueError("Batch barrier does not bind its registered manifest")
        expected = set(data["eventIds"])
        if not expected.issubset(unique):
            continue
        members = {member["evidenceId"]: member for member in manifest["members"]}
        observed = set()
        for event_id in expected:
            item = unique[event_id]
            kind = SCHEMAS[item["eventType"]][1]
            if kind not in {"Photo", "AcceptanceArtifact", "ValidationResult"}:
                raise ValueError("Batch barrier contains a non-evidence event")
            record = item["data"]
            identifier = record.get("photoId", record.get("artifactId"))
            member = members.get(identifier)
            if (
                item.get("batchId") != manifest["batchId"]
                or item.get("program") != manifest["program"]
                or member is None
                or item.get("inputSha256") != member["blob"]["sha256"]
                or item.get("validationSource") != data["validationSource"]
                or (
                    data["validationSource"] == "agent-submitted-assessment"
                    and (
                        item["producer"] != "azure.validation-tool.synthetic-test"
                        or event["producer"] != "azure.validation-tool.synthetic-test"
                    )
                )
                or (kind != "ValidationResult" and record != member["metadata"])
                or (kind == "ValidationResult" and record["caseId"] != identity[1])
            ):
                raise ValueError("Batch evidence event does not match the pinned member")
            observed.add((identifier, item["eventType"]))
        required = {
            (identifier, event_type)
            for identifier, member in members.items()
            for event_type in (
                "PhotoUploaded" if member["kind"] == "photo" else "ArtifactUploaded",
                "ValidationResultCreated",
            )
        }
        if observed != required or len(expected) != len(required):
            raise ValueError(
                "Batch barrier must cover each upload and actual validation exactly once"
            )
        complete[data["batchId"]] = event
    # Do not let a later upload make an unfinished initial batch appear complete.
    if any(manifest["batchId"] not in complete for manifest in manifests):
        return None

    baseline = copy.deepcopy(manifests[0]["facts"])
    if (
        baseline["caseId"] != identity[1]
        or baseline["site"]["siteId"] != identity[2]
        or baseline["project"]["programCode"] != identity[3]
    ):
        raise ValueError("Synthetic baseline identity disagrees")
    snapshot = Snapshot({"schemaVersion": "0.2.0"}, {kind: {} for kind in KEYS})
    for kind, field in FIELDS.items():
        rows = [baseline[field]] if field in {"site", "project"} else baseline[field]
        for row in rows:
            select_validator(validators, f"models.schema.json#/$defs/{kind}").validate(row)
            if "siteId" in row and row["siteId"] != identity[2]:
                raise ValueError("Baseline record belongs to another site")
            snapshot.add(kind, row)
    snapshot.add("CloseOutCase", {"caseId": identity[1], "siteId": identity[2]})
    usable = {
        event_id
        for marker in complete.values()
        for event_id in [marker["eventId"], *marker["data"]["eventIds"]]
    }
    controls = {
        key
        for key, event in unique.items()
        if event["eventType"] in {"TicketChanged", "WorkOrderChanged", "MilestoneChanged"}
    }
    usable |= controls
    validation_sources = {key: "seed" for key in snapshot.tables["ValidationResult"]}
    for event in sorted(
        (unique[key] for key in usable), key=lambda row: (event_time(row), row["eventId"])
    ):
        kind = SCHEMAS[event["eventType"]][1]
        if kind is None:
            continue
        record = event["data"]
        if (
            "siteId" in record
            and record["siteId"] != identity[2]
            or "caseId" in record
            and record["caseId"] not in (None, identity[1])
        ):
            raise ValueError("Event record belongs to another site/case")
        if event["eventId"] in controls and (
            event.get("fixtureOnly") is not True
            or event["producer"] != "azure.operator.synthetic-fixture-control"
        ):
            raise ValueError("Business changes must be explicitly labelled fixture controls")
        key = record[KEYS[kind]]
        if kind == "ValidationResult":
            previous = snapshot.tables[kind].get(key)
            if previous is not None and previous != record:
                raise ValueError("A validation ID cannot change its assessment")
            validation_sources[key] = event["validationSource"]
        snapshot.tables[kind][key] = copy.deepcopy(record)
    replacements = {}
    for photo in snapshot.rows("Photo"):
        visited = {photo["photoId"]}
        ancestor = photo
        while ancestor["replacesPhotoId"]:
            previous_id = ancestor["replacesPhotoId"]
            if previous_id in visited:
                raise ValueError("Replacement photo chain contains a cycle")
            visited.add(previous_id)
            ancestor = snapshot.tables["Photo"].get(previous_id)
            if ancestor is None:
                raise ValueError("Replacement photo chain has an unknown ancestor")
        if photo["replacesPhotoId"]:
            previous_id = photo["replacesPhotoId"]
            if previous_id in replacements:
                raise ValueError("Conflicting replacement branches require explicit resolution")
            replacements[previous_id] = photo["photoId"]
            previous = snapshot.tables["Photo"].get(photo["replacesPhotoId"])
            if previous is None or (previous["artifactId"], previous["checklistItemCode"]) != (
                photo["artifactId"],
                photo["checklistItemCode"],
            ):
                raise ValueError("Replacement must resolve to the same pack/checklist")
            previous["isCurrent"] = False
    threshold = baseline["project"]["lowConfidenceThreshold"]
    for artifact in snapshot.rows("AcceptanceArtifact"):
        validations = [
            row
            for row in snapshot.rows("ValidationResult")
            if row.get("artifactId") == artifact["artifactId"]
        ]
        if validations:
            latest = latest_result(validations)
            artifact["status"] = (
                accepted_document_status(latest, threshold) if latest else "Submitted"
            )
    readiness = evaluate(snapshot, identity[2])
    facts = {field: snapshot.rows(kind) for kind, field in FIELDS.items()}
    facts["site"] = snapshot.get("Site", identity[2])
    facts["project"] = baseline["project"]
    evaluated = max(event_time(unique[key]) for key in usable).isoformat()
    facts.update(
        caseId=identity[1],
        readiness=readiness.readiness,
        readinessMissing=readiness.missing,
        constructionComplete=readiness.construction_complete,
        photoGaps=readiness.photo_gaps,
        validationSource="mixed-seed-and-live",
        validationSources=validation_sources,
    )
    facts["site"].update(
        readiness=readiness.readiness,
        readinessMissing=readiness.missing,
        readinessEvaluatedAt=evaluated,
    )
    revision = digest({"facts": facts, "eventIds": sorted(usable)})
    return {
        "schemaVersion": "0.2.0",
        "runId": identity[0],
        "caseId": identity[1],
        "siteId": identity[2],
        "program": identity[3],
        "factsRevision": revision,
        "sourceRef": f"fabric:headless:{identity[0]}:{identity[2]}:{revision}",
        "evaluatedAt": evaluated,
        "eventIds": sorted(usable),
        "facts": facts,
    }

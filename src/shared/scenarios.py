"""Independent synthetic snapshots; none of these fixture edits writes a real case."""

import copy

from shared.seed import Snapshot, refresh_readiness, validation

SCENARIOS = {
    "initial-blocked",
    "program-b",
    "fallback-ready",
    "replacements-reviewed",
    "replacements-reviewed-rework-open",
    "auto-below-threshold",
    "construction-planned",
    "caller-without-site-access",
}


def scenario(base: Snapshot, name: str) -> Snapshot:
    if name not in SCENARIOS:
        raise ValueError(f"Unknown synthetic scenario: {name}")
    snapshot = base.clone()
    spec = snapshot.recipe["scenario"]
    site_id = spec["mainSiteId"]
    timestamp = snapshot.recipe["scenarioTimestamp"]
    if name == "construction-planned":
        for milestone in snapshot.rows("Milestone"):
            if (
                milestone["siteId"] == site_id
                and milestone["milestoneType"] == "ConstructionComplete"
            ):
                milestone.update(status="Planned", actualDate=None)
    if name in {
        "replacements-reviewed",
        "replacements-reviewed-rework-open",
        "auto-below-threshold",
    }:
        for replacement in spec["replacements"]:
            old = snapshot.get("Photo", replacement["replacesPhotoId"])
            old["isCurrent"] = False
            photo = {
                **copy.deepcopy(old),
                "photoId": replacement["photoId"],
                "isCurrent": True,
                "replacesPhotoId": old["photoId"],
                "capturedAt": timestamp,
                "blobUrl": f"{snapshot.recipe['assetBaseUrl']}/photos/{replacement['photoId']}.png",
            }
            snapshot.add("Photo", photo)
            snapshot.add("ValidationResult", validation(snapshot, photo, [], timestamp))
        ticket = snapshot.get("Ticket", spec["ticketId"])
        pack = next(
            row
            for row in snapshot.rows("AcceptanceArtifact")
            if row["siteId"] == site_id and row["artifactType"] == "photo_pack"
        )
        site = snapshot.get("Site", site_id)
        closing = {
            "photoId": spec["closingPhotoId"],
            "artifactId": pack["artifactId"],
            "siteId": site_id,
            "checklistItemCode": ticket["checklistItemCode"],
            "sector": None,
            "blobUrl": f"{snapshot.recipe['assetBaseUrl']}/photos/{spec['closingPhotoId']}.png",
            "latitude": site["latitude"],
            "longitude": site["longitude"],
            "capturedAt": timestamp,
            "uploadedBy": snapshot.recipe["uploader"],
            "replacesPhotoId": None,
            "isCurrent": True,
        }
        snapshot.add("Photo", closing)
        snapshot.add("ValidationResult", validation(snapshot, closing, [], timestamp))
        ticket.update(status="Closed", closedAt=timestamp, closingPhotoId=closing["photoId"])
        installation = next(row for row in snapshot.rows("WorkOrder") if row["siteId"] == site_id)
        open_rework = name == "replacements-reviewed-rework-open"
        snapshot.add(
            "WorkOrder",
            {
                **copy.deepcopy(installation),
                "workOrderId": spec["reworkId"],
                "workOrderType": "Rework",
                "caseId": snapshot.case(site_id)["caseId"],
                "reason": "Synthetic evidence correction",
                "status": "InProgress" if open_rework else "Closed",
                "closedAt": None if open_rework else timestamp,
            },
        )
        if name == "auto-below-threshold":
            result = snapshot.get("ValidationResult", f"{spec['lowConfidencePhotoId']}-VALIDATION")
            result["confidence"] = spec["lowConfidence"]
    refresh_readiness(
        snapshot,
        timestamp
        if name
        not in {"initial-blocked", "program-b", "fallback-ready", "caller-without-site-access"}
        else snapshot.recipe["timestamp"],
    )
    return snapshot

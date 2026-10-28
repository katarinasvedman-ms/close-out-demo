"""Deterministic local Fabric/Activator stand-in, not the production readiness owner."""

from dataclasses import dataclass
from datetime import datetime

from shared.seed import Snapshot


def latest_validation(snapshot: Snapshot, photo_id: str) -> dict | None:
    results = [row for row in snapshot.rows("ValidationResult") if row.get("photoId") == photo_id]
    return latest_result(results)


def latest_result(results: list[dict]) -> dict | None:
    if not results:
        return None
    newest = max(
        datetime.fromisoformat(row["validatedAt"].replace("Z", "+00:00")) for row in results
    )
    latest = [
        row
        for row in results
        if datetime.fromisoformat(row["validatedAt"].replace("Z", "+00:00")) == newest
    ]
    decisions = {(row["compliant"], row["confidence"], row["reviewStatus"]) for row in latest}
    return latest[0] if len(decisions) == 1 else None


def accepted(snapshot: Snapshot, photo: dict, threshold: float) -> bool:
    result = latest_validation(snapshot, photo["photoId"])
    return bool(
        photo["isCurrent"]
        and result
        and result["compliant"]
        and (
            result["reviewStatus"] == "Confirmed"
            or (result["reviewStatus"] == "Auto" and result["confidence"] >= threshold)
        )
    )


@dataclass(frozen=True)
class Readiness:
    readiness: str
    missing: list[str]
    photo_gaps: list[dict]
    construction_complete: bool


def evaluate(snapshot: Snapshot, site_id: str) -> Readiness:
    site = snapshot.get("Site", site_id)
    case = snapshot.case(site_id)
    project = snapshot.get("CustomerProject", site["projectId"])
    artifacts = [row for row in snapshot.rows("AcceptanceArtifact") if row["siteId"] == site_id]
    missing, gaps = [], []
    for requirement in snapshot.rows("ArtifactRequirement"):
        if requirement["projectId"] != site["projectId"] or not requirement["mandatory"]:
            continue
        candidates = [
            row for row in artifacts if row["requirementId"] == requirement["requirementId"]
        ]
        if requirement["artifactType"] != "photo_pack":
            if not any(row["status"] == "Validated" for row in candidates):
                missing.append(requirement["requirementId"])
            continue
        packs = {row["artifactId"] for row in candidates}
        for item in snapshot.rows("ChecklistItem"):
            if item["requirementId"] != requirement["requirementId"] or not item["mandatory"]:
                continue
            photos = [
                row
                for row in snapshot.rows("Photo")
                if row["siteId"] == site_id
                and row["artifactId"] in packs
                and row["isCurrent"]
                and row["checklistItemCode"] == item["itemCode"]
            ]
            if not any(
                accepted(snapshot, photo, project["lowConfidenceThreshold"]) for photo in photos
            ):
                missing.append(item["checklistItemId"])
                gaps.append(
                    {
                        "checklistItemId": item["checklistItemId"],
                        "itemCode": item["itemCode"],
                        "photoIds": [photo["photoId"] for photo in photos],
                    }
                )
    missing.extend(
        row["ticketId"]
        for row in snapshot.rows("Ticket")
        if row["siteId"] == site_id and row["blocking"] and row["status"] != "Closed"
    )
    missing.extend(
        row["workOrderId"]
        for row in snapshot.rows("WorkOrder")
        if row["caseId"] == case["caseId"]
        and row["workOrderType"] == "Rework"
        and row["status"] != "Closed"
    )
    cc_done = any(
        row["siteId"] == site_id
        and row["milestoneType"] == "ConstructionComplete"
        and row["status"] == "Actualized"
        for row in snapshot.rows("Milestone")
    )
    return Readiness(
        "NotReady" if not cc_done else "Blocked" if missing else "Ready",
        sorted(set(missing)),
        gaps,
        cc_done,
    )


class ReadinessStub:
    def __init__(self):
        self._previous: dict[tuple[str, str], str] = {}

    def signal(self, snapshot: Snapshot, site_id: str, evaluated_at: str) -> dict | None:
        result = evaluate(snapshot, site_id)
        case_id = snapshot.case(site_id)["caseId"]
        key = (case_id, site_id)
        previous = self._previous.get(key)
        self._previous[key] = result.readiness
        if result.readiness == "NotReady" or previous == result.readiness:
            return None
        return {
            "caseId": case_id,
            "siteId": site_id,
            "readiness": result.readiness,
            "readinessMissing": result.missing,
            "evaluatedAt": evaluated_at,
        }

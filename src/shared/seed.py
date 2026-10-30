"""Expand the synthetic seed recipe; never load ingestion defects into mock systems."""

import copy
import json
from dataclasses import dataclass
from pathlib import Path

import yaml

from shared.contract_validation import load_validators, select_validator

KEYS = {
    "CustomerProject": "projectId",
    "ArtifactRequirement": "requirementId",
    "ChecklistItem": "checklistItemId",
    "Site": "siteId",
    "Milestone": "milestoneId",
    "Subcontractor": "subcontractorId",
    "WorkOrder": "workOrderId",
    "Ticket": "ticketId",
    "AcceptanceArtifact": "artifactId",
    "Photo": "photoId",
    "ValidationResult": "validationResultId",
    "CloseOutCase": "caseId",
    "DecisionLogEntry": "entryId",
    "EntityMatchCandidate": "candidateId",
}


@dataclass
class Snapshot:
    recipe: dict
    tables: dict[str, dict[str, dict]]

    def clone(self):
        return copy.deepcopy(self)

    def rows(self, kind: str) -> list[dict]:
        return list(self.tables[kind].values())

    def get(self, kind: str, identifier: str) -> dict:
        return self.tables[kind][identifier]

    def add(self, kind: str, row: dict) -> None:
        identifier = row[KEYS[kind]]
        if identifier in self.tables[kind]:
            raise ValueError(f"Duplicate {kind} ID: {identifier}")
        self.tables[kind][identifier] = copy.deepcopy(row)

    def export(self) -> dict:
        return {kind: self.rows(kind) for kind in KEYS}

    def case(self, site_id: str) -> dict:
        cases = [row for row in self.rows("CloseOutCase") if row["siteId"] == site_id]
        if len(cases) != 1:
            raise ValueError(f"Expected one synthetic active case for {site_id}")
        return cases[0]


def validation(snapshot: Snapshot, photo: dict, findings: list[str], timestamp: str) -> dict:
    artifact = snapshot.get("AcceptanceArtifact", photo["artifactId"])
    rfds = next(
        row
        for row in snapshot.rows("AcceptanceArtifact")
        if row["siteId"] == photo["siteId"] and row["artifactType"] == "rfds"
    )
    sector = photo["sector"]
    program = next(
        row
        for row in snapshot.recipe["programs"]
        if row["projectId"] == snapshot.get("Site", photo["siteId"])["projectId"]
    )
    return {
        "validationResultId": f"{photo['photoId']}-VALIDATION",
        "photoId": photo["photoId"],
        "checklistItemCode": photo["checklistItemCode"],
        "compliant": not findings,
        "confidence": snapshot.recipe["acceptedConfidence"],
        "findings": findings,
        "rfdsArtifactId": rfds["artifactId"] if sector else None,
        "rfdsReference": (
            f"Sector {sector} azimuth {program['rfdsAzimuth'][sector]} degrees" if sector else None
        ),
        "reviewStatus": "Auto",
        "validatedBy": snapshot.recipe["validator"],
        "model": snapshot.recipe["model"],
        "modelVersion": snapshot.recipe["modelVersion"],
        "validatedAt": timestamp,
        "caseId": artifact["caseId"],
    }


def load_seed(path: Path, contracts: Path) -> Snapshot:
    recipe = json.loads(path.read_text(encoding="utf-8"))
    from shared.contract_compatibility import version

    seed_version = version(recipe["schemaVersion"])
    current_version = version((contracts / "VERSION").read_text(encoding="utf-8").strip())
    if seed_version[0] != current_version[0] or seed_version > current_version:
        raise ValueError("Seed schemaVersion does not match the working contracts")
    snapshot = Snapshot(recipe, {kind: {} for kind in KEYS})
    timestamp = recipe["timestamp"]
    for program in recipe["programs"]:
        snapshot.add(
            "CustomerProject",
            {
                key: value
                for key, value in program.items()
                if key not in {"requirements", "checklist", "photoSlots", "rfdsAzimuth"}
            },
        )
        for requirement in program["requirements"]:
            snapshot.add(
                "ArtifactRequirement",
                {
                    **requirement,
                    "projectId": program["projectId"],
                    "phase": "close_out",
                },
            )
        photo_requirement = next(
            row for row in program["requirements"] if row["artifactType"] == "photo_pack"
        )
        for item in program["checklist"]:
            snapshot.add(
                "ChecklistItem",
                {
                    **item,
                    "requirementId": photo_requirement["requirementId"],
                    "mandatory": True,
                },
            )
    for subcontractor in recipe["subcontractors"]:
        snapshot.add("Subcontractor", subcontractor)
    findings = {row["photoId"]: row["findings"] for row in recipe["photoFindings"]}
    for source in recipe["sites"]:
        program = next(p for p in recipe["programs"] if p["projectId"] == source["projectId"])
        site_id, prefix, case_id = source["siteId"], source["prefix"], source["caseId"]
        snapshot.add(
            "Site",
            {
                **{
                    key: source[key]
                    for key in ("siteId", "siteName", "projectId", "latitude", "longitude")
                },
                "sourceSiteIds": [prefix],
                "market": program["market"],
                "siteType": "SyntheticTower",
                "currentPhase": "close_out",
                "readiness": "NotReady",
                "readinessMissing": [],
                "readinessEvaluatedAt": None,
            },
        )
        snapshot.add(
            "Milestone",
            {
                "milestoneId": f"{prefix}-CC",
                "siteId": site_id,
                "projectId": source["projectId"],
                "milestoneType": "ConstructionComplete",
                "status": "Actualized" if source["constructionComplete"] else "Planned",
                "plannedDate": timestamp,
                "actualDate": timestamp if source["constructionComplete"] else None,
                "sourceUpdatedAt": timestamp,
                "isStale": False,
            },
        )
        status = source["expectedReadiness"]
        snapshot.add(
            "CloseOutCase",
            {
                "caseId": case_id,
                "siteId": site_id,
                "status": status,
                "statusReason": {
                    "NotReady": "AwaitingConstructionComplete",
                    "Blocked": "MissingEvidence",
                    "Ready": "PackageCompiling",
                }[status],
                "bpfStage": "Evidence",
                "reworkDecision": None,
                "reworkPolicyVersion": None,
                "submissionDecision": None,
                "packageArtifactId": None,
                "createdAt": timestamp,
                "submittedAt": None,
                "acceptedAt": None,
                "slaDueAt": None,
            },
        )
        snapshot.add(
            "WorkOrder",
            {
                "workOrderId": f"{prefix}-INSTALL",
                "siteId": site_id,
                "subcontractorId": source["subcontractorId"],
                "workOrderType": "Installation",
                "status": "Closed",
                "caseId": None,
                "reason": "Synthetic installation complete",
                "createdAt": timestamp,
                "dueDate": timestamp,
                "closedAt": timestamp,
                "version": "1",
            },
        )
        for requirement in program["requirements"]:
            kind = requirement["artifactType"]
            snapshot.add(
                "AcceptanceArtifact",
                {
                    "artifactId": f"{prefix}-{kind}",
                    "siteId": site_id,
                    "requirementId": requirement["requirementId"],
                    "artifactType": kind,
                    "status": "Validated",
                    "blobUrl": f"{recipe['assetBaseUrl']}/documents/{prefix}-{kind}.pdf",
                    "version": "1",
                    "submittedBy": recipe["uploader"],
                    "submittedAt": timestamp,
                    "caseId": case_id,
                },
            )
        for number, item_code in enumerate(program["photoSlots"], 1):
            photo_id = f"{source['photoPrefix']}{number:02}"
            photo = {
                "photoId": photo_id,
                "artifactId": f"{prefix}-photo_pack",
                "siteId": site_id,
                "checklistItemCode": item_code,
                "sector": item_code[-1].upper()
                if item_code.startswith("azimuth_sector_")
                else None,
                "blobUrl": f"{recipe['assetBaseUrl']}/photos/{photo_id}.png",
                "latitude": source["latitude"],
                "longitude": source["longitude"],
                "capturedAt": timestamp,
                "uploadedBy": recipe["uploader"],
                "replacesPhotoId": None,
                "isCurrent": True,
            }
            snapshot.add("Photo", photo)
            snapshot.add(
                "ValidationResult",
                validation(snapshot, photo, findings.get(photo_id, []), timestamp),
            )
    for ticket in recipe["tickets"]:
        snapshot.add("Ticket", ticket)
    refresh_readiness(snapshot)
    validate_snapshot(snapshot, contracts)
    for site in recipe["sites"]:
        actual = snapshot.get("Site", site["siteId"])["readiness"]
        if actual != site["expectedReadiness"]:
            raise ValueError(f"Seed readiness mismatch for {site['siteId']}: {actual}")
    return snapshot


def refresh_readiness(snapshot: Snapshot, evaluated_at: str | None = None) -> None:
    from shared.readiness import evaluate

    for site in snapshot.rows("Site"):
        result = evaluate(snapshot, site["siteId"])
        site.update(
            readiness=result.readiness,
            readinessMissing=result.missing,
            readinessEvaluatedAt=evaluated_at or snapshot.recipe["timestamp"],
        )


def validate_snapshot(snapshot: Snapshot, contracts: Path) -> None:
    validators = load_validators(contracts)
    for kind in KEYS:
        validator = select_validator(validators, f"models.schema.json#/$defs/{kind}")
        for row in snapshot.rows(kind):
            validator.validate(row)
    for site in snapshot.rows("Site"):
        snapshot.get("CustomerProject", site["projectId"])
        snapshot.case(site["siteId"])
    for requirement in snapshot.rows("ArtifactRequirement"):
        snapshot.get("CustomerProject", requirement["projectId"])
    for item in snapshot.rows("ChecklistItem"):
        snapshot.get("ArtifactRequirement", item["requirementId"])
    for kind in ("Milestone", "Ticket", "WorkOrder", "CloseOutCase", "AcceptanceArtifact", "Photo"):
        for row in snapshot.rows(kind):
            site = snapshot.get("Site", row["siteId"])
            if "projectId" in row and row["projectId"] != site["projectId"]:
                raise ValueError(f"Cross-project {kind}")
            if (
                row.get("caseId")
                and snapshot.get("CloseOutCase", row["caseId"])["siteId"] != site["siteId"]
            ):
                raise ValueError(f"Cross-site {kind} case")
    for order in snapshot.rows("WorkOrder"):
        snapshot.get("Subcontractor", order["subcontractorId"])
    for artifact in snapshot.rows("AcceptanceArtifact"):
        if artifact["requirementId"]:
            requirement = snapshot.get("ArtifactRequirement", artifact["requirementId"])
            site = snapshot.get("Site", artifact["siteId"])
            if (
                requirement["projectId"] != site["projectId"]
                or requirement["artifactType"] != artifact["artifactType"]
            ):
                raise ValueError("Artifact requirement mismatch")
    for photo in snapshot.rows("Photo"):
        artifact = snapshot.get("AcceptanceArtifact", photo["artifactId"])
        if artifact["siteId"] != photo["siteId"] or artifact["artifactType"] != "photo_pack":
            raise ValueError("Cross-site or non-photo artifact")
        if not any(
            item["requirementId"] == artifact["requirementId"]
            and item["itemCode"] == photo["checklistItemCode"]
            for item in snapshot.rows("ChecklistItem")
        ):
            raise ValueError("Photo does not match its program checklist")
        if photo["replacesPhotoId"]:
            old = snapshot.get("Photo", photo["replacesPhotoId"])
            if (old["siteId"], old["checklistItemCode"]) != (
                photo["siteId"],
                photo["checklistItemCode"],
            ):
                raise ValueError("Invalid replacement target")
            if photo["isCurrent"] and old["isCurrent"]:
                raise ValueError("Replacement and predecessor cannot both be current")
    for result in snapshot.rows("ValidationResult"):
        kind, key = (
            ("Photo", "photoId") if "photoId" in result else ("AcceptanceArtifact", "artifactId")
        )
        target = snapshot.get(kind, result[key])
        if (
            result["caseId"]
            and snapshot.get("CloseOutCase", result["caseId"])["siteId"] != target["siteId"]
        ):
            raise ValueError("Cross-site validation")
        if result["rfdsArtifactId"]:
            rfds = snapshot.get("AcceptanceArtifact", result["rfdsArtifactId"])
            if rfds["siteId"] != target["siteId"] or rfds["artifactType"] != "rfds":
                raise ValueError("Invalid RFDS reference")
    for ticket in snapshot.rows("Ticket"):
        if ticket["closingPhotoId"]:
            photo = snapshot.get("Photo", ticket["closingPhotoId"])
            if photo["siteId"] != ticket["siteId"]:
                raise ValueError("Cross-site ticket evidence")


def fabric_extract(snapshot: Snapshot, defects: Path) -> dict:
    tables = snapshot.clone()
    operations = yaml.safe_load(defects.read_text(encoding="utf-8"))["operations"]
    for operation in operations:
        kind = operation["objectType"]
        row = copy.deepcopy(tables.get(kind, operation["id"]))
        row.update(operation["changes"])
        if operation.get("duplicate"):
            tables.add(kind, row)
        else:
            tables.tables[kind][operation["id"]] = row
    return tables.export()

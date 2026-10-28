"""Scoped deterministic facts for local agent development, not the live Fabric tool shape."""

import copy
from datetime import datetime
from pathlib import Path

from shared.contract_validation import load_validators, select_validator
from shared.readiness import evaluate, latest_validation
from shared.scenarios import scenario
from shared.seed import Snapshot


class DataAgentStub:
    def __init__(
        self,
        base: Snapshot,
        allowed_sites: set[str],
        validation_results: list[dict] | None = None,
        contracts: Path | None = None,
    ):
        self.base = base.clone()
        self.allowed_sites = frozenset(allowed_sites)
        unknown = self.allowed_sites - set(base.tables["Site"])
        if unknown:
            raise ValueError(f"Unknown allowed sites: {sorted(unknown)}")
        self.validation_results = copy.deepcopy(validation_results or [])
        if self.validation_results:
            if contracts is None:
                raise ValueError("Live validation overlays require contract validation")
            validator = select_validator(
                load_validators(contracts), "models.schema.json#/$defs/ValidationResult"
            )
            known = scenario(base, "replacements-reviewed")
            identifiers = set(known.tables["ValidationResult"])
            for result in self.validation_results:
                validator.validate(result)
                if "photoId" not in result:
                    raise ValueError("The local overlay supports photo validations only")
                photo = known.get("Photo", result["photoId"])
                if photo["siteId"] not in self.allowed_sites:
                    raise PermissionError("Validation overlay is outside the site allowlist")
                if (
                    result["caseId"] != known.case(photo["siteId"])["caseId"]
                    or result["checklistItemCode"] != photo["checklistItemCode"]
                    or result["reviewStatus"] not in {"Auto", "NeedsReview"}
                ):
                    raise ValueError("Validation overlay does not match the evidence/session")
                if result["validationResultId"] in identifiers:
                    raise ValueError("Validation overlay reuses an existing validation ID")
                identifiers.add(result["validationResultId"])
                existing = latest_validation(known, photo["photoId"])
                if existing and datetime.fromisoformat(
                    result["validatedAt"].replace("Z", "+00:00")
                ) <= datetime.fromisoformat(existing["validatedAt"].replace("Z", "+00:00")):
                    raise ValueError("Live validation predates the synthetic fixture history")
                if result["rfdsArtifactId"] is not None:
                    rfds = known.get("AcceptanceArtifact", result["rfdsArtifactId"])
                    if rfds["siteId"] != photo["siteId"] or rfds["artifactType"] != "rfds":
                        raise ValueError("Validation overlay cites an unrelated RFDS")

    def site_facts(self, site_id: str, scenario_name: str = "initial-blocked") -> dict:
        if site_id not in self.allowed_sites:
            raise PermissionError("Site is not in this local session's allowlist")
        snapshot = scenario(self.base, scenario_name)
        applied = []
        for result in self.validation_results:
            if result["photoId"] in snapshot.tables["Photo"]:
                snapshot.add("ValidationResult", result)
                applied.append(result)
        site = snapshot.get("Site", site_id)
        project = snapshot.get("CustomerProject", site["projectId"])
        result = evaluate(snapshot, site_id)
        if applied:
            newest = max(
                applied,
                key=lambda row: datetime.fromisoformat(row["validatedAt"].replace("Z", "+00:00")),
            )
            site.update(
                readiness=result.readiness,
                readinessMissing=result.missing,
                readinessEvaluatedAt=newest["validatedAt"],
            )
        requirements = [
            row
            for row in snapshot.rows("ArtifactRequirement")
            if row["projectId"] == project["projectId"]
        ]
        requirement_ids = {row["requirementId"] for row in requirements}
        photos = [row for row in snapshot.rows("Photo") if row["siteId"] == site_id]
        artifacts = [row for row in snapshot.rows("AcceptanceArtifact") if row["siteId"] == site_id]
        photo_ids, artifact_ids = (
            {row["photoId"] for row in photos},
            {row["artifactId"] for row in artifacts},
        )
        validations = [
            row
            for row in snapshot.rows("ValidationResult")
            if row.get("photoId") in photo_ids or row.get("artifactId") in artifact_ids
        ]
        evidence = []
        for gap in result.photo_gaps:
            for photo_id in gap["photoIds"]:
                evidence.append({"objectType": "Photo", "id": photo_id})
                validation = latest_validation(snapshot, photo_id)
                if validation:
                    evidence.append(
                        {"objectType": "ValidationResult", "id": validation["validationResultId"]}
                    )
        tickets = [row for row in snapshot.rows("Ticket") if row["siteId"] == site_id]
        evidence.extend(
            {"objectType": "Ticket", "id": row["ticketId"]}
            for row in tickets
            if row["ticketId"] in result.missing
        )
        return copy.deepcopy(
            {
                "mode": "stub",
                "validationSource": "live-agent-overlay" if self.validation_results else "seed",
                "scenario": scenario_name,
                "site": site,
                "caseId": snapshot.case(site_id)["caseId"],
                "project": project,
                "readiness": result.readiness,
                "readinessMissing": result.missing,
                "constructionComplete": result.construction_complete,
                "photoGaps": result.photo_gaps,
                "evidenceRefs": evidence,
                "requirements": requirements,
                "checklist": [
                    row
                    for row in snapshot.rows("ChecklistItem")
                    if row["requirementId"] in requirement_ids
                ],
                "photos": photos,
                "artifacts": artifacts,
                "validations": validations,
                "tickets": tickets,
                "workOrders": [
                    row for row in snapshot.rows("WorkOrder") if row["siteId"] == site_id
                ],
                "milestones": [
                    row for row in snapshot.rows("Milestone") if row["siteId"] == site_id
                ],
            }
        )

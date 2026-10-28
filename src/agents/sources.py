"""Scoped MCP/document access and deterministic checks after probabilistic reasoning."""

import copy
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agents.definitions import Assessments, FoundrySettings, GapDraft
from shared.contract_validation import load_validators, select_validator
from shared.readiness import latest_result
from shared.seed import KEYS
from shared.seed_files import asset_path

FACT_TABLES = {
    "ArtifactRequirement": "requirements",
    "ChecklistItem": "checklist",
    "Photo": "photos",
    "AcceptanceArtifact": "artifacts",
    "ValidationResult": "validations",
    "Ticket": "tickets",
    "WorkOrder": "workOrders",
    "Milestone": "milestones",
}


async def mcp_facts(
    site_id: str, scenario: str, seed: Path, contracts: Path, validation_results: Path | None = None
) -> dict:
    args = [
        "-m",
        "shared.stub_mcp",
        "--site",
        site_id,
        "--seed",
        str(seed.resolve()),
        "--contracts",
        str(contracts.resolve()),
    ]
    if validation_results:
        args.extend(["--validation-results", str(validation_results.resolve())])
    async with (
        stdio_client(StdioServerParameters(command=sys.executable, args=args)) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.call_tool(
            "get_site_facts", {"site_id": site_id, "scenario": scenario}
        )
        if result.isError:
            raise RuntimeError("The scoped Data Agent MCP call failed")
        facts = result.structuredContent
        if facts is None:
            facts = json.loads(result.content[0].text)
    if facts["site"]["siteId"] != site_id or facts["mode"] != "stub":
        raise ValueError("Unexpected MCP site or dependency mode")
    return facts


class Sources:
    def __init__(self, facts: dict, assets: Path, contracts: Path):
        self.facts = copy.deepcopy(facts)
        self.assets = assets
        self.validators = load_validators(contracts)
        self.records = {
            (kind, row[KEYS[kind]]): row
            for kind, field in FACT_TABLES.items()
            for row in facts[field]
        }
        self.manifest = json.loads((assets / "assets-manifest.json").read_text(encoding="utf-8"))

    def read_asset(self, row: dict, *, text: bool = False) -> tuple[bytes, dict]:
        target = asset_path(self.assets, row["blobUrl"])
        if text:
            target = target.with_suffix(".txt")
        relative = target.relative_to(self.assets.resolve()).as_posix()
        identifier = row.get("photoId", row.get("artifactId"))
        declared = next(
            (
                entry
                for entry in self.manifest
                if entry["path"] == relative and entry["id"] == identifier
            ),
            None,
        )
        if declared is None:
            raise ValueError("Evidence is not in the synthetic asset manifest")
        data = target.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != declared["sha256"]:
            raise ValueError("Evidence hash does not match the generated asset manifest")
        return data, {"id": identifier, "path": relative, "sha256": digest}

    def documents(self) -> list[dict]:
        return [
            {
                "artifactId": row["artifactId"],
                "version": row["version"],
                "text": self.read_asset(row, text=True)[0].decode("utf-8"),
            }
            for row in self.facts["artifacts"]
            if row["artifactType"] != "photo_pack"
        ]

    def validation_inputs(self, evidence_ids: list[str]) -> tuple[list[dict], list[tuple]]:
        if not evidence_ids or len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("Request distinct evidence IDs")
        rfds = next(
            (
                row
                for row in self.documents()
                if row["artifactId"]
                in {a["artifactId"] for a in self.facts["artifacts"] if a["artifactType"] == "rfds"}
            ),
            None,
        )
        packets, attachments = [], []
        for identifier in evidence_ids:
            photo = self.records.get(("Photo", identifier))
            artifact = self.records.get(("AcceptanceArtifact", identifier))
            row = photo or artifact
            if row is None:
                raise PermissionError("Evidence is outside the fixed site scope")
            packet = {
                "evidenceId": identifier,
                "kind": "photo" if photo else "document",
                "metadata": row,
                "checklist": (
                    [
                        item
                        for item in self.facts["checklist"]
                        if item["itemCode"] == photo["checklistItemCode"]
                    ]
                    if photo
                    else []
                ),
                "rfds": rfds if photo and photo["sector"] else None,
            }
            data, receipt = self.read_asset(row)
            attachments.append(
                (identifier, data, "image/png" if photo else "application/pdf", receipt)
            )
            packets.append(packet)
        return packets, attachments

    def validation_results(
        self, draft: Assessments, packets: list[dict], settings: FoundrySettings
    ) -> list[dict]:
        assessments = {row.evidenceId: row for row in draft.assessments}
        if len(assessments) != len(draft.assessments) or set(assessments) != {
            row["evidenceId"] for row in packets
        }:
            raise ValueError("The agent must assess each requested evidence ID exactly once")
        results = []
        for packet in packets:
            assessment = assessments[packet["evidenceId"]]
            row = packet["metadata"]
            findings = assessment.findings
            if not findings or any(not item.strip() for item in findings):
                raise ValueError("An assessment must explain its observed findings")
            rfds = packet["rfds"]
            quote = assessment.rfdsReference
            if rfds:
                if not quote or quote not in rfds["text"].splitlines() or "Sector " not in quote:
                    raise ValueError("RFDS citation must quote a supplied sector line")
                if not quote.startswith(f"Sector {row['sector']} "):
                    raise ValueError("RFDS citation is for the wrong sector")
            elif quote is not None:
                raise ValueError("RFDS cannot be cited without a supplied source")
            result = {
                "validationResultId": f"AI-{uuid4()}",
                "photoId" if packet["kind"] == "photo" else "artifactId": packet["evidenceId"],
                "checklistItemCode": row.get("checklistItemCode"),
                "compliant": assessment.compliant,
                "confidence": assessment.confidence,
                "findings": findings,
                "rfdsArtifactId": rfds["artifactId"] if rfds else None,
                "rfdsReference": quote,
                "reviewStatus": (
                    "Auto"
                    if assessment.confidence >= self.facts["project"]["lowConfidenceThreshold"]
                    else "NeedsReview"
                ),
                "validatedBy": settings.validation.name,
                "model": settings.model,
                "modelVersion": settings.modelVersion,
                "validatedAt": datetime.now(UTC).isoformat(),
                "caseId": self.facts["caseId"],
            }
            select_validator(
                self.validators, "models.schema.json#/$defs/ValidationResult"
            ).validate(result)
            results.append(result)
        return results

    def gap_analysis(self, draft: GapDraft, settings: FoundrySettings) -> dict:
        expected = set(self.facts["readinessMissing"])
        if {gap.conditionId for gap in draft.gaps} != expected or len(draft.gaps) != len(expected):
            raise ValueError("Gap IDs must exactly match the supplied readiness conditions")
        photo_gaps = {row["checklistItemId"]: row for row in self.facts["photoGaps"]}
        permitted_validations = set()
        for gap in draft.gaps:
            if (gap.conditionType, gap.conditionId) not in self.records:
                raise ValueError("Gap condition type/ID does not resolve in authorized facts")
            references = {(ref.objectType, ref.id) for ref in gap.evidenceRefs}
            for reference in gap.evidenceRefs:
                source = self.records.get((reference.objectType, reference.id))
                if source is None:
                    raise ValueError("Citation is outside the authorized source set")
                if reference.version != source.get("version"):
                    raise ValueError("Citation version is not the supplied source version")
            if gap.conditionId in photo_gaps:
                for photo_id in photo_gaps[gap.conditionId]["photoIds"]:
                    if ("Photo", photo_id) not in references:
                        raise ValueError("A photo gap must cite its current unaccepted photo")
                    candidates = [
                        v for v in self.facts["validations"] if v.get("photoId") == photo_id
                    ]
                    if candidates:
                        latest = latest_result(candidates)
                        if latest is None:
                            raise ValueError("Ambiguous latest validation requires review")
                        validation_id = latest["validationResultId"]
                        if ("ValidationResult", validation_id) not in references:
                            raise ValueError("A photo gap must cite its latest validation result")
                        permitted_validations.add(validation_id)
                if (
                    not photo_gaps[gap.conditionId]["photoIds"]
                    and (gap.conditionType, gap.conditionId) not in references
                ):
                    raise ValueError("Missing-photo evidence must cite the checklist item")
            elif (gap.conditionType, gap.conditionId) not in references:
                raise ValueError("A non-photo gap must cite its condition record")
        proposal = draft.proposedRework
        tickets = {gap.conditionId for gap in draft.gaps if gap.conditionType == "Ticket"}
        if not expected and proposal is not None:
            raise ValueError("A case without gaps cannot receive a rework proposal")
        if (permitted_validations or tickets) and proposal is None:
            raise ValueError("Photo/ticket gaps require a proposed rework draft")
        if proposal:
            if (
                set(proposal.validationResultIds) != permitted_validations
                or set(proposal.ticketIds) != tickets
                or not (permitted_validations or tickets)
            ):
                raise ValueError("Rework must reference exactly the relevant results and tickets")
        result = {
            "caseId": self.facts["caseId"],
            "siteId": self.facts["site"]["siteId"],
            "generatedAt": datetime.now(UTC).isoformat(),
            "model": settings.model,
            "modelVersion": settings.modelVersion,
            **draft.model_dump(exclude_none=True),
            "proposedRework": proposal.model_dump() if proposal else None,
        }
        select_validator(self.validators, "agents/outputs.schema.json#/$defs/GapAnalysis").validate(
            result
        )
        return result

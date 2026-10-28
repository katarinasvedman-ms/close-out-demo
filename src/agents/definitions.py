"""Versioned prompt-agent definitions and strict internal model-output boundaries."""

import hashlib
import json
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Assessment(StrictOutput):
    evidenceId: str = Field(min_length=1)
    compliant: bool
    confidence: float = Field(ge=0, le=1)
    findings: list[str]
    rfdsReference: str | None


class Assessments(StrictOutput):
    assessments: list[Assessment]


class Citation(StrictOutput):
    objectType: Literal[
        "Photo",
        "AcceptanceArtifact",
        "ValidationResult",
        "Ticket",
        "WorkOrder",
        "Milestone",
        "ChecklistItem",
        "ArtifactRequirement",
    ]
    id: str = Field(min_length=1)
    version: str | None


class Gap(StrictOutput):
    conditionType: Literal["ChecklistItem", "ArtifactRequirement", "Ticket", "WorkOrder"]
    conditionId: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    evidenceRefs: list[Citation] = Field(min_length=1)


class Rework(StrictOutput):
    reason: str = Field(min_length=1)
    validationResultIds: list[str]
    ticketIds: list[str]


class GapDraft(StrictOutput):
    gaps: list[Gap]
    proposedRework: Rework | None


class AgentBinding(StrictOutput):
    name: str
    version: str
    definitionHash: str


class FoundrySettings(StrictOutput):
    endpoint: str
    deployment: str
    model: str
    modelVersion: str
    validation: AgentBinding
    closeout: AgentBinding

    @field_validator("endpoint")
    @classmethod
    def project_endpoint(cls, value: str) -> str:
        url = urlsplit(value)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or not url.path.startswith("/api/projects/")
            or not url.path.removeprefix("/api/projects/").strip("/")
        ):
            raise ValueError("An HTTPS Foundry project endpoint is required")
        return value.rstrip("/")


TOOL_DESCRIPTIONS = {
    "get_site_facts": "Read the fixed session site's facts from the local Data Agent MCP stub.",
    "get_documents": "Read only the fixed site's versioned local synthetic source documents.",
}
NO_ARGUMENTS = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}


def definition(kind: str, deployment: str) -> dict:
    if kind not in {"validation", "closeout"}:
        raise ValueError(f"Unknown agent kind: {kind}")
    output = Assessments if kind == "validation" else GapDraft
    return {
        "kind": "prompt",
        "model": deployment,
        "instructions": (Path(__file__).parent / "prompts" / f"{kind}.txt").read_text(
            encoding="utf-8"
        ),
        "temperature": 0.1,
        "tools": (
            [
                {
                    "type": "function",
                    "name": name,
                    "description": description,
                    "parameters": NO_ARGUMENTS,
                    "strict": True,
                }
                for name, description in TOOL_DESCRIPTIONS.items()
            ]
            if kind == "closeout"
            else []
        ),
        "text": {
            "format": {
                "type": "json_schema",
                "name": output.__name__,
                "schema": output.model_json_schema(),
                "strict": True,
            }
        },
    }


def definition_hash(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

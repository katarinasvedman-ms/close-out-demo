"""Versioned prompt-agent definitions and strict internal model-output boundaries."""

import hashlib
import json
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Assessment(StrictOutput):
    evidenceId: str = Field(min_length=1)
    compliant: bool
    confidence: float = Field(ge=0, le=1)
    findings: list[str]
    rfdsReference: str | None
    observedAzimuthDegrees: float | None = Field(ge=0, le=360)


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


class KnowledgeSettings(StrictOutput):
    searchEndpoint: str
    connectionName: str = Field(min_length=1)
    knowledgeBase: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,126}$")
    sites: list[str] = Field(min_length=1)
    blobRoot: str

    @field_validator("blobRoot")
    @classmethod
    def blob_root(cls, value: str) -> str:
        url = urlsplit(value)
        if (
            url.scheme != "https"
            or not url.hostname
            or not url.hostname.endswith(".blob.core.windows.net")
            or url.username
            or url.password
            or url.port
            or url.query
            or url.fragment
            or not url.path.endswith("/iq/")
        ):
            raise ValueError("An Azure Blob HTTPS document prefix ending in /iq/ is required")
        return value

    def mcp_url(self) -> str:
        return (
            f"{self.searchEndpoint}/knowledgebases/{self.knowledgeBase}"
            "/mcp?api-version=2026-08-01-preview"
        )

    @field_validator("searchEndpoint")
    @classmethod
    def search_endpoint(cls, value: str) -> str:
        url = urlsplit(value)
        if (
            url.scheme != "https"
            or not url.hostname
            or not url.hostname.endswith(".search.windows.net")
            or url.username
            or url.password
            or url.port
            or url.path.strip("/")
            or url.query
            or url.fragment
        ):
            raise ValueError("An Azure AI Search HTTPS service endpoint is required")
        return value.rstrip("/")


class FabricSettings(StrictOutput):
    workspaceId: str
    dataAgentId: str
    databaseId: str
    kustoEndpoint: str
    connectionName: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    toolName: str = Field(pattern=r"^DataAgent_[a-zA-Z0-9_-]+$")

    @field_validator("workspaceId", "dataAgentId", "databaseId")
    @classmethod
    def identifier(cls, value: str) -> str:
        return str(UUID(value))

    @field_validator("kustoEndpoint")
    @classmethod
    def kusto_endpoint(cls, value: str) -> str:
        url = urlsplit(value)
        if (
            url.scheme != "https"
            or not url.hostname
            or not url.hostname.endswith(".kusto.fabric.microsoft.com")
            or url.username
            or url.password
            or url.port not in (None, 443)
            or url.path.strip("/")
            or url.query
            or url.fragment
        ):
            raise ValueError("An HTTPS Fabric Eventhouse KQL endpoint is required")
        return value.rstrip("/")

    def mcp_url(self) -> str:
        return (
            f"https://api.fabric.microsoft.com/v1/mcp/workspaces/{self.workspaceId}"
            f"/dataagents/{self.dataAgentId}/agent"
        )


class PublisherSettings(StrictOutput):
    serverUrl: str
    audience: str = Field(min_length=1)

    @field_validator("serverUrl")
    @classmethod
    def publisher_endpoint(cls, value: str) -> str:
        url = urlsplit(value)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
        ):
            raise ValueError("An HTTPS publisher origin without credentials or a path is required")
        return value.rstrip("/")


class FoundrySettings(StrictOutput):
    endpoint: str
    deployment: str
    model: str
    modelVersion: str
    validation: AgentBinding
    closeout: AgentBinding
    knowledge: KnowledgeSettings | None = None
    fabric: FabricSettings | None = None
    publisher: PublisherSettings | None = None

    @model_validator(mode="after")
    def fabric_requires_knowledge(self):
        if self.fabric and not self.knowledge:
            raise ValueError("Native Fabric testing requires the document IQ binding")
        return self

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


def definition(
    kind: str,
    deployment: str,
    knowledge: KnowledgeSettings | None = None,
    fabric: FabricSettings | None = None,
    publisher: PublisherSettings | None = None,
) -> dict:
    if kind not in {"validation", "closeout"}:
        raise ValueError(f"Unknown agent kind: {kind}")
    output = Assessments if kind == "validation" else GapDraft
    body = {
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
    if kind == "closeout" and knowledge:
        body["instructions"] = (Path(__file__).parent / "prompts" / "closeout-iq.txt").read_text(
            encoding="utf-8"
        )
        body["tools"] = [tool for tool in body["tools"] if tool["name"] == "get_site_facts"] + [
            {
                "type": "mcp",
                "server_label": "documents",
                "server_url": knowledge.mcp_url(),
                "project_connection_id": knowledge.connectionName,
                "require_approval": "never",
                "allowed_tools": ["knowledge_base_retrieve"],
            }
        ]
    if kind == "closeout" and fabric:
        if not knowledge:
            raise ValueError("Native Fabric testing requires the document IQ binding")
        body["instructions"] = (
            Path(__file__).parent / "prompts" / "closeout-fabric.txt"
        ).read_text(encoding="utf-8")
        body["tools"] = [tool for tool in body["tools"] if tool["type"] != "function"] + [
            {
                "type": "fabric_iq_preview",
                "server_label": "fabric-facts",
                "server_url": fabric.mcp_url(),
                "project_connection_id": fabric.connectionName,
                "require_approval": "never",
            }
        ]
    if kind == "validation" and publisher:
        from agents.flow_publish import openapi_document

        body["instructions"] += "\n\n" + (
            Path(__file__).parent / "prompts" / "validation-publication.txt"
        ).read_text(encoding="utf-8")
        body["tools"] = [
            {
                "type": "openapi",
                "openapi": {
                    "name": "trigger_fabric_eventstream",
                    "description": "Publish one assessment against registered synthetic evidence.",
                    "spec": openapi_document(publisher.serverUrl),
                    "auth": {
                        "type": "managed_identity",
                        "security_scheme": {"audience": publisher.audience},
                    },
                },
            }
        ]
    return body


def definition_hash(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

"""Register service-managed agents and invoke their pinned versions with Agent Framework."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from agents.definitions import (
    NO_ARGUMENTS,
    TOOL_DESCRIPTIONS,
    AgentBinding,
    Assessments,
    FabricSettings,
    FoundrySettings,
    GapDraft,
    KnowledgeSettings,
    PublisherSettings,
    definition,
    definition_hash,
)
from agents.sources import Sources
from shared.local_demo import write_json


def final_response_text(response) -> str:
    for message in reversed(response.messages):
        if message.role == "assistant" and message.text:
            return message.text
    raise ValueError("Foundry did not return a final assistant response")


def definition_matches(expected, actual) -> bool:
    if isinstance(expected, dict):
        if expected.get("type") == "mcp" and isinstance(actual, dict):
            # Foundry expands the MCP tool-name shorthand on read-back.
            if isinstance(expected.get("allowed_tools"), list) and isinstance(
                actual.get("allowed_tools"), dict
            ):
                actual = {**actual, "allowed_tools": actual["allowed_tools"].get("tool_names")}
        return isinstance(actual, dict) and all(
            key in actual and definition_matches(value, actual[key])
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(expected) == len(actual)
            and all(definition_matches(a, b) for a, b in zip(expected, actual, strict=True))
        )
    return expected == actual


def register(
    endpoint: str,
    deployment: str,
    model: str,
    model_version: str,
    output: Path,
    validation_name: str,
    closeout_name: str,
    knowledge: KnowledgeSettings | None = None,
    fabric: FabricSettings | None = None,
    publisher: PublisherSettings | None = None,
) -> FoundrySettings:
    if fabric and not knowledge:
        raise ValueError("Native Fabric testing requires the document IQ binding")
    from azure.ai.projects import AIProjectClient
    from azure.ai.projects.models import PromptAgentDefinition
    from azure.core.exceptions import ResourceNotFoundError
    from azure.identity import AzureCliCredential

    bindings = {}
    with (
        AzureCliCredential() as credential,
        AIProjectClient(endpoint=endpoint, credential=credential) as client,
    ):
        for kind, name in (("validation", validation_name), ("closeout", closeout_name)):
            body = definition(kind, deployment, knowledge, fabric, publisher)
            source = (
                "Foundry IQ + external Fabric test fixtures"
                if fabric and kind == "closeout"
                else "Foundry IQ + stub facts"
                if knowledge and kind == "closeout"
                else "synthetic local sources"
            )
            digest = definition_hash(body)
            try:
                versions = list(client.agents.list_versions(agent_name=name))
            except ResourceNotFoundError:
                versions = []
            if any(row.metadata.get("source") != "close-out-demo" for row in versions):
                raise ValueError(f"Refusing to modify an unrelated existing agent: {name}")
            matching = next(
                (
                    row
                    for row in versions
                    if row.metadata.get("definition_sha256") == digest
                    and row.metadata.get("model_version") == model_version
                ),
                None,
            )
            if matching:
                candidate = client.agents.get_version(
                    agent_name=name, agent_version=matching.version
                )
                if not definition_matches(body, candidate.definition.as_dict()):
                    matching = None
            created = matching or client.agents.create_version(
                agent_name=name,
                definition=PromptAgentDefinition(body),
                description=(
                    f"Close-out {kind}; registered synthetic assessment publication."
                    if kind == "validation" and publisher
                    else f"Close-out {kind}; {source}; no write tools."
                ),
                metadata={
                    "source": "close-out-demo",
                    "contract_version": "0.1.0",
                    "definition_sha256": digest,
                    "model_version": model_version,
                },
            )
            verified = client.agents.get_version(agent_name=name, agent_version=created.version)
            if verified.status != "active" or not definition_matches(
                body, verified.definition.as_dict()
            ):
                raise RuntimeError(
                    f"Agent version is not active with the expected definition: {name}"
                )
            bindings[kind] = AgentBinding(name=name, version=created.version, definitionHash=digest)
    settings = FoundrySettings(
        endpoint=endpoint,
        deployment=deployment,
        model=model,
        modelVersion=model_version,
        knowledge=knowledge,
        fabric=fabric,
        publisher=publisher,
        **bindings,
    )
    write_json(output, settings.model_dump())
    return settings


async def invoke(
    kind: str,
    settings: FoundrySettings,
    sources: Sources,
    evidence_ids: list[str] | None = None,
    *,
    managed_identity: bool = False,
) -> dict:
    from agent_framework import Content, Message, tool
    from agent_framework.foundry import FoundryAgent
    from azure.ai.projects.aio import AIProjectClient
    from azure.identity.aio import (
        AzureCliCredential,
        DefaultAzureCredential,
        ManagedIdentityCredential,
    )

    use_knowledge = kind == "closeout" and settings.knowledge is not None
    use_fabric = kind == "closeout" and settings.fabric is not None
    if use_fabric and sources.facts.get("mode") not in {"fabric-test", "fabric-live-test"}:
        raise ValueError("Native Fabric testing cannot use local stub facts")
    if use_knowledge and sources.facts["site"]["siteId"] not in settings.knowledge.sites:
        raise PermissionError("Site is not configured for the shared synthetic knowledge demo")
    binding = settings.validation if kind == "validation" else settings.closeout
    expected_hash = definition_hash(
        definition(
            kind, settings.deployment, settings.knowledge, settings.fabric, settings.publisher
        )
    )
    if binding.definitionHash != expected_hash:
        raise ValueError("Local agent definition changed; register a new version before running")
    calls = []

    @tool(
        name="get_site_facts",
        description=TOOL_DESCRIPTIONS["get_site_facts"],
        schema=NO_ARGUMENTS,
    )
    def get_site_facts() -> str:
        calls.append("get_site_facts")
        return json.dumps(sources.facts)

    @tool(
        name="get_documents",
        description=TOOL_DESCRIPTIONS["get_documents"],
        schema=NO_ARGUMENTS,
    )
    def get_documents() -> str:
        calls.append("get_documents")
        return json.dumps(sources.documents())

    packets, attachments = [], []
    if kind == "validation":
        packets, attachments = sources.validation_inputs(evidence_ids or [])
        contents = [Content.from_text(json.dumps({"evidence": packets}))]
        for identifier, data, media_type, receipt in attachments:
            contents.extend(
                [
                    Content.from_text(f"Attachment for evidenceId={identifier}"),
                    Content.from_data(data, media_type, name=Path(receipt["path"]).name),
                ]
            )
        messages = Message("user", contents)
    else:
        messages = Message(
            "user",
            [
                f"Draft the gap analysis for site {sources.facts['site']['siteId']}, "
                f"case {sources.facts['caseId']}. "
                + (
                    f"Use program {sources.facts['project']['programCode']}, exact test "
                    f"runId {sources.facts['fabricSource']['runId']} and factsRevision "
                    f"{sources.facts['fabricSource']['factsRevision']}. "
                    if use_fabric and sources.facts["mode"] == "fabric-live-test"
                    else f"Use program {sources.facts['project']['programCode']} and exact "
                    f"scenario {sources.facts['scenario']}. "
                    if use_fabric
                    else ""
                )
                + "Obtain both tools' sources before answering."
            ],
        )
    async with (
        (
            ManagedIdentityCredential()
            if managed_identity
            else AzureCliCredential()
            if use_fabric
            else DefaultAzureCredential()
        ) as credential,
        AIProjectClient(endpoint=settings.endpoint, credential=credential) as project,
        FoundryAgent(
            project_client=project,
            agent_name=binding.name,
            agent_version=binding.version,
            credential=credential,
            tools=(
                None
                if use_fabric
                else [get_site_facts]
                if use_knowledge
                else [get_site_facts, get_documents]
                if kind == "closeout"
                else None
            ),
            timeout=180,
            function_invocation_configuration={
                "max_iterations": 4,
                "max_function_calls": 4,
                "max_duration_seconds": 180,
                "terminate_on_unknown_calls": True,
                "include_detailed_errors": False,
            },
        ) as agent,
    ):
        remote = await project.agents.get_version(
            agent_name=binding.name, agent_version=binding.version
        )
        if (
            remote.metadata.get("definition_sha256") != expected_hash
            or remote.metadata.get("model_version") != settings.modelVersion
            or remote.definition.model != settings.deployment
            or remote.status != "active"
        ):
            raise ValueError("Pinned remote version does not match the local definition")
        options = {"max_output_tokens": 4000}
        response = await asyncio.wait_for(
            agent.run(messages, options=options), timeout=180 if managed_identity else 300
        )
    if not response.response_id:
        raise ValueError("Foundry did not return a response ID")
    text = final_response_text(response)
    if kind == "validation":
        result = sources.validation_results(
            Assessments.model_validate_json(text), packets, settings
        )
    else:
        required_calls = (
            set() if use_fabric else {"get_site_facts"} if use_knowledge else set(TOOL_DESCRIPTIONS)
        )
        if set(calls) != required_calls:
            raise ValueError("The coordinator did not retrieve both required source sets")
        draft = GapDraft.model_validate_json(text)
        if use_fabric:
            from agents.fabric import retrieval_receipts as fabric_retrieval_receipts

            fabric_receipts = fabric_retrieval_receipts(response, sources, settings.fabric)
            calls.append(settings.fabric.toolName)
        if use_knowledge:
            from agents.knowledge import retrieval_receipts, validate_document_citations

            knowledge_receipts = retrieval_receipts(response, sources, settings.knowledge)
            validate_document_citations(draft, sources, knowledge_receipts)
            calls.append("knowledge_base_retrieve")
        result = sources.gap_analysis(draft, settings)
    return {
        "correlationId": uuid4().hex,
        "caseId": sources.facts["caseId"],
        "siteId": sources.facts["site"]["siteId"],
        "completedAt": datetime.now(UTC).isoformat(),
        "agent": binding.model_dump(),
        "model": settings.model,
        "modelVersion": settings.modelVersion,
        "deployment": settings.deployment,
        "responseId": response.response_id,
        "usage": dict(response.usage_details or {}),
        "modes": {
            "agent": "live-foundry-prompt-agent",
            "inference": "live",
            "facts": (
                "native-fabric-iq-synthetic-live-test"
                if use_fabric and sources.facts["mode"] == "fabric-live-test"
                else "native-fabric-iq-synthetic-fixtures"
                if use_fabric
                else "registered-blob-validation-context"
                if sources.facts.get("mode") == "registered-blob-evidence"
                else "local-mcp-stub"
            ),
            "invocationIdentity": "managed-identity" if managed_identity else "local-client",
            **(
                {
                    "factsVerification": "direct-fabric-kql-snapshot-not-agent-input",
                    "validationSource": sources.facts["validationSource"],
                    "fabricAuthorization": (
                        "existing-project-connection-managed-identity-invocation"
                        if managed_identity
                        else "signed-in-user-existing-permissions"
                    ),
                }
                if use_fabric
                else {}
            ),
            "documents": "foundry-iq-native-blob-synthetic"
            if use_knowledge
            else "registered-blob-synthetic-files"
            if sources.facts.get("mode") == "registered-blob-evidence"
            else "local-synthetic-files",
            **({"knowledgeScope": "shared-operator-demo-no-user-acl"} if use_knowledge else {}),
            "dataverse": "not-connected",
            "contentUnderstanding": "not-connected",
            "foundryIq": "live" if use_knowledge else "not-connected",
        },
        "toolCalls": calls,
        "inputs": [row[3] for row in attachments],
        "result": result,
        **({"knowledgeRetrieval": knowledge_receipts} if use_knowledge else {}),
        **({"fabricRetrieval": fabric_receipts} if use_fabric else {}),
    }

"""Register service-managed agents and invoke their pinned versions with Agent Framework."""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from agents.definitions import (
    NO_ARGUMENTS,
    TOOL_DESCRIPTIONS,
    AgentBinding,
    Assessments,
    FoundrySettings,
    GapDraft,
    KnowledgeSettings,
    definition,
    definition_hash,
)
from agents.sources import Sources
from shared.local_demo import write_json


def register(
    endpoint: str,
    deployment: str,
    model: str,
    model_version: str,
    output: Path,
    validation_name: str,
    closeout_name: str,
    knowledge: KnowledgeSettings | None = None,
) -> FoundrySettings:
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
            body = definition(kind, deployment, knowledge)
            source = (
                "Foundry IQ + stub facts"
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
            created = matching or client.agents.create_version(
                agent_name=name,
                definition=PromptAgentDefinition(body),
                description=f"Close-out {kind}; {source}; no write tools.",
                metadata={
                    "source": "close-out-demo",
                    "contract_version": "0.1.0",
                    "definition_sha256": digest,
                    "model_version": model_version,
                },
            )
            verified = client.agents.get_version(agent_name=name, agent_version=created.version)
            if verified.status != "active" or verified.definition.model != deployment:
                raise RuntimeError(f"Agent version is not active on the expected model: {name}")
            bindings[kind] = AgentBinding(name=name, version=created.version, definitionHash=digest)
    settings = FoundrySettings(
        endpoint=endpoint,
        deployment=deployment,
        model=model,
        modelVersion=model_version,
        knowledge=knowledge,
        **bindings,
    )
    write_json(output, settings.model_dump())
    return settings


async def invoke(
    kind: str, settings: FoundrySettings, sources: Sources, evidence_ids: list[str] | None = None
) -> dict:
    from agent_framework import Content, Message, tool
    from agent_framework.foundry import FoundryAgent
    from azure.ai.projects.aio import AIProjectClient
    from azure.identity.aio import DefaultAzureCredential

    use_knowledge = kind == "closeout" and settings.knowledge is not None
    if use_knowledge and sources.facts["site"]["siteId"] not in settings.knowledge.sites:
        raise PermissionError("Site is not configured for the shared synthetic knowledge demo")
    binding = settings.validation if kind == "validation" else settings.closeout
    expected_hash = definition_hash(definition(kind, settings.deployment, settings.knowledge))
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
                f"case {sources.facts['caseId']}. Obtain both tools' sources before answering."
            ],
        )
    async with (
        DefaultAzureCredential() as credential,
        AIProjectClient(endpoint=settings.endpoint, credential=credential) as project,
        FoundryAgent(
            project_client=project,
            agent_name=binding.name,
            agent_version=binding.version,
            credential=credential,
            tools=(
                [get_site_facts]
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
        response = await agent.run(messages, options=options)
    if not response.response_id:
        raise ValueError("Foundry did not return a response ID")
    if kind == "validation":
        result = sources.validation_results(
            Assessments.model_validate_json(response.text), packets, settings
        )
    else:
        required_calls = {"get_site_facts"} if use_knowledge else set(TOOL_DESCRIPTIONS)
        if set(calls) != required_calls:
            raise ValueError("The coordinator did not retrieve both required source sets")
        draft = GapDraft.model_validate_json(response.text)
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
            "facts": "local-mcp-stub",
            "documents": "foundry-iq-native-blob-synthetic"
            if use_knowledge
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
    }

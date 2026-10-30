"""Publish a separate read-only workbench prompt agent using existing native IQ bindings."""

import argparse
import copy
import json
import re
from pathlib import Path

import httpx
from pydantic import Field

from agents.definitions import AgentBinding, StrictOutput, definition_hash
from agents.fabric_connection import API_VERSION
from agents.foundry import definition_matches
from shared.local_demo import write_json

DELEGATED_AUDIENCE = "https://analysis.windows.net/powerbi/api"


def reconcile_delegated_connection(
    client: httpx.Client, source_id: str, name: str, target: str, *, create: bool = False
) -> str:
    project_id, separator, source_name = source_id.rpartition("/connections/")
    if (
        not separator
        or not re.fullmatch(r"[a-zA-Z0-9_-]+", name)
        or name.casefold() == source_name.casefold()
        or not re.fullmatch(
            r"/subscriptions/[a-zA-Z0-9-]+/resourceGroups/[a-zA-Z0-9_.()-]+"
            r"/providers/Microsoft\.CognitiveServices/accounts/[a-zA-Z0-9-]+"
            r"/projects/[a-zA-Z0-9-]+",
            project_id,
            flags=re.IGNORECASE,
        )
    ):
        raise ValueError("A separate assistant connection in the source project is required")
    expected = {
        "category": "RemoteTool",
        "authType": "UserEntraToken",
        "target": target,
        "audience": DELEGATED_AUDIENCE,
        "scopes": [f"{DELEGATED_AUDIENCE}/DataAgent.Execute.All"],
    }
    url = f"https://management.azure.com{project_id}/connections/{name}"
    params = {"api-version": API_VERSION}
    response = client.get(url, params=params)
    if response.status_code == 404:
        if not create:
            raise ValueError(
                "Assistant connection is absent; use --create-fabric-connection "
                "to authorize its creation"
            )
        response = client.put(
            url,
            params=params,
            json={
                "properties": {
                    **expected,
                    "metadata": {
                        "type": "generic_mcp",
                        "audience": DELEGATED_AUDIENCE,
                    },
                }
            },
            headers={"If-None-Match": "*"},
        )
        response.raise_for_status()
        response = client.get(url, params=params)
    response.raise_for_status()
    actual = response.json()["properties"]
    if any(actual.get(key) != value for key, value in expected.items()):
        raise ValueError("Assistant connection has a different target or delegated authentication")
    return name


class AssistantSettings(StrictOutput):
    endpoint: str
    agent: AgentBinding
    model: str = Field(min_length=1)
    sourceAgent: str
    sourceVersion: str


def assistant_definition(source: dict) -> dict:
    tools = copy.deepcopy(source.get("tools", []))
    documents = [tool for tool in tools if tool.get("server_label") == "documents"]
    fabric = [tool for tool in tools if tool.get("server_label") == "fabric-facts"]
    if len(documents) != 1 or len(fabric) != 1:
        raise ValueError("Source agent must have exactly one document and one Fabric IQ binding")
    document, facts = documents[0], fabric[0]
    allowed = document.get("allowed_tools")
    if isinstance(allowed, dict):
        allowed = allowed.get("tool_names")
    if (
        document.get("type") != "mcp"
        or allowed != ["knowledge_base_retrieve"]
        or facts.get("type") != "fabric_iq_preview"
        or any(not tool.get("project_connection_id") for tool in (document, facts))
        or any(tool.get("require_approval") != "never" for tool in (document, facts))
    ):
        raise ValueError("Source must use native read-only document retrieval and Fabric IQ")
    document["allowed_tools"] = allowed
    return {
        "kind": "prompt",
        "model": source["model"],
        "instructions": (Path(__file__).parent / "prompts" / "assistant.txt").read_text(
            encoding="utf-8"
        ),
        "temperature": 0.1,
        "tools": [document, facts],
    }


def register(
    endpoint: str,
    source_name: str,
    name: str,
    output: Path,
    *,
    fabric_connection: str | None = None,
    create_fabric_connection: bool = False,
) -> AssistantSettings:
    from azure.ai.projects import AIProjectClient
    from azure.ai.projects.models import PromptAgentDefinition
    from azure.core.exceptions import ResourceNotFoundError
    from azure.identity import AzureCliCredential

    if name.casefold() == source_name.casefold():
        raise ValueError("The assistant must not replace the source agent")
    if create_fabric_connection and not fabric_connection:
        raise ValueError("--create-fabric-connection requires --fabric-connection")
    with (
        AzureCliCredential() as credential,
        AIProjectClient(endpoint=endpoint, credential=credential) as client,
    ):
        source = client.agents.get(agent_name=source_name).versions.latest
        body = assistant_definition(source.definition.as_dict())
        try:
            versions = list(client.agents.list_versions(agent_name=name))
        except ResourceNotFoundError:
            versions = []
        if any(row.metadata.get("component") != "close-out-workbench" for row in versions):
            raise ValueError(f"Refusing to modify an unrelated agent: {name}")
        if fabric_connection:
            facts = body["tools"][1]
            source_connection = client.connections.get(facts["project_connection_id"])
            if source_connection.target != facts["server_url"]:
                raise ValueError("Source Fabric connection target differs from the native tool")
            token = credential.get_token("https://management.azure.com/.default")
            with httpx.Client(
                headers={"Authorization": f"Bearer {token.token}"},
                timeout=30,
                follow_redirects=False,
            ) as management:
                facts["project_connection_id"] = reconcile_delegated_connection(
                    management,
                    source_connection.id,
                    fabric_connection,
                    facts["server_url"],
                    create=create_fabric_connection,
                )
        digest = definition_hash(body)
        candidate = None
        for row in versions:
            if row.metadata.get("definition_sha256") == digest:
                existing = client.agents.get_version(agent_name=name, agent_version=row.version)
                if definition_matches(body, existing.definition.as_dict()):
                    candidate = existing
                    break
        if candidate is None:
            candidate = client.agents.create_version(
                agent_name=name,
                definition=PromptAgentDefinition(body),
                description="Read-only workbench assistant; native Fabric and document IQ.",
                metadata={
                    "source": "close-out-demo",
                    "component": "close-out-workbench",
                    "definition_sha256": digest,
                    "source_agent": source_name,
                    "source_version": source.version,
                },
            )
        verified = client.agents.get_version(agent_name=name, agent_version=candidate.version)
        if verified.status != "active" or not definition_matches(
            body, verified.definition.as_dict()
        ):
            raise RuntimeError("Assistant read-back does not match the active source definition")
        settings = AssistantSettings(
            endpoint=endpoint,
            agent=AgentBinding(name=name, version=verified.version, definitionHash=digest),
            model=body["model"],
            sourceAgent=source_name,
            sourceVersion=source.version,
        )
    write_json(output, settings.model_dump())
    return settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--source-agent", default="close-out-coordinator")
    parser.add_argument("--name", default="close-out-assistant")
    parser.add_argument(
        "--fabric-connection",
        help="Separate UserEntraToken connection for the interactive assistant only",
    )
    parser.add_argument("--create-fabric-connection", action="store_true")
    parser.add_argument(
        "--output", type=Path, default=Path(".local") / "foundry" / "assistant.json"
    )
    args = parser.parse_args()
    settings = register(
        args.endpoint,
        args.source_agent,
        args.name,
        args.output,
        fabric_connection=args.fabric_connection,
        create_fabric_connection=args.create_fabric_connection,
    )
    print(json.dumps(settings.model_dump(), indent=2))


if __name__ == "__main__":
    main()

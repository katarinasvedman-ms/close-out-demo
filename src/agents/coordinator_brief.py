"""Publish a readable coordinator version while retaining its native read-only tools."""

import argparse
from pathlib import Path

from agents.assistant import assistant_definition
from agents.definitions import definition_hash
from agents.foundry import definition_matches
from shared.local_demo import write_json


def brief_definition(source: dict) -> dict:
    if source.get("kind") != "prompt":
        raise ValueError("A native prompt-agent source is required")
    body = assistant_definition(source)
    body["instructions"] = (Path(__file__).parent / "prompts" / "closeout-brief.txt").read_text(
        encoding="utf-8"
    )
    body["text"] = {"format": {"type": "text"}}
    return body


def register(endpoint: str, name: str, source_version: str, output: Path) -> dict:
    from azure.ai.projects import AIProjectClient
    from azure.ai.projects.models import PromptAgentDefinition
    from azure.identity import AzureCliCredential

    with (
        AzureCliCredential() as credential,
        AIProjectClient(endpoint=endpoint, credential=credential) as client,
    ):
        source = client.agents.get_version(agent_name=name, agent_version=source_version)
        body = brief_definition(source.definition.as_dict())
        digest = definition_hash(body)
        candidate = None
        for version in client.agents.list_versions(agent_name=name):
            if version.metadata.get("definition_sha256") == digest:
                existing = client.agents.get_version(agent_name=name, agent_version=version.version)
                if definition_matches(body, existing.definition.as_dict()):
                    candidate = existing
                    break
        if candidate is None:
            candidate = client.agents.create_version(
                agent_name=name,
                definition=PromptAgentDefinition(body),
                description="Readable coordinator review; native scoped IQ; no business writes.",
                metadata={
                    "source": "close-out-demo",
                    "component": "close-out-readable-brief",
                    "definition_sha256": digest,
                    "source_version": source_version,
                },
            )
        verified = client.agents.get_version(agent_name=name, agent_version=candidate.version)
        if verified.status != "active" or not definition_matches(
            body, verified.definition.as_dict()
        ):
            raise RuntimeError(
                "Readable coordinator read-back differs from the published definition"
            )
        result = {
            "endpoint": endpoint,
            "agent": {"name": name, "version": verified.version, "definitionHash": digest},
            "sourceVersion": source_version,
        }
    write_json(output, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--agent-name", required=True)
    parser.add_argument("--source-version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    register(args.endpoint, args.agent_name, args.source_version, args.output)


if __name__ == "__main__":
    main()

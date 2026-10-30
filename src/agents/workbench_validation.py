"""Invoke the existing publication-enabled validator with pinned registered photo bytes."""

import argparse
import base64
import json
import logging
import sqlite3
import time
from pathlib import Path
from uuid import UUID

from pydantic import Field, field_validator

from agents.blob_sources import BlobSources, azure_downloader
from agents.definitions import AgentBinding, Assessments, StrictOutput, definition_hash
from agents.flow import storage_endpoint
from shared.headless import BlobStore, digest, stable_id
from shared.local_demo import write_json

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger(__name__)
ACTIVE = {"Preparing", "Starting", "Running", "Unknown"}


class ValidationSettings(StrictOutput):
    endpoint: str
    storageUrl: str
    agent: AgentBinding
    manifest: dict
    evidenceId: str = Field(min_length=1)

    @field_validator("storageUrl")
    @classmethod
    def storage_url(cls, value):
        return storage_endpoint(value)


class ValidationRequest(StrictOutput):
    siteId: str
    evidenceId: str
    publicationId: str

    @field_validator("publicationId")
    @classmethod
    def identifier(cls, value):
        if str(UUID(value)) != value:
            raise ValueError("A canonical publication UUID is required")
        return value


def sources(settings: ValidationSettings, blobs) -> BlobSources:
    manifest = settings.manifest
    actual, _ = BlobStore(blobs.get_container_client("ledger")).read(
        f"registrations/{manifest['runId']}/{manifest['batchId']}/manifest.json"
    )
    if actual != manifest:
        raise ValueError("Registered source manifest changed; configure validation again")
    inputs = blobs.get_container_client("inputs")
    result = BlobSources(manifest, ROOT / "contracts", inputs.url, azure_downloader(inputs))
    member = result.members.get(settings.evidenceId)
    if member is None or member["kind"] != "photo":
        raise ValueError("Select one registered photo")
    return result


def publication_context(settings: ValidationSettings, publication_id: str) -> dict:
    if publication_id in {settings.manifest["runId"], settings.manifest["batchId"]}:
        raise ValueError("A new assessment needs a fresh publication UUID")
    return {
        "sourceRunId": settings.manifest["runId"],
        "sourceBatchId": settings.manifest["batchId"],
        "publicationId": publication_id,
    }


def validation_input(settings: ValidationSettings, source: BlobSources, publication_id: str):
    packets, attachments = source.validation_inputs([settings.evidenceId])
    if len(attachments) != 1 or attachments[0][2] != "image/png":
        raise ValueError("The workbench currently supports one registered PNG photo")
    identifier, data, media_type, _ = attachments[0]
    return [
        {
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": json.dumps(
                        {
                            "evidence": packets,
                            "publicationContext": publication_context(settings, publication_id),
                        }
                    ),
                },
                {"type": "input_text", "text": f"Attached registered photo: {identifier}"},
                {
                    "type": "input_image",
                    "image_url": f"data:{media_type};base64,{base64.b64encode(data).decode()}",
                    "detail": "high",
                },
            ],
        }
    ]


def publication_receipt(
    settings: ValidationSettings, publication_id: str, blobs, assessment: dict | None = None
) -> dict:
    batch = stable_id(publication_id, "assessment-publication")
    state, _ = BlobStore(blobs.get_container_client("ledger")).read(
        f"validation/{publication_id}/{batch}.json"
    )
    if state is None:
        return {"status": "NotConfirmed", "eventIds": [], "error": None}
    receipt = state.get("result")
    if receipt is None:
        return {"status": "NotConfirmed", "eventIds": [], "error": state["status"]}
    provenance = receipt.get("provenance", {})
    if (
        receipt.get("runId") != publication_id
        or receipt.get("batchId") != batch
        or receipt.get("siteId") != settings.manifest["siteId"]
        or receipt.get("caseId") != settings.manifest["caseId"]
        or provenance.get("sourceRunId") != settings.manifest["runId"]
        or provenance.get("sourceBatchId") != settings.manifest["batchId"]
        or provenance.get("sourceManifestSha256") != digest(settings.manifest)
        or provenance.get("assessment", {}).get("evidenceId") != settings.evidenceId
    ):
        raise ValueError("Publisher receipt does not match the submitted registration")
    if assessment is not None and provenance["assessment"] != assessment:
        raise ValueError("Published assessment differs from the final agent assessment")
    expected = {
        stable_id(publication_id, batch, settings.evidenceId, name)
        for name in ("PhotoUploaded", "ValidationResultCreated")
    } | {stable_id(publication_id, batch, "completed")}
    published = state["published"]
    if not set(published) <= expected or len(published) != len(set(published)):
        raise ValueError("Publisher receipt contains unexpected or duplicate events")
    if state["status"] == "Completed" and set(published) != expected:
        raise ValueError("Completed publisher receipt does not contain the expected three events")
    return {
        "status": "Published" if state["status"] == "Completed" else "NotConfirmed",
        "eventIds": published,
        "error": None if state["status"] == "Completed" else f"Publisher: {state['status']}",
    }


def assessment_result(raw: dict, evidence_id: str) -> dict:
    if raw.get("status") != "completed":
        error = raw.get("error") or {}
        raise ValueError(
            f"Foundry response {raw['id']}: {raw.get('status')}. "
            f"{error.get('message', 'No completed assessment.')}"
        )
    messages = [
        item
        for item in raw.get("output", [])
        if item.get("type") == "message" and item.get("role") == "assistant"
    ]
    text = "\n".join(
        part["text"]
        for part in (messages[-1].get("content", []) if messages else [])
        if part.get("type") == "output_text"
    )
    result = Assessments.model_validate_json(text)
    if len(result.assessments) != 1 or result.assessments[0].evidenceId != evidence_id:
        raise ValueError("Validation response does not assess exactly the registered photo")
    assessment = result.assessments[0]
    if not assessment.findings or any(not item.strip() for item in assessment.findings):
        raise ValueError("Validation response contains no concrete findings")
    return assessment.model_dump()


def configure(
    endpoint: str,
    storage_url: str,
    run_id: str,
    batch_id: str,
    evidence_id: str,
    agent_name: str,
    output: Path,
) -> ValidationSettings:
    from azure.ai.projects import AIProjectClient
    from azure.identity import AzureCliCredential
    from azure.storage.blob import BlobServiceClient

    run_id, batch_id = str(UUID(run_id)), str(UUID(batch_id))
    storage_url = storage_endpoint(storage_url)
    with (
        AzureCliCredential() as credential,
        AIProjectClient(endpoint=endpoint, credential=credential) as project,
        BlobServiceClient(storage_url, credential=credential) as blobs,
    ):
        agent = project.agents.get(agent_name=agent_name).versions.latest
        body = agent.definition.as_dict()
        operations = [
            operation
            for tool in body.get("tools", [])
            if tool.get("type") == "openapi"
            for path in tool["openapi"]["spec"]["paths"].values()
            for operation in path.values()
            if isinstance(operation, dict)
        ]
        if (
            agent.status != "active"
            or "publicationContext" not in body["instructions"]
            or not any(row.get("operationId") == "publish_validation_result" for row in operations)
        ):
            raise ValueError("Select an active validator with the existing publication tool")
        manifest, _ = BlobStore(blobs.get_container_client("ledger")).read(
            f"registrations/{run_id}/{batch_id}/manifest.json"
        )
        if manifest is None or (manifest["runId"], manifest["batchId"]) != (run_id, batch_id):
            raise ValueError("The selected source registration does not exist or is mismatched")
        settings = ValidationSettings(
            endpoint=endpoint,
            storageUrl=storage_url,
            manifest=manifest,
            evidenceId=evidence_id,
            agent=AgentBinding(
                name=agent_name, version=agent.version, definitionHash=definition_hash(body)
            ),
        )
        sources(settings, blobs).validation_inputs([evidence_id])
    write_json(output, settings.model_dump())
    return settings


class ValidationRunner:
    def __init__(self, settings: ValidationSettings, directory: Path):
        self.settings = settings
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.database = directory / "validation.sqlite3"
        self.target = digest(settings.model_dump())
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS validation_runs "
                "(id TEXT PRIMARY KEY, target TEXT NOT NULL, state TEXT NOT NULL)"
            )

    def save(self, state: dict) -> dict:
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "UPDATE validation_runs SET state=? WHERE id=? AND target=?",
                (json.dumps(state), state["publicationId"], self.target),
            )
        return state

    def get(self, identifier: str) -> dict:
        identifier = str(UUID(identifier))
        with sqlite3.connect(self.database) as connection:
            row = connection.execute(
                "SELECT state FROM validation_runs WHERE id=? AND target=?",
                (identifier, self.target),
            ).fetchone()
        if row is None:
            raise ValueError("No validation submission exists for this target and ID")
        return json.loads(row[0])

    def latest(self) -> dict | None:
        with sqlite3.connect(self.database) as connection:
            row = connection.execute(
                "SELECT state FROM validation_runs WHERE target=? ORDER BY rowid DESC LIMIT 1",
                (self.target,),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def info(self) -> dict:
        settings, manifest = self.settings, self.settings.manifest
        member = next(
            row for row in manifest["members"] if row["evidenceId"] == settings.evidenceId
        )
        return {
            "configured": True,
            "siteId": manifest["siteId"],
            "evidenceId": settings.evidenceId,
            "agent": settings.agent.model_dump(),
            "sourceRunId": manifest["runId"],
            "sourceBatchId": manifest["batchId"],
            "sha256": member["blob"]["sha256"],
            "latest": self.latest(),
        }

    def start(self, request: ValidationRequest) -> dict:
        if (request.siteId, request.evidenceId) != (
            self.settings.manifest["siteId"],
            self.settings.evidenceId,
        ):
            raise ValueError("The request is outside the configured photo/site scope")
        publication_context(self.settings, request.publicationId)
        state = {
            "publicationId": request.publicationId,
            "status": "Preparing",
            "createdAt": time.time(),
            "responseId": None,
            "assessment": None,
            "publication": {"status": "NotConfirmed", "eventIds": [], "error": None},
            "error": None,
        }
        with sqlite3.connect(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT target,state FROM validation_runs WHERE id=?", (request.publicationId,)
            ).fetchone()
            if existing:
                if existing[0] != self.target:
                    raise ValueError("Publication ID was used for a different target")
                return json.loads(existing[1])
            active = connection.execute(
                "SELECT state FROM validation_runs WHERE target=? ORDER BY rowid DESC LIMIT 1",
                (self.target,),
            ).fetchone()
            if active and json.loads(active[0])["status"] in ACTIVE:
                return json.loads(active[0])
            connection.execute(
                "INSERT INTO validation_runs VALUES (?,?,?)",
                (request.publicationId, self.target, json.dumps(state)),
            )
        try:
            from azure.ai.projects import AIProjectClient
            from azure.identity import AzureCliCredential
            from azure.storage.blob import BlobServiceClient

            with (
                AzureCliCredential() as credential,
                BlobServiceClient(self.settings.storageUrl, credential=credential) as blobs,
                AIProjectClient(endpoint=self.settings.endpoint, credential=credential) as project,
                project.get_openai_client(timeout=45, max_retries=0) as client,
            ):
                agent = project.agents.get_version(
                    agent_name=self.settings.agent.name, agent_version=self.settings.agent.version
                )
                if (
                    definition_hash(agent.definition.as_dict())
                    != self.settings.agent.definitionHash
                ):
                    raise ValueError("Pinned validation agent changed; configure again")
                inputs = validation_input(
                    self.settings, sources(self.settings, blobs), request.publicationId
                )
                state["status"] = "Starting"
                self.save(state)
                response = client.responses.create(
                    input=inputs,
                    background=True,
                    store=True,
                    extra_body={
                        "agent_reference": {
                            "type": "agent_reference",
                            "name": self.settings.agent.name,
                            "version": self.settings.agent.version,
                        }
                    },
                )
                state.update(status="Running", responseId=response.id)
                self.save(state)
        except Exception as error:
            LOG.exception("Validation submission failed")
            ambiguous = state["status"] == "Starting"
            state.update(
                status=("Running" if state["responseId"] else "Unknown" if ambiguous else "Failed"),
                error=(
                    "Foundry returned a response ID but acknowledgement failed. "
                    "Continue polling the same response; do not resubmit."
                    if state["responseId"]
                    else "The initial Foundry POST outcome is unknown. Do not retry inference; "
                    "inspect the Foundry run and publisher ledger before starting new work."
                    if ambiguous
                    else f"Validation preparation failed: {error}"
                ),
            )
            self.save(state)
        return state

    def poll(self, identifier: str) -> dict:
        state = self.get(identifier)
        if state["status"] in {"Preparing", "Starting"} and time.time() - state["createdAt"] > 120:
            state.update(
                status="Unknown", error="Submission was interrupted. Inspect before retrying."
            )
            return self.save(state)
        if state["status"] not in {"Running", "Completed", "Failed", "Unknown"}:
            return state
        from azure.identity import AzureCliCredential
        from azure.storage.blob import BlobServiceClient

        with (
            AzureCliCredential() as credential,
            BlobServiceClient(self.settings.storageUrl, credential=credential) as blobs,
        ):
            if state["status"] == "Running":
                from azure.ai.projects import AIProjectClient

                with (
                    AIProjectClient(
                        endpoint=self.settings.endpoint, credential=credential
                    ) as project,
                    project.get_openai_client(timeout=45, max_retries=0) as client,
                ):
                    response = client.responses.retrieve(state["responseId"])
                    if response.status in {"queued", "in_progress"}:
                        return state
                    raw = response.model_dump(mode="json")
                    write_json(self.directory / f"{response.id}.json", raw)
                    try:
                        state["assessment"] = assessment_result(raw, self.settings.evidenceId)
                        state.update(status="Completed", error=None)
                    except ValueError as error:
                        state.update(status="Failed", error=str(error))
            try:
                state["publication"] = publication_receipt(
                    self.settings, state["publicationId"], blobs, state["assessment"]
                )
            except Exception as error:
                LOG.exception("Could not verify publication")
                state["publication"] = {"status": "Unknown", "eventIds": [], "error": str(error)}
        return self.save(state)

    def photo(self) -> bytes:
        from azure.identity import AzureCliCredential
        from azure.storage.blob import BlobServiceClient

        with (
            AzureCliCredential() as credential,
            BlobServiceClient(self.settings.storageUrl, credential=credential) as blobs,
        ):
            source = sources(self.settings, blobs)
            return source.read_asset(source.members[self.settings.evidenceId]["metadata"])[0]


def main() -> None:
    parser = argparse.ArgumentParser(description="Pin existing validation inputs without inference")
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--storage-url", required=True)
    parser.add_argument("--source-run-id", required=True)
    parser.add_argument("--source-batch-id", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--agent-name", default="close-out-validation")
    parser.add_argument(
        "--output", type=Path, default=ROOT / ".local" / "foundry" / "validation.json"
    )
    args = parser.parse_args()
    settings = configure(
        args.endpoint,
        args.storage_url,
        args.source_run_id,
        args.source_batch_id,
        args.evidence,
        args.agent_name,
        args.output,
    )
    print(
        json.dumps(
            {
                "config": str(args.output),
                "agent": settings.agent.model_dump(),
                "siteId": settings.manifest["siteId"],
                "evidenceId": settings.evidenceId,
            }
        )
    )


if __name__ == "__main__":
    main()

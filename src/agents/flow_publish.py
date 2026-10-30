"""Publish a registered photo assessment without invoking or impersonating a model."""

import argparse
import copy
import re
from pathlib import Path
from uuid import UUID

from pydantic import field_validator

from agents.blob_sources import BlobSources, azure_downloader
from agents.definitions import Assessment, Assessments, StrictOutput
from shared.headless import Ledger, batch_events, digest, stable_id, timestamp
from shared.local_demo import write_json

SOURCE = "agent-submitted-assessment"


class PublishAssessment(StrictOutput):
    sourceRunId: str
    sourceBatchId: str
    publicationId: str
    assessment: Assessment

    @field_validator("sourceRunId", "sourceBatchId", "publicationId")
    @classmethod
    def identifier(cls, value):
        if str(UUID(value)) != value:
            raise ValueError("Use a canonical UUID")
        return value


def publish_assessment(body, settings, contracts, inputs, ledger: Ledger, results, publish, caller):
    request = PublishAssessment.model_validate(body)
    original, _ = ledger.store.read(
        f"registrations/{request.sourceRunId}/{request.sourceBatchId}/manifest.json"
    )
    if original is None:
        raise ValueError("Source evidence registration does not exist")
    if (original["runId"], original["batchId"]) != (
        request.sourceRunId,
        request.sourceBatchId,
    ):
        raise ValueError("Source registration identity disagrees")
    if request.publicationId == request.sourceRunId:
        raise ValueError("Publication must use a fresh isolated run")
    batch = stable_id(request.publicationId, "assessment-publication")
    key = f"validation/{request.publicationId}/{batch}.json"
    claim, state = ledger.claim(
        key,
        {
            "request": request.model_dump(),
            "sourceManifestSha256": digest(original),
            "callerOid": caller,
        },
    )
    if claim is not None:
        try:
            if state["result"] is None:
                sources = BlobSources(original, contracts, inputs.url, azure_downloader(inputs))
                member = sources.members.get(request.assessment.evidenceId)
                if member is None or member["kind"] != "photo":
                    raise ValueError("Select one photo from the source registration")
                packets, _ = sources.validation_inputs([request.assessment.evidenceId])
                validations = sources.validation_results(
                    Assessments(assessments=[request.assessment]), packets, settings
                )
                for validation in validations:
                    validation.update(
                        validatedBy="foundry-openapi-tool-submission",
                        model="not-attested",
                        modelVersion="not-attested",
                    )
                now = timestamp()
                manifest = {
                    **copy.deepcopy(original),
                    "runId": request.publicationId,
                    "batchId": batch,
                    "registeredAt": now,
                    "members": [copy.deepcopy(member)],
                }
                receipt = {
                    "runId": request.publicationId,
                    "batchId": batch,
                    "caseId": original["caseId"],
                    "siteId": original["siteId"],
                    "completedAt": now,
                    "modes": {"inference": SOURCE, "publisher": "managed-identity-function"},
                    "provenance": {
                        "authenticatedCallerOid": caller,
                        "sourceRunId": request.sourceRunId,
                        "sourceBatchId": request.sourceBatchId,
                        "sourceManifestSha256": digest(original),
                        "assessment": request.assessment.model_dump(),
                        "modelResponseIndependentlyVerified": False,
                        "playgroundAttachmentIndependentlyVerified": False,
                    },
                    "result": validations,
                }
                state["outbox"] = batch_events(
                    manifest, receipt, sources.validators, validation_source=SOURCE
                )
                state["result"] = receipt
                state["manifest"] = manifest
                claim.save()
            ledger.register(
                f"registrations/{request.publicationId}/{batch}/manifest.json", state["manifest"]
            )
            for event in state["outbox"]:
                if event["eventId"] not in state["published"]:
                    claim.save()
                    publish(event)
                    state["published"].append(event["eventId"])
                    claim.save()
            results.register(key, state["result"])
            claim.finish("Completed")
        except Exception:
            claim.finish("Failed", "AssessmentPublicationFailed")
            raise
    return {
        "status": state["status"],
        "runId": request.publicationId,
        "batchId": batch,
        "validationResultId": state["result"]["result"][0]["validationResultId"],
        "eventIds": state["published"],
        "validationSource": SOURCE,
        "resultKey": key,
    }


def openapi_document(server_url):
    if not re.fullmatch(r"https://[a-z0-9][a-z0-9-]*\.azurewebsites\.net", server_url):
        raise ValueError("Use the exact HTTPS Function host without a path, query or credentials")
    request = PublishAssessment.model_json_schema(ref_template="#/components/schemas/{model}")
    definitions = request.pop("$defs")
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Close-out synthetic assessment publisher",
            "version": "0.3.0",
            "description": (
                "Publishes one submitted photo assessment against registered synthetic evidence. "
                "Does not invoke a model, verify its response, approve evidence "
                "or change readiness."
            ),
        },
        "servers": [{"url": server_url}],
        "paths": {
            "/api/publish-validation-result": {
                "post": {
                    "operationId": "publish_validation_result",
                    "description": (
                        "Publish one assessment using the exact supplied source registration IDs. "
                        "Use a supplied fresh publicationId; never invent evidence identifiers. "
                        "Retry with the same publicationId and unchanged assessment. Changed "
                        "assessments require a new publicationId. Returns published event IDs, "
                        "not readiness or downstream completion."
                    ),
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/PublishAssessment"}
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": (
                                "Events published; Fabric ingestion/projection is separate"
                            ),
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "status": {"type": "string"},
                                            "runId": {"type": "string"},
                                            "batchId": {"type": "string"},
                                            "validationResultId": {"type": "string"},
                                            "eventIds": {
                                                "type": "array",
                                                "items": {"type": "string"},
                                            },
                                            "validationSource": {"type": "string"},
                                            "resultKey": {"type": "string"},
                                        },
                                    }
                                }
                            },
                        },
                        "400": {"description": "Invalid assessment or registration"},
                        "403": {"description": "Calling identity is not the authorized publisher"},
                        "409": {"description": "Publication ID reused with changed input"},
                        "429": {"description": "Publication is already claimed; retry unchanged"},
                        "503": {"description": "Dependency failure; retry unchanged"},
                    },
                }
            }
        },
        "components": {"schemas": {**definitions, "PublishAssessment": request}},
    }


def main():
    parser = argparse.ArgumentParser(description="Generate the publisher OpenAPI import file")
    parser.add_argument("--server-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    write_json(args.output, openapi_document(args.server_url))


if __name__ == "__main__":
    main()

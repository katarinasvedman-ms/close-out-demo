"""Thin Python Azure Functions adapter. Foundry owns prompt-agent execution."""

import json
import logging
import os
from functools import partial
from pathlib import Path

import azure.functions as func
from azure.identity import ManagedIdentityCredential
from azure.storage.blob import BlobServiceClient
from jsonschema import ValidationError

from agents.definitions import FoundrySettings
from agents.flow import EventstreamPublisher, eventstream_client
from agents.flow_coordinator import coordinate, snapshot_reader
from agents.flow_publish import publish_assessment
from agents.flow_runtime import process_ready_blob
from agents.foundry import invoke
from shared.headless import BlobStore, Busy, Conflict, Ledger

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)


@app.route(route="publish-validation-result", methods=["POST"])
def publish_validation_result(req: func.HttpRequest) -> func.HttpResponse:
    caller = req.headers.get("x-ms-client-principal-id")
    if not caller or caller not in os.environ.get("FLOW_PUBLISHER_CALLER_OIDS", "").split(","):
        return func.HttpResponse(status_code=403)
    try:
        body = req.get_json()
        settings = FoundrySettings.model_validate_json(os.environ["FLOW_FOUNDRY_CONFIG_JSON"])
        with (
            ManagedIdentityCredential() as credential,
            BlobServiceClient(os.environ["FLOW_STORAGE_URL"], credential=credential) as storage,
            eventstream_client(
                os.environ["FLOW_EVENTSTREAM_NAMESPACE"],
                os.environ["FLOW_EVENTSTREAM_ENTITY"],
                credential,
            ) as publisher,
        ):
            result = publish_assessment(
                body,
                settings,
                Path(os.environ["FLOW_CONTRACTS_ROOT"]),
                storage.get_container_client("inputs"),
                Ledger(BlobStore(storage.get_container_client("ledger"))),
                Ledger(BlobStore(storage.get_container_client("results"))),
                EventstreamPublisher(publisher),
                caller,
            )
        return func.HttpResponse(json.dumps(result), mimetype="application/json")
    except Busy:
        return func.HttpResponse(
            '{"code":"PublicationBusy"}',
            status_code=429,
            mimetype="application/json",
            headers={"Retry-After": "30"},
        )
    except Conflict:
        return func.HttpResponse(
            '{"code":"PublicationConflict"}', status_code=409, mimetype="application/json"
        )
    except (ValueError, KeyError, PermissionError, ValidationError):
        return func.HttpResponse(
            '{"code":"InvalidAssessmentRegistration"}', status_code=400, mimetype="application/json"
        )
    except Exception:
        logging.exception("Assessment publication failed")
        return func.HttpResponse(
            '{"code":"PublicationDependencyFailure"}', status_code=503, mimetype="application/json"
        )


@app.route(route="validation-batch", methods=["POST"])
async def validation_batch(req: func.HttpRequest) -> func.HttpResponse:
    # App Service Authentication validates the bearer token; pin its principal as well.
    caller = req.headers.get("x-ms-client-principal-id")
    if not caller or caller not in os.environ["FLOW_ALLOWED_CALLER_OIDS"].split(","):
        return func.HttpResponse(status_code=403)
    try:
        body = req.get_json()
        if not isinstance(body, dict) or set(body) != {"blobPath"}:
            raise ValueError("Expected only the triggered blobPath")
        settings = FoundrySettings.model_validate_json(os.environ["FLOW_FOUNDRY_CONFIG_JSON"])
        with (
            ManagedIdentityCredential() as credential,
            BlobServiceClient(os.environ["FLOW_STORAGE_URL"], credential=credential) as storage,
            eventstream_client(
                os.environ["FLOW_EVENTSTREAM_NAMESPACE"],
                os.environ["FLOW_EVENTSTREAM_ENTITY"],
                credential,
            ) as publisher,
        ):
            result = await process_ready_blob(
                body["blobPath"],
                storage.get_container_client("inputs"),
                storage.get_container_client("ledger"),
                storage.get_container_client("results"),
                publisher,
                settings,
                Path(os.environ["FLOW_CONTRACTS_ROOT"]),
            )
        return func.HttpResponse(json.dumps(result), mimetype="application/json")
    except Busy:
        return func.HttpResponse(
            '{"code":"RetryableClaimOrRegistration"}',
            status_code=429,
            mimetype="application/json",
            headers={"Retry-After": "30"},
        )
    except (ValueError, KeyError, Conflict, ValidationError):
        return func.HttpResponse(
            '{"code":"InvalidBatch"}', status_code=400, mimetype="application/json"
        )
    except Exception:
        logging.error("Validation batch failed; inspect the correlated durable receipt")
        return func.HttpResponse(
            '{"code":"DependencyFailure"}', status_code=503, mimetype="application/json"
        )


@app.route(route="coordinator-draft", methods=["POST"])
async def coordinator_draft(req: func.HttpRequest) -> func.HttpResponse:
    caller = req.headers.get("x-ms-client-principal-id")
    if not caller or caller not in os.environ["FLOW_ALLOWED_CALLER_OIDS"].split(","):
        return func.HttpResponse(status_code=403)
    try:
        settings = FoundrySettings.model_validate_json(os.environ["FLOW_FOUNDRY_CONFIG_JSON"])
        if settings.fabric is None or settings.knowledge is None:
            raise ValueError("Both native tools must be configured")
        with (
            ManagedIdentityCredential() as credential,
            BlobServiceClient(os.environ["FLOW_STORAGE_URL"], credential=credential) as storage,
        ):
            result = await coordinate(
                req.get_json(),
                settings,
                Path(os.environ["FLOW_CONTRACTS_ROOT"]),
                Ledger(BlobStore(storage.get_container_client("ledger"))),
                Ledger(BlobStore(storage.get_container_client("results"))),
                snapshot_reader(settings.fabric, credential),
                partial(invoke, managed_identity=True),
            )
        return func.HttpResponse(json.dumps(result), mimetype="application/json")
    except Busy:
        return func.HttpResponse(
            '{"code":"RetryableClaim"}',
            status_code=429,
            mimetype="application/json",
            headers={"Retry-After": "30"},
        )
    except (ValueError, KeyError, ValidationError) as error:
        logging.warning("Coordinator rejected (%s): %s", type(error).__name__, error)
        return func.HttpResponse(
            '{"code":"StaleOrInvalidReadiness"}', status_code=409, mimetype="application/json"
        )
    except Exception:
        logging.exception("Coordinator failed; inspect the correlated durable receipt")
        return func.HttpResponse(
            '{"code":"CoordinatorDependencyFailure"}', status_code=503, mimetype="application/json"
        )

"""Workflow-invoked Blob batch boundary; credentials are supplied by the deployed identity."""

import functools
import json
from pathlib import Path
from uuid import UUID

from agents.blob_sources import BlobSources, azure_downloader
from agents.definitions import FoundrySettings
from agents.flow import EventstreamPublisher
from agents.foundry import invoke
from shared.headless import BlobStore, Busy, Ledger, digest, validate_batch


async def process_ready_blob(
    path: str, inputs, receipts, results, publisher, settings: FoundrySettings, contracts: Path
) -> dict:
    parts = path.split("/")
    if not parts or parts[-1] != "_batch.ready":
        return {"status": "Ignored-non-marker"}
    if len(parts) != 3:
        raise ValueError("A batch marker must have exactly runId/batchId/_batch.ready")
    run, batch = str(UUID(parts[0])), str(UUID(parts[1]))
    if (run, batch) != (parts[0], parts[1]):
        raise ValueError("Marker identity must use canonical UUIDs")
    ledger = Ledger(BlobStore(receipts))
    manifest, _ = ledger.store.read(f"registrations/{run}/{batch}/manifest.json")
    if manifest is None:
        raise Busy("Registered manifest is not yet available")
    if (manifest["runId"], manifest["batchId"]) != (run, batch):
        raise ValueError("Marker path disagrees with the registration")
    marker = inputs.get_blob_client(path).download_blob().readall()
    if len(marker) > 1024 or json.loads(marker) != {"manifestSha256": digest(manifest)}:
        raise ValueError("Completion marker does not bind the exact registration")
    sources = BlobSources(manifest, contracts, inputs.url, azure_downloader(inputs))
    state = await validate_batch(
        sources,
        settings,
        ledger,
        functools.partial(invoke, managed_identity=True),
        EventstreamPublisher(publisher),
    )
    result_path = f"validation/{run}/{batch}.json"
    Ledger(BlobStore(results)).register(result_path, state["result"])
    return {"status": state["status"], "resultRef": f"{results.url}/{result_path}"}

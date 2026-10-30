"""Configure native Blob IQ, with optional synthetic document population."""

import argparse
import hashlib
import json
import time
from pathlib import Path

import httpx

from agents.definitions import KnowledgeSettings
from shared.local_demo import write_json
from shared.seed import load_seed
from shared.seed_files import asset_path, document_text

API_VERSION = "2026-08-01-preview"
RETRIEVAL_INSTRUCTIONS = (
    "For site-specific questions, include the exact site ID in searches. "
    "Prioritize RFDS for planned technical values and acceptance documents for acceptance "
    "requirements. Respect explicitly requested document versions. Retrieve passages identifying "
    "the site, document and version. Do not substitute another site's documents when matching "
    "evidence is missing."
)


def native_definitions(
    name: str, source: str, storage_id: str, container: str, model: dict
) -> dict:
    return {
        f"knowledgesources/{source}": {
            "name": source,
            "kind": "azureBlob",
            "description": "Shared synthetic document knowledge; operator-only, no per-user ACL.",
            "azureBlobParameters": {
                "connectionString": f"ResourceId={storage_id};",
                "containerName": container,
                "folderPath": "iq/",
                "isADLSGen2": False,
                "ingestionParameters": {
                    "contentExtractionMode": "minimal",
                    "disableImageVerbalization": True,
                    "ingestionSchedule": {"interval": "PT5M"},
                },
            },
        },
        f"knowledgebases/{name}": {
            "name": name,
            "description": "Shared native Blob knowledge for the synthetic close-out demo.",
            "knowledgeSources": [{"name": source}],
            "outputMode": "extractiveData",
            "retrievalReasoningEffort": {"kind": "low"},
            "retrievalInstructions": RETRIEVAL_INSTRUCTIONS,
            "models": [model],
        },
    }


class SearchSetup:
    def __init__(self, endpoint, credential):
        self.credential = credential
        self.client = httpx.Client(
            base_url=endpoint,
            params={"api-version": API_VERSION},
            timeout=90,
            follow_redirects=False,
        )

    def request(self, method, path, body=None):
        for attempt in range(7):
            token = self.credential.get_token("https://search.azure.com/.default")
            response = self.client.request(
                method, path, json=body, headers={"Authorization": "Bearer " + token.token}
            )
            if response.status_code not in {403, 429, 500, 502, 503, 504}:
                break
            if attempt == 6 or "PublicNetworkAccessDisabled" in response.text:
                break
            time.sleep(min(2**attempt, 16))
        if response.is_error:
            raise RuntimeError(
                f"Search {method} {path}: {response.status_code} {response.text[:2000]}"
            )
        return response.json() if response.content else {}

    def close(self):
        self.client.close()


def indexed_documents_match(rows: list[dict], uploads: list[dict]) -> bool:
    for upload in uploads:
        snippets = [row["snippet"] for row in rows if row["blob_url"] == upload["blobUrl"]]
        text = "\n".join(snippets)
        if not (
            snippets
            and upload["artifactId"] in text.splitlines()
            and f"Site: {upload['siteId']}" in text.splitlines()
            and f"Version: {upload['version']}" in text.splitlines()
        ):
            return False
    return True


def knowledge_settings(values: dict, sites: list[str], name: str = "close-out-demo"):
    if not values.get("knowledgeModel"):
        raise ValueError("Re-deploy knowledge infrastructure with its retrieval model binding")
    return KnowledgeSettings(
        searchEndpoint=values["searchEndpoint"],
        connectionName=values["connectionName"],
        knowledgeBase=values.get("knowledgeBaseName", name),
        sites=sorted(set(sites)),
        blobRoot=f"{values['storageEndpoint'].rstrip('/')}/{values['containerName']}/iq/",
    )


def configure_native(search, knowledge, values, source):
    for path, body in native_definitions(
        knowledge.knowledgeBase,
        source,
        values["storageResourceId"],
        values["containerName"],
        values["knowledgeModel"],
    ).items():
        search.request("PUT", path, body)
    configured = search.request("GET", f"knowledgesources/{source}")
    base = search.request("GET", f"knowledgebases/{knowledge.knowledgeBase}")
    if (
        configured["name"] != source
        or configured["kind"] != "azureBlob"
        or base["name"] != knowledge.knowledgeBase
        or not any(reference["name"] == source for reference in base["knowledgeSources"])
    ):
        raise ValueError("Search did not retain the expected native Blob IQ definitions")
    return configured["azureBlobParameters"]["createdResources"]


def provision(args):
    from azure.identity import AzureCliCredential

    outputs = json.loads(args.infra.read_text(encoding="utf-8-sig"))
    values = {key: value["value"] for key, value in outputs.items()}
    snapshot = load_seed(args.root / "seed" / "seed.json", args.root / "contracts")
    if not args.site or set(args.site) - set(snapshot.tables["Site"]):
        raise ValueError("Select known synthetic sites explicitly")
    knowledge = knowledge_settings(values, args.site, args.knowledge_base)
    with AzureCliCredential() as credential:
        search = SearchSetup(knowledge.searchEndpoint, credential)
        try:
            created = configure_native(search, knowledge, values, args.source_name)
        finally:
            search.close()
    write_json(args.output, knowledge.model_dump())
    write_json(
        args.output.with_name("knowledge-definition-receipt.json"),
        {
            "source": args.source_name,
            "knowledgeBase": knowledge.knowledgeBase,
            "kind": "azureBlob",
            "createdResources": created,
            "definitionsVerified": True,
            "documentVerification": "not-run",
        },
    )
    if args.definitions_only:
        print(json.dumps({"knowledge": str(args.output), "phase": "definitions"}))
        return

    from azure.storage.blob import BlobServiceClient, ContentSettings

    manifest = json.loads((args.assets / "assets-manifest.json").read_text(encoding="utf-8"))
    expected_hashes = {entry["path"]: entry["sha256"] for entry in manifest}
    uploads = []
    with (
        AzureCliCredential() as credential,
        BlobServiceClient(account_url=values["storageEndpoint"], credential=credential) as storage,
    ):
        container = storage.get_container_client(values["containerName"])
        for artifact in snapshot.rows("AcceptanceArtifact"):
            if (
                artifact["siteId"] not in knowledge.sites
                or artifact["artifactType"] == "photo_pack"
            ):
                continue
            path = asset_path(args.assets, artifact["blobUrl"]).with_suffix(".txt")
            data = document_text(snapshot, artifact).encode()
            digest = hashlib.sha256(data).hexdigest()
            relative = path.relative_to(args.assets.resolve()).as_posix()
            if expected_hashes.get(relative) != digest:
                raise ValueError("Seed document does not match the generated asset manifest")
            blob = container.get_blob_client(f"iq/{artifact['siteId']}/{path.name}")
            blob.upload_blob(
                data,
                overwrite=True,
                metadata={
                    "artifact_id": artifact["artifactId"],
                    "site_id": artifact["siteId"],
                    "version": artifact["version"],
                },
                content_settings=ContentSettings(content_type="text/plain"),
            )
            uploads.append(
                {
                    "artifactId": artifact["artifactId"],
                    "siteId": artifact["siteId"],
                    "version": artifact["version"],
                    "blobUrl": blob.url,
                    "sha256": digest,
                }
            )
        search = SearchSetup(knowledge.searchEndpoint, credential)
        try:
            source = args.source_name
            for _ in range(180):
                status = search.request("GET", f"knowledgesources/{source}/status")
                last = status.get("lastSynchronizationState") or {}
                if last.get("itemsUpdatesFailed", 0):
                    raise RuntimeError(
                        f"Native Blob synchronization failed: {json.dumps(last)[:2000]}"
                    )
                rows = search.request(
                    "POST",
                    f"indexes/{created['index']}/docs/search",
                    {"search": "*", "top": 1000, "select": "blob_url,snippet"},
                )["value"]
                if last.get("endTime") and indexed_documents_match(rows, uploads):
                    break
                time.sleep(5)
            else:
                raise TimeoutError("Native Blob ingestion did not expose the expected documents")
        finally:
            search.close()
    write_json(args.output, knowledge.model_dump())
    write_json(args.output.with_name("knowledge-upload-receipt.json"), uploads)
    write_json(
        args.output.with_name("native-blob-ingestion.json"),
        {"source": source, "kind": "azureBlob", "createdResources": created, "status": status},
    )
    print(
        json.dumps({"knowledge": str(args.output), "uploadedFiles": len(uploads), "source": source})
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--infra", type=Path, required=True)
    parser.add_argument("--site", action="append", required=True)
    parser.add_argument("--knowledge-base", default="close-out-demo")
    parser.add_argument("--source-name", default="close-out-documents")
    parser.add_argument(
        "--definitions-only",
        action="store_true",
        help="Publish IQ definitions without document uploads or ingestion checks",
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--assets", type=Path, default=Path(".local") / "demo")
    parser.add_argument(
        "--output", type=Path, default=Path(".local") / "foundry" / "knowledge.json"
    )
    args = parser.parse_args()
    args.assets = args.assets.resolve()
    provision(args)


if __name__ == "__main__":
    main()

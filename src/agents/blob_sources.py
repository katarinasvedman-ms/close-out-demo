"""Version-pinned synthetic Blob evidence; no local file or signed-in-user fallback."""

import copy
import hashlib
from pathlib import Path
from urllib.parse import unquote, urlsplit

from agents.sources import FACT_TABLES, Sources
from shared.contract_validation import load_validators, select_validator
from shared.seed import KEYS

MAX_EVIDENCE_BYTES = 20 * 1024 * 1024
MAX_BATCH_BYTES = 40 * 1024 * 1024


class BlobSources(Sources):
    def __init__(self, manifest: dict, contracts: Path, container_url: str, download):
        self.validators = load_validators(contracts)
        self.validators["uploads/evidence-batch.schema.json"].validate(manifest)
        self.manifest = copy.deepcopy(manifest)
        self.facts = copy.deepcopy(manifest["facts"])
        self.download = download
        self._cache = {}
        self._bytes = 0
        self.container_url = container_url.rstrip("/") + "/"
        origin = urlsplit(self.container_url)
        if (
            origin.scheme != "https"
            or not origin.hostname
            or not origin.hostname.endswith(".blob.core.windows.net")
            or origin.username
            or origin.password
            or origin.port
            or origin.query
            or origin.fragment
            or len(origin.path.strip("/").split("/")) != 1
        ):
            raise ValueError("An exact HTTPS Azure Blob input container is required")
        if (
            self.facts["caseId"] != manifest["caseId"]
            or self.facts["site"]["siteId"] != manifest["siteId"]
            or self.facts["project"]["programCode"] != manifest["program"]
            or self.facts["site"]["projectId"] != self.facts["project"]["projectId"]
        ):
            raise ValueError("Batch and baseline identities disagree")
        self.records = {}
        for kind, rows in {
            "Site": [self.facts["site"]],
            "CustomerProject": [self.facts["project"]],
            **{kind: self.facts[field] for kind, field in FACT_TABLES.items()},
        }.items():
            for row in rows:
                select_validator(self.validators, f"models.schema.json#/$defs/{kind}").validate(row)
                if "siteId" in row and row["siteId"] != manifest["siteId"]:
                    raise PermissionError("Baseline contains another site's evidence")
                key = (kind, row[KEYS[kind]])
                if key in self.records:
                    raise ValueError("Duplicate baseline record")
                self.records[key] = row
        self.members = {}
        urls = set()
        for member in manifest["members"]:
            identifier = member["evidenceId"]
            kind = "Photo" if member["kind"] == "photo" else "AcceptanceArtifact"
            row = member["metadata"]
            if (
                identifier in self.members
                or row.get(KEYS[kind]) != identifier
                or row["siteId"] != manifest["siteId"]
                or row["blobUrl"] != member["blob"]["url"]
                or row.get("caseId", manifest["caseId"]) != manifest["caseId"]
            ):
                raise ValueError("Duplicate or mismatched batch member")
            if member["blob"]["url"] in urls:
                raise ValueError("Distinct evidence cannot share an input blob")
            urls.add(member["blob"]["url"])
            for reference in (member["blob"], member.get("text")):
                if reference:
                    self._path(reference)
            if member.get("text") and (
                member["kind"] != "document" or member["text"]["mediaType"] != "text/plain"
            ):
                raise ValueError("Only documents can declare text counterparts")
            if kind == "AcceptanceArtifact" and not any(
                requirement["requirementId"] == row["requirementId"]
                and requirement["projectId"] == self.facts["project"]["projectId"]
                and requirement["artifactType"] == row["artifactType"]
                for requirement in self.facts["requirements"]
            ):
                raise ValueError("Document does not belong to the registered program requirement")
            if kind == "Photo" and ("AcceptanceArtifact", row["artifactId"]) not in self.records:
                raise ValueError("Photo pack is outside the registered baseline")
            self.members[identifier] = member
            self.records[kind, identifier] = copy.deepcopy(row)
        self.facts["mode"] = "registered-blob-evidence"

    def _path(self, reference: dict) -> str:
        url = reference["url"]
        if not url.startswith(self.container_url):
            raise PermissionError("Evidence URL is outside the configured input container")
        path = unquote(url.removeprefix(self.container_url))
        if (
            not path
            or "\\" in path
            or "%" in path
            or any(part in {"", ".", ".."} for part in path.split("/"))
        ):
            raise ValueError("Evidence URL has an ambiguous Blob path")
        return path

    def read_asset(self, row: dict, *, text: bool = False) -> tuple[bytes, dict]:
        identifier = row.get("photoId", row.get("artifactId"))
        member = self.members.get(identifier)
        if member is None or member["metadata"] != row:
            raise PermissionError("Evidence is not an exact registered batch member")
        reference = member.get("text") if text else member["blob"]
        if reference is None:
            raise ValueError("The document has no registered text counterpart")
        path = self._path(reference)
        cache_key = (path, reference["etag"], reference["sha256"])
        if cache_key not in self._cache:
            data = self.download(path, reference, MAX_EVIDENCE_BYTES)
            self._bytes += len(data)
            if self._bytes > MAX_BATCH_BYTES:
                raise ValueError("Batch exceeds the permitted total attachment size")
            self._cache[cache_key] = data
        data = self._cache[cache_key]
        if (
            len(data) > MAX_EVIDENCE_BYTES
            or hashlib.sha256(data).hexdigest() != reference["sha256"]
        ):
            raise ValueError("Blob bytes do not match the pinned evidence hash/size")
        return data, {"id": identifier, "path": path, **reference}

    def documents(self) -> list[dict]:
        documents = []
        for member in self.members.values():
            if member["kind"] != "document" or "text" not in member:
                continue
            text = self.read_asset(member["metadata"], text=True)[0].decode("utf-8")
            lines = text.splitlines()
            if (
                member["evidenceId"] not in lines
                or [line for line in lines if line.startswith("Site: ")]
                != [f"Site: {self.manifest['siteId']}"]
                or [line for line in lines if line.startswith("Version: ")]
                != [f"Version: {member['metadata']['version']}"]
            ):
                raise ValueError("Document counterpart does not match the registered site/version")
            documents.append(
                {
                    "artifactId": member["evidenceId"],
                    "version": member["metadata"]["version"],
                    "text": text,
                }
            )
        return documents

    def validation_inputs(self, evidence_ids: list[str]):
        packets, attachments = super().validation_inputs(evidence_ids)
        for packet in packets:
            if any(item["rfdsCheck"] for item in packet["checklist"]) and not packet["rfds"]:
                raise ValueError(
                    "Azimuth evidence requires a registered versioned RFDS counterpart"
                )
        return packets, attachments


def azure_downloader(container):
    from azure.core import MatchConditions

    def download(path: str, reference: dict, limit: int) -> bytes:
        blob = container.get_blob_client(path, version_id=reference.get("versionId"))
        options = {
            "etag": reference["etag"],
            "match_condition": MatchConditions.IfNotModified,
        }
        properties = blob.get_blob_properties(**options)
        if properties.size > limit:
            raise ValueError("Evidence exceeds the permitted attachment size")
        return blob.download_blob(**options).readall()

    return download

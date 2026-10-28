"""Verify native Blob references and keep citations relevant to the current case."""

import hashlib
import json
from pathlib import PurePosixPath
from urllib.parse import unquote


def _objects(value):
    if isinstance(value, dict):
        yield value
        if value.get("kind") == "reference":
            return
        for key, child in value.items():
            if key not in {"snippet", "sourceData"} and not (
                key == "content" and isinstance(child, str)
            ):
                yield from _objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _objects(child)
    elif isinstance(value, str) and value.lstrip().startswith(("{", "[")):
        try:
            decoded = json.loads(value)
        except (ValueError, RecursionError):
            return
        yield from _objects(decoded)


def retrieval_receipts(response, sources, knowledge) -> list[dict]:
    receipts = []
    for message in response.messages:
        for content in message.contents:
            raw = content.raw_representation
            if (
                content.type != "mcp_server_tool_result"
                or getattr(raw, "name", None) != "knowledge_base_retrieve"
                or getattr(raw, "server_label", None) != "documents"
            ):
                continue
            if getattr(raw, "error", None) or not isinstance(raw.output, str):
                raise ValueError("Foundry IQ retrieval failed")
            documents = {}
            ignored = set()
            for reference in _objects(raw.output):
                if reference.get("kind") == "reference":
                    row = reference.get("sourceData", {})
                    uri = reference.get("uri")
                elif (
                    isinstance(reference.get("knowledgeSourceIndex"), int)
                    and reference.get("metadata") == "application/json"
                    and isinstance(reference.get("content"), str)
                ):
                    # Foundry normalizes native references into document records.
                    try:
                        row = json.loads(reference["content"])
                    except ValueError:
                        continue
                    uri = reference.get("url")
                else:
                    continue
                if not isinstance(row, dict):
                    continue
                url, text = row.get("blob_url"), row.get("snippet")
                if not isinstance(url, str) or not isinstance(text, str):
                    continue
                if not url.startswith(knowledge.blobRoot) or uri != url:
                    raise ValueError("Foundry IQ reference has an unexpected Blob origin")
                relative = unquote(url.removeprefix(knowledge.blobRoot))
                path = PurePosixPath(relative)
                if len(path.parts) != 2 or path.suffix != ".txt" or ".." in path.parts:
                    raise ValueError("Foundry IQ reference has an unexpected document path")
                site, identifier = path.parts[0], path.stem
                if site != sources.facts["site"]["siteId"]:
                    ignored.add(url)
                    continue
                record = sources.records.get(("AcceptanceArtifact", identifier))
                lines = text.splitlines()
                if (
                    not record
                    or identifier not in lines
                    or f"Site: {site}" not in lines
                    or [line for line in lines if line.startswith("Version: ")]
                    != [f"Version: {record['version']}"]
                ):
                    raise ValueError("Foundry IQ reference does not match the case site/version")
                documents[identifier] = {
                    "artifactId": identifier,
                    "siteId": site,
                    "version": record["version"],
                    "blobUrl": url,
                }
            if not documents:
                raise ValueError(
                    "Foundry IQ returned no verifiable references for this site/version"
                )
            receipts.append(
                {
                    "callId": raw.id,
                    "tool": raw.name,
                    "outputSha256": hashlib.sha256(raw.output.encode()).hexdigest(),
                    "documents": list(documents.values()),
                    "ignoredOtherSiteDocuments": len(ignored),
                }
            )
    if not receipts:
        raise ValueError("The coordinator did not execute Foundry IQ retrieval")
    return receipts


def validate_document_citations(draft, sources, receipts) -> None:
    retrieved = {row["artifactId"] for receipt in receipts for row in receipt["documents"]}
    for gap in draft.gaps:
        cited = {ref.id for ref in gap.evidenceRefs if ref.objectType == "AcceptanceArtifact"}
        if cited - retrieved:
            raise ValueError("A document citation was not returned by Foundry IQ")
        photo_gap = next(
            (
                row
                for row in sources.facts["photoGaps"]
                if row["checklistItemId"] == gap.conditionId
            ),
            None,
        )
        checklist = sources.records.get(("ChecklistItem", gap.conditionId), {})
        if photo_gap and checklist.get("rfdsCheck"):
            rfds_ids = {
                row["artifactId"]
                for row in sources.facts["artifacts"]
                if row["artifactType"] == "rfds"
            }
            if not cited & rfds_ids:
                raise ValueError("An azimuth gap must cite its retrieved RFDS")

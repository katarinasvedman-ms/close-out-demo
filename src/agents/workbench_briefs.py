"""Read immutable, scoped Markdown briefs; never expose raw response traces."""

import re

MAX_BRIEF_BYTES = 128_000
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
BRIEF_NAME = re.compile(
    rf"coordinator/(?P<run>{UUID})/(?P<case>{UUID})/(?P<readiness>Blocked|Ready)/"
    rf"{UUID}/(?P<workflow>[A-Za-z0-9_-]{{1,128}})\.md"
)


class BriefReader:
    def __init__(self, container):
        self.container = container

    def summary(self, blob, scope: dict) -> dict | None:
        match = BRIEF_NAME.fullmatch(blob.name)
        if match is None or match["case"] != scope["caseId"]:
            return None
        metadata = blob.metadata or {}
        if (
            metadata.get("briefversion") != "1"
            or metadata.get("siteid") != scope["siteId"]
            or metadata.get("caseid") != scope["caseId"]
            or metadata.get("program") != scope["program"]
            or metadata.get("readiness") != match["readiness"]
            or metadata.get("runid") != match["run"]
            or metadata.get("workflowrunid") != match["workflow"]
            or not re.fullmatch(r"[a-f0-9]{64}", metadata.get("factsrevision", ""))
            or metadata.get("sourceref")
            != (f"fabric:headless:{match['run']}:{scope['siteId']}:{metadata.get('factsrevision')}")
            or not re.fullmatch(r"resp_[A-Za-z0-9_-]{1,128}", metadata.get("responseid", ""))
            or not re.fullmatch(r"[1-9][0-9]*", metadata.get("agentversion", ""))
        ):
            raise RuntimeError("A saved brief has invalid scope or publication metadata")
        if blob.size > MAX_BRIEF_BYTES or blob.last_modified.tzinfo is None:
            raise RuntimeError("A saved brief has an invalid size or timestamp")
        return {
            "id": blob.name,
            "siteId": scope["siteId"],
            "readiness": match["readiness"],
            "savedAt": blob.last_modified.isoformat(),
        }

    def list(self, scope: dict) -> list[dict]:
        rows = []
        for blob in self.container.list_blobs(
            name_starts_with="coordinator/", include=["metadata"]
        ):
            summary = self.summary(blob, scope)
            if summary is not None:
                rows.append(summary)
        return sorted(rows, key=lambda row: (row["savedAt"], row["id"]), reverse=True)

    def read(self, identifier: str, scope: dict) -> dict:
        match = BRIEF_NAME.fullmatch(identifier)
        if match is None or match["case"] != scope["caseId"]:
            raise ValueError("Select a readable brief belonging to this case")
        summary = next((row for row in self.list(scope) if row["id"] == identifier), None)
        if summary is None:
            raise FileNotFoundError("The selected brief is unavailable")
        data = (
            self.container.get_blob_client(identifier)
            .download_blob(offset=0, length=MAX_BRIEF_BYTES + 1)
            .readall()
        )
        if len(data) > MAX_BRIEF_BYTES:
            raise RuntimeError("The saved brief exceeds the supported size")
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise RuntimeError("The saved brief is not UTF-8 text") from error
        if not content.startswith("# Close-out review") or scope["siteId"] not in content:
            raise RuntimeError("The saved brief has an invalid document or site")
        return {**summary, "markdown": content}

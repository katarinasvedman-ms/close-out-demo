"""Runs inside a Fabric notebook; no Azure worker computes this projection."""

import base64
import copy
import json
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import UUID

from agents.flow_projection import event_time, project_run
from shared.headless import canonical, digest

EVENTS_TABLE = "CloseoutFlowEventsV02"
FACTS_TABLE = "CloseoutFlowFactsV02"
MAX_EVENTS = 100_000


def validate_target(config):
    for key in ("workspaceId", "databaseId"):
        UUID(config[key])
    for key, suffix in (
        ("kustoEndpoint", ".kusto.fabric.microsoft.com"),
        ("storageEndpoint", ".blob.core.windows.net"),
    ):
        parsed = urlsplit(config[key])
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or not parsed.hostname.endswith(suffix)
            or parsed.path.strip("/")
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
        ):
            raise ValueError(f"Expected a credential-free HTTPS {key}")


def token_identity(token):
    """Audit the platform-provided identity, never retain the token."""
    segment = token.split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))
    return {key: claims.get(key) for key in ("oid", "tid", "idtyp")}


class NotebookTransport:
    def __init__(self, config, get_token, session):
        validate_target(config)
        self.config = config
        self.get_token = get_token
        self.session = session

    def blob(self, path="", params=None):
        response = self.session.get(
            self.config["storageEndpoint"].rstrip("/") + "/ledger/" + path,
            params=params,
            headers={
                "Authorization": "Bearer " + self.get_token("storage"),
                "x-ms-version": "2023-11-03",
            },
            timeout=90,
            allow_redirects=False,
        )
        response.raise_for_status()
        return response

    def manifests(self):
        result, marker, seen = [], "", set()
        for _ in range(100):
            page = ET.fromstring(
                self.blob(
                    params={
                        "restype": "container",
                        "comp": "list",
                        "prefix": "registrations/",
                        "marker": marker,
                    }
                ).content
            )
            for entry in page.findall("./Blobs/Blob"):
                name = entry.findtext("Name", "")
                match = re.fullmatch(
                    r"registrations/([0-9a-f-]{36})/([0-9a-f-]{36})/manifest\.json", name
                )
                if not match:
                    continue
                run, batch = (str(UUID(value)) for value in match.groups())
                manifest = self.blob(name).json()
                if (manifest["runId"], manifest["batchId"]) != (run, batch):
                    raise ValueError("Blob registration path disagrees with manifest identity")
                result.append(manifest)
            marker = page.findtext("NextMarker", "")
            if not marker:
                return result
            if marker in seen:
                raise ValueError("Repeated Blob registration continuation marker")
            seen.add(marker)
        raise ValueError("Too many registration pages for this bounded test projector")

    def kql(self, query, *, write=False):
        response = self.session.post(
            self.config["kustoEndpoint"].rstrip("/")
            + ("/v1/rest/mgmt" if write else "/v1/rest/query"),
            headers={"Authorization": "Bearer " + self.get_token("kusto")},
            json={"db": self.config["databaseId"], "csl": query},
            timeout=180,
            allow_redirects=False,
        )
        response.raise_for_status()
        body = response.json()
        if body.get("error"):
            raise RuntimeError(json.dumps(body["error"]))
        table = body["Tables"][0]
        columns = [column["ColumnName"] for column in table["Columns"]]
        return [dict(zip(columns, row, strict=True)) for row in table["Rows"]]


def verify_snapshot(snapshot):
    revision = digest({"facts": snapshot["facts"], "eventIds": snapshot["eventIds"]})
    if snapshot["factsRevision"] != revision:
        raise ValueError("Fabric facts digest does not bind the exact facts and event IDs")
    if snapshot["facts"]["caseId"] != snapshot["caseId"]:
        raise ValueError("Snapshot facts belong to a different case")
    if snapshot["facts"]["site"]["siteId"] != snapshot["siteId"]:
        raise ValueError("Snapshot facts belong to a different site")
    if snapshot["facts"]["project"]["programCode"] != snapshot["program"]:
        raise ValueError("Snapshot facts belong to a different program")


def kusto_value(value):
    """Keep JSON strings as strings: dynamic(JSON) silently coerces ISO timestamps."""
    if isinstance(value, dict):
        if not value:
            return "dynamic({})"
        return (
            "bag_pack("
            + ", ".join(
                part for key, item in value.items() for part in (json.dumps(key), kusto_value(item))
            )
            + ")"
        )
    if isinstance(value, list):
        return (
            "pack_array(" + ", ".join(kusto_value(item) for item in value) + ")"
            if value
            else "dynamic([])"
        )
    if value is None:
        return "dynamic(null)"
    return canonical(value).decode()


def restore_upload_metadata(manifests, events):
    """Undo only verified Eventhouse date coercion, retaining manifest-pinned metadata."""
    members = {
        (manifest["batchId"], member["evidenceId"]): member["metadata"]
        for manifest in manifests
        for member in manifest["members"]
    }
    restored = copy.deepcopy(events)
    for event in restored:
        if event["eventType"] not in {"PhotoUploaded", "ArtifactUploaded"}:
            continue
        data = event["data"]
        identifier = data.get("photoId", data.get("artifactId"))
        expected = members.get((event.get("batchId"), identifier))
        if expected is None:
            continue
        timestamp = "capturedAt" if event["eventType"] == "PhotoUploaded" else "submittedAt"
        comparison = copy.deepcopy(data)
        if timestamp in comparison and timestamp in expected:
            actual_time = datetime.fromisoformat(comparison[timestamp].replace("Z", "+00:00"))
            expected_time = datetime.fromisoformat(expected[timestamp].replace("Z", "+00:00"))
            if actual_time == expected_time:
                comparison[timestamp] = expected[timestamp]
        if comparison != expected:
            raise ValueError("Eventhouse upload metadata differs from its pinned Blob manifest")
        event["data"] = copy.deepcopy(expected)
    return restored


def persist_snapshot(transport, snapshot):
    """Append once, then verify actual stored facts before exposing readiness."""
    verify_snapshot(snapshot)
    run = str(UUID(snapshot["runId"]))
    revision = snapshot["factsRevision"]
    query = (
        f"{FACTS_TABLE} | where tostring(snapshot.runId) == '{run}' "
        f"and tostring(snapshot.factsRevision) == '{revision}' | project snapshot"
    )

    def readback():
        rows = transport.kql(query)
        for row in rows:
            stored = row["snapshot"]
            if isinstance(stored, str):
                stored = json.loads(stored)
            verify_snapshot(stored)
            for field in ("caseId", "siteId", "program", "sourceRef", "evaluatedAt"):
                if stored[field] != snapshot[field]:
                    raise ValueError("Conflicting immutable Fabric snapshot identity")
        return rows

    if readback():
        return False
    tag = f"closeout-flow:{run}:{revision}"
    properties = (
        f"tags={json.dumps(json.dumps(['ingest-by:' + tag]))}, "
        f"ingestIfNotExists={json.dumps(json.dumps([tag]))}"
    )
    try:
        transport.kql(
            f".append {FACTS_TABLE} with ({properties}) <| print snapshot={kusto_value(snapshot)}",
            write=True,
        )
    except Exception:
        # A concurrent notebook can win the ingest-by race. Only exact readback is success.
        if not readback():
            raise
        return False
    if not readback():
        raise RuntimeError("Fabric ingestion returned without a readable immutable snapshot")
    return True


def run_projection(transport, contracts, *, bundle_sha256, identity=None):
    grouped = defaultdict(list)
    for manifest in transport.manifests():
        grouped[manifest["runId"]].append(manifest)
    receipts = []
    for run, manifests in sorted(grouped.items()):
        run = str(UUID(run))
        rows = transport.kql(
            f"{EVENTS_TABLE} | where tostring(event.runId) == '{run}' "
            f"| take {MAX_EVENTS + 1} | project event"
        )
        if len(rows) > MAX_EVENTS:
            raise ValueError("Run exceeds bounded projector event limit; no partial snapshot")
        events = [
            json.loads(row["event"]) if isinstance(row["event"], str) else row["event"]
            for row in rows
        ]
        if not events:
            continue
        events = restore_upload_metadata(manifests, events)
        boundaries = sorted(
            {
                event_time(event)
                for event in events
                if event["eventType"]
                in {
                    "EvidenceBatchCompleted",
                    "TicketChanged",
                    "WorkOrderChanged",
                    "MilestoneChanged",
                }
            }
        )
        for boundary in boundaries:
            registered = [
                manifest
                for manifest in manifests
                if datetime.fromisoformat(manifest["registeredAt"].replace("Z", "+00:00"))
                <= boundary
            ]
            if not registered:
                continue
            prefix = [event for event in events if event_time(event) <= boundary]
            snapshot = project_run(registered, prefix, contracts)
            if snapshot is None:
                receipts.append({"runId": run, "status": "AwaitingCompleteBatch"})
                continue
            snapshot["projectedAt"] = datetime.now(UTC).isoformat()
            snapshot["projection"] = {
                "engine": "FabricNotebook",
                "bundleSha256": bundle_sha256,
                "identity": identity,
                "manifestTransport": "private-blob-platform-token",
            }
            created = persist_snapshot(transport, snapshot)
            receipts.append(
                {
                    "runId": run,
                    "factsRevision": snapshot["factsRevision"],
                    "readiness": snapshot["facts"]["readiness"],
                    "status": "Appended" if created else "AlreadyPresent",
                }
            )
    return {"projection": "FabricNotebook", "runs": receipts}


def readiness_query():
    """Only persisted transitions, partitioned by test run and case, not each evaluation."""
    return f"""
{FACTS_TABLE}
| extend runId=tostring(snapshot.runId), caseId=tostring(snapshot.caseId),
         siteId=tostring(snapshot.siteId), program=tostring(snapshot.program),
         factsRevision=tostring(snapshot.factsRevision),
         projectedAt=todatetime(snapshot.projectedAt), evaluatedAt=todatetime(snapshot.evaluatedAt),
         readiness=tostring(snapshot.facts.readiness),
         readinessMissing=snapshot.facts.readinessMissing,
         sourceRef=tostring(snapshot.sourceRef)
| summarize take_any(*) by runId, caseId, siteId, program, factsRevision
| sort by runId asc, caseId asc, siteId asc, program asc, evaluatedAt asc, factsRevision asc
| serialize
| extend sameObject=runId == prev(runId) and caseId == prev(caseId)
                    and siteId == prev(siteId) and program == prev(program)
| extend previousReadiness=iff(sameObject, prev(readiness), "NotReady")
| where readiness != previousReadiness and readiness in ("Blocked", "Ready")
| project runId, caseId, siteId, program, factsRevision, projectedAt, evaluatedAt,
          readiness, readinessMissing, sourceRef
""".strip()


def notebook_main(config, contracts, bundle_sha256):
    import notebookutils
    import requests

    transport = NotebookTransport(config, notebookutils.credentials.getToken, requests.Session())
    identity = token_identity(notebookutils.credentials.getToken("storage"))
    result = run_projection(transport, contracts, bundle_sha256=bundle_sha256, identity=identity)
    notebookutils.notebook.exit(json.dumps(result))

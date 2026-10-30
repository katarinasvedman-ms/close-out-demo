"""Verify Fabric-owned live-test revisions without supplying KQL facts to the agent."""

import copy
import json
from datetime import datetime

import httpx

from agents.fabric_setup import primary_rows
from agents.sources import FACT_TABLES, Sources
from shared.contract_validation import load_validators, select_validator
from shared.headless import digest, validate_callback
from shared.seed import KEYS


class SnapshotSources(Sources):
    def __init__(self, snapshot: dict, callback: dict, contracts):
        self.validators = load_validators(contracts)
        validate_callback(callback, self.validators)
        data = callback["data"]
        if any(
            snapshot[key] != value
            for key, value in {
                "schemaVersion": "0.2.0",
                "runId": data["runId"],
                "caseId": callback["caseId"],
                "siteId": callback["siteId"],
                "program": data["program"],
                "factsRevision": data["factsRevision"],
                "sourceRef": data["sourceRef"],
            }.items()
        ):
            raise ValueError("Stale or mismatched Fabric callback revision")
        expected = digest({"facts": snapshot["facts"], "eventIds": sorted(snapshot["eventIds"])})
        if expected != snapshot["factsRevision"] or snapshot["sourceRef"] != (
            f"fabric:headless:{data['runId']}:{callback['siteId']}:{expected}"
        ):
            raise ValueError("Fabric revision does not bind the exact immutable facts")
        self.facts = copy.deepcopy(snapshot["facts"])
        if (
            self.facts["caseId"] != callback["caseId"]
            or self.facts["site"]["siteId"] != callback["siteId"]
            or self.facts["project"]["programCode"] != data["program"]
            or self.facts["readiness"] != data["signal"]["readiness"]
            or self.facts["readinessMissing"] != data["signal"]["readinessMissing"]
            or datetime.fromisoformat(snapshot["evaluatedAt"])
            != datetime.fromisoformat(data["signal"]["evaluatedAt"])
            or self.facts["validationSource"] != "mixed-seed-and-live"
        ):
            raise ValueError("Fabric facts and callback state disagree")
        self.records = {}
        for kind, rows in {
            "Site": [self.facts["site"]],
            "CustomerProject": [self.facts["project"]],
            **{kind: self.facts[field] for kind, field in FACT_TABLES.items()},
        }.items():
            for row in rows:
                select_validator(self.validators, f"models.schema.json#/$defs/{kind}").validate(row)
                if "siteId" in row and row["siteId"] != callback["siteId"]:
                    raise ValueError("Fabric record belongs to another site")
                key = (kind, row[KEYS[kind]])
                if key in self.records:
                    raise ValueError("Duplicate Fabric record")
                self.records[key] = row
        self.facts["mode"] = "fabric-live-test"
        self.facts["fabricSource"] = {
            key: snapshot[key]
            for key in ("sourceRef", "factsRevision", "schemaVersion", "evaluatedAt", "runId")
        }

    def documents(self):
        raise RuntimeError("Only native document IQ is allowed for this coordinator")


async def coordinate(
    callback: dict, settings, contracts, ledger, result_ledger, read_latest, infer
):
    validators = load_validators(contracts)
    key = validate_callback(callback, validators)
    if not settings.fabric or not settings.knowledge:
        raise ValueError("The workflow coordinator requires both native tools; no stub fallback")
    claim, state = ledger.claim(
        key, {"callback": callback, "agent": settings.closeout.model_dump()}
    )
    if claim is None:
        return {"status": "Completed", "resultKey": key}
    try:
        sources = SnapshotSources(read_latest(callback), callback, contracts)
        if state["result"] is None:
            receipt = await infer("closeout", settings, sources)
            if (
                not receipt.get("responseId")
                or not receipt.get("fabricRetrieval")
                or not receipt.get("knowledgeRetrieval")
            ):
                raise ValueError("Actual native Fabric and document IQ receipts are required")
            # The model can finish after a new observation; never publish that stale draft.
            SnapshotSources(read_latest(callback), callback, contracts)
            state["result"] = receipt
            claim.save()
        result_ledger.register(key, state["result"])
        claim.finish("Completed")
        return {"status": "Completed", "resultKey": key}
    except Exception:
        claim.finish("Failed", "CoordinatorFailed")
        raise


def snapshot_reader(settings, credential):
    def read_latest(callback):
        data = callback["data"]
        filters = " and ".join(
            f"tostring(snapshot.{key}) == {json.dumps(value)}"
            for key, value in {
                "runId": data["runId"],
                "caseId": callback["caseId"],
                "siteId": callback["siteId"],
                "program": data["program"],
            }.items()
        )
        query = (
            f"CloseoutFlowFactsV02 | where {filters} "
            "| extend evaluatedAt = todatetime(snapshot.evaluatedAt), "
            "projectedAt = todatetime(snapshot.projectedAt) "
            # Late events can change a revision without moving the source-event watermark.
            "| sort by evaluatedAt desc, projectedAt desc | take 1 | project snapshot"
        )
        token = credential.get_token("https://api.kusto.windows.net/.default")
        with httpx.Client(timeout=20, follow_redirects=False) as client:
            response = client.post(
                settings.kustoEndpoint + "/v1/rest/query",
                headers={"Authorization": "Bearer " + token.token},
                json={"db": settings.databaseId, "csl": query},
            )
            response.raise_for_status()
            rows = primary_rows(response.json())
        if len(rows) != 1:
            raise ValueError("No current live-test Fabric snapshot; no seeded fallback")
        value = rows[0]["snapshot"]
        return json.loads(value) if isinstance(value, str) else value

    return read_latest

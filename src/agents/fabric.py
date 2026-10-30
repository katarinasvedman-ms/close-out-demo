"""Read external Fabric fixture snapshots for verification, never as agent tool substitutes."""

import copy
import hashlib
import json
import re
from pathlib import Path

from agents.definitions import FabricSettings
from agents.fabric_setup import FACTS_TABLE, FabricSetup
from agents.sources import FACT_TABLES
from shared.contract_validation import load_validators, select_validator


def verified_facts(
    rows: list[dict], site: str, program: str, scenario: str, contracts: Path
) -> dict:
    if len(rows) != 1:
        raise ValueError("Expected exactly one Fabric fixture row; no local fallback")
    row = rows[0]
    facts = copy.deepcopy(row["facts"])
    if (
        row["schemaVersion"] != "0.1.0"
        or (row["siteId"], row["program"], row["scenario"]) != (site, program, scenario)
        or facts["site"]["siteId"] != site
        or facts["project"]["programCode"] != program
        or facts["site"]["projectId"] != facts["project"]["projectId"]
        or facts["scenario"] != scenario
        or facts["caseId"] != row["caseId"]
        or facts["validationSource"] != "seed"
        or not re.fullmatch(r"[a-f0-9]{64}", row["seedHash"])
        or row["sourceRef"] != f"fabric:contract-v01:{scenario}:{site}:{row['seedHash']}"
        or any(
            facts[field] != row[field]
            for field in ("readiness", "readinessMissing", "constructionComplete")
        )
    ):
        raise ValueError("Fabric fixture identity, provenance or computed state disagrees")
    validators = load_validators(contracts)
    records = {"Site": [facts["site"]], "CustomerProject": [facts["project"]]}
    records.update({kind: facts[field] for kind, field in FACT_TABLES.items()})
    for kind, values in records.items():
        validator = select_validator(validators, f"models.schema.json#/$defs/{kind}")
        for value in values:
            validator.validate(value)
            if "siteId" in value and value["siteId"] != site:
                raise ValueError("Fabric returned a record from another site")
    facts["photoGaps"] = []
    for item in facts["checklist"]:
        if item["checklistItemId"] not in facts["readinessMissing"]:
            continue
        packs = {
            artifact["artifactId"]
            for artifact in facts["artifacts"]
            if artifact["requirementId"] == item["requirementId"]
        }
        facts["photoGaps"].append(
            {
                "checklistItemId": item["checklistItemId"],
                "itemCode": item["itemCode"],
                "photoIds": [
                    photo["photoId"]
                    for photo in facts["photos"]
                    if photo["isCurrent"]
                    and photo["artifactId"] in packs
                    and photo["checklistItemCode"] == item["itemCode"]
                ],
            }
        )
    facts["mode"] = "fabric-test"
    facts["fabricSource"] = {
        key: row[key]
        for key in ("sourceRef", "seedHash", "schemaVersion", "evaluatedAt", "scenario")
    }
    return facts


def read_facts(
    settings: FabricSettings, site: str, program: str, scenario: str, contracts: Path
) -> dict:
    from azure.identity import AzureCliCredential

    filters = " and ".join(
        f"{field} == {json.dumps(value)}"
        for field, value in (("siteId", site), ("program", program), ("scenario", scenario))
    )
    with AzureCliCredential() as credential:
        client = FabricSetup(credential, settings.model_dump(), Path(".local") / "foundry")
        try:
            rows = client.kql(f"{FACTS_TABLE} | where {filters} | take 2")
        finally:
            client.client.close()
    return verified_facts(rows, site, program, scenario, contracts)


def retrieval_receipts(response, sources, settings: FabricSettings) -> list[dict]:
    receipts = []
    expected = sources.facts["fabricSource"]
    for message in response.messages:
        for content in message.contents:
            raw = content.raw_representation
            if (
                content.type != "mcp_server_tool_result"
                or getattr(raw, "server_label", None) != "fabric-facts"
            ):
                continue
            if (
                getattr(raw, "name", None) != settings.toolName
                or getattr(raw, "error", None)
                or not isinstance(raw.output, str)
                or expected["sourceRef"] not in raw.output
            ):
                raise ValueError("Native Fabric retrieval failed or lacks matching provenance")
            arguments = json.loads(raw.arguments)
            question = arguments.get("userQuestion", "")
            scope = [
                sources.facts["site"]["siteId"],
                sources.facts["project"]["programCode"],
                *(
                    [expected["runId"], expected["factsRevision"]]
                    if sources.facts["mode"] == "fabric-live-test"
                    else [sources.facts["scenario"]]
                ),
            ]
            if not isinstance(question, str) or any(value not in question for value in scope):
                raise ValueError("Native Fabric question omitted the site, program or scenario")
            receipts.append(
                {
                    "callId": raw.id,
                    "tool": raw.name,
                    "outputSha256": hashlib.sha256(raw.output.encode()).hexdigest(),
                    **expected,
                }
            )
    if not receipts:
        raise ValueError("The coordinator did not execute native Fabric retrieval")
    return receipts

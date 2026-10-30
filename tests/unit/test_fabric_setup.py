import base64
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from agents.definitions import FabricSettings, KnowledgeSettings, definition
from agents.fabric import retrieval_receipts, verified_facts
from agents.fabric_setup import (
    FACTS_TABLE,
    FabricSetup,
    agent_definition,
    fixture_rows,
    live_agent_definition,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def rows():
    return fixture_rows(ROOT)


def select(rows, scenario, site="SE-STO-0042"):
    return next(row for row in rows if row["scenario"] == scenario and row["siteId"] == site)


def test_fabric_export_preserves_exact_contract_evidence_and_excludes_computed_outcomes(rows):
    row = select(rows, "initial-blocked")
    facts = row["facts"]
    assert row["expected"]["readinessMissing"] == ["CHK-A-AZ-B", "CHK-A-GROUNDING", "PL-0042-01"]
    assert row["expected"]["readiness"] == "Blocked"
    assert "readiness" not in facts and "readinessMissing" not in facts
    assert "readiness" not in facts["site"] and "readinessEvaluatedAt" not in facts["site"]
    assert "mode" not in facts
    assert facts["validationSource"] == "seed"
    assert len(facts["photos"]) == 20
    photo = next(row for row in facts["photos"] if row["photoId"] == "PHOTO-20")
    assert photo["checklistItemCode"] == "azimuth_sector_b"
    result = next(row for row in facts["validations"] if row.get("photoId") == "PHOTO-20")
    assert result["rfdsArtifactId"] == "STO42-rfds"
    assert "127" in " ".join(result["findings"])
    assert facts["caseId"] == "00000000-0000-4000-8000-000000000042"


def test_fabric_export_covers_independent_scenarios_and_programs(rows):
    assert select(rows, "fallback-ready", "SE-GOT-0017")["expected"]["readiness"] == "Ready"
    assert select(rows, "construction-planned")["expected"]["readiness"] == "NotReady"
    assert select(rows, "replacements-reviewed")["expected"]["readiness"] == "Ready"
    assert select(rows, "replacements-reviewed-rework-open")["expected"]["readiness"] == "Blocked"
    assert select(rows, "auto-below-threshold")["expected"]["readiness"] == "Blocked"
    assert len({(row["scenario"], row["siteId"], row["program"]) for row in rows}) == len(rows)
    assert {row["program"] for row in rows} == {"CUST-A-5G-ROLLOUT", "CUST-B-5G-ROLLOUT"}
    assert not any(row["scenario"] == "caller-without-site-access" for row in rows)
    assert all(photo["siteId"] == row["siteId"] for row in rows for photo in row["facts"]["photos"])
    assert fixture_rows(ROOT) == rows


def test_agent_definition_replaces_old_grounding_in_both_stages_and_preserves_identity():
    original = {
        "definition": {
            "parts": [
                {
                    "path": "Files/Config/data_agent.json",
                    "payload": base64.b64encode(b'{"$schema":"data-agent-schema"}').decode(),
                },
                {
                    "path": "Files/Config/draft/kusto-old/datasource.json",
                    "payload": base64.b64encode(
                        json.dumps(
                            {
                                "artifactId": "db",
                                "workspaceId": "workspace",
                                "type": "kusto",
                                "displayName": "closeout",
                                "elements": [{"display_name": "Events"}],
                            }
                        ).encode()
                    ).decode(),
                },
                {
                    "path": "Files/Config/published/semantic-model-old/datasource.json",
                    "payload": base64.b64encode(b'{"type":"semantic_model"}').decode(),
                },
                {"path": ".platform", "payload": "unchanged"},
            ]
        },
    }
    before = copy.deepcopy(original)
    columns = [{"Name": "siteId", "CslType": "string"}]
    result = agent_definition(original, "workspace", "db", columns)
    assert original == before
    assert result["definition"]["parts"][-1] == original["definition"]["parts"][-1]
    decoded = {
        part["path"]: json.loads(base64.b64decode(part["payload"]))
        for part in result["definition"]["parts"]
        if part["path"] != ".platform"
    }
    for stage in ("draft", "published"):
        source = decoded[f"Files/Config/{stage}/kusto-closeout/datasource.json"]
        assert [row["display_name"] for row in source["elements"]] == [FACTS_TABLE]
        assert source["artifactId"] == "db"
        assert source["elements"][0]["children"][0]["display_name"] == "siteId"
        examples = decoded[f"Files/Config/{stage}/kusto-closeout/fewshots.json"]
        assert all(
            "siteId ==" in row["query"]
            and "program ==" in row["query"]
            and "scenario ==" in row["query"]
            for row in examples["fewShots"]
        )
    assert not any("semantic-model" in path for path in decoded)
    with pytest.raises(ValueError, match="existing workspace KQL source"):
        agent_definition(original, "other-workspace", "db", columns)


def test_setup_rejects_non_fabric_endpoint_before_using_credentials(tmp_path):
    with pytest.raises(ValueError, match="Fabric Eventhouse"):
        FabricSetup(None, {"kustoEndpoint": "https://untrusted.example"}, tmp_path)


def test_live_agent_source_is_additive_and_queries_one_exact_run_revision():
    def part(path, value):
        return {
            "path": path,
            "payloadType": "InlineBase64",
            "payload": base64.b64encode(json.dumps(value).encode()).decode(),
        }

    original = {"definition": {"parts": [{"path": ".platform", "payload": "unchanged"}]}}
    for stage in ("draft", "published"):
        prefix = f"Files/Config/{stage}"
        original["definition"]["parts"].extend(
            [
                part(
                    f"{prefix}/stage_config.json", {"aiInstructions": "Existing fixture guidance"}
                ),
                part(
                    f"{prefix}/kusto-test/datasource.json",
                    {
                        "artifactId": "db",
                        "workspaceId": "workspace",
                        "type": "kusto",
                        "elements": [{"display_name": FACTS_TABLE}],
                        "dataSourceInstructions": "Existing source guidance",
                    },
                ),
                part(
                    f"{prefix}/kusto-test/fewshots.json",
                    {
                        "fewShots": [{"id": "existing", "query": "Existing fixture query"}],
                    },
                ),
            ]
        )
    before = copy.deepcopy(original)
    snapshot = {
        "runId": "00000000-0000-4000-8000-000000000001",
        "siteId": "SE-STO-0042",
        "program": "CUST-A-5G-ROLLOUT",
        "factsRevision": "a" * 64,
    }
    result = live_agent_definition(original, "workspace", "db", snapshot)
    assert original == before
    assert result["definition"]["parts"][0] == original["definition"]["parts"][0]
    assert live_agent_definition(result, "workspace", "db", snapshot) == result
    for item in result["definition"]["parts"][1:]:
        value = json.loads(base64.b64decode(item["payload"]))
        if item["path"].endswith("datasource.json"):
            assert [row["display_name"] for row in value["elements"]] == [
                FACTS_TABLE,
                "CloseoutFlowFactsV02",
            ]
        if item["path"].endswith("fewshots.json"):
            assert value["fewShots"][0]["query"] == "Existing fixture query"
            for field, expected in snapshot.items():
                assert (
                    f"tostring(snapshot.{field}) == {json.dumps(expected)}"
                    in (value["fewShots"][-1]["query"])
                )


def fabric_settings(**changes):
    return FabricSettings(
        **{
            "workspaceId": "00000000-0000-4000-8000-000000000001",
            "dataAgentId": "00000000-0000-4000-8000-000000000002",
            "databaseId": "00000000-0000-4000-8000-000000000003",
            "kustoEndpoint": "https://example.kusto.fabric.microsoft.com",
            "connectionName": "test-fabric",
            "toolName": "DataAgent_test",
            **changes,
        }
    )


def test_inspection_pages_inventory_and_backs_up_definitions_without_modification(tmp_path):
    settings = fabric_settings()
    reflex = "00000000-0000-4000-8000-000000000004"
    requests, queries = [], []

    def handle(request):
        requests.append(request)
        if request.method == "POST":
            assert request.url.path.endswith("/getDefinition")
            return httpx.Response(
                200,
                json={
                    "definition": {
                        "parts": [
                            {
                                "path": "ReflexEntities.json",
                                "payload": base64.b64encode(b"[]").decode(),
                            }
                        ]
                        if reflex in request.url.path
                        else []
                    }
                },
            )
        assert request.method == "GET"
        if not request.url.path.endswith("/items"):
            return httpx.Response(200, json={"capacityId": "assigned-capacity"})
        if "continuationToken" not in request.url.params:
            return httpx.Response(
                200,
                json={
                    "value": [{"id": reflex, "displayName": "monitor", "type": "Reflex"}],
                    "continuationToken": "next-page",
                },
            )
        assert request.url.params["continuationToken"] == "next-page"
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": settings.dataAgentId,
                        "displayName": "selected-agent",
                        "type": "DataAgent",
                    },
                    {
                        "id": "00000000-0000-4000-8000-000000000005",
                        "displayName": "unrelated-agent",
                        "type": "DataAgent",
                    },
                ]
            },
        )

    setup = FabricSetup(
        SimpleNamespace(get_token=lambda scope: SimpleNamespace(token="test")),
        settings.model_dump(),
        tmp_path,
    )
    setup.client.close()
    setup.client = httpx.Client(transport=httpx.MockTransport(handle))

    def kql(query, *, write=False):
        queries.append((query, write))
        return [{"TableName": FACTS_TABLE, "DocString": "fixture"}]

    setup.kql = kql
    with setup.client:
        report = setup.inspect()
    assert queries == [(".show tables | project TableName, DocString", True)]
    assert report["capacityId"] == "assigned-capacity"
    assert report["unattendedVerification"] == report["eventReadinessVerification"] == "not-run"
    assert len(report["items"]) == 3
    assert len(report["definitions"]) == 2
    assert report["definitions"][0]["entityCount"] == 0
    assert all(Path(row["backup"]).exists() for row in report["definitions"])
    assert json.loads((tmp_path / "inspection.json").read_text()) == report
    assert len([request for request in requests if request.method == "POST"]) == 2


def test_inspection_rejects_repeated_pagination_without_publishing(tmp_path):
    settings = fabric_settings()
    setup = FabricSetup(
        SimpleNamespace(get_token=lambda scope: SimpleNamespace(token="test")),
        settings.model_dump(),
        tmp_path,
    )
    setup.client.close()
    setup.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"value": [], "continuationToken": "repeated"}
                if request.url.path.endswith("/items")
                else {"capacityId": "assigned-capacity"},
            )
        )
    )
    with setup.client, pytest.raises(ValueError, match="repeated continuation"):
        setup.inspect()
    assert not (tmp_path / "inspection.json").exists()


def materialized(rows, scenario="initial-blocked", site="SE-STO-0042"):
    row = copy.deepcopy(select(rows, scenario, site))
    expected = row.pop("expected")
    row.update(
        expected,
        caseId=row["facts"]["caseId"],
        evaluatedAt="2026-10-28T14:46:21Z",
        sourceRef=f"fabric:contract-v01:{scenario}:{site}:{row['seedHash']}",
    )
    row["facts"].update(expected)
    row["facts"]["site"].update(
        readiness=expected["readiness"],
        readinessMissing=expected["readinessMissing"],
        readinessEvaluatedAt=row["evaluatedAt"],
    )
    return row


@pytest.mark.parametrize(
    "scenario,site,missing,photos",
    [
        (
            "initial-blocked",
            "SE-STO-0042",
            ["CHK-A-AZ-B", "CHK-A-GROUNDING", "PL-0042-01"],
            ["PHOTO-19", "PHOTO-20"],
        ),
        ("fallback-ready", "SE-GOT-0017", [], []),
        ("replacements-reviewed-rework-open", "SE-STO-0042", ["RW-0042-01"], []),
    ],
)
def test_fabric_snapshot_keeps_fabric_state_and_current_gap_evidence(
    rows, scenario, site, missing, photos
):
    row = materialized(rows, scenario, site)
    before = copy.deepcopy(row)
    facts = verified_facts([row], site, row["program"], scenario, ROOT / "contracts")
    assert row == before
    assert facts["mode"] == "fabric-test"
    assert facts["readinessMissing"] == missing
    assert sorted(photo for gap in facts["photoGaps"] for photo in gap["photoIds"]) == photos
    assert facts["fabricSource"]["sourceRef"] == row["sourceRef"]
    assert facts["validationSource"] == "seed"


@pytest.mark.parametrize("count", [0, 2])
def test_fabric_missing_or_duplicate_rows_never_fall_back_to_seed(rows, count):
    row = materialized(rows)
    with pytest.raises(ValueError, match="exactly one"):
        verified_facts(
            [row] * count, row["siteId"], row["program"], row["scenario"], ROOT / "contracts"
        )


@pytest.mark.parametrize("failure", ["state", "source", "scope"])
def test_fabric_rejects_mismatched_computed_state_provenance_and_record_scope(rows, failure):
    row = materialized(rows)
    if failure == "state":
        row["facts"]["readinessMissing"] = []
    elif failure == "source":
        row["sourceRef"] = row["sourceRef"].replace("initial-blocked", "fallback-ready")
    else:
        row["facts"]["photos"][0]["siteId"] = "SE-GOT-0017"
    with pytest.raises(ValueError):
        verified_facts([row], row["siteId"], row["program"], row["scenario"], ROOT / "contracts")


def test_native_fabric_replaces_local_functions_but_preserves_document_iq():
    knowledge = KnowledgeSettings(
        searchEndpoint="https://example.search.windows.net",
        connectionName="test-knowledge",
        knowledgeBase="shared-demo",
        sites=["SE-STO-0042", "SE-GOT-0017"],
        blobRoot="https://example.blob.core.windows.net/documents/iq/",
    )
    settings = fabric_settings()
    normal = definition("closeout", "test-model", knowledge)
    native = definition("closeout", "test-model", knowledge, settings)
    assert not any(tool["type"] == "function" for tool in native["tools"])
    assert [tool for tool in native["tools"] if tool["type"] == "mcp"] == [
        tool for tool in normal["tools"] if tool["type"] == "mcp"
    ]
    remote = next(tool for tool in native["tools"] if tool["type"] == "fabric_iq_preview")
    assert remote["server_url"] == settings.mcp_url()
    assert remote["project_connection_id"] == settings.connectionName
    assert definition("validation", "test-model", knowledge, settings) == definition(
        "validation", "test-model", knowledge
    )
    with pytest.raises(ValueError, match="requires the document IQ"):
        definition("closeout", "test-model", fabric=settings)


@pytest.mark.parametrize(
    "changes",
    [
        {"dataAgentId": "../another-agent"},
        {"kustoEndpoint": "https://untrusted.example"},
        {"kustoEndpoint": "https://user@example.kusto.fabric.microsoft.com"},
        {"kustoEndpoint": "https://example.kusto.fabric.microsoft.com/path"},
        {"connectionName": "../another-connection"},
    ],
)
def test_native_fabric_rejects_unscoped_endpoints_and_identifiers(changes):
    with pytest.raises(ValueError):
        fabric_settings(**changes)


@pytest.mark.parametrize("failure", [None, "error", "reference", "question", "tool", "missing"])
def test_native_fabric_receipt_requires_actual_matching_tool_result(rows, failure):
    row = materialized(rows)
    facts = verified_facts(
        [row], row["siteId"], row["program"], row["scenario"], ROOT / "contracts"
    )
    raw = SimpleNamespace(
        id="mcp-test",
        name="DataAgent_test",
        server_label="fabric-facts",
        arguments=json.dumps(
            {"userQuestion": f"{row['siteId']} {row['program']} {row['scenario']}"}
        ),
        error=None,
        output=json.dumps({"response": row["sourceRef"]}),
    )
    if failure == "error":
        raw.error = "CONSENT_REQUIRED"
    elif failure == "reference":
        raw.output = "No matching facts"
    elif failure == "question":
        raw.arguments = '{"userQuestion":"Use the default site"}'
    elif failure == "tool":
        raw.name = "unknown_tool"
    response = SimpleNamespace(
        messages=[
            SimpleNamespace(
                contents=[SimpleNamespace(type="mcp_server_tool_result", raw_representation=raw)]
                if failure != "missing"
                else []
            )
        ]
    )
    if failure:
        with pytest.raises(ValueError):
            retrieval_receipts(response, SimpleNamespace(facts=facts), fabric_settings())
    else:
        receipt = retrieval_receipts(response, SimpleNamespace(facts=facts), fabric_settings())[0]
        assert receipt["sourceRef"] == row["sourceRef"]
        assert receipt["tool"] == "DataAgent_test"
        assert receipt["outputSha256"]

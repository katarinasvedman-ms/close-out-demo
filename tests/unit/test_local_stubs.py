import asyncio
import copy
import json
import os
import sys
from pathlib import Path

import pytest
import yaml
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from shared.data_agent_stub import DataAgentStub
from shared.dependency_modes import DependencyModes
from shared.event_stub import EVENTS, EventProjection, EventSinkStub
from shared.local_demo import run_demo

ROOT = Path(__file__).resolve().parents[2]
QUESTIONS = yaml.safe_load(
    (ROOT / "contracts" / "mcp" / "data-agent-tests.yaml").read_text(encoding="utf-8")
)["cases"]


@pytest.mark.parametrize("question", QUESTIONS, ids=lambda row: row["id"])
def test_data_agent_contract_questions_are_grounded_in_scenario_facts(snapshot, question):
    spec = snapshot.recipe["scenario"]
    site_id = {
        "program-b": spec["programBSiteId"],
        "fallback-ready": spec["fallbackSiteId"],
    }.get(question["scenario"], spec["mainSiteId"])
    allowed = set() if question["scenario"] == "caller-without-site-access" else {site_id}
    service = DataAgentStub(snapshot, allowed)
    if not allowed:
        with pytest.raises(PermissionError):
            service.site_facts(site_id, question["scenario"])
        return
    facts = service.site_facts(site_id, question["scenario"])
    assert facts["mode"] == "stub"
    ids = {site_id, facts["project"]["projectId"]}
    for collection in ("photos", "validations", "tickets", "workOrders", "milestones"):
        key = {
            "photos": "photoId",
            "validations": "validationResultId",
            "tickets": "ticketId",
            "workOrders": "workOrderId",
            "milestones": "milestoneId",
        }[collection]
        ids.update(row[key] for row in facts[collection])
    assert set(question["expectedRefs"]) <= ids
    match question["id"]:
        case "blocked-main":
            assert facts["constructionComplete"] and facts["readiness"] == "Blocked"
            assert set(question["expectedRefs"]) <= {ref["id"] for ref in facts["evidenceRefs"]}
        case "grounding-gap" | "azimuth-spec":
            photo_id = question["expectedRefs"][0]
            result = next(row for row in facts["validations"] if row.get("photoId") == photo_id)
            text = " ".join(result["findings"]).casefold()
            assert not result["compliant"]
            if question["id"] == "azimuth-spec":
                assert result["rfdsArtifactId"] and "120" in result["rfdsReference"]
                assert all(value in text for value in ("120", "127", "7"))
            else:
                assert "grounding" in text and "label missing" in text
        case "ticket-blocker":
            ticket = next(
                row for row in facts["tickets"] if row["ticketId"] in question["expectedRefs"]
            )
            assert ticket["status"] == "Open" and ticket["blocking"]
        case "program-a-threshold":
            assert facts["project"]["lowConfidenceThreshold"] == float(question["expectedFacts"][0])
        case "program-b-requirements":
            assert facts["project"]["lowConfidenceThreshold"] == float(question["expectedFacts"][0])
            assert any(
                row["artifactType"] == "customer_pre_acceptance" and row["mandatory"]
                for row in facts["requirements"]
            )
            assert any(row["itemCode"] == "access_road" for row in facts["checklist"])
        case "ready-fallback":
            assert facts["readiness"] == "Ready" and not facts["readinessMissing"]
            assert not facts["photoGaps"]
        case "current-photo":
            replacements = [
                row for row in facts["photos"] if row["photoId"] in question["expectedRefs"]
            ]
            assert all(row["isCurrent"] and row["replacesPhotoId"] for row in replacements)
            replaced = {row["replacesPhotoId"] for row in replacements}
            assert all(
                not row["isCurrent"] for row in facts["photos"] if row["photoId"] in replaced
            )
        case "rework-open":
            assert facts["readiness"] == "Blocked" and not facts["photoGaps"]
            assert facts["readinessMissing"] == [spec["reworkId"]]
        case "low-confidence":
            assert facts["readiness"] == "Blocked"
            assert any(
                row["compliant"]
                and row["reviewStatus"] == "Auto"
                and row["confidence"] < facts["project"]["lowConfidenceThreshold"]
                for row in facts["validations"]
            )
        case "construction-first":
            assert facts["readiness"] == "NotReady" and not facts["constructionComplete"]
        case _:
            pytest.fail(f"New contract question needs a semantic assertion: {question['id']}")


def test_data_agent_scope_cannot_be_expanded_or_mutated_by_a_tool_caller(snapshot):
    spec = snapshot.recipe["scenario"]
    service = DataAgentStub(snapshot, {spec["mainSiteId"]})
    with pytest.raises(PermissionError):
        service.site_facts(spec["fallbackSiteId"])
    result = service.site_facts(spec["mainSiteId"])
    result["project"]["customerId"] = "modified-by-caller"
    assert service.site_facts(spec["mainSiteId"])["project"]["customerId"] != "modified-by-caller"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("READINESS_SOURCE", "fabric"),
        ("DATA_AGENT_MODE", "live"),
        ("EVENT_SINK", "eventstream"),
    ],
)
def test_live_modes_never_fall_back_to_stubs(key, value):
    with pytest.raises(NotImplementedError, match="no fallback"):
        DependencyModes.from_env({key: value}).require_offline()
    with pytest.raises(ValueError):
        DependencyModes.from_env({key: "typo"})


def test_event_sink_validates_all_event_examples_and_deduplicates_across_restarts(tmp_path):
    payloads = json.loads(
        (ROOT / "contracts" / "examples" / "events.json").read_text(encoding="utf-8")
    )
    payloads = {name: event for name, event in payloads.items() if event["eventType"] in EVENTS}
    path = tmp_path / "events.sqlite3"
    sink = EventSinkStub(path, ROOT / "contracts")
    for event in payloads.values():
        assert sink.publish(event)
    reopened = EventSinkStub(path, ROOT / "contracts")
    for event in payloads.values():
        assert not reopened.publish(event)
    assert len(reopened.events()) == len(payloads)
    changed = copy.deepcopy(next(iter(payloads.values())))
    changed["producer"] = "different-producer"
    with pytest.raises(ValueError, match="different payload"):
        reopened.publish(changed)


def validation_event(snapshot, *, site_id=None):
    site_id = site_id or snapshot.recipe["scenario"]["mainSiteId"]
    photo = next(row for row in snapshot.rows("Photo") if row["siteId"] == site_id)
    result = next(
        row for row in snapshot.rows("ValidationResult") if row.get("photoId") == photo["photoId"]
    )
    return {
        "schemaVersion": snapshot.recipe["schemaVersion"],
        "eventType": "ValidationResultCreated",
        "eventId": "f9936503-e998-4b1f-8283-bf93611a032a",
        "caseId": snapshot.case(site_id)["caseId"],
        "siteId": site_id,
        "occurredAt": snapshot.recipe["scenarioTimestamp"],
        "producer": "azure.local-stub",
        "data": copy.deepcopy(result),
    }


def test_projection_checks_identity_and_ignores_out_of_order_snapshots(snapshot):
    projection = EventProjection(snapshot, ROOT / "contracts")
    event = validation_event(snapshot)
    assert projection.apply(event)
    assert not projection.apply(event)
    changed_id = {**copy.deepcopy(event), "eventId": "95caa45d-e357-422a-a1ae-4e021ab12d6f"}
    changed_id["data"]["compliant"] = False
    with pytest.raises(ValueError, match="equal-time"):
        projection.apply(changed_id)
    changed_id["occurredAt"] = "2026-10-26T09:00:00Z"
    assert not projection.apply(changed_id)
    mismatched = {**copy.deepcopy(event), "siteId": snapshot.recipe["scenario"]["fallbackSiteId"]}
    with pytest.raises(ValueError, match="siteId mismatch"):
        EventProjection(snapshot, ROOT / "contracts").apply(mismatched)
    wrong_case = {**copy.deepcopy(event), "caseId": snapshot.case(mismatched["siteId"])["caseId"]}
    with pytest.raises(ValueError, match="does not belong"):
        EventProjection(snapshot, ROOT / "contracts").apply(wrong_case)


def test_projection_logs_future_codes_and_keeps_structural_checks(snapshot, caplog):
    projection = EventProjection(snapshot, ROOT / "contracts")
    event = validation_event(snapshot)
    event["data"].update(reviewStatus="FutureReview", futureField=True)
    assert projection.apply(event)
    assert "Unknown contract code" in caplog.text
    result_id = event["data"]["validationResultId"]
    assert "futureField" not in projection.snapshot.get("ValidationResult", result_id)
    unsupported = {**copy.deepcopy(event), "eventType": "FutureEvent"}
    assert not projection.apply(unsupported)
    assert "Unknown event type ignored" in caplog.text


def test_unit_only_dataverse_fake_requires_current_etag(snapshot, dataverse_fake):
    case_id = snapshot.case(snapshot.recipe["scenario"]["mainSiteId"])["caseId"]
    first, etag = dataverse_fake.read(case_id)
    second, same_etag = dataverse_fake.read(case_id)
    assert first == second and etag == same_etag
    first["statusReason"] = "caller-local-edit"
    assert dataverse_fake.read(case_id)[0]["statusReason"] != "caller-local-edit"
    updated, new_etag = dataverse_fake.update(
        case_id, {"statusReason": "ReworkDraftReady"}, if_match=etag
    )
    assert updated["statusReason"] == "ReworkDraftReady" and new_etag != etag
    with pytest.raises(ValueError, match="re-read and reconsider"):
        dataverse_fake.update(case_id, {"statusReason": "ReworkApproved"}, if_match=same_etag)
    with pytest.raises(ValueError):
        dataverse_fake.update(case_id, {}, if_match="*")


def test_offline_runner_replays_required_states_and_reuses_durable_event_receipts(
    tmp_path, monkeypatch
):
    for name in ("READINESS_SOURCE", "DATA_AGENT_MODE", "EVENT_SINK"):
        monkeypatch.delenv(name, raising=False)
    args = (
        ROOT / "seed" / "seed.json",
        ROOT / "contracts",
        ROOT / "seed" / "defects.yaml",
        tmp_path,
    )
    first = run_demo(*args, files=False)
    second = run_demo(*args, files=False)
    assert first["eventsPublished"] > 0
    assert second["eventsPublished"] == 0
    assert first["eventsStored"] == second["eventsStored"]
    assert [row["readiness"] for row in first["stages"]] == ["Blocked", "Blocked", "Ready"]
    assert first["readinessSignals"] == 2
    signals = json.loads((tmp_path / "readiness-signals.json").read_text(encoding="utf-8"))
    assert [row["readiness"] for row in signals] == ["Blocked", "Ready"]
    assert first["dependencies"]["dataverse"] == "not-connected"
    canonical = json.loads((tmp_path / "canonical.json").read_text(encoding="utf-8"))
    assert all(row["siteId"] for row in canonical["Ticket"])
    mock = json.loads((tmp_path / "mock-sor.json").read_text(encoding="utf-8"))
    assert not any(row["isStale"] for row in mock["Milestone"])


def test_actual_stdio_mcp_session_returns_stub_facts_and_denies_another_site(snapshot):
    async def exercise():
        site_id = snapshot.recipe["scenario"]["mainSiteId"]
        environment = {
            **os.environ,
            "READINESS_SOURCE": "stub",
            "DATA_AGENT_MODE": "stub",
            "EVENT_SINK": "stub",
        }
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "shared.stub_mcp",
                "--seed",
                str(ROOT / "seed" / "seed.json"),
                "--contracts",
                str(ROOT / "contracts"),
                "--site",
                site_id,
            ],
            env=environment,
        )
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert [tool.name for tool in tools.tools] == ["get_site_facts"]
                response = await session.call_tool("get_site_facts", {"site_id": site_id})
                assert not response.isError
                facts = json.loads(response.content[0].text)
                assert facts["mode"] == "stub" and facts["readiness"] == "Blocked"
                denied = await session.call_tool(
                    "get_site_facts",
                    {
                        "site_id": snapshot.recipe["scenario"]["fallbackSiteId"],
                    },
                )
                assert denied.isError
                assert "allowlist" in denied.content[0].text
                assert "photos" not in denied.content[0].text

    asyncio.run(asyncio.wait_for(exercise(), timeout=45))

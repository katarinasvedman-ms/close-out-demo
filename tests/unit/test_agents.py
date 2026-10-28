import asyncio
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents.definitions import (
    Assessment,
    Assessments,
    FoundrySettings,
    GapDraft,
    KnowledgeSettings,
    definition,
)
from agents.knowledge import retrieval_receipts, validate_document_citations
from agents.knowledge_setup import indexed_documents_match, native_definitions
from agents.sources import Sources, mcp_facts
from shared.data_agent_stub import DataAgentStub
from shared.local_demo import write_json
from shared.scenarios import scenario
from shared.seed import load_seed
from shared.seed_files import asset_path, document_text

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "contracts"


def test_native_knowledge_tool_is_shared_and_has_no_local_documents():
    knowledge = KnowledgeSettings(
        searchEndpoint="https://example.search.windows.net",
        connectionName="test-knowledge",
        knowledgeBase="shared-demo",
        sites=["SE-STO-0042", "SE-GOT-0017"],
        blobRoot="https://example.blob.core.windows.net/documents/iq/",
    )
    body = definition("closeout", "test-model", knowledge)
    functions = [tool["name"] for tool in body["tools"] if tool["type"] == "function"]
    assert functions == ["get_site_facts"]
    remote = next(tool for tool in body["tools"] if tool["type"] == "mcp")
    assert remote["server_url"] == (
        "https://example.search.windows.net/knowledgebases/shared-demo/mcp?api-version=2026-08-01-preview"
    )
    assert remote["allowed_tools"] == ["knowledge_base_retrieve"]
    assert remote["project_connection_id"] == "test-knowledge"
    assert "structured_inputs" not in body
    other_site = knowledge.model_copy(update={"sites": ["SE-GOT-0017"]})
    assert definition("closeout", "test-model", other_site) == body
    with pytest.raises(ValueError):
        KnowledgeSettings(
            searchEndpoint="https://untrusted.example",
            connectionName="test",
            knowledgeBase="test",
            sites=["site"],
            blobRoot="https://example.blob.core.windows.net/documents/iq/",
        )


def test_blob_source_delegates_the_indexing_pipeline_to_iq():
    model = {
        "kind": "azureOpenAI",
        "azureOpenAIParameters": {
            "resourceUri": "https://example.openai.azure.com/",
            "deploymentId": "test-regional-deployment",
            "modelName": "gpt-4o",
        },
    }
    bodies = native_definitions(
        "test", "documents", "/subscriptions/test/storage", "documents", model
    )
    assert set(bodies) == {"knowledgesources/documents", "knowledgebases/test"}
    source = bodies["knowledgesources/documents"]
    assert source["kind"] == "azureBlob"
    assert source["azureBlobParameters"]["folderPath"] == "iq/"
    assert "searchIndexParameters" not in source
    base = bodies["knowledgebases/test"]
    assert base["knowledgeSources"] == [{"name": "documents"}]
    assert base["models"] == [model]
    assert base["retrievalReasoningEffort"] == {"kind": "low"}
    assert base["outputMode"] == "extractiveData"
    assert "exact site ID" in base["retrievalInstructions"]


@pytest.mark.parametrize("change", [{}, {"version": "2"}, {"siteId": "other-site"}])
def test_native_ingestion_waits_for_matching_source_identity_and_version(change):
    upload = {
        "artifactId": "STO42-rfds",
        "siteId": "SE-STO-0042",
        "version": "1",
        "blobUrl": "https://example.blob.core.windows.net/documents/iq/SE-STO-0042/STO42-rfds.txt",
        **change,
    }
    rows = [
        {"blob_url": upload["blobUrl"], "snippet": "STO42-rfds\nSite: SE-STO-0042\nVersion: 1\n"}
    ]
    assert indexed_documents_match(rows, [upload]) == (not change)
    assert not indexed_documents_match([], [upload])


def knowledge_settings():
    return KnowledgeSettings(
        searchEndpoint="https://example.search.windows.net",
        connectionName="test-knowledge",
        knowledgeBase="shared-demo",
        sites=["SE-STO-0042", "SE-GOT-0017"],
        blobRoot="https://example.blob.core.windows.net/documents/iq/",
    )


def native_retrieval(sources, **changes):
    rfds = next(row for row in sources.facts["artifacts"] if row["artifactType"] == "rfds")
    reference = {
        "artifactId": rfds["artifactId"],
        "siteId": rfds["siteId"],
        "version": rfds["version"],
        **changes,
    }
    url = f"{knowledge_settings().blobRoot}{reference['siteId']}/{reference['artifactId']}.txt"
    native = {
        "kind": "reference",
        "uri": url,
        "sourceData": {
            "blob_url": url,
            "snippet": (
                f"{reference['artifactId']}\nSite: {reference['siteId']}\n"
                f"Version: {reference['version']}\n"
            ),
        },
    }
    return SimpleNamespace(
        messages=[
            SimpleNamespace(
                contents=[
                    SimpleNamespace(
                        type="mcp_server_tool_result",
                        raw_representation=SimpleNamespace(
                            id="mcp-test",
                            name="knowledge_base_retrieve",
                            server_label="documents",
                            output=json.dumps(
                                {"content": [{"type": "text", "text": json.dumps(native)}]}
                            ),
                            error=None,
                        ),
                    )
                ]
            )
        ]
    )


def test_native_knowledge_requires_real_returned_references_and_rfds_citation(sources):
    receipts = retrieval_receipts(native_retrieval(sources), sources, knowledge_settings())
    draft = GapDraft.model_validate(draft_for(sources))
    with pytest.raises(ValueError, match="RFDS"):
        validate_document_citations(draft, sources, receipts)
    rfds = next(row for row in sources.facts["artifacts"] if row["artifactType"] == "rfds")
    value = draft_for(sources)
    azimuth = next(row for row in value["gaps"] if row["conditionId"] == "CHK-A-AZ-B")
    azimuth["evidenceRefs"].append(
        {
            "objectType": "AcceptanceArtifact",
            "id": rfds["artifactId"],
            "version": rfds["version"],
        }
    )
    validate_document_citations(GapDraft.model_validate(value), sources, receipts)
    with pytest.raises(ValueError, match="not returned"):
        validate_document_citations(GapDraft.model_validate(value), sources, [{"documents": []}])


@pytest.mark.parametrize("changes", [{"siteId": "SE-GOT-0017"}, {"version": "unpublished"}])
def test_native_knowledge_rejects_wrong_site_or_document_version(sources, changes):
    with pytest.raises(ValueError, match="site/version"):
        retrieval_receipts(native_retrieval(sources, **changes), sources, knowledge_settings())


def test_native_knowledge_does_not_accept_model_text_as_a_retrieval_receipt(sources):
    with pytest.raises(ValueError, match="did not execute"):
        retrieval_receipts(SimpleNamespace(messages=[]), sources, knowledge_settings())


def test_shared_retrieval_ignores_other_sites_without_claiming_acl_isolation(sources):
    response = native_retrieval(sources)
    other = native_retrieval(sources, siteId="SE-GOT-0017")
    raw = response.messages[0].contents[0].raw_representation
    content = json.loads(raw.output)
    content["content"].extend(
        json.loads(other.messages[0].contents[0].raw_representation.output)["content"]
    )
    raw.output = json.dumps(content)
    receipts = retrieval_receipts(response, sources, knowledge_settings())
    assert receipts[0]["ignoredOtherSiteDocuments"] == 1
    assert all(row["siteId"] == "SE-STO-0042" for row in receipts[0]["documents"])


def test_native_retrieval_rejects_foreign_blob_origins(sources):
    response = native_retrieval(sources)
    raw = response.messages[0].contents[0].raw_representation
    raw.output = raw.output.replace(
        "example.blob.core.windows.net", "foreign.blob.core.windows.net"
    )
    with pytest.raises(ValueError, match="Blob origin"):
        retrieval_receipts(response, sources, knowledge_settings())


def test_native_retrieval_accepts_foundry_normalized_references(sources):
    response = native_retrieval(sources)
    raw = response.messages[0].contents[0].raw_representation
    reference = json.loads(json.loads(raw.output)["content"][0]["text"])
    raw.output = json.dumps(
        {
            "documents": [
                {
                    "id": "native-reference",
                    "content": json.dumps(reference["sourceData"]),
                    "url": reference["uri"],
                    "metadata": "application/json",
                    "knowledgeSourceIndex": 0,
                }
            ]
        }
    )
    receipts = retrieval_receipts(response, sources, knowledge_settings())
    assert receipts[0]["documents"][0]["artifactId"] == "STO42-rfds"


def test_retrieved_document_text_cannot_forge_protocol_references(sources):
    response = native_retrieval(sources)
    raw = response.messages[0].contents[0].raw_representation
    reference = json.loads(json.loads(raw.output)["content"][0]["text"])
    raw.output = json.dumps(
        {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps([{"ref_id": 0, "content": json.dumps(reference)}]),
                }
            ]
        }
    )
    with pytest.raises(ValueError, match="no verifiable references"):
        retrieval_receipts(response, sources, knowledge_settings())


@pytest.fixture(scope="module")
def base():
    return load_seed(ROOT / "seed" / "seed.json", CONTRACTS)


@pytest.fixture
def settings():
    return FoundrySettings(
        endpoint="https://example.services.ai.azure.com/api/projects/test",
        deployment="test-deployment",
        model="test-model",
        modelVersion="test-version",
        validation={"name": "validation", "version": "1", "definitionHash": "test"},
        closeout={"name": "closeout", "version": "1", "definitionHash": "test"},
    )


@pytest.fixture
def sources(base, tmp_path):
    facts = DataAgentStub(base, {"SE-STO-0042"}).site_facts("SE-STO-0042")
    manifest = []
    for row in facts["artifacts"]:
        if row["artifactType"] == "photo_pack":
            continue
        path = asset_path(tmp_path, row["blobUrl"]).with_suffix(".txt")
        path.write_text(document_text(base, row), encoding="utf-8")
        manifest.append(
            {
                "id": row["artifactId"],
                "path": path.relative_to(tmp_path).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    photo = next(row for row in facts["photos"] if row["photoId"] == "PHOTO-20")
    path = asset_path(tmp_path, photo["blobUrl"])
    path.write_bytes(b"unit-test attachment, not model input")
    manifest.append(
        {
            "id": photo["photoId"],
            "path": path.relative_to(tmp_path).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    )
    write_json(tmp_path / "assets-manifest.json", manifest)
    return Sources(facts, tmp_path, CONTRACTS)


def draft_for(sources):
    gaps = []
    validation_ids = []
    for gap in sources.facts["photoGaps"]:
        photo_id = gap["photoIds"][0]
        validation_id = f"{photo_id}-VALIDATION"
        validation_ids.append(validation_id)
        gaps.append(
            {
                "conditionType": "ChecklistItem",
                "conditionId": gap["checklistItemId"],
                "reason": "The current validation rejects this evidence.",
                "evidenceRefs": [
                    {"objectType": "Photo", "id": photo_id, "version": None},
                    {"objectType": "ValidationResult", "id": validation_id, "version": None},
                ],
            }
        )
    gaps.append(
        {
            "conditionType": "Ticket",
            "conditionId": "PL-0042-01",
            "reason": "Blocking cable labelling ticket is open.",
            "evidenceRefs": [{"objectType": "Ticket", "id": "PL-0042-01", "version": None}],
        }
    )
    return {
        "gaps": gaps,
        "proposedRework": {
            "reason": "Proposed corrections only, not approval.",
            "validationResultIds": validation_ids,
            "ticketIds": ["PL-0042-01"],
        },
    }


def test_photo_inputs_include_bytes_and_rfds_but_no_seeded_validation_outcome(sources):
    packets, attachments = sources.validation_inputs(["PHOTO-20"])
    assert "Sector B azimuth 120 degrees" in packets[0]["rfds"]["text"]
    assert attachments[0][1] == b"unit-test attachment, not model input"
    assert "validations" not in json.dumps(packets)
    with pytest.raises(PermissionError, match="scope"):
        sources.validation_inputs(["GOT17-PHOTO-20"])
    with pytest.raises(ValueError, match="distinct"):
        sources.validation_inputs(["PHOTO-20", "PHOTO-20"])


def test_tampered_asset_is_not_sent_to_the_model(sources):
    photo = sources.records[("Photo", "PHOTO-20")]
    asset_path(sources.assets, photo["blobUrl"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash"):
        sources.validation_inputs(["PHOTO-20"])


def test_low_confidence_is_routed_to_review_and_not_confirmed(sources, settings):
    packets, _ = sources.validation_inputs(["PHOTO-20"])
    draft = Assessments(
        assessments=[
            Assessment(
                evidenceId="PHOTO-20",
                compliant=True,
                confidence=0.74,
                findings=["Uncertain observation"],
                rfdsReference="Sector B azimuth 120 degrees",
            )
        ]
    )
    result = sources.validation_results(draft, packets, settings)[0]
    assert result["reviewStatus"] == "NeedsReview"
    assert result["modelVersion"] == settings.modelVersion


@pytest.mark.parametrize(
    "change,error",
    [
        ({"evidenceId": "invented"}, "exactly once"),
        ({"rfdsReference": "Sector B azimuth 121 degrees"}, "supplied sector"),
        ({"rfdsReference": "Sector A azimuth 0 degrees"}, "wrong sector"),
        ({"findings": []}, "findings"),
    ],
)
def test_validation_rejects_unbound_ids_quotes_and_empty_findings(sources, settings, change, error):
    packets, _ = sources.validation_inputs(["PHOTO-20"])
    value = {
        "evidenceId": "PHOTO-20",
        "compliant": False,
        "confidence": 0.9,
        "findings": ["Observed 127 degrees, planned 120 degrees"],
        "rfdsReference": "Sector B azimuth 120 degrees",
        **change,
    }
    with pytest.raises(ValueError, match=error):
        sources.validation_results(
            Assessments(assessments=[Assessment(**value)]), packets, settings
        )


def test_gap_analysis_produces_contract_valid_proposal_without_authority(sources, settings):
    output = sources.gap_analysis(GapDraft.model_validate(draft_for(sources)), settings)
    assert len(output["gaps"]) == 3
    assert output["caseId"] == sources.facts["caseId"]
    assert "approval" not in output


@pytest.mark.parametrize("change", ["omitted", "invented", "foreign", "stale-version", "proposal"])
def test_gap_analysis_rejects_incomplete_or_unsupported_outputs(sources, settings, change):
    value = draft_for(sources)
    if change == "omitted":
        value["gaps"].pop()
    elif change == "invented":
        value["gaps"][0]["conditionId"] = "invented"
    elif change == "foreign":
        value["gaps"][0]["evidenceRefs"][0]["id"] = "GOT17-PHOTO-20"
    elif change == "stale-version":
        value["gaps"][0]["evidenceRefs"].append(
            {"objectType": "AcceptanceArtifact", "id": "STO42-rfds", "version": "999"}
        )
    else:
        value["proposedRework"]["validationResultIds"] = ["unrelated"]
    with pytest.raises(ValueError):
        sources.gap_analysis(GapDraft.model_validate(value), settings)


def test_ready_case_cannot_receive_invented_rework(base, sources, settings):
    sources.facts = DataAgentStub(base, {"SE-GOT-0017"}).site_facts("SE-GOT-0017", "fallback-ready")
    valid = sources.gap_analysis(GapDraft(gaps=[], proposedRework=None), settings)
    assert valid["gaps"] == [] and valid["proposedRework"] is None
    with pytest.raises(ValueError, match="without gaps"):
        sources.gap_analysis(
            GapDraft(gaps=[], proposedRework=draft_for(sources)["proposedRework"]), settings
        )


def live_result(base):
    result = copy.deepcopy(base.get("ValidationResult", "PHOTO-19-VALIDATION"))
    result.update(
        validationResultId="unit-live-result",
        validatedAt="2030-01-01T00:00:00Z",
        model="unit-test-model",
        reviewStatus="NeedsReview",
        confidence=0.5,
    )
    return result


def test_live_overlay_reaches_real_scoped_stdio_mcp(base, tmp_path):
    result = live_result(base)
    overlay = tmp_path / "results.json"
    write_json(overlay, [result])
    facts = asyncio.run(
        mcp_facts("SE-STO-0042", "initial-blocked", ROOT / "seed" / "seed.json", CONTRACTS, overlay)
    )
    assert facts["validationSource"] == "live-agent-overlay"
    assert result in facts["validations"]
    assert facts["readiness"] == "Blocked"
    assert base.tables["ValidationResult"].get("unit-live-result") is None


@pytest.mark.parametrize("change", ["foreign-site", "wrong-case", "approval", "old", "duplicate"])
def test_overlay_cannot_cross_scope_or_forge_review(base, change):
    result = live_result(base)
    allowed = {"SE-STO-0042"}
    if change == "foreign-site":
        allowed = {"SE-GOT-0017"}
    elif change == "wrong-case":
        result["caseId"] = base.case("SE-GOT-0017")["caseId"]
    elif change == "approval":
        result["reviewStatus"] = "Confirmed"
    elif change == "old":
        result["validatedAt"] = "2020-01-01T00:00:00Z"
    else:
        result["validationResultId"] = "PHOTO-19-VALIDATION"
    with pytest.raises((ValueError, PermissionError)):
        DataAgentStub(base, allowed, [result], CONTRACTS)


def test_latest_conflicting_validation_does_not_become_a_cited_fact(base, sources, settings):
    first = next(v for v in sources.facts["validations"] if v.get("photoId") == "PHOTO-19")
    sources.facts["validations"].append(
        {**first, "validationResultId": "conflict", "compliant": not first["compliant"]}
    )
    with pytest.raises(ValueError, match="Ambiguous"):
        sources.gap_analysis(GapDraft.model_validate(draft_for(sources)), settings)


def test_overlay_affects_readiness_without_changing_canonical_seed(base):
    corrected = scenario(base, "replacements-reviewed")
    result = copy.deepcopy(
        next(v for v in corrected.rows("ValidationResult") if v.get("photoId") == "PHOTO-19b")
    )
    result.update(
        validationResultId="unit-new-rejection",
        validatedAt="2030-01-01T00:00:00Z",
        compliant=False,
        findings=["Label unreadable"],
    )
    service = DataAgentStub(base, {"SE-STO-0042"}, [result], CONTRACTS)
    facts = service.site_facts("SE-STO-0042", "replacements-reviewed")
    assert facts["readiness"] == "Blocked"
    assert "CHK-A-GROUNDING" in facts["readinessMissing"]
    assert facts["site"]["readiness"] == facts["readiness"]
    assert facts["site"]["readinessMissing"] == facts["readinessMissing"]

import copy
import hashlib
from pathlib import Path

import pytest
from PIL import Image

from shared.contract_validation import load_validators
from shared.readiness import ReadinessStub, evaluate
from shared.scenarios import SCENARIOS, scenario
from shared.seed import fabric_extract, validate_snapshot
from shared.seed_files import generate_assets

ROOT = Path(__file__).resolve().parents[2]


def test_seed_has_consistent_relationships_and_main_blockers(snapshot):
    validate_snapshot(snapshot, ROOT / "contracts")
    main = snapshot.recipe["scenario"]["mainSiteId"]
    result = evaluate(snapshot, main)
    assert result.readiness == "Blocked"
    assert set(result.missing) == {"CHK-A-GROUNDING", "CHK-A-AZ-B", "PL-0042-01"}
    assert {photo for gap in result.photo_gaps for photo in gap["photoIds"]} == {
        "PHOTO-19",
        "PHOTO-20",
    }
    assert evaluate(snapshot, snapshot.recipe["scenario"]["fallbackSiteId"]).readiness == "Ready"
    assert len(snapshot.rows("Site")) == 10
    assert all(
        sum(photo["siteId"] == site["siteId"] for photo in snapshot.rows("Photo")) == 20
        for site in snapshot.rows("Site")
    )


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_scenario_snapshots_remain_schema_valid_and_do_not_mutate_seed(snapshot, name):
    original = copy.deepcopy(snapshot.export())
    derived = scenario(snapshot, name)
    validate_snapshot(derived, ROOT / "contracts")
    assert snapshot.export() == original


def test_fabric_defects_are_isolated_from_canonical_and_mock_source(snapshot):
    before = copy.deepcopy(snapshot.export())
    extract = fabric_extract(snapshot, ROOT / "seed" / "defects.yaml")
    assert snapshot.export() == before
    assert len(extract["Site"]) == len(before["Site"]) + 1
    assert (
        next(row for row in extract["Ticket"] if row["ticketId"] == "PL-0017-02")["siteId"] is None
    )
    assert next(row for row in before["Ticket"] if row["ticketId"] == "PL-0017-02")["siteId"]
    assert next(row for row in extract["Milestone"] if row["milestoneId"] == "STO42-CC")["isStale"]
    wrong_gps = next(row for row in extract["Photo"] if row["photoId"] == "GOT17-PHOTO-01")
    assert (wrong_gps["latitude"], wrong_gps["longitude"]) == (10, 10)
    names = {
        row["subcontractorSourceName"]
        for row in extract["WorkOrder"]
        if "subcontractorSourceName" in row
    }
    assert len(names) == 2


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("replacements-reviewed", "Ready"),
        ("replacements-reviewed-rework-open", "Blocked"),
        ("auto-below-threshold", "Blocked"),
        ("construction-planned", "NotReady"),
    ],
)
def test_readiness_scenario_boundaries(snapshot, name, expected):
    derived = scenario(snapshot, name)
    result = evaluate(derived, snapshot.recipe["scenario"]["mainSiteId"])
    assert result.readiness == expected
    if name == "replacements-reviewed-rework-open":
        assert result.missing == [snapshot.recipe["scenario"]["reworkId"]]


@pytest.mark.parametrize(
    ("review", "confidence", "compliant", "expected"),
    [
        ("Auto", 0.75, True, "Ready"),
        ("Auto", 0.749, True, "Blocked"),
        ("Confirmed", 0.01, True, "Ready"),
        ("Confirmed", 1.0, False, "Blocked"),
        ("NeedsReview", 1.0, True, "Blocked"),
        ("Overridden", 1.0, True, "Blocked"),
        ("FutureReview", 1.0, True, "Blocked"),
    ],
)
def test_review_status_and_confidence_not_just_compliance(
    snapshot, review, confidence, compliant, expected
):
    site_id = snapshot.recipe["scenario"]["fallbackSiteId"]
    photo = next(
        row
        for row in snapshot.rows("Photo")
        if row["siteId"] == site_id and row["checklistItemCode"] == "azimuth_sector_b"
    )
    result = next(
        row for row in snapshot.rows("ValidationResult") if row.get("photoId") == photo["photoId"]
    )
    result.update(reviewStatus=review, confidence=confidence, compliant=compliant)
    assert evaluate(snapshot, site_id).readiness == expected


def test_program_b_uses_its_own_threshold_and_extra_evidence(snapshot):
    site_id = snapshot.recipe["scenario"]["programBSiteId"]
    photo = next(
        row
        for row in snapshot.rows("Photo")
        if row["siteId"] == site_id and row["checklistItemCode"] == "azimuth_sector_b"
    )
    result = next(
        row for row in snapshot.rows("ValidationResult") if row.get("photoId") == photo["photoId"]
    )
    result["confidence"] = 0.84
    assert evaluate(snapshot, site_id).readiness == "Blocked"
    result["confidence"] = 0.85
    assert evaluate(snapshot, site_id).readiness == "Ready"
    pre = next(
        row
        for row in snapshot.rows("AcceptanceArtifact")
        if row["siteId"] == site_id and row["artifactType"] == "customer_pre_acceptance"
    )
    pre["status"] = "Rejected"
    assert pre["requirementId"] in evaluate(snapshot, site_id).missing


def test_latest_result_and_current_replacements_are_required(snapshot):
    derived = scenario(snapshot, "replacements-reviewed")
    spec = derived.recipe["scenario"]
    new = derived.get("ValidationResult", f"{spec['lowConfidencePhotoId']}-VALIDATION")
    old = derived.get("ValidationResult", "PHOTO-19-VALIDATION")
    old.update(compliant=True, reviewStatus="Confirmed")
    new.update(compliant=False, validatedAt="2026-10-27T11:00:00Z")
    assert evaluate(derived, spec["mainSiteId"]).readiness == "Blocked"
    earlier = {
        **new,
        "validationResultId": "earlier-confirmation",
        "compliant": True,
        "validatedAt": "2026-10-27T09:00:00Z",
        "reviewStatus": "Confirmed",
    }
    derived.add("ValidationResult", earlier)
    assert evaluate(derived, spec["mainSiteId"]).readiness == "Blocked"
    tied = {**new, "validationResultId": "ambiguous-confirmation", "compliant": True}
    derived.add("ValidationResult", tied)
    assert evaluate(derived, spec["mainSiteId"]).readiness == "Blocked"


def test_only_active_case_rework_and_blocking_open_tickets_block(snapshot):
    site_id = snapshot.recipe["scenario"]["fallbackSiteId"]
    order = next(row for row in snapshot.rows("WorkOrder") if row["siteId"] == site_id)
    order.update(workOrderType="Rework", status="Open", caseId=snapshot.case(site_id)["caseId"])
    assert order["workOrderId"] in evaluate(snapshot, site_id).missing
    order["caseId"] = snapshot.case(snapshot.recipe["scenario"]["mainSiteId"])["caseId"]
    assert evaluate(snapshot, site_id).readiness == "Ready"
    ticket = next(row for row in snapshot.rows("Ticket") if row["siteId"] == site_id)
    ticket.update(status="Open", blocking=False)
    assert evaluate(snapshot, site_id).readiness == "Ready"
    ticket["blocking"] = True
    assert evaluate(snapshot, site_id).readiness == "Blocked"


def test_not_ready_precedence_and_only_blocked_ready_transitions_emit(snapshot):
    main = snapshot.recipe["scenario"]["mainSiteId"]
    tracker = ReadinessStub()
    validator = load_validators(ROOT / "contracts")["activator/readiness-signal.schema.json"]
    planned = scenario(snapshot, "construction-planned")
    assert tracker.signal(planned, main, snapshot.recipe["timestamp"]) is None
    first = tracker.signal(snapshot, main, snapshot.recipe["timestamp"])
    validator.validate(first)
    assert first["readiness"] == "Blocked"
    assert tracker.signal(snapshot, main, snapshot.recipe["timestamp"]) is None
    ready = scenario(snapshot, "replacements-reviewed")
    second = tracker.signal(ready, main, snapshot.recipe["scenarioTimestamp"])
    validator.validate(second)
    assert second["readiness"] == "Ready"
    assert tracker.signal(ready, main, snapshot.recipe["scenarioTimestamp"]) is None
    assert tracker.signal(planned, main, snapshot.recipe["scenarioTimestamp"]) is None
    assert tracker.signal(ready, main, snapshot.recipe["scenarioTimestamp"])["readiness"] == "Ready"


def test_generated_evidence_is_deterministic_and_matches_rfds(snapshot, tmp_path):
    subset = scenario(snapshot, "replacements-reviewed")
    subset.tables["Photo"] = {
        key: value
        for key, value in subset.tables["Photo"].items()
        if key in {"PHOTO-19", "PHOTO-20", "PHOTO-19b", "PHOTO-20b"}
    }
    subset.tables["AcceptanceArtifact"] = {
        key: value
        for key, value in subset.tables["AcceptanceArtifact"].items()
        if key == "STO42-rfds"
    }
    first = generate_assets(subset, tmp_path / "first")
    second = generate_assets(subset, tmp_path / "second")
    assert first == second
    for entry in first:
        path = tmp_path / "first" / entry["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
        if path.suffix == ".png":
            with Image.open(path) as image:
                image.verify()
        if path.suffix == ".pdf":
            assert path.read_bytes().startswith(b"%PDF-")
    text = (tmp_path / "first" / "documents" / "STO42-rfds.txt").read_text(encoding="utf-8")
    assert "Sector B azimuth 120 degrees" in text
    assert (tmp_path / "first" / "photos" / "PHOTO-19.png").read_bytes() != (
        tmp_path / "first" / "photos" / "PHOTO-19b.png"
    ).read_bytes()
    assert not any("adversarial" in row["blobUrl"] for row in snapshot.rows("AcceptanceArtifact"))

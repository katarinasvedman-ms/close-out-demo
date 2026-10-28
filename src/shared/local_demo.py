"""Reproduce the offline evidence scenarios without Azure, Dataverse, or business approvals."""

import argparse
import copy
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid5

from shared.contract_validation import load_validators
from shared.dependency_modes import DependencyModes
from shared.event_stub import EVENTS, EventProjection, EventSinkStub
from shared.readiness import ReadinessStub, evaluate
from shared.scenarios import scenario
from shared.seed import KEYS, fabric_extract, load_seed, validate_snapshot
from shared.seed_files import generate_assets


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8", newline="\n"
    )


def run_demo(
    seed: Path, contracts: Path, defects: Path, output: Path, *, files: bool = True
) -> dict:
    modes = DependencyModes.from_env(os.environ)
    modes.require_offline()
    base = load_seed(seed, contracts)
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "canonical.json", base.export())
    write_json(
        output / "mock-sor.json",
        {
            kind: base.rows(kind)
            for kind in (
                "CustomerProject",
                "Site",
                "Milestone",
                "Subcontractor",
                "WorkOrder",
                "Ticket",
            )
        },
    )
    write_json(output / "fabric-extract.json", fabric_extract(base, defects))
    completed = scenario(base, "replacements-reviewed")
    validate_snapshot(completed, contracts)
    if files:
        write_json(output / "assets-manifest.json", generate_assets(completed, output))
    projection = EventProjection(base, contracts)
    sink = EventSinkStub(output / "events.sqlite3", contracts)
    tracker = ReadinessStub()
    signal_validator = load_validators(contracts)["activator/readiness-signal.schema.json"]
    spec = base.recipe["scenario"]
    namespace = UUID(base.recipe["eventNamespace"])
    stages, signals, published = [], [], 0
    names = ("initial-blocked", "replacements-reviewed-rework-open", "replacements-reviewed")
    start = datetime.fromisoformat(base.recipe["timestamp"].replace("Z", "+00:00"))
    for step, name in enumerate(names):
        desired = scenario(base, name)
        validate_snapshot(desired, contracts)
        occurred_at = (start + timedelta(hours=step)).isoformat().replace("+00:00", "Z")
        for event_type, (_, kind) in EVENTS.items():
            if event_type == "ValidationResultReviewed":
                continue
            for row in desired.rows(kind):
                if kind == "ValidationResult":
                    target_kind, field = (
                        ("Photo", "photoId")
                        if "photoId" in row
                        else ("AcceptanceArtifact", "artifactId")
                    )
                    site_id = desired.get(target_kind, row[field])["siteId"]
                else:
                    site_id = row["siteId"]
                if site_id != spec["mainSiteId"]:
                    continue
                identifier = row[KEYS[kind]]
                if step and projection.snapshot.tables[kind].get(identifier) == row:
                    continue
                event = {
                    "schemaVersion": base.recipe["schemaVersion"],
                    "eventType": event_type,
                    "eventId": str(uuid5(namespace, f"{name}:{event_type}:{identifier}")),
                    "caseId": base.case(site_id)["caseId"],
                    "siteId": site_id,
                    "occurredAt": occurred_at,
                    "producer": "azure.local-stub",
                    "data": copy.deepcopy(row),
                }
                published += sink.publish(event)
                projection.apply(event)
        result = evaluate(projection.snapshot, spec["mainSiteId"])
        signal = tracker.signal(projection.snapshot, spec["mainSiteId"], occurred_at)
        if signal:
            signal_validator.validate(signal)
            signals.append(signal)
        stages.append(
            {"scenario": name, "readiness": result.readiness, "readinessMissing": result.missing}
        )
    fallback = evaluate(base, spec["fallbackSiteId"])
    expected = ["Blocked", "Blocked", "Ready"]
    if [row["readiness"] for row in stages] != expected or fallback.readiness != "Ready":
        raise ValueError("Offline replay did not reproduce the required scenarios")
    events = sink.events()
    (output / "events.jsonl").write_text(
        "".join(json.dumps(event, sort_keys=True) + "\n" for event in events),
        encoding="utf-8",
        newline="\n",
    )
    write_json(output / "readiness-signals.json", signals)
    report = {
        "mode": "offline-stubs",
        "dependencies": modes.labels(),
        "contractVersion": base.recipe["schemaVersion"],
        "contractApproval": "provisional",
        "mainSiteId": spec["mainSiteId"],
        "stages": stages,
        "fallback": {"siteId": spec["fallbackSiteId"], "readiness": fallback.readiness},
        "eventsPublished": published,
        "eventsStored": len(events),
        "readinessSignals": len(signals),
        "sites": len(base.rows("Site")),
        "photos": len(completed.rows("Photo")),
        "limitations": [
            "Synthetic illustrations and seeded validation results; no model was invoked.",
            "No Dataverse connection, approval flow, APIM action or live Fabric integration.",
            "Readiness transitions are reconstructed locally; no external notification is sent.",
            "PDFs are synthetic input documents, not a compiled close-out package.",
        ],
    }
    write_json(output / "report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=Path, default=Path("seed") / "seed.json")
    parser.add_argument("--contracts", type=Path, default=Path("contracts"))
    parser.add_argument("--defects", type=Path, default=Path("seed") / "defects.yaml")
    parser.add_argument("--output", type=Path, default=Path(".local") / "demo")
    parser.add_argument(
        "--no-files", action="store_true", help="Replay without regenerating evidence"
    )
    args = parser.parse_args()
    report = run_demo(args.seed, args.contracts, args.defects, args.output, files=not args.no_files)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

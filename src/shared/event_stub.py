"""Local event ledger and snapshot projection, not Eventstream or a business-action backend."""

import copy
import json
import logging
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from shared.contract_validation import load_validators
from shared.seed import KEYS, Snapshot

EVENTS = {
    "PhotoUploaded": ("photo-uploaded", "Photo"),
    "ArtifactUploaded": ("artifact-uploaded", "AcceptanceArtifact"),
    "ValidationResultCreated": ("validation-result-created", "ValidationResult"),
    "ValidationResultReviewed": ("validation-result-reviewed", "ValidationResult"),
    "WorkOrderChanged": ("work-order-changed", "WorkOrder"),
    "TicketChanged": ("ticket-changed", "Ticket"),
    "MilestoneChanged": ("milestone-changed", "Milestone"),
}
logger = logging.getLogger(__name__)


def canonical(event: dict) -> str:
    return json.dumps(event, sort_keys=True, separators=(",", ":"), allow_nan=False)


class EventSinkStub:
    def __init__(self, path: Path, contracts: Path):
        self.path = path
        self.validators = load_validators(contracts)
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS events "
                "(event_id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )

    def publish(self, event: dict) -> bool:
        if event.get("eventType") not in EVENTS:
            raise ValueError(f"Unsupported event type: {event.get('eventType')}")
        schema, _ = EVENTS[event["eventType"]]
        self.validators[f"events/{schema}.schema.json"].validate(event)
        payload = canonical(event)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT payload FROM events WHERE event_id = ?", (event["eventId"],)
            ).fetchone()
            if existing:
                if existing[0] != payload:
                    raise ValueError("eventId reused with a different payload")
                return False
            connection.execute("INSERT INTO events VALUES (?, ?)", (event["eventId"], payload))
        return True

    def events(self) -> list[dict]:
        with closing(sqlite3.connect(self.path)) as connection:
            return [
                json.loads(row[0])
                for row in connection.execute("SELECT payload FROM events ORDER BY rowid")
            ]


class EventProjection:
    def __init__(self, snapshot: Snapshot, contracts: Path):
        self.snapshot = snapshot.clone()
        self.strict = load_validators(contracts)
        self.readers = load_validators(contracts, tolerant=True)
        self.seen: dict[str, str] = {}
        watermark = datetime.fromisoformat(snapshot.recipe["timestamp"].replace("Z", "+00:00"))
        self.latest: dict[tuple[str, str], tuple[datetime, str]] = {
            (kind, identifier): (watermark, canonical(row))
            for kind, rows in snapshot.tables.items()
            for identifier, row in rows.items()
        }

    def apply(self, event: dict) -> bool:
        if event.get("eventType") not in EVENTS:
            self.readers["events/event-envelope.schema.json"].validate(event)
            logger.warning("Unknown event type ignored: %s", event["eventType"])
            return False
        schema, kind = EVENTS[event["eventType"]]
        name = f"events/{schema}.schema.json"
        self.readers[name].validate(event)
        for error in self.strict[name].iter_errors(event):
            if error.validator == "enum":
                logger.warning("Unknown contract code: %s", error.instance)
        payload = canonical(event)
        event_id = event["eventId"]
        if event_id in self.seen:
            if self.seen[event_id] != payload:
                raise ValueError("eventId reused with a different payload")
            return False
        row = copy.deepcopy(event["data"])
        if event["eventType"] == "ValidationResultReviewed":
            row = row["validationResult"]
        fields = self.strict["models.schema.json"].schema["$defs"][kind]["properties"]
        row = {key: value for key, value in row.items() if key in fields}
        site_id = row.get("siteId")
        if kind == "ValidationResult":
            target_kind, field = (
                ("Photo", "photoId") if "photoId" in row else ("AcceptanceArtifact", "artifactId")
            )
            site_id = self.snapshot.get(target_kind, row[field])["siteId"]
        if site_id != event["siteId"]:
            raise ValueError("Event/body siteId mismatch")
        case_id = self.snapshot.case(site_id)["caseId"]
        if event["caseId"] is not None and event["caseId"] != case_id:
            raise ValueError("Event caseId does not belong to its site")
        if row.get("caseId") is not None and row["caseId"] != event["caseId"]:
            raise ValueError("Event/body caseId mismatch")
        identifier = row[KEYS[kind]]
        stamp = datetime.fromisoformat(event["occurredAt"].replace("Z", "+00:00"))
        row_json = canonical(row)
        previous = self.latest.get((kind, identifier))
        if previous and (stamp < previous[0] or (stamp == previous[0] and row_json == previous[1])):
            self.seen[event_id] = payload
            return False
        if previous and stamp == previous[0]:
            raise ValueError("Ambiguous equal-time snapshots; ordering requires review")
        if kind == "Photo" and row["isCurrent"] and row["replacesPhotoId"]:
            old = self.snapshot.get("Photo", row["replacesPhotoId"])
            if (old["siteId"], old["checklistItemCode"]) != (site_id, row["checklistItemCode"]):
                raise ValueError("Invalid replacement target")
            siblings = [
                photo
                for photo in self.snapshot.rows("Photo")
                if photo["replacesPhotoId"] == old["photoId"]
                and photo["isCurrent"]
                and photo["photoId"] != identifier
            ]
            if siblings:
                raise ValueError("Ambiguous concurrent replacement")
            old["isCurrent"] = False
        self.snapshot.tables[kind][identifier] = row
        self.latest[kind, identifier] = (stamp, row_json)
        self.seen[event_id] = payload
        return True

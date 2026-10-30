"""Publish only a new isolated Fabric notebook; schedule uses its operator owner's identity."""

import argparse
import base64
import hashlib
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from agents.fabric_flow_runtime import readiness_query, validate_target, verify_snapshot
from agents.fabric_setup import FabricSetup
from shared.local_demo import write_json

NOTEBOOK_NAME = "closeout-headless-v02-projector"
OWNER = "close-out-demo v0.2.0 isolated synthetic automatic Fabric projector"
MODULES = (
    "agents/__init__.py",
    "agents/flow_projection.py",
    "agents/fabric_flow_runtime.py",
    "shared/__init__.py",
    "shared/contract_validation.py",
    "shared/headless.py",
    "shared/readiness.py",
    "shared/seed.py",
)


def get_notebook_definition(setup, workspace, item):
    headers = {
        "Authorization": "Bearer "
        + setup.credential.get_token("https://api.fabric.microsoft.com/.default").token
    }
    response = setup.client.post(
        f"https://api.fabric.microsoft.com/v1/workspaces/{workspace}"
        f"/items/{item}/getDefinition?format=ipynb",
        headers=headers,
        json={},
    )
    response.raise_for_status()
    if response.status_code == 202:
        operation = str(UUID(response.headers["x-ms-operation-id"]))
        location = f"https://api.fabric.microsoft.com/v1/operations/{operation}"
        for _ in range(90):
            time.sleep(2)
            state = setup.client.get(location, headers=headers)
            state.raise_for_status()
            if state.json()["status"] == "Succeeded":
                response = setup.client.get(location + "/result", headers=headers)
                response.raise_for_status()
                break
            if state.json()["status"] in {"Failed", "Cancelled"}:
                raise RuntimeError(state.text)
        else:
            raise TimeoutError("Notebook definition readback timed out")
    return response.json()


def projection_bundle(root):
    files = {f"src/{name}": (root / "src" / name).read_text("utf-8") for name in MODULES}
    files.update(
        {
            path.relative_to(root).as_posix(): path.read_text("utf-8")
            for path in sorted((root / "contracts").rglob("*.schema.json"))
        }
    )
    payload = json.dumps(files, sort_keys=True, separators=(",", ":"))
    return files, hashlib.sha256(payload.encode()).hexdigest()


def notebook_definition(root, config):
    validate_target(config)
    files, bundle_hash = projection_bundle(root)
    source = f"""
import json
import sys
from pathlib import Path

bundle = json.loads({json.dumps(json.dumps(files))})
root = Path(".closeout_flow_bundle") / {bundle_hash!r}
for relative, text in bundle.items():
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
sys.path.insert(0, str(root / "src"))
from agents.fabric_flow_runtime import notebook_main
notebook_main(json.loads({json.dumps(json.dumps(config))}), root / "contracts", {bundle_hash!r})
""".strip()
    notebook = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "language_info": {"name": "python"},
            "kernel_info": {"name": "synapse_pyspark"},
        },
        "cells": [
            {
                "cell_type": "code",
                "execution_count": None,
                "outputs": [],
                "metadata": {},
                "source": source.splitlines(keepends=True),
            }
        ],
    }
    return {
        "format": "ipynb",
        "parts": [
            {
                "path": "artifact.content.ipynb",
                "payloadType": "InlineBase64",
                "payload": base64.b64encode(json.dumps(notebook).encode()).decode(),
            }
        ],
    }, bundle_hash


def deploy(config, credential, output, root, *, schedule_minutes=0):
    validate_target(config)
    if schedule_minutes and not 1 <= schedule_minutes <= 60:
        raise ValueError("Test schedule interval must be between 1 and 60 minutes")
    setup = FabricSetup(credential, config, output)
    base = f"https://api.fabric.microsoft.com/v1/workspaces/{config['workspaceId']}"

    def request(method, path, **kwargs):
        response = setup.client.request(
            method,
            base + path,
            headers={
                "Authorization": "Bearer "
                + credential.get_token("https://api.fabric.microsoft.com/.default").token
            },
            **kwargs,
        )
        response.raise_for_status()
        return response

    try:
        items = []
        token = None
        for _ in range(100):
            page = request(
                "GET", "/items", params={"continuationToken": token} if token else {}
            ).json()
            items.extend(page["value"])
            token = page.get("continuationToken")
            if not token:
                break
        else:
            raise ValueError("Fabric inventory exceeded page limit")
        matches = [item for item in items if item["displayName"] == NOTEBOOK_NAME]
        if len(matches) > 1:
            raise ValueError("Ambiguous isolated projector notebook")
        definition, bundle_hash = notebook_definition(root, config)
        write_json(output / "fabric-projector-request.json", definition)
        if matches:
            notebook = matches[0]
            receipt_path = output / "fabric-projector.json"
            if (
                not receipt_path.exists()
                or json.loads(receipt_path.read_text())["notebookId"] != notebook["id"]
                or notebook["type"] != "Notebook"
                or notebook.get("description") != OWNER
            ):
                raise ValueError("Refusing to overwrite an unowned existing Fabric item")
            backup = get_notebook_definition(setup, config["workspaceId"], notebook["id"])
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
            write_json(output / f"fabric-projector-before-{stamp}.json", backup)
            setup.fabric(
                f"workspaces/{config['workspaceId']}/items/{notebook['id']}/updateDefinition",
                {"definition": definition},
            )
        else:
            notebook = setup.fabric(
                f"workspaces/{config['workspaceId']}/notebooks",
                {"displayName": NOTEBOOK_NAME, "description": OWNER, "definition": definition},
            )
            if not notebook.get("id"):
                candidates = request("GET", "/items").json()["value"]
                notebook = next(item for item in candidates if item["displayName"] == NOTEBOOK_NAME)
        item = str(UUID(notebook["id"]))
        previous = (
            json.loads((output / "fabric-projector.json").read_text())
            if (output / "fabric-projector.json").exists()
            else {}
        )
        receipt = {
            **previous,
            "workspaceId": config["workspaceId"],
            "notebookId": item,
            "bundleSha256": bundle_hash,
            "identity": "Fabric notebook/schedule operator-delegated user; not workspace MI",
            "automaticProjection": False,
            "activatorConfigured": False,
        }
        write_json(output / "fabric-projector.json", receipt)
        readback = get_notebook_definition(setup, config["workspaceId"], item)
        write_json(output / "fabric-projector-readback.json", readback)
        actual = next(
            part for part in readback["definition"]["parts"] if part["path"].endswith(".ipynb")
        )
        content = json.loads(base64.b64decode(actual["payload"]))
        expected = json.loads(base64.b64decode(definition["parts"][0]["payload"]))
        if "".join(content["cells"][0]["source"]) != "".join(expected["cells"][0]["source"]):
            raise ValueError("Published notebook content failed exact code readback")
        if schedule_minutes:
            # Bounded by default: unattended Fabric execution, not a local poller.
            now = datetime.now(UTC)
            schedule = {
                "enabled": True,
                "configuration": {
                    "type": "Cron",
                    "interval": schedule_minutes,
                    "startDateTime": (now + timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "endDateTime": (now + timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "localTimeZoneId": "UTC",
                },
            }
            existing = request("GET", f"/items/{item}/jobs/RunNotebook/schedules").json()
            write_json(output / "fabric-projector-schedules-before.json", existing)
            enabled = [row for row in existing["value"] if row["enabled"]]
            if enabled:
                if (
                    len(enabled) != 1
                    or enabled[0]["id"] != receipt.get("scheduleId")
                    or enabled[0]["configuration"]["interval"] != schedule_minutes
                ):
                    raise ValueError("An unowned/different projector schedule already exists")
                return receipt
            created = request(
                "POST", f"/items/{item}/jobs/RunNotebook/schedules", json=schedule
            ).json()
            write_json(output / "fabric-projector-schedule.json", created)
            read_schedule = request(
                "GET", f"/items/{item}/jobs/RunNotebook/schedules/{created['id']}"
            ).json()
            write_json(output / "fabric-projector-schedule-readback.json", read_schedule)
            receipt["scheduleId"] = created["id"]
            receipt["scheduleExpiresAt"] = schedule["configuration"]["endDateTime"]
            receipt["automaticProjection"] = "scheduled-not-yet-verified"
            write_json(output / "fabric-projector.json", receipt)
        return receipt
    finally:
        setup.client.close()


def verify(config, credential, output):
    """One bounded verification of scheduled cloud work; not an ongoing runner."""
    setup = FabricSetup(credential, config, output)
    try:
        receipt = json.loads((output / "fabric-projector.json").read_text())
        item = str(UUID(receipt["notebookId"]))
        base = f"https://api.fabric.microsoft.com/v1/workspaces/{config['workspaceId']}"
        headers = {
            "Authorization": "Bearer "
            + credential.get_token("https://api.fabric.microsoft.com/.default").token
        }
        response = setup.client.get(base + f"/items/{item}/jobs/instances", headers=headers)
        response.raise_for_status()
        jobs = response.json()
        write_json(output / "fabric-projector-jobs.json", jobs)
        rows = setup.kql(f"{'CloseoutFlowFactsV02'} | project snapshot")
        for row in rows:
            verify_snapshot(row["snapshot"])
        write_json(output / "fabric-projector-snapshots.json", rows)
        transitions = setup.kql(readiness_query())
        write_json(output / "fabric-readiness-transitions.json", transitions)
        receipt["automaticProjection"] = bool(
            rows
            and any(
                job.get("invokeType") == "Scheduled" and job["status"] == "Completed"
                for job in jobs["value"]
            )
        )
        write_json(output / "fabric-projector-verification.json", receipt)
        return {
            "receipt": receipt,
            "jobs": jobs,
            "snapshots": len(rows),
            "transitions": transitions,
        }
    finally:
        setup.client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--storage-endpoint", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--schedule-minutes", type=int, default=0)
    args = parser.parse_args()
    config = json.loads(args.config.read_text("utf-8-sig"))
    config = {**config.get("fabric", config), "storageEndpoint": args.storage_endpoint}
    from azure.identity import AzureCliCredential

    with AzureCliCredential(process_timeout=60) as credential:
        if args.verify:
            result = verify(config, credential, args.output)
        elif args.apply:
            result = deploy(
                config,
                credential,
                args.output,
                Path(__file__).resolve().parents[2],
                schedule_minutes=args.schedule_minutes,
            )
        else:
            parser.error("Choose --apply or --verify")
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

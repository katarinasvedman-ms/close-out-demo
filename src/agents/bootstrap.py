"""Populate native IQ and register prompt agents after a successful portal deployment."""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from shared.local_demo import write_json

SITES = ("SE-STO-0042", "SE-GOT-0017")
ROOT = Path(__file__).resolve().parents[2]


def command(executable: str, *args: str) -> str:
    executable = shutil.which(executable) or executable
    result = subprocess.run(
        [executable, *args],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    return result.stdout


def validate_target(values: dict) -> None:
    subscription = values["subscriptionId"]
    group = values["resourceGroupName"]
    account = values["foundryName"]
    project = values["projectName"]
    expected = (
        f"/subscriptions/{subscription}/resourceGroups/{group}"
        f"/providers/Microsoft.CognitiveServices/accounts/{account}/projects/{project}"
    )
    if values["projectResourceId"].lower() != expected.lower():
        raise ValueError("Deployment outputs do not describe the selected Foundry project")


def verify_result(receipt: dict, site: str) -> dict:
    if "error" in receipt:
        raise ValueError("Prompt-agent invocation failed")
    expected = {"CHK-A-GROUNDING", "CHK-A-AZ-B", "PL-0042-01"} if site == SITES[0] else set()
    result = receipt["result"]
    if (
        result["siteId"] != site
        or {gap["conditionId"] for gap in result["gaps"]} != expected
        or len(result["gaps"]) != len(expected)
        or (bool(expected) != (result["proposedRework"] is not None))
        or not receipt.get("knowledgeRetrieval")
    ):
        raise ValueError("Prompt-agent result did not match the cited blocked/Ready fixture")
    return receipt


def complete(resource_group: str, deployment: str) -> Path:
    account = json.loads(command("az", "account", "show", "--output", "json"))
    subscription = account["id"]
    deployed = json.loads(
        command(
            "az",
            "deployment",
            "group",
            "show",
            "--resource-group",
            resource_group,
            "--subscription",
            subscription,
            "--name",
            deployment,
            "--output",
            "json",
        )
    )
    if deployed["properties"]["provisioningState"] != "Succeeded":
        raise ValueError("Complete the infrastructure deployment successfully first")
    outputs = deployed["properties"]["outputs"]
    values = {key: output["value"] for key, output in outputs.items()}
    if (
        values["subscriptionId"].lower() != subscription.lower()
        or values["tenantId"].lower() != account["tenantId"].lower()
        or values["resourceGroupName"].lower() != resource_group.lower()
    ):
        raise ValueError("Azure CLI context and deployment target differ")
    validate_target(values)
    name = values["foundryName"]
    state = ROOT / ".local" / "fresh" / subscription / name
    state.mkdir(parents=True, exist_ok=True)
    write_json(state / "infra.json", outputs)
    (state / "completion.json").unlink(missing_ok=True)
    print("Generating synthetic evidence and configuring native Blob IQ...", flush=True)
    command(sys.executable, "-m", "shared.local_demo", "--output", str(state / "demo"))
    command(
        sys.executable,
        "-m",
        "agents.knowledge_setup",
        "--infra",
        str(state / "infra.json"),
        "--assets",
        str(state / "demo"),
        "--site",
        SITES[0],
        "--site",
        SITES[1],
        "--output",
        str(state / "knowledge.json"),
    )
    print("Registering versioned Foundry prompt agents...", flush=True)
    command(
        sys.executable,
        "-m",
        "agents",
        "register",
        "--endpoint",
        values["projectEndpoint"],
        "--deployment",
        values["modelDeploymentName"],
        "--model",
        values["modelName"],
        "--model-version",
        values["modelVersion"],
        "--knowledge-config",
        str(state / "knowledge.json"),
        "--config",
        str(state / "config.json"),
    )
    print("Verifying prompt agents with the local synthetic-facts client...", flush=True)
    for site, scenario in zip(SITES, ("initial-blocked", "fallback-ready"), strict=True):
        output = state / "verification" / site
        output.mkdir(parents=True, exist_ok=True)
        previous = set(output.glob("gaps-*.json"))
        command(
            sys.executable,
            "-m",
            "agents",
            "gaps",
            "--site",
            site,
            "--scenario",
            scenario,
            "--config",
            str(state / "config.json"),
            "--assets",
            str(state / "demo"),
            "--output",
            str(output),
        )
        created = set(output.glob("gaps-*.json")) - previous
        if len(created) != 1:
            raise ValueError("Prompt verification did not produce one new receipt")
        receipt = verify_result(json.loads(created.pop().read_text(encoding="utf-8")), site)
        write_json(state / f"{site}-verified.json", receipt)
    write_json(
        state / "completion.json",
        {
            "passed": True,
            "foundryName": name,
            "projectEndpoint": values["projectEndpoint"],
            "sites": list(SITES),
            "facts": "stub",
            "documents": "live-synthetic-iq",
            "verificationRuntime": "local-agent-framework-client",
        },
    )
    return state


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource-group", required=True)
    parser.add_argument("--deployment", required=True, help="Successful portal deployment name")
    args = parser.parse_args()
    print(complete(args.resource_group, args.deployment))


if __name__ == "__main__":
    main()

"""Publish prompt/IQ definitions, optional Azure flow code and optional seed verification."""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from agents.definitions import FabricSettings, FoundrySettings, PublisherSettings
from agents.fabric_connection import ensure_connection
from agents.knowledge_setup import knowledge_settings
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
    endpoint = urlsplit(values["projectEndpoint"])
    if (
        endpoint.scheme != "https"
        or endpoint.netloc.lower() != f"{account}.services.ai.azure.com".lower()
        or endpoint.path.rstrip("/") != f"/api/projects/{project}"
        or endpoint.query
        or endpoint.fragment
    ):
        raise ValueError("Deployment endpoint does not describe the selected Foundry project")
    if "flow" in values:
        flow = values["flow"]
        publisher = PublisherSettings.model_validate(flow["publisherConfig"])
        if publisher.serverUrl != f"https://{flow['adapterName']}.azurewebsites.net":
            raise ValueError("Publisher endpoint does not describe the selected flow adapter")


def publish_flow(values: dict, state: Path) -> dict:
    flow = values["flow"]
    settings = FoundrySettings.model_validate_json((state / "config.json").read_text())
    if not settings.fabric or settings.publisher != PublisherSettings.model_validate(
        flow["publisherConfig"]
    ):
        raise ValueError(
            "Flow publication requires the native Fabric and selected publisher binding"
        )
    if settings.endpoint != values["projectEndpoint"]:
        raise ValueError("Agent configuration does not describe the selected Foundry project")
    if any(
        binding.version == "pending-publication"
        for binding in (settings.validation, settings.closeout)
    ):
        raise ValueError("Publish actual agent versions before deploying the flow")
    settings_files = {
        flow["adapterName"]: {"FLOW_FOUNDRY_CONFIG_JSON": settings.model_dump_json()},
        flow["workflowName"]: {
            "FLOW_FOUNDRY_PROJECT_ENDPOINT": settings.endpoint,
            "FLOW_COORDINATOR_AGENT_NAME": settings.closeout.name,
            "FLOW_COORDINATOR_AGENT_VERSION": settings.closeout.version,
            "Workflows.coordinator.FlowState": "Disabled",
            "Workflows.validation.FlowState": "Disabled",
        },
    }
    for app, app_settings in settings_files.items():
        target = state / f"{app}-settings.json"
        write_json(target, app_settings)
        command(
            "az",
            "webapp",
            "config",
            "appsettings",
            "set",
            "--subscription",
            values["subscriptionId"],
            "--resource-group",
            values["resourceGroupName"],
            "--name",
            app,
            "--settings",
            f"@{target}",
            "--output",
            "none",
        )
    packages = state / "packages" / uuid4().hex
    command(
        "pwsh",
        "-NoProfile",
        "-File",
        str(ROOT / "scripts" / "package-flow.ps1"),
        "-OutputDirectory",
        str(packages),
    )
    deployments = {}
    for app, kind, filename, extra in (
        (flow["adapterName"], "functionapp", "adapter.zip", ("--build-remote", "true")),
        (flow["workflowName"], "webapp", "workflows.zip", ()),
    ):
        result = json.loads(
            command(
                "az",
                kind,
                "deployment",
                "source",
                "config-zip",
                "--subscription",
                values["subscriptionId"],
                "--resource-group",
                values["resourceGroupName"],
                "--name",
                app,
                "--src",
                str(packages / filename),
                "--timeout",
                "1200",
                "--output",
                "json",
                *extra,
            )
        )
        if result.get("status") != 4 or result.get("complete") is not True:
            raise RuntimeError(f"ZIP deployment did not report completed success: {app}")
        deployments[app] = {"id": result.get("id"), "status": result["status"]}
    receipt = {
        "passed": True,
        "phase": "runtime-publication",
        "deployments": deployments,
        "validationAgent": settings.validation.model_dump(),
        "coordinatorAgent": settings.closeout.model_dump(),
        "workflowStates": {"validation": "Disabled", "coordinator": "Disabled"},
        "unattendedVerification": "not-run",
        "manualSteps": "docs/MANUAL_STEPS.md#playground-flow-deployment-checklist",
    }
    write_json(state / "flow-deployment.json", receipt)
    return receipt


def verify_result(receipt: dict, site: str, *, fabric: FabricSettings | None = None) -> dict:
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
    if fabric and (
        not receipt.get("fabricRetrieval")
        or receipt.get("modes", {}).get("facts") != "native-fabric-iq-synthetic-fixtures"
        or any(row.get("tool") != fabric.toolName for row in receipt["fabricRetrieval"])
    ):
        raise ValueError("Native verification requires actual Fabric retrieval, not stub facts")
    return receipt


def complete(
    resource_group: str,
    deployment: str,
    *,
    seed_and_verify: bool = False,
    facts_mode: str = "fabric",
    fabric: FabricSettings | None = None,
    create_fabric_connection: bool = False,
    publish_runtime: bool = False,
) -> Path:
    if facts_mode not in {"fabric", "stub"}:
        raise ValueError("Facts mode must be fabric or stub")
    if (facts_mode == "fabric") != (fabric is not None):
        raise ValueError("Fabric mode requires Fabric configuration; stub mode must not supply it")
    if create_fabric_connection and not fabric:
        raise ValueError("Creating a Fabric connection requires Fabric mode")
    if publish_runtime and not fabric:
        raise ValueError("Flow runtime publication requires native Fabric, not stub facts")
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
    if publish_runtime and "flow" not in values:
        raise ValueError(
            "This deployment has no flow foundation; deploy the updated template first"
        )
    name = values["foundryName"]
    state = ROOT / ".local" / "fresh" / subscription / name
    state.mkdir(parents=True, exist_ok=True)
    write_json(state / "infra.json", outputs)
    (state / "deployment.json").unlink(missing_ok=True)
    (state / "completion.json").unlink(missing_ok=True)
    if publish_runtime:
        (state / "flow-deployment.json").unlink(missing_ok=True)
    fabric_args = ()
    publisher_args = ()
    if "flow" in values:
        publisher = PublisherSettings.model_validate(values["flow"]["publisherConfig"])
        write_json(state / "publisher.json", publisher.model_dump())
        from agents.flow_publish import openapi_document

        write_json(
            state / "validation-publisher.openapi.json", openapi_document(publisher.serverUrl)
        )
        publisher_args = ("--publisher-config", str(state / "publisher.json"))
    if fabric:
        (state / "fabric-connection-receipt.json").unlink(missing_ok=True)
        connection = ensure_connection(
            values["projectResourceId"], fabric, create=create_fabric_connection
        )
        write_json(state / "fabric-connection-receipt.json", connection)
        write_json(state / "fabric.json", fabric.model_dump())
        fabric_args = ("--fabric-config", str(state / "fabric.json"))
    knowledge = knowledge_settings(values, list(SITES))
    write_json(state / "knowledge.json", knowledge.model_dump())
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
        *fabric_args,
        *publisher_args,
    )
    print("Publishing native Blob IQ definitions without uploading documents...", flush=True)
    knowledge_command = (
        "-m",
        "agents.knowledge_setup",
        "--infra",
        str(state / "infra.json"),
        "--site",
        SITES[0],
        "--site",
        SITES[1],
        "--output",
        str(state / "knowledge.json"),
    )
    command(sys.executable, *knowledge_command, "--definitions-only")
    write_json(
        state / "deployment.json",
        {
            "passed": True,
            "phase": "definitions",
            "foundryName": name,
            "projectEndpoint": values["projectEndpoint"],
            "knowledgeBase": knowledge.knowledgeBase,
            "agents": json.loads((state / "config.json").read_text(encoding="utf-8")),
            "facts": "native-fabric-iq-synthetic-fixtures" if fabric else "stub",
            "fabricVerification": "not-run" if fabric else "not-configured",
            "unattendedVerification": "not-run",
            "documentVerification": "not-run",
        },
    )
    if publish_runtime:
        print("Publishing the selected flow runtime; both workflows remain disabled...", flush=True)
        publish_flow(values, state)
    if not seed_and_verify:
        print(
            "Definitions published. Document loading and inference were not run. "
            + (
                "Complete the manual flow checklist before enabling coordinator dispatch. "
                if "flow" in values
                else ""
            )
            + "Use --seed-and-verify separately when document access is available.",
            flush=True,
        )
        return state
    print("Generating and uploading synthetic evidence, then checking ingestion...", flush=True)
    command(sys.executable, "-m", "shared.local_demo", "--output", str(state / "demo"))
    command(sys.executable, *knowledge_command, "--assets", str(state / "demo"))
    print(
        "Verifying native Fabric and document IQ with the signed-in local client..."
        if fabric
        else "Verifying prompt agents with the local synthetic-facts client...",
        flush=True,
    )
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
            *(("--program", "CUST-A-5G-ROLLOUT") if fabric else ()),
        )
        created = set(output.glob("gaps-*.json")) - previous
        if len(created) != 1:
            raise ValueError("Prompt verification did not produce one new receipt")
        receipt = verify_result(
            json.loads(created.pop().read_text(encoding="utf-8")), site, fabric=fabric
        )
        write_json(state / f"{site}-verified.json", receipt)
    write_json(
        state / "completion.json",
        {
            "passed": True,
            "foundryName": name,
            "projectEndpoint": values["projectEndpoint"],
            "sites": list(SITES),
            "facts": "native-fabric-iq-synthetic-fixtures" if fabric else "stub",
            "fabricAuthorization": "foundry-project-managed-identity"
            if fabric
            else "not-configured",
            "invocationAuthorization": "signed-in-user",
            "unattendedVerification": "not-run",
            "documents": "live-synthetic-iq",
            "verificationRuntime": "local-agent-framework-client",
        },
    )
    return state


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource-group", required=True)
    parser.add_argument("--deployment", required=True, help="Successful portal deployment name")
    parser.add_argument(
        "--facts-mode",
        choices=("fabric", "stub"),
        default="fabric",
        help="Native external Fabric by default; local stub facts require explicit opt-in",
    )
    parser.add_argument("--fabric-config", type=Path, help="External FabricSettings JSON file")
    parser.add_argument(
        "--create-fabric-connection",
        action="store_true",
        help="Create an absent project-managed-identity connection in the selected Foundry project",
    )
    parser.add_argument(
        "--publish-flow",
        action="store_true",
        help="Deploy the selected flow ZIPs with actual agent pins; keep workflows disabled",
    )
    parser.add_argument(
        "--seed-and-verify",
        action="store_true",
        help="Also upload synthetic documents, wait for indexing and invoke both demo cases",
    )
    args = parser.parse_args()
    fabric = None
    if args.facts_mode == "stub":
        if args.fabric_config or args.create_fabric_connection:
            parser.error("Stub mode cannot configure a Fabric connection")
    elif args.fabric_config:
        fabric = FabricSettings.model_validate_json(
            args.fabric_config.read_text(encoding="utf-8-sig")
        )
    elif sys.stdin.isatty():
        print("Supply the existing published Fabric target. No Fabric items will be deployed.")
        fabric = FabricSettings.model_validate(
            {
                field: input(f"{label}: ").strip()
                for field, label in (
                    ("workspaceId", "Fabric workspace GUID"),
                    ("dataAgentId", "Published Data Agent GUID"),
                    ("databaseId", "KQL database GUID (independent verifier)"),
                    ("kustoEndpoint", "HTTPS Fabric Kusto endpoint (independent verifier)"),
                    ("connectionName", "Connection name in the selected Foundry project"),
                    ("toolName", "Actual exposed DataAgent_ tool name"),
                )
            }
        )
    else:
        parser.error(
            "Supply --fabric-config for native Fabric or --facts-mode stub explicitly; "
            "interactive terminals can prompt for Fabric settings"
        )
    print(
        complete(
            args.resource_group,
            args.deployment,
            seed_and_verify=args.seed_and_verify,
            facts_mode=args.facts_mode,
            fabric=fabric,
            create_fabric_connection=args.create_fabric_connection,
            publish_runtime=args.publish_flow,
        )
    )


if __name__ == "__main__":
    main()

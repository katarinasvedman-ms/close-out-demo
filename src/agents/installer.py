"""Guided Azure/Foundry installation against explicitly selected existing Fabric."""

import argparse
import fnmatch
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from agents import assistant, bootstrap, coordinator_brief, installer_fabric, knowledge_setup
from agents.coordinator_oauth import OWNER, OAuthClient
from agents.coordinator_workflow import delegated_workflow, readable_workflow
from agents.definitions import AgentBinding, FabricSettings, FoundrySettings
from agents.fabric_connection import ensure_connection
from agents.installer_state import (
    ROOT,
    Blocked,
    Profile,
    Progress,
    atomic_json,
    az,
    confirm,
    digest,
    select,
    source_hash,
    text,
)

PROVIDERS = (
    "Microsoft.CognitiveServices",
    "Microsoft.Search",
    "Microsoft.Storage",
    "Microsoft.OperationalInsights",
    "Microsoft.Insights",
    "Microsoft.Web",
    "Microsoft.KeyVault",
    "Microsoft.ManagedIdentity",
)
BLOB_READER = "2a2b9908-6ea1-4ae2-8e65-a410df84e7d1"


def choose_target(progress):
    profile = progress.profile
    accounts = az("account", "list")
    if not profile:
        account = select(
            "Azure subscription", [row for row in accounts if row["state"] == "Enabled"], "name"
        )
    else:
        account = next(
            (
                row
                for row in accounts
                if row["id"] == profile.subscriptionId and row["tenantId"] == profile.tenantId
            ),
            None,
        )
        if not account:
            raise Blocked(
                "Operator", "Sign in to the tenant/subscription saved for this environment."
            )
    az("account", "set", "--subscription", account["id"])
    operator = az("ad", "signed-in-user", "show", "--query", "id")
    if profile:
        if profile.operatorId != operator:
            raise Blocked(
                "Operator",
                "Resume using the original sign-in. An administrator should grant that user "
                "access rather than silently replace the deployment operator.",
            )
        return
    environment = progress.environment
    group = text(
        "Dedicated Azure resource group (new or existing)",
        f"rg-{environment}",
        r"[a-zA-Z0-9_-]{1,80}",
    )
    name = text(
        "New globally unique Foundry name",
        f"closeout-{environment}-{uuid4().hex[:6]}"[:64],
        r"[a-z0-9][a-z0-9-]{1,62}[a-z0-9]",
    )
    profile = Profile(
        environment=environment,
        subscriptionId=str(UUID(account["id"])),
        tenantId=str(UUID(account["tenantId"])),
        operatorId=str(UUID(operator)),
        resourceGroup=group,
        foundryName=name,
        deploymentName=f"closeout-{environment}",
    )
    print(f"\nAzure: {account['name']} / {group}\nFoundry: {name}")
    if not confirm(
        "Use this dedicated target? The Azure foundation will be prepared before application "
        "registrations and existing Fabric setup. No Fabric items will be created."
    ):
        raise Blocked("Operator", "Restart when you are ready to select a target.")
    progress.profile = profile
    progress.save_profile()


def permissions(scope):
    rows = az(
        "rest",
        "--method",
        "get",
        "--url",
        f"https://management.azure.com{scope}/providers/Microsoft.Authorization/"
        "permissions?api-version=2022-04-01",
    )["value"]

    def permits(action):
        return any(
            any(fnmatch.fnmatchcase(action.lower(), pattern.lower()) for pattern in row["actions"])
            and not any(
                fnmatch.fnmatchcase(action.lower(), pattern.lower())
                for pattern in row.get("notActions", [])
            )
            for row in rows
        )

    return permits


def preflight(progress):
    profile = progress.profile
    subscription = f"/subscriptions/{profile.subscriptionId}"
    group_scope = subscription + "/resourceGroups/" + profile.resourceGroup
    exists = az("group", "exists", "--name", profile.resourceGroup)
    allowed = permissions(group_scope if exists else subscription)
    required = [
        "Microsoft.Resources/deployments/write",
        "Microsoft.Authorization/roleAssignments/write",
        "Microsoft.CognitiveServices/accounts/write",
        "Microsoft.Web/sites/write",
        "Microsoft.Storage/storageAccounts/write",
        "Microsoft.Search/searchServices/write",
        "Microsoft.KeyVault/vaults/write",
    ]
    if not exists:
        required.append("Microsoft.Resources/subscriptions/resourceGroups/write")
    if not all(allowed(action) for action in required):
        raise Blocked(
            "Azure subscription/resource-group administrator",
            f"Grant user {profile.operatorId} resource-creation and role-assignment access "
            "(for example Owner) on the dedicated target. For a new group, either allow group "
            "creation or create the group and grant access there. Entra permissions are separate.",
            group_scope,
        )
    if exists:
        group = az("group", "show", "--name", profile.resourceGroup)
        marker = group.get("tags", {}).get("closeout-installer")
        if marker and marker != profile.environment:
            raise Blocked(
                "Azure administrator",
                "This group is assigned to another demo environment.",
                group_scope,
            )
        progress.approve(
            "dedicated-group",
            group_scope,
            f"Confirm {profile.resourceGroup} is a dedicated demo group "
            "and may host this solution. "
            "Unrelated resources will not be removed.",
        )
    missing = [
        name
        for name in PROVIDERS
        if az("provider", "show", "--namespace", name, "--query", "registrationState")
        != "Registered"
    ]
    if missing:
        progress.approve(
            "providers",
            missing,
            "Register these Azure resource providers in the selected subscription: "
            + ", ".join(missing),
        )
        for name in missing:
            az("provider", "register", "--namespace", name, "--wait")
    if not any(
        progress.data.get(key)
        for key in ("foundation", "foundationPending", "infra", "infraPending")
    ):
        models = az(
            "cognitiveservices",
            "model",
            "list",
            "--location",
            "swedencentral",
            "--query",
            "[?model.name=='gpt-4o' && model.version=='2024-11-20']",
        )
        if not models:
            raise Blocked(
                "Azure administrator",
                "Make regional GPT-4o 2024-11-20 available; no model/region fallback is allowed.",
            )
        quota = az(
            "cognitiveservices",
            "usage",
            "list",
            "--location",
            "swedencentral",
            "--query",
            "[?contains(name.value, 'OpenAI.Standard.gpt-4o')].{limit:limit,current:currentValue}",
        )
        if not quota or not any(row["limit"] - row["current"] >= 30 for row in quota):
            raise Blocked(
                "Azure quota administrator",
                "Confirm at least capacity 30 of regional Standard GPT-4o quota in Sweden Central "
                "and obtain any necessary quota approval. No alternate SKU or region was selected.",
            )
    progress.approve(
        "target-policy",
        {"group": group_scope, "hosting": "WS1/EP1", "search": "francecentral"},
        "This demo uses billable WS1/EP1 hosting, Basic Search in France Central, models and "
        "monitoring in Sweden Central. It requires authenticated public connectivity and Azure "
        "Files host keys held in Key Vault. The callback uses an approved service-principal "
        "credential and the notebook runs as its owner; this is NOT all-managed-identity. "
        "Has the target owner approved these choices? Any supported resource-group networking "
        "exception requires a separate, explicit policy-owner approval.",
    )


def prepare_application(progress, *, callback=False):
    profile = progress.profile
    purpose = "callback" if callback else "api"
    name = f"closeout-{profile.environment}-{purpose}"
    marker = f"close-out-demo installer: {profile.subscriptionId}/{profile.resourceGroup}/{purpose}"
    if callback and profile.dispatchPrincipalId:
        principal = az(
            "ad",
            "sp",
            "show",
            "--id",
            profile.dispatchPrincipalId,
            "--query",
            "{id:id,appId:appId}",
        )
        if profile.dispatchAppId and profile.dispatchAppId != principal["appId"]:
            raise Blocked("Entra administrator", "The saved callback identity changed.")
        profile.dispatchAppId = str(UUID(principal["appId"]))
        progress.save_profile()
        return principal
    if not callback and profile.apiClientId:
        app = az(
            "ad",
            "app",
            "show",
            "--id",
            profile.apiClientId,
            "--query",
            "{id:id,appId:appId,signInAudience:signInAudience,identifierUris:identifierUris,api:api}",
        )
    else:
        if callback:
            print(
                "\nApplication 2 of 2: Fabric callback caller identity.\n"
                "This is a DIFFERENT application from the API registration above. "
                "Fabric uses this identity to call the coordinator Logic App."
            )
            prompt = "Existing Fabric callback application (client) ID"
        else:
            print(
                "\nApplication 1 of 2: Protected API registration.\n"
                "This application identifies the publisher and coordinator APIs receiving calls. "
                "It is not the caller identity used by Fabric."
            )
            prompt = "Existing protected API application (client) ID"
        print(
            "Saved applications are reused automatically. Otherwise the installer can prepare "
            "a dedicated registration without a secret, after your approval."
        )
        client_id = ""
        if confirm(
            "Use an existing application supplied by your administrator instead? "
            "Choose n for automatic preparation of a dedicated registration."
        ):
            client_id = text(
                prompt,
                pattern=r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-"
                r"[a-fA-F0-9]{4}-[a-fA-F0-9]{12}",
            )
        if client_id:
            client_id = str(UUID(client_id))
            app = az(
                "ad",
                "app",
                "show",
                "--id",
                client_id,
                "--query",
                "{id:id,appId:appId,signInAudience:signInAudience,identifierUris:identifierUris,api:api}",
            )
        else:
            matches = az(
                "ad",
                "app",
                "list",
                "--filter",
                f"displayName eq '{name}'",
                "--query",
                "[].{id:id,appId:appId,notes:notes,signInAudience:signInAudience,identifierUris:identifierUris,api:api}",
            )
            if len(matches) > 1 or (matches and matches[0].get("notes") != marker):
                raise Blocked(
                    "Entra administrator", f"Resolve ambiguous or unrelated application {name}."
                )
            progress.approve(
                purpose + "-registration",
                marker,
                f"Prepare the dedicated single-tenant {purpose} application {name}? "
                "No password, API permission or consent grant will be created. "
                "Directory permission is required separately from Azure Owner.",
            )
            body = {
                "displayName": name,
                "signInAudience": "AzureADMyOrg",
                "notes": marker,
                "requiredResourceAccess": [],
            }
            if matches:
                app = matches[0]
            else:
                path = progress.directory / f"{purpose}-application-request.json"
                atomic_json(path, body)
                try:
                    app = az(
                        "rest",
                        "--method",
                        "post",
                        "--url",
                        "https://graph.microsoft.com/v1.0/applications",
                        "--body",
                        "@" + str(path),
                    )
                except Blocked as error:
                    raise Blocked(
                        "Entra application administrator",
                        f"Create single-tenant registration {name}, without default permissions "
                        "or secret. Give the operator its client ID to select on rerun. "
                        + (
                            ""
                            if callback
                            else "Set identifier URI api://<client-id> and token version 2."
                        ),
                        profile.tenantId,
                    ) from error
    if app.get("signInAudience") != "AzureADMyOrg":
        raise Blocked(
            "Entra administrator", "Use an approved single-tenant application.", app["appId"]
        )
    if callback:
        progress.approve(
            "callback-exception",
            app["appId"],
            "Has the target owner explicitly approved service-principal credential authentication "
            "for Fabric dispatch? The installer creates no password or connection.",
        )
        principals = az(
            "ad",
            "sp",
            "list",
            "--filter",
            f"appId eq '{app['appId']}'",
            "--query",
            "[].{id:id,appId:appId}",
        )
        principal = (
            principals[0]
            if len(principals) == 1
            else az("ad", "sp", "create", "--id", app["appId"], "--query", "{id:id,appId:appId}")
        )
        profile.dispatchPrincipalId = str(UUID(principal["id"]))
        profile.dispatchAppId = str(UUID(principal["appId"]))
        progress.save_profile()
        print(f"Fabric callback caller selected: {profile.dispatchAppId}")
        return principal
    profile.apiClientId, profile.apiObjectId = str(UUID(app["appId"])), str(UUID(app["id"]))
    progress.save_profile()
    uri = "api://" + profile.apiClientId
    if (
        app.get("identifierUris") != [uri]
        or (app.get("api") or {}).get("requestedAccessTokenVersion") != 2
    ):
        progress.approve(
            "api-settings",
            {"id": app["id"], "uri": uri},
            f"Set the dedicated API {profile.apiClientId} identifier URI to {uri} "
            "and token version "
            "to 2? Do not use an application shared by another solution.",
        )
        payload = progress.directory / "api-settings.json"
        atomic_json(
            payload,
            {
                "identifierUris": [uri],
                "api": {**(app.get("api") or {}), "requestedAccessTokenVersion": 2},
            },
        )
        az(
            "rest",
            "--method",
            "patch",
            "--url",
            f"https://graph.microsoft.com/v1.0/applications/{profile.apiObjectId}",
            "--body",
            "@" + str(payload),
        )
    print(f"Protected API registration ready: {profile.apiClientId}")
    return app


def validate_outputs(profile, values):
    bootstrap.validate_target(values)
    if any(
        values[key].lower() != expected.lower()
        for key, expected in (
            ("subscriptionId", profile.subscriptionId),
            ("tenantId", profile.tenantId),
            ("resourceGroupName", profile.resourceGroup),
            ("foundryName", profile.foundryName),
        )
    ):
        raise Blocked("Azure administrator", "ARM outputs do not match the saved selected target.")


def supports_group_network_exception(policy):
    base = "https://management.azure.com"
    definition = az(
        "rest",
        "--method",
        "get",
        "--url",
        base + policy["policyDefinitionId"] + "?api-version=2023-04-01",
        "--query",
        "properties",
    )
    expected = {"AllowedTagName": "SecurityControl", "AllowedTagValue": "Ignore"}
    if any(
        definition.get("parameters", {}).get(key, {}).get("defaultValue") != value
        for key, value in expected.items()
    ):
        return False
    exclusions = [
        row
        for condition in definition.get("policyRule", {}).get("if", {}).get("allOf", [])
        for row in condition.get("not", {}).get("anyOf", [])
    ]
    if {
        "value": "[resourceGroup().tags[parameters('AllowedTagName')]]",
        "equals": "[parameters('AllowedTagValue')]",
    } not in exclusions:
        return False
    assignment = az(
        "rest",
        "--method",
        "get",
        "--url",
        base + policy["policyAssignmentId"] + "?api-version=2023-04-01",
        "--query",
        "properties",
    )
    source_id = policy.get("policySetDefinitionId") or policy["policyDefinitionId"]
    if assignment.get("policyDefinitionId", "").lower() != source_id.lower():
        return False
    if policy.get("policySetDefinitionId"):
        initiative = az(
            "rest",
            "--method",
            "get",
            "--url",
            base + source_id + "?api-version=2023-04-01",
            "--query",
            "properties.policyDefinitions",
        )
        references = [
            row
            for row in initiative
            if row["policyDefinitionReferenceId"] == policy["policyDefinitionReferenceId"]
            and row["policyDefinitionId"].lower() == policy["policyDefinitionId"].lower()
        ]
        if len(references) != 1:
            return False
        parameters = references[0].get("parameters") or {}
    else:
        parameters = assignment.get("parameters") or {}
    return all(
        parameters.get(key, {}).get("value", value) == value for key, value in expected.items()
    )


def apply_group_network_exception(progress):
    profile = progress.profile
    group = az(
        "group", "show", "--subscription", profile.subscriptionId, "--name", profile.resourceGroup
    )
    tags = group.get("tags") or {}
    control = next((value for key, value in tags.items() if key.lower() == "securitycontrol"), None)
    if control == "Ignore":
        return False
    vaults = az(
        "resource",
        "list",
        "--subscription",
        profile.subscriptionId,
        "--resource-group",
        profile.resourceGroup,
        "--resource-type",
        "Microsoft.KeyVault/vaults",
    )
    vaults = [
        row for row in vaults if (row.get("tags") or {}).get("azd-env-name") == profile.foundryName
    ]
    if len(vaults) != 1:
        return False
    vault = vaults[0]
    network = az(
        "keyvault",
        "show",
        "--subscription",
        profile.subscriptionId,
        "--resource-group",
        profile.resourceGroup,
        "--name",
        vault["name"],
        "--query",
        "properties.publicNetworkAccess",
    )
    if network != "Disabled":
        return False
    events = az(
        "monitor",
        "activity-log",
        "list",
        "--subscription",
        profile.subscriptionId,
        "--resource-id",
        vault["id"],
        "--offset",
        "1d",
        "--max-events",
        "100",
        "--query",
        "[?operationName.value=='Microsoft.Authorization/policies/modify/action'].properties",
    )
    policy = None
    for event in events:
        fields = json.loads(event.get("fields") or "[]")
        if not any(
            row.get("field") == "Microsoft.KeyVault/vaults/publicNetworkAccess" for row in fields
        ):
            continue
        for candidate in json.loads(event.get("policies") or "[]"):
            if candidate.get(
                "policyDefinitionName"
            ) == "KeyVault_PublicNetwork_Modify" and supports_group_network_exception(candidate):
                policy = candidate
                break
        if policy:
            break
    if not policy:
        raise Blocked(
            "Azure policy / networking administrator",
            "The demo Key Vault has public networking disabled. The installer cannot confirm "
            "a supported SecurityControl=Ignore policy exception. Approve a supported exception "
            "or provide compliant connectivity; no tag or policy was changed.",
            vault["id"],
        )
    if tags.get("closeout-installer") != profile.environment or control is not None:
        raise Blocked(
            "Azure policy administrator",
            "The networking exception is supported, but this group is not marked for this "
            "installer environment or already has a different SecurityControl tag. Review and "
            "apply any approved exception yourself; existing tags were not changed.",
            group["id"],
        )
    # A removed exception tag requires fresh approval, not a cached authorization.
    profile.approvals.pop("group-network-exception", None)
    progress.save_profile()
    progress.approve(
        "group-network-exception",
        {
            "group": group["id"],
            "tag": {"SecurityControl": "Ignore"},
            "policy": policy["policyAssignmentId"],
            "definition": policy["policyDefinitionId"],
        },
        f"Azure policy disabled networking on this demo's Key Vault. Has the policy owner "
        f"approved setting SecurityControl=Ignore on ONLY {profile.resourceGroup} in subscription "
        f"{profile.subscriptionId}, and are you authorized to apply it? This exempts matching "
        "security controls across this dedicated group, not just Key Vault. The installer will "
        "set the tag, retain other tags, and redeploy the declared authenticated public "
        "networking. Authentication remains required. Cost/deployment approval is NOT approval "
        "for this exception. Decline if owner approval is missing.",
    )
    current = (
        az(
            "group",
            "show",
            "--subscription",
            profile.subscriptionId,
            "--name",
            profile.resourceGroup,
            "--query",
            "tags",
        )
        or {}
    )
    if current != tags:
        raise Blocked(
            "Azure policy administrator",
            "Resource-group tags changed during approval. Review them and rerun; no tag was set.",
            group["id"],
        )
    try:
        az(
            "tag",
            "update",
            "--subscription",
            profile.subscriptionId,
            "--resource-id",
            group["id"],
            "--operation",
            "Merge",
            "--tags",
            "SecurityControl=Ignore",
        )
    except Blocked as error:
        raise Blocked(
            "Azure policy administrator",
            f"The exception was approved, but this user could not set the tag. Set ONLY "
            f"SecurityControl=Ignore on {profile.resourceGroup}, preserving its other tags, "
            "then rerun. No broader role or subscription-wide exception was substituted.",
            group["id"],
        ) from error
    actual = (
        az(
            "group",
            "show",
            "--subscription",
            profile.subscriptionId,
            "--name",
            profile.resourceGroup,
            "--query",
            "tags",
        )
        or {}
    )
    if actual.get("SecurityControl") != "Ignore" or any(
        actual.get(key) != value for key, value in tags.items()
    ):
        raise Blocked(
            "Azure policy administrator",
            "Confirm the approved exception tag and original resource-group tags before retrying.",
            group["id"],
        )
    print(
        f"Applied approved SecurityControl=Ignore to {profile.resourceGroup}; other tags retained."
    )
    return True


def deploy_azure(progress, *, foundation=False):
    profile = progress.profile
    phase = "foundation" if foundation else "infra"
    deployment_name = profile.deploymentName + ("-foundation" if foundation else "")
    parameters = {"foundryName": profile.foundryName}
    if not foundation:
        if not profile.apiClientId or not profile.dispatchPrincipalId:
            raise Blocked(
                "Entra application administrator",
                "Complete the protected API and callback registrations before runtime deployment.",
            )
        parameters.update(
            {
                "flowApiClientId": profile.apiClientId,
                "fabricDispatchPrincipalId": profile.dispatchPrincipalId,
                **{
                    key: progress.data["ingress"][key]
                    for key in ("eventstreamNamespace", "eventstreamEntity")
                },
            }
        )
    sources = (
        ("infra/fresh/foundation.bicep", "infra/models.bicep", "infra/knowledge/*.bicep")
        if foundation
        else ("infra/**/*.bicep",)
    )
    fingerprint = digest(
        {
            "source": source_hash(*sources),
            "parameters": parameters,
            "operator": profile.operatorId,
        }
    )
    recovering = False
    for pending_phase in ("foundation", "infra"):
        pending = progress.data.get(pending_phase + "Pending")
        if not pending:
            continue
        pending_name = profile.deploymentName + (
            "-foundation" if pending_phase == "foundation" else ""
        )
        rows = az(
            "deployment",
            "group",
            "list",
            "--resource-group",
            profile.resourceGroup,
            "--query",
            f"[?name=='{pending_name}'].properties.provisioningState",
        )
        if rows and rows[0] in {"Running", "Accepted", "Creating"}:
            raise Blocked(
                "Operator", "Wait for the existing Azure deployment to finish, then rerun."
            )
        if pending_phase == phase and pending == fingerprint:
            recovering = rows == ["Succeeded"]
    if progress.matches(phase, fingerprint) or recovering:
        deployed = az(
            "deployment",
            "group",
            "show",
            "--resource-group",
            profile.resourceGroup,
            "--name",
            deployment_name,
        )
        if deployed["properties"]["provisioningState"] != "Succeeded":
            raise Blocked(
                "Azure administrator", "Resolve the incomplete infrastructure deployment."
            )
        values = {key: value["value"] for key, value in deployed["properties"]["outputs"].items()}
        validate_outputs(profile, values)
        if not recovering and values != progress.data[phase]:
            raise Blocked(
                "Azure administrator",
                "Deployment outputs changed outside this installer; inspect before resuming.",
            )
        restore_document_storage_network(progress, values)
        print("Reusing the selected successful Azure deployment; not redeploying its settings.")
        progress.data[phase] = values
        progress.data.pop(phase + "Pending", None)
        progress.done(phase, fingerprint)
        return values
    if not foundation and progress.data.get("infra"):
        stop_coordinator(progress, progress.data["infra"])
    path = progress.directory / ("foundation-parameters.json" if foundation else "parameters.json")
    atomic_json(
        path,
        {
            "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
            "contentVersion": "1.0.0.0",
            "parameters": {key: {"value": value} for key, value in parameters.items()},
        },
    )
    try:
        version = bootstrap.command("az", "bicep", "version")
    except Exception as error:
        raise Blocked(
            "Operator", "Install Bicep with: az bicep install --version v0.39.26; then rerun."
        ) from error
    if "0.39.26" not in version:
        raise Blocked(
            "Operator",
            "Use the repository's Bicep version: az bicep install --version v0.39.26; then rerun.",
        )
    compiled = progress.directory / ("foundation.json" if foundation else "azuredeploy.json")
    az(
        "bicep",
        "build",
        "--file",
        str(ROOT / "infra" / "fresh" / ("foundation.bicep" if foundation else "main.bicep")),
        "--outfile",
        str(compiled),
    )
    if not az("group", "exists", "--name", profile.resourceGroup):
        progress.approve(
            "create-group",
            profile.resourceGroup,
            f"Create dedicated group {profile.resourceGroup} in Sweden Central?",
        )
        az(
            "group",
            "create",
            "--name",
            profile.resourceGroup,
            "--location",
            "swedencentral",
            "--tags",
            f"closeout-installer={profile.environment}",
        )
    apply_group_network_exception(progress)
    changes = az(
        "deployment",
        "group",
        "what-if",
        "--resource-group",
        profile.resourceGroup,
        "--name",
        deployment_name,
        "--template-file",
        str(compiled),
        "--parameters",
        "@" + str(path),
        "--no-pretty-print",
        "--query",
        "changes[].{change:changeType,resource:resourceId}",
    )
    print("\nProposed Azure changes (no resources will be deleted by the installer):")
    print(json.dumps(changes, indent=2))
    progress.approve(
        "deploy-foundation" if foundation else "deploy",
        {"fingerprint": fingerprint, "changes": changes},
        "Deploy these Azure resources and scoped role assignments? Billable resources can remain "
        "if a later phase pauses. No Fabric items will be created.",
    )
    progress.data[phase + "Pending"] = fingerprint
    progress.save()
    deployment_args = (
        "deployment",
        "group",
        "create",
        "--resource-group",
        profile.resourceGroup,
        "--name",
        deployment_name,
        "--template-file",
        str(compiled),
        "--parameters",
        "@" + str(path),
    )
    try:
        deployed = az(*deployment_args)
    except Blocked:
        if not apply_group_network_exception(progress):
            raise
        print("Retrying the same Azure deployment once with the approved exception.")
        deployed = az(*deployment_args)
    if deployed["properties"]["provisioningState"] != "Succeeded":
        raise Blocked("Azure administrator", "Resolve the incomplete Azure deployment and rerun.")
    values = {key: value["value"] for key, value in deployed["properties"]["outputs"].items()}
    validate_outputs(profile, values)
    restore_document_storage_network(progress, values)
    progress.data[phase] = values
    progress.data.pop(phase + "Pending", None)
    progress.done(phase, fingerprint)
    return values


def restore_document_storage_network(progress, values):
    from agents.flow import storage_endpoint

    profile = progress.profile
    endpoints = [values["storageEndpoint"]]
    if values.get("flow"):
        endpoints.append(values["flow"]["storageUrl"])
    query = (
        "{publicNetworkAccess:publicNetworkAccess,"
        "allowBlobPublicAccess:allowBlobPublicAccess,allowSharedKeyAccess:allowSharedKeyAccess}"
    )
    for endpoint in dict.fromkeys(endpoints):
        name = storage_endpoint(endpoint).split("://")[1].split(".")[0]
        target = (
            "--subscription",
            profile.subscriptionId,
            "--resource-group",
            profile.resourceGroup,
            "--name",
            name,
        )
        network = az("storage", "account", "show", *target, "--query", query)
        if network["publicNetworkAccess"] == "Enabled":
            continue
        tags = (
            az(
                "group",
                "show",
                "--subscription",
                profile.subscriptionId,
                "--name",
                profile.resourceGroup,
                "--query",
                "tags",
            )
            or {}
        )
        if not any(
            key.lower() == "securitycontrol" and value == "Ignore" for key, value in tags.items()
        ):
            raise Blocked(
                "Azure policy / networking administrator",
                f"Document Storage {name} has public networking disabled. Obtain the policy-owner "
                "approved SecurityControl=Ignore exception on this dedicated group or provide "
                "compliant connectivity, then rerun. No networking setting was changed.",
            )
        if (
            network.get("allowBlobPublicAccess") is not False
            or network.get("allowSharedKeyAccess") is not False
        ):
            raise Blocked(
                "Azure administrator",
                f"Restore disabled anonymous Blob and shared-key access on document Storage "
                f"{name} before public networking can be restored.",
            )
        print(f"Restoring authenticated public networking on document Storage {name}.")
        az(
            "storage",
            "account",
            "update",
            *target,
            "--public-network-access",
            "Enabled",
            "--query",
            query,
        )
        actual = az("storage", "account", "show", *target, "--query", query)
        if actual != {**network, "publicNetworkAccess": "Enabled"}:
            raise Blocked(
                "Azure policy / networking administrator",
                f"Document Storage {name} did not retain the declared authenticated public "
                "networking. Review the approved exception and security controls, then rerun.",
            )


def app_settings(progress, values):
    return {
        row["name"]: row["value"]
        for row in az(
            "webapp",
            "config",
            "appsettings",
            "list",
            "--resource-group",
            progress.profile.resourceGroup,
            "--name",
            values["flow"]["workflowName"],
            "--query",
            "[?starts_with(name, 'FLOW_COORDINATOR_') || starts_with(name, 'Workflows.') "
            "|| name=='FLOW_FOUNDRY_PROJECT_ENDPOINT']",
        )
    }


def stop_coordinator(progress, values, *, fabric=None):
    current = app_settings(progress, values)
    if current.get("Workflows.coordinator.FlowState") == "Enabled":
        progress.approve(
            "pause-runtime",
            {"deployment": values["foundryName"], "settings": current},
            "A deployment update requires pausing the coordinator. Resume enablement will need "
            "your confirmation after setup finishes. Continue?",
        )
    if fabric is not None:
        before = fabric.definition(progress.profile, "activatorId")
        stopped = installer_fabric.activator_binding(before, progress.profile, enabled=False)
        fabric.update(progress, "activatorId", before, stopped)
    az(
        "webapp",
        "config",
        "appsettings",
        "set",
        "--resource-group",
        progress.profile.resourceGroup,
        "--name",
        values["flow"]["workflowName"],
        "--settings",
        "Workflows.coordinator.FlowState=Disabled",
        "Workflows.validation.FlowState=Disabled",
        "--query",
        "[?starts_with(name, 'Workflows.')].{name:name,value:value}",
    )
    progress.data["enabled"] = False
    progress.save()


def require_coordinator_auth(progress, values):
    if not progress.data.get("coordinatorAuth") or not progress.data.get("readableCoordinator"):
        raise Blocked(
            "Tenant / Foundry owner",
            "KQL-backed Fabric Data Agent grounding requires delegated user authentication, "
            "not project MI or a service principal. Approve delegated automation, register a "
            "dedicated public OAuth client, and authorize its renewable credential directly "
            "into a NEW secret in this target's Key Vault. See "
            "docs/DEPLOY-TO-AZURE.md#coordinator-authentication-handoff. Rerun with "
            "-CoordinatorClientConfig <nonsecret-client.json> "
            "-CoordinatorCredentialUri https://<vault>.vault.azure.net/secrets/<secret>. "
            "Azure agents, inputs and the disabled runtime are retained. "
            "No Fabric permissions/bindings/schedules will be changed by this continuation.",
            values["projectEndpoint"],
        )


def prepare_review_agents(progress, values, state, settings):
    progress.approve(
        "assistant-delegation",
        {"project": values["projectResourceId"], "fabric": progress.profile.fabric},
        "Publish the separate read-only close-out assistant with its own UserEntraToken "
        "Fabric connection? Document IQ keeps managed identity. This creates no Fabric "
        "items, credentials, consent grants or UI hosting.",
    )
    interactive = assistant.register(
        settings.endpoint,
        settings.closeout.name,
        "close-out-assistant",
        state / "assistant.json",
        fabric_connection="close-out-assistant-fabric-user",
        create_fabric_connection=True,
    )
    progress.data["assistant"] = interactive.model_dump()
    progress.save()
    authentication = progress.data.get("coordinatorAuth")
    if not authentication:
        print(
            "Coordinator authentication is NOT configured: publishing only the disabled "
            "baseline runtime. Fabric continuation/enablement will require owner authorization."
        )
        return settings.closeout, None
    client = OAuthClient.model_validate(authentication["client"])
    uri = authentication["credentialUri"]
    match = re.fullmatch(r"https://([a-z0-9-]+)\.vault\.azure\.net/secrets/([a-zA-Z0-9-]+)", uri)
    if not match or client.tenantId != values["tenantId"]:
        raise Blocked("Operator", "Use an unversioned target Key Vault URI and matching tenant.")
    vault, secret = match.groups()
    vault_id = az(
        "keyvault",
        "show",
        "--name",
        vault,
        "--resource-group",
        progress.profile.resourceGroup,
        "--subscription",
        progress.profile.subscriptionId,
        "--query",
        "id",
    )
    secret_id = f"{vault_id}/secrets/{secret}"
    metadata = az(
        "rest",
        "--method",
        "get",
        "--url",
        f"https://management.azure.com{secret_id}?api-version=2023-07-01",
        "--query",
        "{tags:tags,enabled:properties.attributes.enabled,contentType:properties.contentType}",
    )
    if (
        metadata.get("enabled") is not True
        or metadata.get("contentType") != "application/json"
        or (metadata.get("tags") or {}).get("component") != OWNER
        or (metadata.get("tags") or {}).get("client-id") != client.clientId
    ):
        raise Blocked(
            "Credential owner",
            "Restore the enabled, target-owned OAuth credential metadata.",
            secret_id,
        )
    progress.approve(
        "coordinator-delegation",
        authentication,
        f"Approve delegated-user coordinator automation for client {client.clientId}, "
        f"tenant {client.tenantId} and ONLY credential {uri}? The authorized user needs "
        "Foundry/Fabric access; tenant policy or revocation can require reauthentication. "
        "This is not all-managed-identity authentication or an end-to-end verification.",
    )
    rotation_role = "b86a8fe4-44ce-4948-aee5-eccb2c155cd7"
    principal = values["flow"]["workflowPrincipalId"]
    assignments = az(
        "role",
        "assignment",
        "list",
        "--scope",
        secret_id,
        "--include-inherited",
        "--query",
        "[].{principalId:principalId,roleDefinitionId:roleDefinitionId}",
    )
    if not any(
        row["principalId"] == principal and row["roleDefinitionId"].endswith("/" + rotation_role)
        for row in assignments
    ):
        arguments = (
            "--subscription",
            progress.profile.subscriptionId,
            "--resource-group",
            progress.profile.resourceGroup,
            "--template-file",
            str(ROOT / "infra" / "flow" / "coordinator-credential-access.bicep"),
            "--parameters",
            f"vaultName={vault}",
            f"secretName={secret}",
            f"workflowPrincipalId={principal}",
        )
        changes = az(
            "deployment",
            "group",
            "what-if",
            *arguments,
            "--query",
            "changes[].{resourceId:resourceId,changeType:changeType}",
        )
        print(json.dumps(changes, indent=2))
        progress.approve(
            "coordinator-secret-rotation",
            {"principal": principal, "scope": secret_id},
            f"Approve the displayed secret-only Key Vault Secrets Officer assignment to "
            f"Logic App identity {principal}? No vault-wide access will be created.",
        )
        az("deployment", "group", "create", *arguments, "--query", "properties.provisioningState")
    result = coordinator_brief.register(
        settings.endpoint,
        settings.closeout.name,
        settings.closeout.version,
        state / "coordinator-brief.json",
        fabric_connection="close-out-coordinator-fabric-user",
        create_fabric_connection=True,
    )
    binding = AgentBinding.model_validate(result["agent"])
    source = json.loads((ROOT / "src" / "logicapps" / "coordinator" / "workflow.json").read_text())
    workflow = readable_workflow(
        delegated_workflow(source, client, uri, binding.version), binding.version
    )
    path = state / "coordinator-workflow.json"
    atomic_json(path, workflow)
    progress.data["readableCoordinator"] = binding.model_dump()
    progress.save()
    return binding, path


def publish(progress, values):
    from azure.ai.projects import AIProjectClient
    from azure.core.exceptions import ResourceNotFoundError
    from azure.identity import AzureCliCredential

    state = ROOT / ".local" / "fresh" / values["subscriptionId"] / values["foundryName"]
    definition_hash = digest(
        {
            "source": source_hash(
                "src/agents/*.py", "src/agents/prompts/*.txt", "contracts/**/*.schema.json"
            ),
            "values": values,
            "fabric": progress.profile.fabric,
        }
    )
    settings = None
    if progress.matches("definitions", definition_hash) and (state / "config.json").exists():
        settings = FoundrySettings.model_validate_json((state / "config.json").read_text("utf-8"))
        if settings.endpoint != values[
            "projectEndpoint"
        ] or settings.fabric != FabricSettings.model_validate(progress.profile.fabric):
            raise Blocked(
                "Operator", "Pinned agents do not match the selected project/Fabric binding."
            )
        with (
            AzureCliCredential() as credential,
            AIProjectClient(endpoint=settings.endpoint, credential=credential) as client,
        ):
            for binding in (settings.validation, settings.closeout):
                try:
                    version = client.agents.get_version(
                        agent_name=binding.name, agent_version=binding.version
                    )
                except ResourceNotFoundError:
                    settings = None
                    break
                if (
                    version.status != "active"
                    or version.metadata.get("definition_sha256") != binding.definitionHash
                ):
                    settings = None
                    break
    if settings is None:
        print("\nPublishing target-bound prompt agents and native IQ definitions (no inference)...")
        stop_coordinator(progress, values)
        state = bootstrap.complete(
            progress.profile.resourceGroup,
            progress.profile.deploymentName,
            fabric=FabricSettings.model_validate(progress.profile.fabric),
            create_fabric_connection=True,
        )
        settings = FoundrySettings.model_validate_json((state / "config.json").read_text("utf-8"))
        progress.done("definitions", definition_hash)
    else:
        ensure_connection(
            values["projectResourceId"], FabricSettings.model_validate(progress.profile.fabric)
        )
        from agents.foundry import definition_matches

        with AzureCliCredential() as credential:
            search = knowledge_setup.SearchSetup(settings.knowledge.searchEndpoint, credential)
            try:
                expected = knowledge_setup.native_definitions(
                    settings.knowledge.knowledgeBase,
                    "close-out-documents",
                    values["storageResourceId"],
                    values["containerName"],
                    values["knowledgeModel"],
                )
                for path, body in expected.items():
                    if not definition_matches(body, search.request("GET", path)):
                        raise Blocked(
                            "Azure/Foundry owner",
                            "Native document IQ settings changed outside setup. Restore the "
                            "source/base or review the target before resuming.",
                            path,
                        )
            finally:
                search.close()
    coordinator, workflow = prepare_review_agents(progress, values, state, settings)
    runtime_hash = digest(
        {
            "source": source_hash(
                "scripts/package-flow.ps1",
                "uv.lock",
                "src/agents/**/*.py",
                "src/agents/prompts/*.txt",
                "src/agents/*.kql",
                "src/shared/**/*.py",
                "contracts/**/*.json",
                "src/flow_functions/*",
                "src/logicapps/**/*.json",
            ),
            "settings": settings.model_dump(),
            "coordinator": coordinator.model_dump(),
            "authentication": progress.data.get("coordinatorAuth"),
        }
    )
    current = app_settings(progress, values)
    pinned = (
        current.get("FLOW_COORDINATOR_AGENT_VERSION") == coordinator.version
        and current.get("FLOW_COORDINATOR_AGENT_NAME") == coordinator.name
        and current.get("FLOW_FOUNDRY_PROJECT_ENDPOINT") == settings.endpoint
    )
    adapter = az(
        "webapp",
        "config",
        "appsettings",
        "list",
        "--resource-group",
        progress.profile.resourceGroup,
        "--name",
        values["flow"]["adapterName"],
        "--query",
        "[?name=='FLOW_FOUNDRY_CONFIG_JSON'].value",
    )
    pinned = pinned and bool(adapter) and json.loads(adapter[0]) == settings.model_dump()
    installed = False
    receipt = state / "flow-deployment.json"
    if progress.matches("runtime", runtime_hash) and pinned and receipt.exists():
        published = json.loads(receipt.read_text("utf-8"))
        installed = True
        for app in (values["flow"]["adapterName"], values["flow"]["workflowName"]):
            expected = published.get("deployments", {}).get(app, {})
            deployments = az(
                "webapp",
                "log",
                "deployment",
                "list",
                "--resource-group",
                progress.profile.resourceGroup,
                "--name",
                app,
                "--query",
                "[].{id:id,status:status,active:active}",
            )
            if not any(
                row["id"].rsplit("/", 1)[-1] == (expected.get("id") or "").rsplit("/", 1)[-1]
                and row["status"] == 4
                and row.get("active")
                for row in deployments
            ):
                installed = False
    if not installed:
        stop_coordinator(progress, values)
        bootstrap.publish_flow(
            values,
            state,
            coordinator=coordinator if workflow else None,
            coordinator_workflow=workflow,
        )
        progress.done("runtime", runtime_hash)
    else:
        for app in (values["flow"]["adapterName"], values["flow"]["workflowName"]):
            status = az(
                "webapp",
                "show",
                "--resource-group",
                progress.profile.resourceGroup,
                "--name",
                app,
                "--query",
                "state",
            )
            if status != "Running":
                raise Blocked("Azure administrator", "Start the selected app before resuming.", app)
        print("Reusing published runtime and agent pins; workflow state is unchanged.")
    return state, settings


def fabric_permissions(progress, fabric, values):
    require_coordinator_auth(progress, values)
    profile = progress.profile
    workspace = profile.fabric["workspaceId"]
    sender = values["flow"]["adapterPrincipalId"]
    roles = fabric.rows(f"/workspaces/{workspace}/roleAssignments")
    if not any(
        row["principal"]["id"] == sender and row["role"] in {"Contributor", "Member", "Admin"}
        for row in roles
    ):
        progress.approve(
            "publisher-contributor",
            {"workspace": workspace, "principal": sender},
            f"Grant publisher Function identity {sender} Contributor in ONLY Fabric workspace "
            f"{workspace}? The tested Eventstream endpoint requires this broader-than-item role; "
            "the Fabric owner must approve it.",
        )
        try:
            fabric.request(
                "POST",
                f"/workspaces/{workspace}/roleAssignments",
                {"principal": {"id": sender, "type": "ServicePrincipal"}, "role": "Contributor"},
            )
        except Blocked as error:
            raise Blocked(
                "Fabric workspace administrator",
                f"Grant publisher Function identity {sender} Contributor in workspace {workspace}. "
                "Azure RBAC does not grant this Fabric role. Then rerun.",
                workspace,
            ) from error
    agent_id = profile.fabric["dataAgentId"]
    workspace_name = fabric.request("GET", f"/workspaces/{workspace}")["displayName"]
    agent_name = fabric.request("GET", f"/workspaces/{workspace}/items/{agent_id}")["displayName"]
    instructions = (
        "MANUAL FABRIC STEP: grant the authorized Entra USER access to the Data Agent.\n"
        "The selected KQL-backed agent does not support project MI/SPN authentication.\n"
        f"1. In Fabric, open workspace: {workspace_name}\n"
        f"2. Find Data Agent: {agent_name} in the workspace item list.\n"
        "   Open its ... menu > Manage permissions (not the workspace access settings).\n"
        "   Alternative: open the agent > Share > Manage permissions icon; select Advanced "
        "if needed.\n"
        "3. Select Direct access > Add user.\n"
        "4. Select the USER authorized through the coordinator's public OAuth client, "
        "not its application or the Foundry project managed identity.\n"
        "5. Grant Read only. Leave additional permissions unchecked, turn email notification "
        "off, and select Grant.\n"
        f"6. The same user needs Viewer on KQL database {profile.fabric['databaseId']} "
        "and permission to invoke the selected Foundry agent/model. The interactive "
        "assistant's signed-in Azure CLI user needs those permissions separately.\n"
        "If sharing/permission controls are unavailable, ask the Data Agent owner to perform "
        "this step. Do not grant workspace-wide access as a substitute.\n"
        f"Workspace ID: {workspace}\n"
        f"Data Agent ID: {agent_id}\n"
        "Typing y below DOES NOT grant access. Answer y only AFTER the owner has completed "
        "the exact grant above. Otherwise answer n to pause and rerun after it is done."
    )
    (progress.directory / "handoff.txt").write_text(
        instructions
        + f"\n\nRerun: .\\scripts\\deploy-demo.ps1 -Environment {profile.environment}\n"
        "This step records owner confirmation, not verified runtime access.\n",
        encoding="utf-8",
    )
    progress.approve(
        "delegated-data-agent-read",
        {
            "authentication": progress.data["coordinatorAuth"],
            "agent": agent_id,
            "workspace": workspace,
            "database": profile.fabric["databaseId"],
        },
        instructions,
    )
    scope = values["flow"]["storageUrl"].split("//", 1)[1].split(".", 1)[0]
    storage_id = az(
        "storage",
        "account",
        "show",
        "--name",
        scope,
        "--resource-group",
        profile.resourceGroup,
        "--query",
        "id",
    )
    ledger = storage_id + "/blobServices/default/containers/ledger"
    assignments = az(
        "role",
        "assignment",
        "list",
        "--scope",
        ledger,
        "--include-inherited",
        "--query",
        "[].{principalId:principalId,roleDefinitionId:roleDefinitionId}",
    )
    if not any(
        row["principalId"] == profile.projectorOwnerId
        and row["roleDefinitionId"].endswith("/" + BLOB_READER)
        for row in assignments
    ):
        progress.approve(
            "projector-ledger-reader",
            {"principal": profile.projectorOwnerId, "scope": ledger},
            f"Grant existing notebook schedule owner {profile.projectorOwnerId} Blob Data Reader "
            "on ONLY the new registration ledger container? Notebook execution remains delegated.",
        )
        az(
            "role",
            "assignment",
            "create",
            "--assignee-object-id",
            profile.projectorOwnerId,
            "--assignee-principal-type",
            "User",
            "--role",
            BLOB_READER,
            "--scope",
            ledger,
            "--name",
            str(uuid5(NAMESPACE_URL, ledger + profile.projectorOwnerId + BLOB_READER)),
        )


def prepare_playground(progress, values, state):
    from azure.identity import AzureCliCredential
    from azure.storage.blob import BlobServiceClient

    from agents.blob_sources import BlobSources, azure_downloader
    from agents.flow import prepare_batch, upload_batch
    from shared.data_agent_stub import DataAgentStub
    from shared.headless import BlobStore, Ledger
    from shared.seed import load_seed

    assets = Path(progress.data.get("assets", str(state / "demo")))
    assets_hash = source_hash("seed/*.json", "seed/*.yaml", "src/shared/seed*.py")
    if not (assets / "assets-manifest.json").exists() or not progress.matches(
        "assets", assets_hash
    ):
        assets = state / ("demo-" + assets_hash[:12])
        bootstrap.command(sys.executable, "-m", "shared.local_demo", "--output", str(assets))
        progress.data["assets"] = str(assets)
        progress.done("assets", assets_hash)
    documents_hash = digest({"assets": assets_hash, "storage": values["storageEndpoint"]})
    indexed = False
    if progress.matches("documents", documents_hash):
        uploaded = state / "knowledge-upload-receipt.json"
        ingestion = state / "native-blob-ingestion.json"
        if uploaded.exists() and ingestion.exists():
            saved = json.loads(ingestion.read_text("utf-8"))
            with AzureCliCredential() as credential:
                search = knowledge_setup.SearchSetup(values["searchEndpoint"], credential)
                try:
                    rows = search.request(
                        "POST",
                        f"indexes/{saved['createdResources']['index']}/docs/search",
                        {"search": "*", "top": 1000, "select": "blob_url,snippet"},
                    )["value"]
                    indexed = knowledge_setup.indexed_documents_match(
                        rows, json.loads(uploaded.read_text("utf-8"))
                    )
                finally:
                    search.close()
    if not indexed:
        print("Loading synthetic document knowledge and waiting for indexing (no inference)...")
        knowledge_setup.provision(
            SimpleNamespace(
                infra=state / "infra.json",
                root=ROOT,
                site=list(bootstrap.SITES),
                knowledge_base=values["knowledgeBaseName"],
                source_name="close-out-documents",
                output=state / "knowledge.json",
                assets=assets,
                definitions_only=False,
            )
        )
        progress.done("documents", documents_hash)
    registration = progress.data.setdefault(
        "playground",
        {
            "runId": str(uuid4()),
            "batchId": str(uuid4()),
            "publicationId": str(uuid4()),
            "assetsHash": assets_hash,
        },
    )
    if registration["assetsHash"] != assets_hash:
        raise Blocked(
            "Operator",
            "Seed/evidence changed since this assessment was prepared. Rerun the same command "
            "with -NewAssessment to explicitly prepare separate input and publication IDs.",
        )
    progress.save()
    output = progress.directory / "playground" / registration["publicationId"]
    output.mkdir(parents=True, exist_ok=True)
    facts = DataAgentStub(
        load_seed(ROOT / "seed" / "seed.json", ROOT / "contracts"), {"SE-STO-0042"}
    ).site_facts("SE-STO-0042", "initial-blocked")
    with (
        AzureCliCredential() as credential,
        BlobServiceClient(values["flow"]["storageUrl"], credential=credential) as storage,
    ):
        inputs = storage.get_container_client("inputs")
        ledger = Ledger(BlobStore(storage.get_container_client("ledger")))
        manifest, uploads = prepare_batch(
            facts,
            assets,
            ROOT / "contracts",
            inputs.url,
            ["PHOTO-20"],
            registration["runId"],
            registration["batchId"],
        )
        upload_batch(manifest, uploads, inputs, ledger, ROOT / "contracts")
        sources = BlobSources(manifest, ROOT / "contracts", inputs.url, azure_downloader(inputs))
        packets, attachments = sources.validation_inputs(["PHOTO-20"])
        for identifier, data, _, _ in attachments:
            path = output / f"{identifier}.png"
            if path.exists() and path.read_bytes() != data:
                raise Blocked(
                    "Operator",
                    "Previously exported playground image differs from its pinned source.",
                )
            path.write_bytes(data)
        message = {
            "evidence": packets,
            "publicationContext": {
                "sourceRunId": registration["runId"],
                "sourceBatchId": registration["batchId"],
                "publicationId": registration["publicationId"],
            },
        }
        path = output / "message.json"
        if path.exists() and json.loads(path.read_text("utf-8")) != message:
            raise Blocked(
                "Operator", "Previously exported playground message differs from its pinned source."
            )
        atomic_json(path, message)
    return output


def enable(progress, fabric, values, settings):
    from agents.flow_callback import restricted_auth

    require_coordinator_auth(progress, values)
    coordinator = AgentBinding.model_validate(progress.data["readableCoordinator"])
    profile = progress.profile
    workflow_id = (
        f"/subscriptions/{profile.subscriptionId}/resourceGroups/{profile.resourceGroup}"
        f"/providers/Microsoft.Web/sites/{values['flow']['workflowName']}"
    )
    auth = az(
        "rest",
        "--method",
        "get",
        "--url",
        f"https://management.azure.com{workflow_id}/config/authsettingsV2?api-version=2023-12-01",
    )["properties"]
    protected = restricted_auth(
        auth,
        {
            "apiClientId": profile.apiClientId,
            "tenantId": profile.tenantId,
            "audience": "api://" + profile.apiClientId,
        },
        profile.dispatchPrincipalId,
    )
    if protected != auth:
        raise Blocked(
            "Azure administrator",
            "Restore the workflow's bearer-only Easy Auth and exact approved callback principal. "
            "The installer will not relax ingress authentication.",
            workflow_id,
        )
    before = fabric.definition(profile, "activatorId")
    after = installer_fabric.activator_binding(
        before, profile, enabled=True, start_time=progress.data.get("dispatchFrom")
    )
    schedule = installer_fabric.schedule_change(progress, fabric)
    current = app_settings(progress, values)
    if (
        progress.data.get("enabled")
        and current.get("Workflows.coordinator.FlowState") == "Enabled"
        and current.get("Workflows.validation.FlowState") == "Disabled"
        and before == after
        and schedule is None
    ):
        print("Runtime is already enabled; no enablement changes made.")
        return
    if not confirm(
        f"Setup is prepared for {values['foundryName']} and Fabric workspace "
        f"{profile.fabric['workspaceId']}. Enable coordinator {coordinator.name}:"
        f"{coordinator.version}, the selected readiness Activator and approved bounded "
        "notebook schedule? Scheduled work incurs charges. Blob validation stays disabled. "
        "No inference or end-to-end verification is being performed by this installer."
    ):
        raise Blocked(
            "Operator",
            "Rerun and confirm enablement when ready. Setup and playground files are retained.",
        )
    if (
        not progress.data.get("enabled")
        or current.get("Workflows.coordinator.FlowState") != "Enabled"
    ):
        progress.data["dispatchFrom"] = datetime.now(UTC).isoformat()
        progress.save()
    after = installer_fabric.activator_binding(
        before, profile, enabled=True, start_time=progress.data["dispatchFrom"]
    )
    schedule_attempted = False
    activator_attempted = False
    try:
        az(
            "webapp",
            "config",
            "appsettings",
            "set",
            "--resource-group",
            profile.resourceGroup,
            "--name",
            values["flow"]["workflowName"],
            "--settings",
            "Workflows.coordinator.FlowState=Enabled",
            "Workflows.validation.FlowState=Disabled",
            "--query",
            "[?starts_with(name, 'Workflows.')].{name:name,value:value}",
        )
        if schedule:
            schedule_attempted = True
            installer_fabric.schedule_change(progress, fabric, apply=True)
        if before != after:
            if digest(fabric.definition(profile, "activatorId")) != digest(before):
                raise Blocked(
                    "Fabric owner", "Activator changed before enablement; inspect and rerun."
                )
            atomic_json(
                progress.directory
                / "backups"
                / f"activator-before-enable-{digest(before)[:12]}.json",
                {"definition": before},
            )
            activator_attempted = True
            fabric.request(
                "POST",
                fabric.item_path(profile, "activatorId") + "/updateDefinition",
                {"definition": after},
            )
        actual = app_settings(progress, values)
        if (
            actual.get("Workflows.coordinator.FlowState") != "Enabled"
            or actual.get("Workflows.validation.FlowState") != "Disabled"
        ):
            raise Blocked(
                "Azure administrator", "The requested workflow enablement did not persist."
            )
        progress.data["enabled"] = True
        progress.save()
    except (Exception, KeyboardInterrupt) as error:
        recovery_errors = []
        try:
            az(
                "webapp",
                "config",
                "appsettings",
                "set",
                "--resource-group",
                profile.resourceGroup,
                "--name",
                values["flow"]["workflowName"],
                "--settings",
                "Workflows.coordinator.FlowState=Disabled",
                "Workflows.validation.FlowState=Disabled",
                "--query",
                "[?starts_with(name, 'Workflows.')].{name:name,value:value}",
            )
        except Exception:
            recovery_errors.append("disable the coordinator workflow")
        if activator_attempted:
            try:
                actual = fabric.definition(profile, "activatorId")
                if digest(actual) == digest(after):
                    fabric.request(
                        "POST",
                        fabric.item_path(profile, "activatorId") + "/updateDefinition",
                        {"definition": before},
                    )
                elif digest(actual) != digest(before):
                    recovery_errors.append("inspect the concurrently changed Activator")
            except Exception:
                recovery_errors.append("stop the readiness Activator")
        if schedule_attempted:
            try:
                actual = fabric.request("GET", schedule[0])
                actual = {key: actual[key] for key in ("enabled", "configuration")}
                old = {key: progress.data["scheduleBefore"][key] for key in actual}
                if actual == schedule[1]:
                    fabric.request("PATCH", schedule[0], old)
                elif actual != old:
                    recovery_errors.append("inspect the concurrently changed notebook schedule")
            except Exception:
                recovery_errors.append("restore the notebook schedule from its local backup")
        progress.data["enabled"] = False
        progress.save()
        if recovery_errors:
            raise Blocked(
                "Azure/Fabric owner",
                "Enablement was interrupted. Before resuming: " + "; ".join(recovery_errors) + ".",
                workflow_id,
            ) from error
        raise


def playground_handoff(values, settings, output, *, progress=None):
    portal = (
        f"https://ai.azure.com/nextgen/r/{values['projectResourceId'].lstrip('/')}/build/agents"
    )
    coordinator = settings.closeout
    extra = ""
    if progress is not None:
        if progress.data.get("coordinatorAuth"):
            coordinator = AgentBinding.model_validate(progress.data["readableCoordinator"])
        interactive = progress.data["assistant"]["agent"]
        state = ROOT / ".local" / "fresh" / values["subscriptionId"] / values["foundryName"]
        extra = (
            f"Read-only assistant: {interactive['name']}, version {interactive['version']}\n"
            f"Local assistant configuration: {state / 'assistant.json'}\n"
            "UI is LOCAL ONLY; see README.md#local-close-out-workbench for startup.\n"
            + (
                "Coordinator output: readable .md plus diagnostic .json in private "
                f"{values['flow']['storageUrl'].rstrip('/')}/results/coordinator/.\n"
                if progress.data.get("coordinatorAuth")
                else "Coordinator authentication is pending; runtime stays disabled.\n"
            )
        )
    return (
        f"Open Foundry project agents: {portal}\n"
        f"Validation agent: {settings.validation.name}, version {settings.validation.version}\n"
        f"Coordinator agent: {coordinator.name}, version {coordinator.version}\n"
        + extra
        + f"Select {settings.validation.name}, version {settings.validation.version}, "
        "then open its playground.\n"
        f"Attach: {output / 'PHOTO-20.png'}\n"
        f"Paste the contents of: {output / 'message.json'}\n"
    )


def install(
    progress, *, new_assessment=False, coordinator_client=None, coordinator_credential_uri=None
):
    from azure.identity import AzureCliCredential

    allow_database_correction = installer_fabric.can_correct_database(progress)
    progress.phase("Selecting the Azure target")
    choose_target(progress)
    if coordinator_client is not None:
        client = OAuthClient.model_validate_json(coordinator_client.read_text(encoding="utf-8"))
        if client.tenantId != progress.profile.tenantId or not re.fullmatch(
            r"https://[a-z0-9-]+\.vault\.azure\.net/secrets/[a-zA-Z0-9-]+",
            coordinator_credential_uri or "",
        ):
            raise Blocked(
                "Operator", "Use an unversioned target Key Vault URI and matching tenant."
            )
        authentication = {
            "client": client.model_dump(),
            "credentialUri": coordinator_credential_uri,
        }
        progress.bind("coordinatorAuthTarget", authentication)
        progress.data["coordinatorAuth"] = authentication
        progress.save()
    progress.bind(
        "azureTarget",
        {
            key: getattr(progress.profile, key)
            for key in ("tenantId", "subscriptionId", "resourceGroup", "foundryName", "operatorId")
        },
    )
    progress.phase("Checking setup prerequisites and target approvals")
    preflight(progress)
    progress.phase("Deploying or resuming the Azure/Foundry foundation")
    foundation = deploy_azure(progress, foundation=True)
    print(
        "\nAzure/Foundry foundation prepared: "
        f"{foundation['projectEndpoint']}\n"
        "This is infrastructure only; the agents are not yet published by this phase. "
        "Next, prepare API/callback registrations and select existing Fabric tool settings."
    )
    progress.phase("Preparing application registrations without secrets")
    prepare_application(progress)
    prepare_application(progress, callback=True)
    with AzureCliCredential(process_timeout=60) as credential:
        fabric = installer_fabric.Fabric(credential)
        try:
            progress.phase("Selecting existing Fabric items")
            installer_fabric.discover(
                progress, fabric, allow_database_correction=allow_database_correction
            )
            progress.bind(
                "fabricTarget",
                {
                    "fabric": progress.profile.fabric,
                    "items": progress.profile.items,
                    "projectorOwnerId": progress.profile.projectorOwnerId,
                },
            )
            progress.phase("Reading existing Fabric configuration")
            installer_fabric.inspect_compatibility(progress, fabric)
            if not progress.data.get("ingress"):
                progress.data["ingress"] = installer_fabric.ingress(progress, fabric)
                progress.save()
            else:
                installer_fabric.current_ingress(progress, fabric)
            progress.data["fabricSelectionLocked"] = True
            progress.save()
            progress.phase("Deploying or resuming the secured Azure runtime")
            values = deploy_azure(progress)
            progress.phase("Publishing or reusing agents and runtime")
            state, settings = publish(progress, values)
            progress.phase("Preparing synthetic documents and playground inputs")
            if new_assessment and progress.data.get("playground"):
                if not confirm(
                    "Prepare a NEW assessment with new run/batch/publication IDs? "
                    "Previous input remains available. Do not do this for an unchanged retry."
                ):
                    raise Blocked(
                        "Operator", "Rerun without -NewAssessment to retain the current input."
                    )
                progress.data.pop("playground")
                progress.save()
            output = prepare_playground(progress, values, state)
            message = (
                "Azure and Foundry setup prepared. Fabric permissions, bindings and schedules "
                "have not been changed in this phase.\n\n"
                + playground_handoff(values, settings, output, progress=progress)
                + "\nDo not send the playground message until Fabric setup and runtime "
                "enablement are completed. No inference or end-to-end verification has "
                "been performed.\n"
            )
            (progress.directory / "handoff.txt").write_text(message, encoding="utf-8")
            print("\n" + message)
            if not confirm(
                "Continue with Fabric permissions and changes to the selected existing "
                "automation? Answer n to keep your current Fabric demo unchanged. "
                "Azure/Foundry setup and playground inputs are retained."
            ):
                progress.phase("Azure and Foundry prepared; Fabric setup deferred")
                return
            require_coordinator_auth(progress, values)
            progress.phase("Preparing target-specific Fabric identity grants")
            fabric_permissions(progress, fabric, values)
            progress.phase("Binding the existing Fabric items")
            installer_fabric.choose_callback(progress, fabric, values["flow"]["workflowName"])
            notebook = fabric.definition(progress.profile, "notebookId")
            pipeline = fabric.definition(progress.profile, "pipelineId")
            if (
                installer_fabric.notebook_binding(
                    notebook, progress.profile, values["flow"]["storageUrl"]
                )
                != notebook
                or installer_fabric.pipeline_binding(
                    pipeline, progress.profile.callbackConnectionId
                )
                != pipeline
            ):
                stop_coordinator(progress, values, fabric=fabric)
            installer_fabric.bind_existing(progress, fabric, values)
            progress.phase("Preparing final runtime enablement")
            enable(progress, fabric, values, settings)
        finally:
            fabric.close()
    message = (
        "Installation prepared; live end-to-end verification is deferred.\n\n"
        + playground_handoff(values, settings, output, progress=progress)
        + f"Projector schedule ends: {progress.data['scheduleEndsAt']}\n"
        "Send once. Retry unchanged input with the same publication ID.\n"
        "This demo saves a readable draft and raw response, not an approved package or "
        "Dataverse update.\n"
        "Resume setup with: .\\scripts\\deploy-demo.ps1 "
        f"-Environment {progress.profile.environment}\n"
    )
    (progress.directory / "handoff.txt").write_text(message, encoding="utf-8")
    print("\n" + message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--new-assessment", action="store_true")
    parser.add_argument("--coordinator-client-config", type=Path)
    parser.add_argument("--coordinator-credential-uri")
    args = parser.parse_args()
    if bool(args.coordinator_client_config) != bool(args.coordinator_credential_uri):
        parser.error("Supply both coordinator client configuration and credential URI")
    try:
        progress = Progress(args.environment)
    except Blocked as error:
        print(str(error))
        raise SystemExit(2) from None
    except (ValueError, OSError):
        print(
            "Setup paused: the environment name or saved local profile is invalid/unreadable. "
            "Use 2-24 lowercase letters/digits/hyphens, starting with a letter. "
            "Restore the original profile before resuming; no profile contents were printed."
        )
        raise SystemExit(2) from None
    try:
        with progress.lock():
            install(
                progress,
                new_assessment=args.new_assessment,
                coordinator_client=args.coordinator_client_config,
                coordinator_credential_uri=args.coordinator_credential_uri,
            )
    except Blocked as error:
        progress.handoff(error)
        raise SystemExit(2) from None
    except (KeyboardInterrupt, EOFError):
        print("\nSetup paused. Rerun the same command to continue.")
        raise SystemExit(2) from None
    except Exception as error:
        progress.handoff(
            Blocked(
                "Target administrator",
                f"Setup stopped during {type(error).__name__}. "
                "Check target access, service availability and the last displayed phase, "
                "then rerun. Raw service errors are suppressed to avoid "
                "exposing credentials. No end-to-end success is claimed.",
            )
        )
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()

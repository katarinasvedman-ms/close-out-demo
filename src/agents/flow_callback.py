"""Configure an explicitly authorized app-only Fabric to Logic Apps connection."""

import argparse
import copy
import json
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import httpx

from shared.local_demo import write_json

OWNER = "close-out-demo scoped Fabric to Logic Apps callback"
ARM = "https://management.azure.com"
GRAPH = "https://graph.microsoft.com/v1.0"
FABRIC = "https://api.fabric.microsoft.com/v1"
SECRET_NAME = "fabric-callback-client-secret"


def probe_definition(connection_id):
    from agents.fabric_flow_activator import encoded_definition

    connection_id = str(UUID(connection_id))
    return encoded_definition(
        "pipeline-content.json",
        {
            "properties": {
                "activities": [
                    {
                        "name": "CheckProtectedIngress",
                        "type": "WebActivity",
                        "dependsOn": [],
                        "policy": {
                            "timeout": "0.00:02:00",
                            "retry": 0,
                            "secureInput": True,
                            "secureOutput": True,
                        },
                        "typeProperties": {
                            "method": "GET",
                            "relativeUrl": "/",
                            "turnOffAsync": True,
                        },
                        "externalReferences": {"connection": connection_id},
                    },
                ]
            }
        },
    )


def validate_config(config):
    for key in ("tenantId", "workspaceId", "apiClientId"):
        UUID(config[key])
    for key, provider in (
        ("workflowResourceId", "Microsoft.Web/sites"),
        ("vaultResourceId", "Microsoft.KeyVault/vaults"),
    ):
        if not re.fullmatch(
            r"/subscriptions/[a-fA-F0-9-]+/resourceGroups/[a-zA-Z0-9_.()-]+/providers/"
            + re.escape(provider)
            + r"/[a-zA-Z0-9-]+",
            config[key],
        ):
            raise ValueError(f"An explicit {key} is required")
    if config["workflowResourceId"].split("/providers/")[0].lower() != (
        config["vaultResourceId"].split("/providers/")[0].lower()
    ):
        raise ValueError("The callback vault must belong to the selected workflow group")
    if config["audience"] != "api://" + config["apiClientId"]:
        raise ValueError("The audience must identify the existing protected API")
    if not re.fullmatch(r"[a-zA-Z0-9-]{1,80}", config["connectionName"]):
        raise ValueError("An explicit callback connection name is required")


def restricted_auth(properties, config, principal):
    result = copy.deepcopy(properties)
    aad = result["identityProviders"]["azureActiveDirectory"]
    if (
        not result["platform"]["enabled"]
        or not result["globalValidation"]["requireAuthentication"]
        or result["globalValidation"]["unauthenticatedClientAction"] != "Return401"
        or aad["registration"]["clientId"] != config["apiClientId"]
        or aad["registration"]["openIdIssuer"]
        != f"https://login.microsoftonline.com/{config['tenantId']}/v2.0"
        or aad["validation"]["allowedAudiences"] != [config["audience"]]
    ):
        raise ValueError("The selected host does not have the expected existing API protection")
    aad["validation"]["defaultAuthorizationPolicy"] = {
        "allowedPrincipals": {"identities": [str(UUID(principal))]}
    }
    return result


class CallbackSetup:
    def __init__(self, config, output, credential, client):
        validate_config(config)
        self.config, self.output = config, output
        self.credential, self.client = credential, client
        self.path = output / "fabric-callback-credentials.json"
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {}
        if self.state and self.state["target"] != config:
            raise ValueError("Existing callback receipt belongs to a different target")
        self.state.update(target=config)

    def save(self):
        self.state["updatedAt"] = datetime.now(UTC).isoformat()
        write_json(self.path, self.state)

    def request(self, method, url, *, scope=None, **kwargs):
        scope = scope or (
            GRAPH.removesuffix("/v1.0")
            if url.startswith(GRAPH + "/")
            else FABRIC.removesuffix("/v1")
            if url.startswith(FABRIC + "/")
            else ARM
        )
        token = self.credential.get_token(scope + "/.default")
        response = self.client.request(
            method, url, headers={"Authorization": "Bearer " + token.token}, **kwargs
        )
        if response.is_error:
            # Credential APIs can return supplied secrets in error details.
            raise RuntimeError(f"Callback setup HTTP {response.status_code}")
        return response.json() if response.content else {}

    def secret(self):
        config = self.config
        vault = self.request("GET", ARM + config["vaultResourceId"] + "?api-version=2023-07-01")
        if vault["properties"]["tenantId"] != config["tenantId"]:
            raise ValueError("The selected vault belongs to another tenant")
        if not self.state.get("credentialKeyId"):
            expires = datetime.now(UTC) + timedelta(days=30)
            value = self.request(
                "POST",
                GRAPH + f"/applications/{self.state['applicationObjectId']}/addPassword",
                json={
                    "passwordCredential": {
                        "displayName": OWNER,
                        "endDateTime": expires.isoformat(),
                    }
                },
            )
            try:
                self.request(
                    "PUT",
                    ARM
                    + config["vaultResourceId"]
                    + f"/secrets/{SECRET_NAME}?api-version=2023-07-01",
                    json={
                        "properties": {
                            "value": value["secretText"],
                            "attributes": {"enabled": True, "exp": int(expires.timestamp())},
                        }
                    },
                )
            except Exception:
                self.request(
                    "POST",
                    GRAPH + f"/applications/{self.state['applicationObjectId']}/removePassword",
                    json={"keyId": value["keyId"]},
                )
                raise
            self.state.update(
                credentialKeyId=value["keyId"],
                expiresAt=value["endDateTime"],
                secretUri=vault["properties"]["vaultUri"] + f"secrets/{SECRET_NAME}",
            )
            self.save()
            return value["secretText"]
        secret_url = self.state["secretUri"] + "?api-version=7.4"
        try:
            return self.request("GET", secret_url, scope="https://vault.azure.net")["value"]
        except RuntimeError as error:
            if str(error) != "Callback setup HTTP 403":
                raise
        # Recovery/rotation reads only this credential, never other vault secrets.
        operator = self.request("GET", GRAPH + "/me?$select=id")["id"]
        scope = config["vaultResourceId"] + "/secrets/" + SECRET_NAME
        role = str(uuid5(NAMESPACE_URL, scope + operator + "secret-reader"))
        role_id = scope + "/providers/Microsoft.Authorization/roleAssignments/" + role
        subscription = config["vaultResourceId"].split("/")[2]
        self.request(
            "PUT",
            ARM + role_id + "?api-version=2022-04-01",
            json={
                "properties": {
                    "principalId": operator,
                    "principalType": "User",
                    "roleDefinitionId": f"/subscriptions/{subscription}/providers/"
                    "Microsoft.Authorization/roleDefinitions/4633458b-17de-408a-b874-0445c86b69e6",
                }
            },
        )
        self.state["operatorSecretReadRoleId"] = role_id
        self.state["operatorSecretReadRoleRemoved"] = False
        self.save()
        for attempt in range(12):
            try:
                return self.request("GET", secret_url, scope="https://vault.azure.net")["value"]
            except RuntimeError:
                if attempt == 11:
                    raise
                time.sleep(5)

    def release_operator_reader(self):
        role_id = self.state.get("operatorSecretReadRoleId")
        if not role_id or self.state.get("operatorSecretReadRoleRemoved"):
            return
        operator = self.request("GET", GRAPH + "/me?$select=id")["id"]
        scope = self.config["vaultResourceId"] + "/secrets/" + SECRET_NAME
        expected = (
            scope
            + "/providers/Microsoft.Authorization/roleAssignments/"
            + str(uuid5(NAMESPACE_URL, scope + operator + "secret-reader"))
        )
        if role_id != expected:
            raise ValueError("Refusing to remove another operator's secret-read assignment")
        self.request("DELETE", ARM + role_id + "?api-version=2022-04-01")
        self.state["operatorSecretReadRoleRemoved"] = True
        self.save()

    def apply(self):
        config = self.config
        name = config["connectionName"]
        marker = OWNER + ": " + config["workflowResourceId"]
        if not self.state.get("applicationObjectId"):
            apps = self.request(
                "GET",
                GRAPH + "/applications",
                params={
                    "$filter": f"displayName eq '{name}'",
                    "$select": "id,appId,notes",
                },
            )["value"]
            if len(apps) > 1 or (apps and apps[0].get("notes") != marker):
                raise ValueError("Refusing to reuse an unrelated callback application")
            app = (
                apps[0]
                if apps
                else self.request(
                    "POST",
                    GRAPH + "/applications",
                    json={
                        "displayName": name,
                        "notes": marker,
                        "signInAudience": "AzureADMyOrg",
                        "requiredResourceAccess": [],
                    },
                )
            )
            self.state.update(applicationObjectId=app["id"], clientId=app["appId"])
            self.save()
        if not self.state.get("principalId"):
            principals = self.request(
                "GET",
                GRAPH + "/servicePrincipals",
                params={"$filter": f"appId eq '{self.state['clientId']}'", "$select": "id"},
            )["value"]
            principal = (
                principals[0]
                if len(principals) == 1
                else self.request(
                    "POST",
                    GRAPH + "/servicePrincipals",
                    json={"appId": self.state["clientId"]},
                )
            )
            self.state["principalId"] = principal["id"]
            self.save()
        url = ARM + config["workflowResourceId"] + "/config/authsettingsV2?api-version=2023-12-01"
        original = self.request("GET", url)["properties"]
        if not (self.output / "callback-auth-before.json").exists():
            write_json(self.output / "callback-auth-before.json", {"properties": original})
        desired = restricted_auth(original, config, self.state["principalId"])
        self.request("PUT", url, json={"properties": desired})
        actual = self.request("GET", url)["properties"]
        if restricted_auth(actual, config, self.state["principalId"]) != actual:
            raise ValueError("The exact callback principal restriction did not persist")
        self.state["ingressPrincipalVerified"] = True
        self.save()
        if not self.state.get("connectionId"):
            secret = self.secret()
            connection = self.request(
                "POST",
                FABRIC + "/connections",
                json={
                    "connectivityType": "ShareableCloud",
                    "displayName": name,
                    "connectionDetails": {
                        "type": "WebForPipeline",
                        "creationMethod": "WebForPipeline.Contents",
                        "parameters": [
                            {
                                "dataType": "Text",
                                "name": "baseUrl",
                                "value": self.workflow_url,
                            },
                            {"dataType": "Text", "name": "audience", "value": config["audience"]},
                        ],
                    },
                    "privacyLevel": "Organizational",
                    "credentialDetails": {
                        "singleSignOnType": "None",
                        "connectionEncryption": "NotEncrypted",
                        "skipTestConnection": False,
                        "credentials": {
                            "credentialType": "ServicePrincipal",
                            "tenantId": config["tenantId"],
                            "servicePrincipalClientId": self.state["clientId"],
                            "servicePrincipalSecret": secret,
                        },
                    },
                    "allowUsageInUserControlledCode": False,
                },
            )
            self.state["connectionId"] = connection["id"]
            self.save()
        connection = self.request("GET", FABRIC + "/connections/" + self.state["connectionId"])
        if (
            connection["displayName"] != name
            or connection["connectionDetails"]["type"] != "WebForPipeline"
            or connection["connectionDetails"]["path"]
            != self.workflow_url + ";" + config["audience"]
            or connection["credentialDetails"]["credentialType"] != "ServicePrincipal"
            or connection["credentialDetails"]["skipTestConnection"]
        ):
            raise ValueError("The Fabric callback credential read-back disagrees")
        self.state["fabricConnectionVerified"] = True
        self.save()
        return self.state

    @property
    def workflow_url(self):
        return "https://" + self.config["workflowResourceId"].split("/")[-1] + ".azurewebsites.net"


def probe_connection(callback):
    config, state = callback.config, callback.state
    workspace = config["workspaceId"]
    definition = probe_definition(state["connectionId"])
    item_id = None
    terminal = False
    proof = {
        "purpose": "Fabric callback authentication only; no coordinator or model invocation",
        "restrictedPrincipalId": state["principalId"],
        "identityCheck": "Host authorization permits only this principal; anonymous denied",
    }
    proof_path = callback.output / "fabric-callback-auth-probe.json"
    if proof_path.exists():
        previous = json.loads(proof_path.read_text())
        previous_id = str(UUID(previous.get("job", {}).get("id", str(uuid4()))))
        write_json(callback.output / f"fabric-callback-auth-probe-{previous_id}.json", previous)
    headers = {
        "Authorization": "Bearer "
        + callback.credential.get_token("https://api.fabric.microsoft.com/.default").token
    }
    base = FABRIC + f"/workspaces/{workspace}/items"
    try:
        name = "closeout-callback-auth-" + uuid4().hex[:12]
        item = callback.request(
            "POST",
            base,
            json={
                "displayName": name,
                "description": OWNER + " temporary authentication verification",
                "type": "DataPipeline",
                "definition": definition,
            },
        )
        if not item.get("id"):
            listing = callback.request("GET", base)["value"]
            item = next(row for row in listing if row["displayName"] == name)
        item_id = str(UUID(item["id"]))
        proof.update(pipelineId=item_id, connectionId=state["connectionId"])
        write_json(callback.output / "fabric-callback-auth-probe.json", proof)
        response = callback.client.post(
            base + f"/{item_id}/jobs/instances?jobType=Pipeline",
            headers=headers,
            json={"executionData": {"parameters": {}}},
        )
        response.raise_for_status()
        location = response.headers["Location"]
        if not location.startswith(base + f"/{item_id}/jobs/instances/"):
            raise ValueError("Unexpected Fabric job polling location")
        for _ in range(90):
            job = callback.request("GET", location)
            proof["job"] = {
                key: job[key]
                for key in ("id", "status", "startTimeUtc", "endTimeUtc")
                if key in job
            }
            if job.get("failureReason"):
                proof["failureCode"] = job["failureReason"].get("errorCode")
                message = job["failureReason"].get("message", "")
                message = re.sub(r"Bearer\s+\S+|eyJ[\w.-]+", "[redacted-token]", message)
                proof["failureSummary"] = message[:2000]
            write_json(callback.output / "fabric-callback-auth-probe.json", proof)
            if job["status"] in {"Completed", "Failed", "Cancelled", "Deduped"}:
                terminal = True
                if job["status"] != "Completed":
                    raise ValueError("The actual Fabric callback authentication probe failed")
                break
            time.sleep(2)
        else:
            raise TimeoutError("The Fabric callback authentication probe is still running")
        anonymous = callback.client.get(callback.workflow_url)
        spoofed = callback.client.get(
            callback.workflow_url,
            headers={"x-ms-client-principal-id": state["principalId"]},
        )
        if anonymous.status_code != 401 or spoofed.status_code != 401:
            raise ValueError("Anonymous or principal-header-only access was not rejected")
        proof["unauthenticatedStatuses"] = [anonymous.status_code, spoofed.status_code]
        state["nativeFabricAuthProbeVerified"] = True
        state["authProbeJobId"] = job["id"]
        callback.save()
        return proof
    finally:
        if item_id and terminal:
            response = callback.client.delete(base + "/" + item_id, headers=headers)
            response.raise_for_status()
            proof["temporaryPipelineDeleted"] = True
        write_json(callback.output / "fabric-callback-auth-probe.json", proof)


def main():
    from azure.identity import AzureCliCredential

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", required=True)
    parser.add_argument(
        "--probe",
        action="store_true",
        help="Verify actual Fabric Web activity identity using a temporary pipeline",
    )
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8-sig"))
    with (
        AzureCliCredential(tenant_id=config["tenantId"], process_timeout=60) as credential,
        httpx.Client(timeout=60, follow_redirects=False) as client,
    ):
        setup = CallbackSetup(config, args.output, credential, client)
        try:
            result = setup.apply()
            if args.probe:
                probe_connection(setup)
        finally:
            setup.release_operator_reader()
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

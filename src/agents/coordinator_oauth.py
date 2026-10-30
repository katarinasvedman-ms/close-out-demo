"""Explicitly provision and authorize a dedicated delegated Foundry demo client."""

import argparse
import json
import re
from pathlib import Path
from uuid import UUID

import httpx
from pydantic import field_validator

from agents.definitions import StrictOutput
from shared.local_demo import write_json

AUDIENCE = "https://ai.azure.com"
SCOPE = f"{AUDIENCE}/user_impersonation"
OWNER = "close-out-demo:coordinator-delegated-foundry"
GRAPH = "https://graph.microsoft.com/v1.0"


class OAuthClient(StrictOutput):
    tenantId: str
    clientId: str
    objectId: str

    @field_validator("tenantId", "clientId", "objectId")
    @classmethod
    def identifier(cls, value):
        if str(UUID(value)) != value:
            raise ValueError("Use a canonical UUID")
        return value


def register_client(
    graph: httpx.Client, name: str, tenant: str, *, create: bool = False
) -> OAuthClient:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9-]{0,99}", name):
        raise ValueError("Use an explicit simple OAuth client name")
    if str(UUID(tenant)) != tenant:
        raise ValueError("Use an explicit canonical tenant UUID")
    response = graph.get(
        f"{GRAPH}/servicePrincipals",
        params={
            "$filter": f"servicePrincipalNames/any(p:p eq '{AUDIENCE}')",
            "$select": "appId,oauth2PermissionScopes",
        },
    )
    response.raise_for_status()
    resources = response.json()["value"]
    if len(resources) != 1:
        raise ValueError("Expected exactly one Foundry resource service principal")
    scopes = [
        row
        for row in resources[0]["oauth2PermissionScopes"]
        if row.get("value") == "user_impersonation" and row.get("isEnabled")
    ]
    if len(scopes) != 1:
        raise ValueError("The Foundry delegated user_impersonation scope is unavailable")
    expected = {
        "displayName": name,
        "notes": OWNER,
        "signInAudience": "AzureADMyOrg",
        "isFallbackPublicClient": False,
        "publicClient": {"redirectUris": ["http://localhost"]},
        "requiredResourceAccess": [
            {
                "resourceAppId": resources[0]["appId"],
                "resourceAccess": [{"id": scopes[0]["id"], "type": "Scope"}],
            }
        ],
    }
    response = graph.get(f"{GRAPH}/applications", params={"$filter": f"displayName eq '{name}'"})
    response.raise_for_status()
    existing = response.json()["value"]
    if len(existing) > 1:
        raise ValueError("OAuth client name is ambiguous; no application was changed")
    if not existing:
        if not create:
            raise ValueError("OAuth client is absent; --create explicitly authorizes registration")
        response = graph.post(f"{GRAPH}/applications", json=expected)
        response.raise_for_status()
        object_id = str(UUID(response.json()["id"]))
    else:
        object_id = str(UUID(existing[0]["id"]))
    response = graph.get(f"{GRAPH}/applications/{object_id}")
    response.raise_for_status()
    actual = response.json()
    if any(actual.get(key) != value for key, value in expected.items()) or any(
        actual.get(key) for key in ("passwordCredentials", "keyCredentials")
    ):
        raise ValueError("Existing OAuth client differs; refusing to alter its configuration")
    return OAuthClient(tenantId=tenant, clientId=actual["appId"], objectId=object_id)


def store_credential(
    management: httpx.Client,
    vault_id: str,
    secret_name: str,
    client: OAuthClient,
    refresh_token: str,
) -> str:
    if not re.fullmatch(
        r"/subscriptions/[a-zA-Z0-9-]+/resourceGroups/[a-zA-Z0-9_.()-]+"
        r"/providers/Microsoft\.KeyVault/vaults/[a-zA-Z0-9-]+",
        vault_id,
        flags=re.IGNORECASE,
    ) or not re.fullmatch(r"[a-zA-Z0-9-]{1,127}", secret_name):
        raise ValueError("Select an explicit existing Key Vault ARM ID and a new secret name")
    if not refresh_token:
        raise ValueError("No renewable user credential was returned")
    secret_id = f"{vault_id}/secrets/{secret_name}"
    url = f"https://management.azure.com{secret_id}"
    params = {"api-version": "2023-07-01"}
    response = management.get(url, params=params)
    if response.status_code != 404:
        response.raise_for_status()
        raise ValueError("The credential secret already exists; refusing to overwrite it")
    response = management.put(
        url,
        params=params,
        headers={"If-None-Match": "*"},
        json={
            "tags": {"component": OWNER, "client-id": client.clientId},
            "properties": {
                "contentType": "application/json",
                "attributes": {"enabled": True},
                "value": json.dumps(
                    {
                        "tenantId": client.tenantId,
                        "clientId": client.clientId,
                        "refreshToken": refresh_token,
                    }
                ),
            },
        },
    )
    response.raise_for_status()
    response = management.get(url, params=params)
    response.raise_for_status()
    actual = response.json()
    if actual.get("tags", {}).get("client-id") != client.clientId:
        raise RuntimeError("Credential ownership read-back did not match")
    return secret_id


def sign_in(client: OAuthClient, login_hint: str | None = None) -> str:
    import msal

    cache = msal.TokenCache()
    application = msal.PublicClientApplication(
        client.clientId,
        authority=f"https://login.microsoftonline.com/{client.tenantId}",
        token_cache=cache,
    )
    result = application.acquire_token_interactive(
        scopes=[SCOPE],
        login_hint=login_hint,
        prompt="select_account",
        timeout=300,
    )
    if "access_token" not in result:
        raise RuntimeError(
            f"Microsoft user authorization failed: {result.get('error', 'NoAccessToken')}. "
            f"{result.get('error_description', 'No access token was returned.')}"
        )
    if result.get("id_token_claims", {}).get("tid") != client.tenantId:
        raise RuntimeError("The authorized user is not in the selected Foundry tenant")
    tokens = cache.find(
        msal.TokenCache.CredentialType.REFRESH_TOKEN, query={"client_id": client.clientId}
    )
    if len(tokens) != 1 or not tokens[0].get("secret"):
        raise RuntimeError("User authorization did not return one renewable credential")
    return tokens[0]["secret"]


def main() -> None:
    from azure.identity import AzureCliCredential

    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    register = commands.add_parser("register")
    register.add_argument("--name", required=True)
    register.add_argument("--tenant", required=True)
    register.add_argument("--create", action="store_true")
    register.add_argument("--output", type=Path, required=True)
    authorize = commands.add_parser("sign-in")
    authorize.add_argument("--config", type=Path, required=True)
    authorize.add_argument("--vault-resource-id", required=True)
    authorize.add_argument("--secret-name", required=True)
    authorize.add_argument("--login-hint")
    args = parser.parse_args()
    with AzureCliCredential() as credential:
        if args.command == "register":
            token = credential.get_token("https://graph.microsoft.com/.default")
            with httpx.Client(
                headers={"Authorization": f"Bearer {token.token}"},
                timeout=30,
                follow_redirects=False,
            ) as graph:
                client = register_client(graph, args.name, args.tenant, create=args.create)
            write_json(args.output, client.model_dump())
            print(client.model_dump_json())
        else:
            client = OAuthClient.model_validate_json(args.config.read_text(encoding="utf-8"))
            refresh = sign_in(client, args.login_hint)
            token = credential.get_token("https://management.azure.com/.default")
            with httpx.Client(
                headers={"Authorization": f"Bearer {token.token}"},
                timeout=30,
                follow_redirects=False,
            ) as management:
                secret_id = store_credential(
                    management, args.vault_resource_id, args.secret_name, client, refresh
                )
            print(json.dumps({"credentialStored": True, "credentialResourceId": secret_id}))


if __name__ == "__main__":
    main()

import json

import httpx
import pytest

from agents.coordinator_oauth import OWNER, OAuthClient, register_client, store_credential

TENANT = "00000000-0000-4000-8000-000000000001"
CLIENT = "00000000-0000-4000-8000-000000000002"
OBJECT = "00000000-0000-4000-8000-000000000003"
VAULT = (
    "/subscriptions/00000000-0000-4000-8000-000000000004"
    "/resourceGroups/demo/providers/Microsoft.KeyVault/vaults/demo-vault"
)


def test_registration_creates_only_a_public_single_tenant_client_without_secrets():
    created = {}

    def handler(request):
        if request.url.path.endswith("/servicePrincipals"):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "appId": CLIENT,
                            "oauth2PermissionScopes": [
                                {"id": OBJECT, "value": "user_impersonation", "isEnabled": True}
                            ],
                        }
                    ]
                },
            )
        if request.method == "POST":
            created.update(json.loads(request.content))
            return httpx.Response(201, json={"id": OBJECT})
        if request.url.path.endswith(f"/applications/{OBJECT}"):
            return httpx.Response(200, json={**created, "id": OBJECT, "appId": CLIENT})
        return httpx.Response(200, json={"value": []})

    with httpx.Client(transport=httpx.MockTransport(handler)) as graph:
        result = register_client(graph, "coordinator-test", TENANT, create=True)
    assert result.clientId == CLIENT
    assert created["notes"] == OWNER
    assert created["signInAudience"] == "AzureADMyOrg"
    assert created["publicClient"] == {"redirectUris": ["http://localhost"]}
    assert created["isFallbackPublicClient"] is False
    assert "passwordCredentials" not in created
    assert "keyCredentials" not in created
    assert created["requiredResourceAccess"][0]["resourceAccess"] == [
        {"id": OBJECT, "type": "Scope"}
    ]


@pytest.mark.parametrize("status", [200, 403, 500])
def test_credential_store_never_overwrites_or_creates_after_a_read_error(status):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, json={})

    client = OAuthClient(tenantId=TENANT, clientId=CLIENT, objectId=OBJECT)
    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as management,
        pytest.raises((ValueError, httpx.HTTPStatusError)),
    ):
        store_credential(management, VAULT, "coordinator-token", client, "test-credential")
    assert [request.method for request in requests] == ["GET"]


def test_credential_store_uses_private_secret_payload_and_verifies_ownership():
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(404)
        return httpx.Response(200, json={"tags": {"client-id": CLIENT}})

    client = OAuthClient(tenantId=TENANT, clientId=CLIENT, objectId=OBJECT)
    with httpx.Client(transport=httpx.MockTransport(handler)) as management:
        secret_id = store_credential(management, VAULT, "coordinator-token", client, "test-refresh")
    assert secret_id == f"{VAULT}/secrets/coordinator-token"
    assert [request.method for request in requests] == ["GET", "PUT", "GET"]
    put = requests[1]
    assert put.headers["If-None-Match"] == "*"
    payload = json.loads(put.content)
    value = json.loads(payload["properties"]["value"])
    assert value == {"tenantId": TENANT, "clientId": CLIENT, "refreshToken": "test-refresh"}
    assert "test-refresh" not in secret_id


def test_invalid_vault_fails_before_network_access():
    client = OAuthClient(tenantId=TENANT, clientId=CLIENT, objectId=OBJECT)
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: pytest.fail(str(request.url)))
    ) as m:
        with pytest.raises(ValueError, match="Key Vault"):
            store_credential(m, "https://untrusted.example", "token", client, "test-refresh")

import copy

import httpx
import pytest

from agents.flow_callback import CallbackSetup, probe_definition, restricted_auth, validate_config

TENANT = "00000000-0000-4000-8000-000000000001"
PRINCIPAL = "00000000-0000-4000-8000-000000000002"
API = "00000000-0000-4000-8000-000000000003"


def config():
    group = f"/subscriptions/{TENANT}/resourceGroups/selected"
    return {
        "tenantId": TENANT,
        "workspaceId": TENANT,
        "apiClientId": API,
        "audience": "api://" + API,
        "workflowResourceId": group + "/providers/Microsoft.Web/sites/selected-workflow",
        "vaultResourceId": group + "/providers/Microsoft.KeyVault/vaults/selected-vault",
        "connectionName": "isolated-callback",
    }


def auth():
    return {
        "platform": {"enabled": True},
        "globalValidation": {
            "requireAuthentication": True,
            "unauthenticatedClientAction": "Return401",
        },
        "identityProviders": {
            "azureActiveDirectory": {
                "registration": {
                    "clientId": API,
                    "openIdIssuer": f"https://login.microsoftonline.com/{TENANT}/v2.0",
                },
                "validation": {
                    "allowedAudiences": ["api://" + API],
                    "defaultAuthorizationPolicy": {"allowedPrincipals": {"identities": [TENANT]}},
                },
            }
        },
        "httpSettings": {"requireHttps": True},
    }


def test_callback_pins_only_the_authorized_principal_without_changing_protection():
    original = auth()
    before = copy.deepcopy(original)
    result = restricted_auth(original, config(), PRINCIPAL)
    assert original == before
    aad = result["identityProviders"]["azureActiveDirectory"]
    assert aad["validation"]["defaultAuthorizationPolicy"] == {
        "allowedPrincipals": {"identities": [PRINCIPAL]}
    }
    aad["validation"]["defaultAuthorizationPolicy"] = before["identityProviders"][
        "azureActiveDirectory"
    ]["validation"]["defaultAuthorizationPolicy"]
    assert result == before
    original["globalValidation"]["requireAuthentication"] = False
    with pytest.raises(ValueError, match="API protection"):
        restricted_auth(original, config(), PRINCIPAL)


def test_callback_rejects_cross_group_secret_storage_and_changed_api_audience():
    values = config()
    validate_config(values)
    values["vaultResourceId"] = values["vaultResourceId"].replace("/selected/", "/unrelated/")
    with pytest.raises(ValueError, match="selected workflow group"):
        validate_config(values)
    values = config()
    values["audience"] = "https://management.azure.com"
    with pytest.raises(ValueError, match="existing protected API"):
        validate_config(values)


def test_callback_credential_errors_do_not_echo_secret_response_bodies(tmp_path):
    from types import SimpleNamespace

    credential = SimpleNamespace(get_token=lambda scope: SimpleNamespace(token="synthetic-token"))
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(400, json={"error": {"message": "synthetic-secret"}})
        )
    ) as client:
        setup = CallbackSetup(config(), tmp_path, credential, client)
        with pytest.raises(RuntimeError, match="HTTP 400") as error:
            setup.request("POST", "https://graph.microsoft.com/v1.0/applications")
    assert "synthetic-secret" not in str(error.value)
    assert not list(tmp_path.iterdir())


def test_fabric_callback_probe_checks_real_identity_without_starting_inference():
    import base64
    import json

    definition = probe_definition(TENANT)
    pipeline = json.loads(base64.b64decode(definition["parts"][0]["payload"]))
    (check,) = pipeline["properties"]["activities"]
    assert check["typeProperties"] == {
        "method": "GET",
        "relativeUrl": "/",
        "turnOffAsync": True,
    }
    assert check["externalReferences"] == {"connection": TENANT}
    assert check["policy"]["secureInput"] and check["policy"]["secureOutput"]


@pytest.mark.parametrize("storage_fails", [False, True])
def test_callback_secret_is_vault_only_and_revoked_if_storage_fails(tmp_path, storage_fails):
    import json
    from types import SimpleNamespace

    secret = "synthetic-credential-never-write-to-disk"
    revoked = []

    def handle(request):
        if request.url.path.endswith("/addPassword"):
            return httpx.Response(
                200,
                json={
                    "secretText": secret,
                    "keyId": PRINCIPAL,
                    "endDateTime": "2026-11-28T12:00:00Z",
                },
            )
        if request.url.path.endswith("/removePassword"):
            revoked.append(json.loads(request.content)["keyId"])
            return httpx.Response(204)
        if request.method == "PUT":
            assert request.url.path.endswith("/secrets/fabric-callback-client-secret")
            assert json.loads(request.content)["properties"]["value"] == secret
            return httpx.Response(503 if storage_fails else 200, json={})
        return httpx.Response(
            200,
            json={
                "properties": {
                    "tenantId": TENANT,
                    "vaultUri": "https://selected-vault.vault.azure.net/",
                }
            },
        )

    credential = SimpleNamespace(get_token=lambda scope: SimpleNamespace(token="synthetic-token"))
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        setup = CallbackSetup(config(), tmp_path, credential, client)
        setup.state["applicationObjectId"] = TENANT
        if storage_fails:
            with pytest.raises(RuntimeError, match="503"):
                setup.secret()
            assert revoked == [PRINCIPAL]
            assert "credentialKeyId" not in setup.state
        else:
            assert setup.secret() == secret
            assert setup.state["credentialKeyId"] == PRINCIPAL
            assert revoked == []
        assert secret not in json.dumps(setup.state)
        assert all(secret not in path.read_text() for path in tmp_path.iterdir())

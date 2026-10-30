import json
from types import SimpleNamespace

import pytest

from agents import installer
from agents.assistant import AssistantSettings
from agents.coordinator_oauth import OWNER
from agents.definitions import AgentBinding
from agents.installer_state import Blocked


@pytest.fixture
def storage_setup(monkeypatch):
    progress = SimpleNamespace(
        profile=SimpleNamespace(subscriptionId="subscription", resourceGroup="demo-group")
    )
    values = {
        "storageEndpoint": "https://knowledge.blob.core.windows.net/",
        "flow": {"storageUrl": "https://playground.blob.core.windows.net"},
    }
    accounts = {
        name: {
            "publicNetworkAccess": "Disabled",
            "allowBlobPublicAccess": False,
            "allowSharedKeyAccess": False,
        }
        for name in ("knowledge", "playground")
    }
    tags = {"SecurityControl": "Ignore"}
    calls = []

    def az(*args):
        calls.append(args)
        if args[:2] == ("group", "show"):
            return tags.copy()
        assert args[:2] == ("storage", "account")
        assert args[args.index("--subscription") + 1] == "subscription"
        assert args[args.index("--resource-group") + 1] == "demo-group"
        account = accounts[args[args.index("--name") + 1]]
        if args[2] == "update":
            assert args[args.index("--public-network-access") + 1] == "Enabled"
            account["publicNetworkAccess"] = "Enabled"
        return account.copy()

    monkeypatch.setattr(installer, "az", az)
    return progress, values, accounts, tags, calls


def test_restores_only_document_accounts_with_exception(storage_setup):
    progress, values, accounts, _, calls = storage_setup
    installer.restore_document_storage_network(progress, values)
    assert all(account["publicNetworkAccess"] == "Enabled" for account in accounts.values())
    updates = [args for args in calls if args[:3] == ("storage", "account", "update")]
    assert len(updates) == 2
    assert all("--allow-shared-key-access" not in args for args in updates)
    calls.clear()
    installer.restore_document_storage_network(progress, values)
    assert len(calls) == 2
    assert all(args[:3] == ("storage", "account", "show") for args in calls)


def test_does_not_restore_without_exception(storage_setup):
    progress, values, _, tags, calls = storage_setup
    tags.clear()
    with pytest.raises(Blocked, match="policy-owner"):
        installer.restore_document_storage_network(progress, values)
    assert not any(args[:3] == ("storage", "account", "update") for args in calls)


@pytest.mark.parametrize("setting", ["allowBlobPublicAccess", "allowSharedKeyAccess"])
def test_does_not_expose_insecure_storage(storage_setup, setting):
    progress, values, accounts, _, calls = storage_setup
    accounts["knowledge"][setting] = True
    with pytest.raises(Blocked, match="anonymous Blob and shared-key"):
        installer.restore_document_storage_network(progress, values)
    assert not any(args[:3] == ("storage", "account", "update") for args in calls)


def test_checks_persisted_network_state(storage_setup, monkeypatch):
    progress, values, _, _, _ = storage_setup
    original = installer.az

    def az(*args):
        if args[:3] == ("storage", "account", "update"):
            return {}
        return original(*args)

    monkeypatch.setattr(installer, "az", az)
    with pytest.raises(Blocked, match="did not retain"):
        installer.restore_document_storage_network(progress, values)


def test_resume_checks_storage_without_redeploying(storage_setup, monkeypatch):
    progress, values, accounts, _, _ = storage_setup
    progress.profile.foundryName = "demo-foundry"
    progress.profile.operatorId = "operator"
    progress.profile.deploymentName = "demo-deployment"
    progress.data = {
        "infra": values,
        "ingress": {"eventstreamNamespace": "namespace", "eventstreamEntity": "entity"},
    }
    progress.profile.apiClientId = "api-client"
    progress.profile.dispatchPrincipalId = "dispatch"
    progress.matches = lambda *args: True
    progress.done = lambda *args: None
    original = installer.az

    def az(*args):
        if args[:3] == ("deployment", "group", "show"):
            return {
                "properties": {
                    "provisioningState": "Succeeded",
                    "outputs": {key: {"value": value} for key, value in values.items()},
                }
            }
        return original(*args)

    monkeypatch.setattr(installer, "az", az)
    monkeypatch.setattr(installer, "validate_outputs", lambda *args: None)
    monkeypatch.setattr(installer, "source_hash", lambda *args: "source")
    assert installer.deploy_azure(progress) == values
    assert all(account["publicNetworkAccess"] == "Enabled" for account in accounts.values())


@pytest.fixture
def review_setup(tmp_path, monkeypatch):
    endpoint = "https://demo.services.ai.azure.com/api/projects/demo"
    tenant = "00000000-0000-4000-8000-000000000001"
    settings = SimpleNamespace(
        endpoint=endpoint,
        closeout=AgentBinding(name="close-out-coordinator", version="1", definitionHash="baseline"),
    )
    interactive = AssistantSettings(
        endpoint=endpoint,
        agent=AgentBinding(name="close-out-assistant", version="4", definitionHash="assistant"),
        model="existing-model",
        sourceAgent=settings.closeout.name,
        sourceVersion="1",
    )
    calls = []
    progress = SimpleNamespace(
        profile=SimpleNamespace(
            subscriptionId="subscription", tenantId=tenant, resourceGroup="demo", fabric={}
        ),
        data={},
        approve=lambda *args: calls.append(("approve", args)),
        save=lambda: None,
    )
    values = {
        "tenantId": tenant,
        "projectEndpoint": endpoint,
        "projectResourceId": "target-project",
        "flow": {"workflowPrincipalId": "workflow-identity"},
    }

    def assistant(*args, **kwargs):
        calls.append(("assistant", args, kwargs))
        return interactive

    def coordinator(*args, **kwargs):
        calls.append(("coordinator", args, kwargs))
        return {
            "endpoint": endpoint,
            "agent": {"name": "close-out-coordinator", "version": "21", "definitionHash": "brief"},
        }

    def az(*args):
        calls.append(("az", args))
        if args[:2] == ("keyvault", "show"):
            return (
                "/subscriptions/subscription/resourceGroups/demo"
                "/providers/Microsoft.KeyVault/vaults/target-vault"
            )
        if args[:2] == ("rest", "--method"):
            return {
                "enabled": True,
                "contentType": "application/json",
                "tags": {
                    "component": OWNER,
                    "client-id": "00000000-0000-4000-8000-000000000002",
                },
            }
        if args[:3] == ("role", "assignment", "list"):
            return []
        if args[:3] == ("deployment", "group", "what-if"):
            return [{"changeType": "Create", "resourceId": "secret-only-role"}]
        assert args[:3] == ("deployment", "group", "create")
        return "Succeeded"

    monkeypatch.setattr(installer.assistant, "register", assistant)
    monkeypatch.setattr(installer.coordinator_brief, "register", coordinator)
    monkeypatch.setattr(installer, "az", az)
    return progress, values, settings, tmp_path, calls


def authorize_review(progress):
    progress.data["coordinatorAuth"] = {
        "client": {
            "tenantId": progress.profile.tenantId,
            "clientId": "00000000-0000-4000-8000-000000000002",
            "objectId": "00000000-0000-4000-8000-000000000003",
        },
        "credentialUri": "https://target-vault.vault.azure.net/secrets/coordinator-user",
    }


def test_publishes_separate_assistant_without_enabling_unsupported_runtime(review_setup):
    progress, values, settings, state, calls = review_setup
    binding, workflow = installer.prepare_review_agents(progress, values, state, settings)
    assert binding == settings.closeout
    assert workflow is None
    publication = next(call for call in calls if call[0] == "assistant")
    assert publication[1][-1] == state / "assistant.json"
    assert publication[2] == {
        "fabric_connection": "close-out-assistant-fabric-user",
        "create_fabric_connection": True,
    }
    assert progress.data["assistant"]["agent"]["version"] == "4"
    assert not any(call[0] in {"az", "coordinator"} for call in calls)
    with pytest.raises(Blocked, match="KQL-backed"):
        installer.require_coordinator_auth(progress, values)


def test_fabric_permissions_stop_before_any_mutation_without_delegation(review_setup):
    progress, values, _, _, calls = review_setup
    with pytest.raises(Blocked, match="KQL-backed"):
        installer.fabric_permissions(progress, None, values)
    assert calls == []


def test_auth_metadata_alone_cannot_enable_an_unpublished_readable_runtime(review_setup):
    progress, values, _, _, _ = review_setup
    authorize_review(progress)
    with pytest.raises(Blocked, match="KQL-backed"):
        installer.enable(progress, None, values, None)


def test_delegated_resume_pins_readable_version_and_secret_only_rotation(review_setup):
    progress, values, settings, state, calls = review_setup
    authorize_review(progress)
    binding, path = installer.prepare_review_agents(progress, values, state, settings)
    assert binding.version == "21"
    assert settings.closeout.version == "1"
    installer.require_coordinator_auth(progress, values)
    workflow = json.loads(path.read_text())
    actions = workflow["definition"]["actions"]
    start = actions["Start_agent_response"]
    assert start["inputs"]["body"]["agent_reference"]["version"] == "21"
    assert "authentication" not in start["inputs"]
    assert start["runAfter"] == {"Persist_coordinator_credential": ["Succeeded"]}
    assert actions["Persist_coordinator_credential"]["inputs"]["authentication"]["type"] == (
        "ManagedServiceIdentity"
    )
    assert ".md" in json.dumps(workflow)
    assert all(
        trigger["runtimeConfiguration"]["concurrency"]["runs"] == 1
        for trigger in workflow["definition"]["triggers"].values()
    )
    deployment = next(
        call[1]
        for call in calls
        if call[0] == "az" and call[1][:3] == ("deployment", "group", "create")
    )
    assert "vaultName=target-vault" in deployment
    assert "secretName=coordinator-user" in deployment
    assert "workflowPrincipalId=workflow-identity" in deployment
    assert deployment[deployment.index("--template-file") + 1].endswith(
        "coordinator-credential-access.bicep"
    )
    publication = next(call for call in calls if call[0] == "coordinator")
    assert publication[1][2] == "1"
    assert publication[2]["fabric_connection"] == "close-out-coordinator-fabric-user"
    assert not any("secret" in call[1][:2] for call in calls if call[0] == "az")


@pytest.mark.parametrize(
    "uri",
    [
        "https://wrong.example/secrets/credential",
        "https://target-vault.vault.azure.net/secrets/name/version",
        "https://target-vault.vault.azure.net/secrets/name?secret=bad",
    ],
)
def test_delegation_refuses_unsafe_or_versioned_secret_uris(review_setup, uri):
    progress, values, settings, state, calls = review_setup
    authorize_review(progress)
    progress.data["coordinatorAuth"]["credentialUri"] = uri
    with pytest.raises(Blocked, match="unversioned"):
        installer.prepare_review_agents(progress, values, state, settings)
    assert not any(call[0] in {"az", "coordinator"} for call in calls)


def test_delegation_refuses_other_tenant_before_accessing_credential(review_setup):
    progress, values, settings, state, calls = review_setup
    authorize_review(progress)
    progress.data["coordinatorAuth"]["client"]["tenantId"] = "00000000-0000-4000-8000-000000000099"
    with pytest.raises(Blocked, match="matching tenant"):
        installer.prepare_review_agents(progress, values, state, settings)
    assert not any(call[0] in {"az", "coordinator"} for call in calls)


@pytest.mark.parametrize("metadata", [{}, {"enabled": False}, {"enabled": True, "tags": None}])
def test_delegation_refuses_unowned_or_disabled_credentials(review_setup, monkeypatch, metadata):
    progress, values, settings, state, calls = review_setup
    authorize_review(progress)
    original = installer.az

    def az(*args):
        if args[:2] == ("rest", "--method"):
            return metadata
        return original(*args)

    monkeypatch.setattr(installer, "az", az)
    with pytest.raises(Blocked, match="ownership|target-owned"):
        installer.prepare_review_agents(progress, values, state, settings)
    assert not any(call[0] == "coordinator" for call in calls)
    assert not any(
        call[0] == "az" and call[1][:2] in {("role", "assignment"), ("deployment", "group")}
        for call in calls
    )

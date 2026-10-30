from types import SimpleNamespace

import pytest

from agents import installer
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

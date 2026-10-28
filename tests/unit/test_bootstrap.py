import copy
import json
import subprocess
from pathlib import Path

import pytest

from agents import bootstrap

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def infra_values():
    return {
        "subscriptionId": "target-subscription",
        "tenantId": "target-tenant",
        "resourceGroupName": "target-group",
        "foundryName": "target-foundry",
        "projectName": "target-project",
        "projectResourceId": (
            "/subscriptions/target-subscription/resourceGroups/target-group"
            "/providers/Microsoft.CognitiveServices/accounts/target-foundry/projects/target-project"
        ),
        "projectEndpoint": "https://target-foundry.services.ai.azure.com/api/projects/target-project",
        "modelDeploymentName": "target-model",
        "modelName": "gpt-4o",
        "modelVersion": "2024-11-20",
        "knowledgeModel": {
            "azureOpenAIParameters": {"resourceUri": "https://target-foundry.openai.azure.com/"}
        },
    }


def receipt(site):
    blocked = site == bootstrap.SITES[0]
    return {
        "result": {
            "siteId": site,
            "gaps": [
                {"conditionId": condition}
                for condition in ("CHK-A-GROUNDING", "CHK-A-AZ-B", "PL-0042-01")
            ]
            if blocked
            else [],
            "proposedRework": {"reason": "Synthetic gaps"} if blocked else None,
        },
        "knowledgeRetrieval": [
            {"blobUrl": "https://example.blob.core.windows.net/documents/iq/test"}
        ],
    }


def mock_commands(monkeypatch, tmp_path, infra_values, fail_at=None):
    calls = []
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)

    def command(executable, *args):
        calls.append((executable, args))
        if executable == "az":
            if args[:2] == ("account", "show"):
                return json.dumps({"id": "target-subscription", "tenantId": "target-tenant"})
            if args[:3] == ("deployment", "group", "show"):
                return json.dumps(
                    {
                        "properties": {
                            "provisioningState": "Succeeded",
                            "outputs": {
                                key: {"value": value} for key, value in infra_values.items()
                            },
                        }
                    }
                )
        if fail_at and args[:2] == ("-m", fail_at):
            raise subprocess.CalledProcessError(1, args)
        if args[:3] == ("-m", "agents", "gaps"):
            output = Path(args[args.index("--output") + 1])
            site = args[args.index("--site") + 1]
            number = len(list(output.glob("gaps-*.json")))
            (output / f"gaps-{number}.json").write_text(json.dumps(receipt(site)), encoding="utf-8")
        if args[:3] == ("-m", "agents", "register"):
            config = Path(args[args.index("--config") + 1])
            config.write_text('{"endpoint":"target-endpoint"}', encoding="utf-8")
        return ""

    monkeypatch.setattr(bootstrap, "command", command)
    return calls


def test_completion_uses_target_outputs_and_existing_infrastructure(
    monkeypatch,
    tmp_path,
    infra_values,
):
    calls = mock_commands(monkeypatch, tmp_path, infra_values)
    state = bootstrap.complete("target-group", "portal-deployment")
    assert json.loads((state / "completion.json").read_text())["passed"] is True
    registration = next(args for _, args in calls if args[:3] == ("-m", "agents", "register"))
    assert registration[registration.index("--endpoint") + 1] == infra_values["projectEndpoint"]
    assert registration[registration.index("--deployment") + 1] == "target-model"
    assert (state / f"{bootstrap.SITES[1]}-verified.json").exists()
    assert not any(executable == "azd" for executable, _ in calls)
    assert not any(args[:3] == ("deployment", "group", "create") for _, args in calls)
    verification = [args for _, args in calls if args[:3] == ("-m", "agents", "gaps")]
    assert len(verification) == 2
    assert verification[1][verification[1].index("--scenario") + 1] == "fallback-ready"


def test_failure_does_not_leave_stale_success(
    monkeypatch,
    tmp_path,
    infra_values,
):
    mock_commands(monkeypatch, tmp_path, infra_values, fail_at="agents.knowledge_setup")
    state = tmp_path / ".local" / "fresh" / "target-subscription" / "target-foundry"
    state.mkdir(parents=True)
    (state / "completion.json").write_text('{"passed":true}')
    with pytest.raises(subprocess.CalledProcessError):
        bootstrap.complete("target-group", "portal-deployment")
    assert not (state / "completion.json").exists()


def test_completion_rejects_a_deployment_in_another_tenant(
    monkeypatch,
    tmp_path,
    infra_values,
):
    infra_values["tenantId"] = "another-tenant"
    calls = mock_commands(monkeypatch, tmp_path, infra_values)
    with pytest.raises(ValueError, match="context and deployment target differ"):
        bootstrap.complete("target-group", "portal-deployment")
    assert not any(args[0] == "-m" for _, args in calls)


def test_completion_rejects_an_output_project_in_another_group(infra_values):
    infra_values["projectResourceId"] = infra_values["projectResourceId"].replace(
        "target-group", "another-group"
    )
    with pytest.raises(ValueError, match="selected Foundry project"):
        bootstrap.validate_target(infra_values)


@pytest.mark.parametrize("site", bootstrap.SITES)
def test_prompt_verification_accepts_both_fixtures(site):
    value = receipt(site)
    assert bootstrap.verify_result(value, site) == value


@pytest.mark.parametrize(
    "value",
    [
        {"error": {"code": "analysis_failed"}},
        {"result": {"siteId": "wrong-site", "gaps": [], "proposedRework": None}},
    ],
)
def test_prompt_verification_rejects_failed_or_wrong_site_results(value):
    with pytest.raises(ValueError):
        bootstrap.verify_result(value, bootstrap.SITES[1])


def test_prompt_verification_rejects_ready_rework_or_missing_iq():
    for field in ("rework", "iq"):
        value = copy.deepcopy(receipt(bootstrap.SITES[1]))
        if field == "rework":
            value["result"]["proposedRework"] = {"reason": "Fabricated"}
        else:
            value["knowledgeRetrieval"] = []
        with pytest.raises(ValueError):
            bootstrap.verify_result(value, bootstrap.SITES[1])


def test_portal_template_is_self_contained_and_never_creates_resource_groups():
    template = json.loads((ROOT / "infra" / "fresh" / "azuredeploy.json").read_text())
    assert template["$schema"].endswith("/deploymentTemplate.json#")
    required = {key for key, value in template["parameters"].items() if "defaultValue" not in value}
    assert required == {"foundryName"}
    resource_types = []

    def inspect(node):
        if isinstance(node, dict):
            resource_types.append(node.get("type"))
            assert "templateLink" not in node
            if node.get("type") == "Microsoft.Resources/deployments":
                assert "resourceGroup" not in node
                assert "subscriptionId" not in node
            for value in node.values():
                inspect(value)
        elif isinstance(node, list):
            for value in node:
                inspect(value)

    inspect(template)
    assert "Microsoft.Resources/resourceGroups" not in resource_types
    assert "Microsoft.ContainerRegistry/registries" not in resource_types
    assert "Microsoft.Authorization/roleDefinitions" not in resource_types
    assert not any(kind and "capabilityHosts" in kind for kind in resource_types)
    assert "Microsoft.CognitiveServices/accounts" in resource_types
    assert "Microsoft.CognitiveServices/accounts/projects" in resource_types
    assert "Microsoft.Search/searchServices" in resource_types
    assert "Microsoft.Storage/storageAccounts" in resource_types

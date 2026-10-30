import copy
import json
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from agents import bootstrap, fabric_connection, knowledge_setup
from agents.definitions import FabricSettings

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
        "searchEndpoint": "https://target.search.windows.net",
        "storageEndpoint": "https://target.blob.core.windows.net/",
        "storageResourceId": "/subscriptions/target-subscription/storage",
        "containerName": "documents",
        "connectionName": "target-knowledge",
        "knowledgeBaseName": "target-demo",
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
        if (
            fail_at == "document-upload"
            and args[:2] == ("-m", "agents.knowledge_setup")
            and "--definitions-only" not in args
        ):
            raise subprocess.CalledProcessError(1, args)
        if args[:3] == ("-m", "agents", "gaps"):
            output = Path(args[args.index("--output") + 1])
            site = args[args.index("--site") + 1]
            number = len(list(output.glob("gaps-*.json")))
            (output / f"gaps-{number}.json").write_text(json.dumps(receipt(site)), encoding="utf-8")
        if args[:3] == ("-m", "agents", "register"):
            config = Path(args[args.index("--config") + 1])
            value = {"endpoint": "target-endpoint"}
            if "flow" in infra_values:
                value = {
                    "endpoint": infra_values["projectEndpoint"],
                    "deployment": infra_values["modelDeploymentName"],
                    "model": infra_values["modelName"],
                    "modelVersion": infra_values["modelVersion"],
                    "validation": {
                        "name": "close-out-validation",
                        "version": "7",
                        "definitionHash": "v",
                    },
                    "closeout": {
                        "name": "close-out-coordinator",
                        "version": "19",
                        "definitionHash": "c",
                    },
                    **{
                        key: json.loads((config.parent / f"{key}.json").read_text())
                        for key in ("knowledge", "fabric", "publisher")
                    },
                }
            config.write_text(json.dumps(value), encoding="utf-8")
        if args[1:5] == ("deployment", "source", "config-zip", "--subscription"):
            return json.dumps({"id": "zip-deployment", "status": 4, "complete": True})
        return ""

    monkeypatch.setattr(bootstrap, "command", command)
    return calls


def test_completion_uses_target_outputs_and_existing_infrastructure(
    monkeypatch,
    tmp_path,
    infra_values,
):
    calls = mock_commands(monkeypatch, tmp_path, infra_values)
    state = bootstrap.complete(
        "target-group", "portal-deployment", seed_and_verify=True, facts_mode="stub"
    )
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
    definitions = next(
        index for index, (_, args) in enumerate(calls) if "--definitions-only" in args
    )
    generation = next(
        index for index, (_, args) in enumerate(calls) if args[:2] == ("-m", "shared.local_demo")
    )
    assert definitions < generation


def test_default_completion_publishes_definitions_without_assets_uploads_or_inference(
    monkeypatch, tmp_path, infra_values
):
    calls = mock_commands(monkeypatch, tmp_path, infra_values)
    state = bootstrap.complete("target-group", "portal-deployment", facts_mode="stub")
    deployed = json.loads((state / "deployment.json").read_text())
    assert deployed["passed"] and deployed["phase"] == "definitions"
    assert deployed["documentVerification"] == "not-run"
    assert not (state / "completion.json").exists()
    assert not (state / "demo").exists()
    phases = [args for executable, args in calls if executable != "az"]
    assert phases[0][:3] == ("-m", "agents", "register")
    assert phases[1][:2] == ("-m", "agents.knowledge_setup")
    assert "--definitions-only" in phases[1]
    assert len(phases) == 2
    assert json.loads((state / "knowledge.json").read_text())["knowledgeBase"] == "target-demo"


def test_optional_upload_failure_preserves_successful_definition_deployment(
    monkeypatch, tmp_path, infra_values
):
    mock_commands(monkeypatch, tmp_path, infra_values, fail_at="document-upload")
    with pytest.raises(subprocess.CalledProcessError):
        bootstrap.complete(
            "target-group", "portal-deployment", seed_and_verify=True, facts_mode="stub"
        )
    state = tmp_path / ".local" / "fresh" / "target-subscription" / "target-foundry"
    assert json.loads((state / "deployment.json").read_text())["passed"]
    assert not (state / "completion.json").exists()


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
        bootstrap.complete("target-group", "portal-deployment", facts_mode="stub")
    assert not (state / "completion.json").exists()
    assert not (state / "deployment.json").exists()
    assert (state / "config.json").exists()


@pytest.mark.parametrize("fail", [False, True])
def test_native_iq_definition_publishing_never_reads_assets_or_waits_for_ingestion(
    monkeypatch, tmp_path, infra_values, fail
):
    monkeypatch.setitem(
        sys.modules,
        "azure.identity",
        SimpleNamespace(AzureCliCredential=lambda: nullcontext(object())),
    )
    calls, definitions = [], {}

    class Search:
        def __init__(self, endpoint, credential):
            assert endpoint == infra_values["searchEndpoint"]

        def request(self, method, path, body=None):
            calls.append((method, path))
            if fail:
                raise RuntimeError("Search definition publication failed")
            if method == "PUT":
                definitions[path] = copy.deepcopy(body)
                return {}
            result = copy.deepcopy(definitions[path])
            if path.startswith("knowledgesources/"):
                result["azureBlobParameters"]["createdResources"] = {"index": "managed-index"}
            else:
                for reference in result["knowledgeSources"]:
                    reference.update({"enableImageServing": None, "enableFreshness": None})
            return result

        def close(self):
            pass

    monkeypatch.setattr(knowledge_setup, "SearchSetup", Search)
    infra = tmp_path / "infra.json"
    infra.write_text(json.dumps({key: {"value": value} for key, value in infra_values.items()}))
    args = SimpleNamespace(
        infra=infra,
        root=ROOT,
        site=["SE-STO-0042", "SE-GOT-0017"],
        knowledge_base="unused-default",
        source_name="test-documents",
        output=tmp_path / "knowledge.json",
        assets=tmp_path / "assets-do-not-exist",
        definitions_only=True,
    )
    if fail:
        with pytest.raises(RuntimeError, match="publication failed"):
            knowledge_setup.provision(args)
        assert not args.output.with_name("knowledge-definition-receipt.json").exists()
        return
    knowledge_setup.provision(args)
    assert json.loads(args.output.read_text())["knowledgeBase"] == "target-demo"
    assert calls == [
        ("PUT", "knowledgesources/test-documents"),
        ("PUT", "knowledgebases/target-demo"),
        ("GET", "knowledgesources/test-documents"),
        ("GET", "knowledgebases/target-demo"),
    ]
    receipt = json.loads(args.output.with_name("knowledge-definition-receipt.json").read_text())
    assert receipt["definitionsVerified"]
    assert receipt["documentVerification"] == "not-run"
    assert not args.assets.exists()
    assert not args.output.with_name("knowledge-upload-receipt.json").exists()


def test_completion_rejects_a_deployment_in_another_tenant(
    monkeypatch,
    tmp_path,
    infra_values,
):
    infra_values["tenantId"] = "another-tenant"
    calls = mock_commands(monkeypatch, tmp_path, infra_values)
    with pytest.raises(ValueError, match="context and deployment target differ"):
        bootstrap.complete("target-group", "portal-deployment", facts_mode="stub")
    assert not any(args[0] == "-m" for _, args in calls)


def test_completion_rejects_an_output_project_in_another_group(infra_values):
    infra_values["projectResourceId"] = infra_values["projectResourceId"].replace(
        "target-group", "another-group"
    )
    with pytest.raises(ValueError, match="selected Foundry project"):
        bootstrap.validate_target(infra_values)


def test_completion_rejects_an_output_endpoint_in_another_project(infra_values):
    infra_values["projectEndpoint"] = infra_values["projectEndpoint"].replace(
        "target-project", "another-project"
    )
    with pytest.raises(ValueError, match="selected Foundry project"):
        bootstrap.validate_target(infra_values)


@pytest.fixture
def fabric_settings():
    return FabricSettings(
        workspaceId="00000000-0000-4000-8000-000000000001",
        dataAgentId="00000000-0000-4000-8000-000000000002",
        databaseId="00000000-0000-4000-8000-000000000003",
        kustoEndpoint="https://test.kusto.fabric.microsoft.com",
        connectionName="test-fabric",
        toolName="DataAgent_test",
    )


def test_native_completion_binds_selected_project_without_reading_assets(
    monkeypatch, tmp_path, infra_values, fabric_settings
):
    calls = mock_commands(monkeypatch, tmp_path, infra_values)
    checked = []

    def ensure(project, settings, *, create):
        checked.append((project, settings, create))
        return {"definitionsVerified": True, "retrievalVerification": "not-run"}

    monkeypatch.setattr(bootstrap, "ensure_connection", ensure)
    state = bootstrap.complete(
        "target-group", "portal-deployment", fabric=fabric_settings, create_fabric_connection=True
    )
    assert checked == [(infra_values["projectResourceId"], fabric_settings, True)]
    registration = next(args for _, args in calls if args[:3] == ("-m", "agents", "register"))
    assert registration[registration.index("--fabric-config") + 1] == str(state / "fabric.json")
    assert registration[registration.index("--knowledge-config") + 1] == str(
        state / "knowledge.json"
    )
    assert json.loads((state / "fabric.json").read_text()) == fabric_settings.model_dump()
    deployed = json.loads((state / "deployment.json").read_text())
    assert deployed["facts"] == "native-fabric-iq-synthetic-fixtures"
    assert deployed["fabricVerification"] == deployed["unattendedVerification"] == "not-run"
    assert not (state / "demo").exists()
    assert not (state / "completion.json").exists()


def test_flow_completion_publishes_tool_and_runtime_with_actual_versions_but_no_inference(
    monkeypatch, tmp_path, infra_values, fabric_settings
):
    infra_values["flow"] = {
        "adapterName": "target-function",
        "workflowName": "target-workflow",
        "publisherConfig": {
            "serverUrl": "https://target-function.azurewebsites.net",
            "audience": "api://target-api",
        },
    }
    calls = mock_commands(monkeypatch, tmp_path, infra_values)
    monkeypatch.setattr(bootstrap, "ensure_connection", lambda *args, **kwargs: {})
    state = bootstrap.complete(
        "target-group", "portal-deployment", fabric=fabric_settings, publish_runtime=True
    )
    registration = next(args for _, args in calls if args[:3] == ("-m", "agents", "register"))
    assert registration[registration.index("--publisher-config") + 1] == str(
        state / "publisher.json"
    )
    tool = json.loads((state / "validation-publisher.openapi.json").read_text())
    assert tool["servers"] == [{"url": "https://target-function.azurewebsites.net"}]
    workflow = json.loads((state / "target-workflow-settings.json").read_text())
    assert workflow["FLOW_COORDINATOR_AGENT_VERSION"] == "19"
    assert workflow["Workflows.coordinator.FlowState"] == "Disabled"
    assert workflow["Workflows.validation.FlowState"] == "Disabled"
    deployments = [args for _, args in calls if args[1:4] == ("deployment", "source", "config-zip")]
    assert len(deployments) == 2
    assert {args[args.index("--name") + 1] for args in deployments} == {
        "target-function",
        "target-workflow",
    }
    assert all(args[args.index("--resource-group") + 1] == "target-group" for args in deployments)
    report = json.loads((state / "flow-deployment.json").read_text())
    assert report["unattendedVerification"] == "not-run"
    assert not (state / "completion.json").exists()
    assert not any(args[:3] == ("-m", "agents", "gaps") for _, args in calls)
    assert not any(args[:2] == ("-m", "shared.local_demo") for _, args in calls)


def test_flow_completion_rejects_missing_foundation_before_agent_publication(
    monkeypatch, tmp_path, infra_values, fabric_settings
):
    calls = mock_commands(monkeypatch, tmp_path, infra_values)
    with pytest.raises(ValueError, match="no flow foundation"):
        bootstrap.complete(
            "target-group", "portal-deployment", fabric=fabric_settings, publish_runtime=True
        )
    assert all(executable == "az" for executable, _ in calls)


def test_flow_runtime_failure_does_not_claim_completed_deployment(
    monkeypatch, tmp_path, infra_values, fabric_settings
):
    infra_values["flow"] = {
        "adapterName": "target-function",
        "workflowName": "target-workflow",
        "publisherConfig": {
            "serverUrl": "https://target-function.azurewebsites.net",
            "audience": "api://target-api",
        },
    }
    calls = mock_commands(monkeypatch, tmp_path, infra_values)
    original = bootstrap.command

    def command(executable, *args):
        result = original(executable, *args)
        if args[1:4] == ("deployment", "source", "config-zip"):
            return json.dumps({"status": 3, "complete": True})
        return result

    monkeypatch.setattr(bootstrap, "command", command)
    monkeypatch.setattr(bootstrap, "ensure_connection", lambda *args, **kwargs: {})
    with pytest.raises(RuntimeError, match="completed success"):
        bootstrap.complete(
            "target-group", "portal-deployment", fabric=fabric_settings, publish_runtime=True
        )
    state = tmp_path / ".local" / "fresh" / "target-subscription" / "target-foundry"
    assert (state / "deployment.json").exists()
    assert not (state / "flow-deployment.json").exists()
    assert (
        len([args for _, args in calls if args[1:4] == ("deployment", "source", "config-zip")]) == 1
    )


def test_flow_completion_rejects_wrong_publisher_host(infra_values):
    infra_values["flow"] = {
        "adapterName": "target-function",
        "publisherConfig": {
            "serverUrl": "https://another.azurewebsites.net",
            "audience": "api://api",
        },
    }
    with pytest.raises(ValueError, match="selected flow adapter"):
        bootstrap.validate_target(infra_values)


def test_native_completion_verifies_native_receipts_and_program(
    monkeypatch, tmp_path, infra_values, fabric_settings
):
    calls = mock_commands(monkeypatch, tmp_path, infra_values)
    original_command = bootstrap.command

    def command(executable, *args):
        result = original_command(executable, *args)
        if args[:3] == ("-m", "agents", "gaps"):
            output = Path(args[args.index("--output") + 1])
            target = next(output.glob("gaps-*.json"))
            value = json.loads(target.read_text())
            value["modes"] = {"facts": "native-fabric-iq-synthetic-fixtures"}
            value["fabricRetrieval"] = [{"tool": fabric_settings.toolName}]
            target.write_text(json.dumps(value))
        return result

    monkeypatch.setattr(bootstrap, "command", command)
    monkeypatch.setattr(bootstrap, "ensure_connection", lambda *args, **kwargs: {})
    state = bootstrap.complete(
        "target-group", "portal-deployment", fabric=fabric_settings, seed_and_verify=True
    )
    verification = [args for _, args in calls if args[:3] == ("-m", "agents", "gaps")]
    assert len(verification) == 2
    assert all(args[args.index("--program") + 1] == "CUST-A-5G-ROLLOUT" for args in verification)
    completed = json.loads((state / "completion.json").read_text())
    assert completed["facts"] == "native-fabric-iq-synthetic-fixtures"
    assert completed["fabricAuthorization"] == "foundry-project-managed-identity"
    assert completed["invocationAuthorization"] == "signed-in-user"
    assert completed["unattendedVerification"] == "not-run"


@pytest.mark.parametrize("failure", ["stub", "no-receipt", "wrong-tool"])
def test_native_completion_does_not_accept_stub_verification(fabric_settings, failure):
    value = receipt(bootstrap.SITES[0])
    value["modes"] = {
        "facts": "stub" if failure == "stub" else "native-fabric-iq-synthetic-fixtures"
    }
    if failure != "no-receipt":
        value["fabricRetrieval"] = [
            {"tool": "wrong-tool" if failure == "wrong-tool" else fabric_settings.toolName}
        ]
    with pytest.raises(ValueError, match="actual Fabric retrieval"):
        bootstrap.verify_result(value, bootstrap.SITES[0], fabric=fabric_settings)


def test_missing_or_conflicting_facts_selection_fails_before_azure(monkeypatch, fabric_settings):
    monkeypatch.setattr(bootstrap, "command", lambda *args: pytest.fail("Unexpected Azure call"))
    for options in (
        {},
        {"facts_mode": "unknown"},
        {"facts_mode": "stub", "fabric": fabric_settings},
        {"facts_mode": "stub", "create_fabric_connection": True},
    ):
        with pytest.raises(ValueError):
            bootstrap.complete("target-group", "deployment", **options)


@pytest.mark.parametrize("interactive", [False, True])
def test_bootstrap_prompts_only_interactive_operators(monkeypatch, fabric_settings, interactive):
    monkeypatch.setattr(
        sys, "argv", ["bootstrap", "--resource-group", "target-group", "--deployment", "portal"]
    )
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: interactive))
    values = iter(fabric_settings.model_dump().values())
    monkeypatch.setattr("builtins.input", lambda label: next(values))
    calls = []
    monkeypatch.setattr(bootstrap, "complete", lambda *args, **kwargs: calls.append(kwargs))
    if interactive:
        bootstrap.main()
        assert calls[0]["fabric"] == fabric_settings
        assert calls[0]["facts_mode"] == "fabric"
    else:
        with pytest.raises(SystemExit) as error:
            bootstrap.main()
        assert error.value.code == 2
        assert not calls


def test_noninteractive_bootstrap_accepts_a_settings_file_without_prompting(
    monkeypatch, tmp_path, fabric_settings
):
    config = tmp_path / "fabric.json"
    config.write_text(fabric_settings.model_dump_json(), encoding="utf-8-sig")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "bootstrap",
            "--resource-group",
            "target-group",
            "--deployment",
            "portal",
            "--fabric-config",
            str(config),
            "--create-fabric-connection",
        ],
    )
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: False))
    monkeypatch.setattr("builtins.input", lambda label: pytest.fail("Unexpected prompt"))
    calls = []
    monkeypatch.setattr(bootstrap, "complete", lambda *args, **kwargs: calls.append(kwargs))
    bootstrap.main()
    assert calls[0]["fabric"] == fabric_settings
    assert calls[0]["create_fabric_connection"]


def test_fabric_connection_reuses_a_matching_target_without_mutation(infra_values, fabric_settings):
    requests = []

    def handle(request):
        requests.append(request)
        expected = fabric_connection.connection_properties(fabric_settings)
        assert expected["authType"] == "ProjectManagedIdentity"
        assert expected["audience"] == "https://api.fabric.microsoft.com"
        assert "scopes" not in expected
        assert request.url.host == "management.azure.com"
        assert request.url.path == (
            infra_values["projectResourceId"] + "/connections/" + fabric_settings.connectionName
        )
        return httpx.Response(
            200, json={"properties": fabric_connection.connection_properties(fabric_settings)}
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        result = fabric_connection.reconcile_connection(
            client, infra_values["projectResourceId"], fabric_settings
        )
    assert [request.method for request in requests] == ["GET"]
    assert result["definitionsVerified"] and not result["created"]
    assert result["authType"] == "ProjectManagedIdentity"
    assert result["unattendedVerification"] == "not-run"


def test_fabric_connection_does_not_silently_reuse_delegated_auth(infra_values, fabric_settings):
    requests = []

    def handle(request):
        requests.append(request)
        properties = fabric_connection.connection_properties(fabric_settings)
        properties["authType"] = "UserEntraToken"
        return httpx.Response(200, json={"properties": properties})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ValueError, match="different target or authentication"):
            fabric_connection.reconcile_connection(
                client, infra_values["projectResourceId"], fabric_settings, create=True
            )
    assert [request.method for request in requests] == ["GET"]


@pytest.mark.parametrize("status", [403, 404, 500])
def test_fabric_connection_errors_do_not_create_or_overwrite(infra_values, fabric_settings, status):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(status, json={})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises((ValueError, httpx.HTTPStatusError)):
            fabric_connection.reconcile_connection(
                client, infra_values["projectResourceId"], fabric_settings, create=status != 404
            )
    assert [request.method for request in requests] == ["GET"]


@pytest.mark.parametrize("field", ["target", "authType", "audience", "scopes", "category"])
def test_fabric_connection_rejects_a_mismatching_existing_connection(
    infra_values, fabric_settings, field
):
    requests = []

    def handle(request):
        requests.append(request)
        properties = fabric_connection.connection_properties(fabric_settings)
        properties[field] = "different"
        return httpx.Response(200, json={"properties": properties})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ValueError, match="different target or authentication"):
            fabric_connection.reconcile_connection(
                client, infra_values["projectResourceId"], fabric_settings, create=True
            )
    assert [request.method for request in requests] == ["GET"]


@pytest.mark.parametrize("readback_matches", [False, True])
def test_fabric_connection_creation_requires_explicit_opt_in_and_matching_readback(
    infra_values, fabric_settings, readback_matches
):
    requests = []

    def handle(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(404, json={})
        if request.method == "PUT":
            assert request.headers["If-None-Match"] == "*"
            assert json.loads(request.content)["properties"] == (
                fabric_connection.connection_properties(fabric_settings)
            )
            return httpx.Response(200, json={})
        properties = fabric_connection.connection_properties(fabric_settings)
        if not readback_matches:
            properties["target"] += "/different"
        return httpx.Response(200, json={"properties": properties})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        if readback_matches:
            result = fabric_connection.reconcile_connection(
                client, infra_values["projectResourceId"], fabric_settings, create=True
            )
            assert result["created"]
        else:
            with pytest.raises(ValueError, match="different target"):
                fabric_connection.reconcile_connection(
                    client, infra_values["projectResourceId"], fabric_settings, create=True
                )
    assert [request.method for request in requests] == ["GET", "PUT", "GET"]


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
    assert required == {
        "foundryName",
        "flowApiClientId",
        "fabricDispatchPrincipalId",
        "eventstreamNamespace",
        "eventstreamEntity",
    }
    resource_types = []

    def inspect(node):
        if isinstance(node, dict):
            resource_types.append(node.get("type"))
            assert "templateLink" not in node
            if node.get("type") == "Microsoft.Resources/deployments":
                if "resourceGroup" in node or "subscriptionId" in node:
                    assert node["name"] == "flow-project-access"
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
    assert "Microsoft.Web/sites" in resource_types
    flow = next(
        resource
        for resource in template["resources"]
        if resource.get("name") == "close-out-playground-flow"
    )
    assert flow["properties"]["parameters"]["enableBlobValidation"]["value"] is False
    assert flow["properties"]["parameters"]["enableCoordinatorDispatch"]["value"] is False

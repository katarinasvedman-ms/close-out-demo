"""Narrow bindings on selected existing Fabric items; no item creation or inference."""

import ast
import asyncio
import base64
import copy
import json
import re
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import quote
from uuid import UUID

import httpx

from agents.definitions import FabricSettings
from agents.fabric_flow_activator import FIELDS, dispatch_pipeline_definition, source_query
from agents.installer_state import Blocked, atomic_json, az, confirm, digest, select, text

BASE = "https://api.fabric.microsoft.com/v1"


class Fabric:
    def __init__(self, credential):
        self.credential = credential
        self.client = httpx.Client(timeout=180, follow_redirects=False)

    def close(self):
        self.client.close()

    def request(self, method, path, body=None):
        if not path.startswith("/") or "://" in path or ".." in path:
            raise ValueError("An exact Fabric API path is required")
        response = self.client.request(
            method,
            BASE + path,
            json=body,
            headers={
                "Authorization": "Bearer "
                + self.credential.get_token("https://api.fabric.microsoft.com/.default").token
            },
        )
        if response.is_error:
            try:
                error_code = response.json().get("errorCode")
            except (ValueError, AttributeError):
                error_code = None
            if error_code == "CapacityNotActive":
                owner = "Fabric capacity administrator"
                action = (
                    "The selected workspace's Fabric capacity is inactive (CapacityNotActive). "
                    "In Azure portal > Microsoft Fabric, select the capacity assigned to this "
                    "workspace and choose Resume. Resuming restarts billing. Wait until it is "
                    "active, then rerun. No permission change is requested."
                )
            elif response.status_code == 401:
                owner = "Signed-in user"
                action = "Sign in to Azure again with the intended account, then rerun (HTTP 401)."
            elif response.status_code == 403:
                owner = "Fabric owner"
                action = (
                    f"Allow the signed-in user to perform {method} on this selected resource "
                    "(HTTP 403). No alternate credential was used."
                )
            elif response.status_code == 404:
                owner = "Fabric owner"
                action = (
                    "Fabric could not find or expose the selected resource (HTTP 404). "
                    "Check that it still exists in the selected workspace and is accessible "
                    "to this user. This response alone does not establish missing permissions."
                )
            elif response.status_code == 429:
                owner = "Signed-in user"
                action = "Fabric is rate limiting requests (HTTP 429). Wait, then rerun."
            else:
                owner = "Setup maintainer"
                action = (
                    f"Inspect the failed Fabric {method} operation (HTTP {response.status_code}) "
                    "and service availability. Missing permissions have not been established."
                )
            raise Blocked(owner, action, path)
        if response.status_code == 202:
            operation = str(UUID(response.headers["x-ms-operation-id"]))
            for _ in range(90):
                time.sleep(2)
                state = self.request("GET", f"/operations/{operation}")
                if state["status"] == "Succeeded":
                    return (
                        self.request("GET", f"/operations/{operation}/result")
                        if path.split("?", 1)[0].endswith("getDefinition")
                        else {}
                    )
                if state["status"] in {"Failed", "Cancelled"}:
                    raise Blocked("Fabric owner", "Resolve the failed item operation.", path)
            raise Blocked("Fabric owner", "Wait for the pending item operation, then rerun.", path)
        return response.json() if response.content else {}

    def rows(self, path):
        result, token, seen = [], None, set()
        for _ in range(100):
            page = self.request(
                "GET", path + ("?continuationToken=" + quote(token, safe="") if token else "")
            )
            result.extend(page["value"])
            token = page.get("continuationToken")
            if not token:
                return result
            if token in seen:
                break
            seen.add(token)
        raise Blocked("Fabric owner", "The item listing could not be completed safely.", path)

    def item_path(self, profile, key):
        return f"/workspaces/{UUID(profile.fabric['workspaceId'])}/items/{UUID(profile.items[key])}"

    def definition(self, profile, key):
        path = self.item_path(profile, key)
        if key == "notebookId":
            path = path.replace("/items/", "/notebooks/")
        definition = self.request(
            "POST", path + "/getDefinition" + ("?format=ipynb" if key == "notebookId" else ""), {}
        )["definition"]
        if key == "notebookId":
            definition["format"] = "ipynb"
        return definition

    def update(self, progress, key, before, after):
        if before == after:
            return
        profile = progress.profile
        progress.approve(
            "fabric-" + key,
            {"id": profile.items[key], "before": digest(before), "after": digest(after)},
            f"Update only the selected existing {key} ({profile.items[key]})? "
            "It must be dedicated to this demo, not serving another environment. "
            "A local definition backup will be kept.",
        )
        if digest(self.definition(profile, key)) != digest(before):
            raise Blocked(
                "Fabric owner", "The item changed during setup; inspect it and rerun.", key
            )
        backup = (
            progress.directory
            / "backups"
            / (f"{profile.items[key]}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')}.json")
        )
        atomic_json(backup, {"definition": before})
        path = self.item_path(profile, key)
        if key == "notebookId":
            path = path.replace("/items/", "/notebooks/")
        self.request("POST", path + "/updateDefinition", {"definition": after})


def decode_part(definition, filename):
    parts = [part for part in definition["parts"] if part["path"].endswith(filename)]
    if len(parts) != 1 or parts[0].get("payloadType") != "InlineBase64":
        raise Blocked("Fabric owner", f"Use a compatible existing {filename} definition.")
    return parts[0], json.loads(base64.b64decode(parts[0]["payload"]))


def encode_part(part, value):
    part["payload"] = base64.b64encode(json.dumps(value).encode()).decode()


async def tool_names(fabric, settings):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    token = fabric.credential.get_token("https://api.fabric.microsoft.com/.default").token
    async with (
        streamablehttp_client(settings.mcp_url(), headers={"Authorization": "Bearer " + token}) as (
            reader,
            writer,
            _,
        ),
        ClientSession(reader, writer) as session,
    ):
        await session.initialize()
        tools = await session.list_tools()
        return [row.name for row in tools.tools if row.name.startswith("DataAgent_")]


def data_agent_databases(fabric, workspace_id, agent_id):
    definition = fabric.request(
        "POST", f"/workspaces/{workspace_id}/items/{agent_id}/getDefinition", {}
    )["definition"]
    stages = {"draft": set(), "published": set()}
    for part in definition["parts"]:
        for stage, databases in stages.items():
            if part["path"].startswith(f"Files/Config/{stage}/") and part["path"].endswith(
                "/datasource.json"
            ):
                source = json.loads(base64.b64decode(part["payload"]))
                if source.get("type") == "kusto" and source.get("workspaceId") == workspace_id:
                    databases.add(str(UUID(source["artifactId"])))
    return stages["draft"] & stages["published"]


def can_correct_database(progress):
    profile = progress.profile
    return (
        profile is not None
        and progress.data.get("phase")
        in {"Selecting existing Fabric items", "Reading existing Fabric configuration"}
        and not set(progress.data) - {"phase", "updatedAt", "azureTarget", "fabricTarget"}
        and not any(
            (
                profile.apiClientId,
                profile.apiObjectId,
                profile.dispatchPrincipalId,
                profile.dispatchAppId,
                profile.callbackConnectionId,
            )
        )
    )


def correct_database(progress, fabric, databases, *, allow_correction):
    profile = progress.profile
    settings = FabricSettings.model_validate(profile.fabric)
    workspace = settings.workspaceId
    items = fabric.rows(f"/workspaces/{workspace}/items")
    choices = [
        row
        for row in items
        if row["type"] in {"KQLDatabase", "KustoDatabase"} and row["id"] in databases
    ]
    current = next(
        (row["displayName"] for row in items if row["id"] == settings.databaseId),
        settings.databaseId,
    )
    compatible = ", ".join(row["displayName"] for row in choices) or "no shared database"
    if not allow_correction or not choices:
        raise Blocked(
            "Fabric owner / target owner",
            f"Saved KQL database {current} does not match the Data Agent's draft/published "
            f"sources ({compatible}). Do not add tables or replace the agent to fix this "
            "selection mismatch. Setup cannot safely redirect an already-started target; "
            "review its existing bindings with the owner.",
            settings.dataAgentId,
        )
    chosen = (
        choices[0]
        if len(choices) == 1
        else select("KQL database used by the selected Data Agent", choices)
    )
    if not confirm(
        f"Saved KQL database {current} does not match the selected Data Agent. "
        f"Use {chosen['displayName']} ({chosen['id']}) instead? Only local installer selections "
        "will change; no Fabric item will be modified. Azure selections will be retained."
    ):
        raise Blocked(
            "Operator",
            f"Rerun and confirm database {chosen['displayName']} to correct the saved selection.",
            settings.dataAgentId,
        )
    database = fabric.request("GET", f"/workspaces/{workspace}/kqlDatabases/{chosen['id']}")
    endpoint = database.get("properties", {}).get("queryServiceUri")
    if not endpoint:
        raise Blocked("Fabric owner", "Expose the selected database's Query URI.", chosen["id"])
    updated = FabricSettings.model_validate(
        {**settings.model_dump(), "databaseId": chosen["id"], "kustoEndpoint": endpoint}
    )
    progress.bind(
        "fabricTarget",
        {
            "fabric": profile.fabric,
            "items": profile.items,
            "projectorOwnerId": profile.projectorOwnerId,
        },
    )
    backup = (
        progress.directory
        / "backups"
        / f"database-selection-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')}.json"
    )
    atomic_json(backup, {"profile": profile.model_dump(), "state": progress.data})
    progress.data.pop("fabricTarget", None)
    progress.save()
    profile.fabric = updated.model_dump()
    progress.save_profile()
    return updated


def discover(progress, fabric, *, allow_database_correction=False):
    profile = progress.profile
    if not profile.fabric:
        workspace = select("Fabric workspace", fabric.rows("/workspaces"))
        workspace_id = str(UUID(workspace["id"]))
        items = fabric.rows(f"/workspaces/{workspace_id}/items")
        chosen = {}
        for key, label, kinds in (
            ("dataAgentId", "Published Data Agent", {"DataAgent"}),
            ("databaseId", "Existing KQL database", {"KQLDatabase", "KustoDatabase"}),
            ("eventstreamId", "Existing Eventstream", {"Eventstream"}),
            ("notebookId", "Existing readiness notebook", {"Notebook"}),
            ("pipelineId", "Existing dispatch pipeline", {"DataPipeline"}),
            ("activatorId", "Existing readiness Activator", {"Reflex"}),
        ):
            choices = [x for x in items if x["type"] in kinds]
            if key == "databaseId":
                databases = data_agent_databases(fabric, workspace_id, chosen["dataAgentId"])
                choices = [row for row in choices if row["id"] in databases]
                label = "KQL database used by the selected Data Agent"
                if not choices:
                    raise Blocked(
                        "Fabric owner",
                        "The selected Data Agent needs an existing workspace KQL source shared "
                        "by its draft and published stages. No database or agent was changed.",
                        chosen["dataAgentId"],
                    )
            chosen[key] = str(UUID(select(label, choices)["id"]))
        database = fabric.request(
            "GET", f"/workspaces/{workspace_id}/kqlDatabases/{chosen['databaseId']}"
        )
        endpoint = database.get("properties", {}).get("queryServiceUri")
        if not endpoint:
            endpoint = text("KQL database Query URI (from its Fabric settings)")
        settings = FabricSettings(
            workspaceId=workspace_id,
            dataAgentId=chosen.pop("dataAgentId"),
            databaseId=chosen.pop("databaseId"),
            kustoEndpoint=endpoint,
            connectionName=f"closeout-{profile.environment}",
            toolName="DataAgent_pending",
        )
        profile.fabric = settings.model_dump()
        profile.items = chosen
        progress.save_profile()
    else:
        settings = FabricSettings.model_validate(profile.fabric)
        databases = data_agent_databases(fabric, settings.workspaceId, settings.dataAgentId)
        if settings.databaseId not in databases:
            settings = correct_database(
                progress, fabric, databases, allow_correction=allow_database_correction
            )
    if settings.toolName == "DataAgent_pending":
        try:
            names = asyncio.run(asyncio.wait_for(tool_names(fabric, settings), timeout=60))
        except Exception as error:
            raise Blocked(
                "Fabric owner",
                "Publish the Data Agent and allow this user to list its tools; then rerun.",
                settings.mcp_url(),
            ) from error
        if len(names) != 1:
            raise Blocked(
                "Fabric owner", "Expose exactly one DataAgent_ tool on the selected agent."
            )
        settings = FabricSettings.model_validate({**settings.model_dump(), "toolName": names[0]})
        profile.fabric = settings.model_dump()
        progress.save_profile()
    for key, expected in (
        ("notebookId", {"Notebook"}),
        ("pipelineId", {"DataPipeline"}),
        ("activatorId", {"Reflex"}),
        ("eventstreamId", {"Eventstream"}),
    ):
        item = fabric.request("GET", fabric.item_path(profile, key))
        if item["type"] not in expected:
            raise Blocked(
                "Fabric owner", "Restore the selected item type; no item was overwritten.", key
            )
    if not profile.projectorOwnerId:
        assignments = fabric.rows(f"/workspaces/{profile.fabric['workspaceId']}/roleAssignments")
        users = [row["principal"] for row in assignments if row["principal"]["type"] == "User"]
        if not any(row["id"] == profile.operatorId for row in users):
            users.insert(0, {"id": profile.operatorId, "displayName": "Signed-in user"})
        profile.projectorOwnerId = str(
            UUID(select("User who owns the existing readiness notebook schedule", users)["id"])
        )
        progress.save_profile()


def ingress(progress, fabric):
    profile = progress.profile
    definition = fabric.definition(profile, "eventstreamId")
    _, topology = decode_part(definition, "eventstream.json")
    sources = [row for row in topology["sources"] if row["type"] == "CustomEndpoint"]
    source = select("Existing Eventstream custom endpoint", sources, label="name")
    reachable = {source["name"]}
    nodes = topology.get("streams", []) + topology.get("operators", []) + topology["destinations"]
    for _ in range(len(nodes)):
        reachable.update(
            row["name"]
            for row in nodes
            if any(parent["name"] in reachable for parent in row.get("inputNodes", []))
        )
    if not any(
        row["name"] in reachable
        and row.get("type") == "Eventhouse"
        and row.get("properties", {}).get("workspaceId") == profile.fabric["workspaceId"]
        and row["properties"].get("itemId") == profile.fabric["databaseId"]
        and row["properties"].get("tableName") == "CloseoutFlowEventsV02"
        for row in topology["destinations"]
    ):
        raise Blocked(
            "Fabric owner",
            "Connect this existing endpoint to CloseoutFlowEventsV02 in the selected KQL database. "
            "The installer will not create or change Eventstream topology.",
            profile.items["eventstreamId"],
        )
    source_id = str(UUID(source["id"]))
    value = fabric.request(
        "GET",
        f"/workspaces/{profile.fabric['workspaceId']}/eventstreams/"
        f"{profile.items['eventstreamId']}/sources/{source_id}/connection",
    )
    # This API also returns keys. Only these two nonsecret coordinates leave this function.
    return {
        "eventstreamNamespace": value["fullyQualifiedNamespace"],
        "eventstreamEntity": value["eventHubName"],
        "sourceId": source_id,
    }


def current_ingress(progress, fabric):
    profile = progress.profile
    saved = progress.data["ingress"]
    value = fabric.request(
        "GET",
        f"/workspaces/{profile.fabric['workspaceId']}/eventstreams/"
        f"{profile.items['eventstreamId']}/sources/{saved['sourceId']}/connection",
    )
    if (
        value["fullyQualifiedNamespace"] != saved["eventstreamNamespace"]
        or value["eventHubName"] != saved["eventstreamEntity"]
    ):
        raise Blocked(
            "Fabric owner",
            "The selected Eventstream endpoint changed. Inspect the target before resuming; "
            "the installer will not redirect publication silently.",
            profile.items["eventstreamId"],
        )


def inspect_compatibility(progress, fabric):
    profile = progress.profile
    definition = fabric.request(
        "POST",
        f"/workspaces/{profile.fabric['workspaceId']}/items/"
        f"{profile.fabric['dataAgentId']}/getDefinition",
        {},
    )["definition"]
    stages = set()
    instructions = {}
    for part in definition["parts"]:
        if part["path"].endswith("/stage_config.json"):
            instructions[part["path"].split("/")[2]] = json.loads(
                base64.b64decode(part["payload"])
            ).get("aiInstructions", "")
    for part in definition["parts"]:
        if part["path"].startswith(("Files/Config/draft/", "Files/Config/published/")) and part[
            "path"
        ].endswith("/datasource.json"):
            source = json.loads(base64.b64decode(part["payload"]))
            if (
                source.get("artifactId") == profile.fabric["databaseId"]
                and source.get("workspaceId") == profile.fabric["workspaceId"]
                and any(
                    row.get("display_name") == "CloseoutFlowFactsV02"
                    and row.get("is_selected")
                    and any(
                        child.get("display_name") == "snapshot"
                        and child.get("data_type") == "dynamic"
                        and child.get("is_selected")
                        for child in row.get("children", [])
                    )
                    for row in source.get("elements", [])
                )
                and all(
                    field
                    in source.get("dataSourceInstructions", "")
                    + instructions.get(part["path"].split("/")[2], "")
                    for field in ("runId", "factsRevision", "sourceRef")
                )
            ):
                stages.add(part["path"].split("/")[2])
    if not {"draft", "published"} <= stages:
        raise Blocked(
            "Fabric owner",
            "Expose the existing CloseoutFlowFactsV02 snapshot:dynamic source in both Data Agent "
            "stages, with exact runId/siteId/program/factsRevision lookup instructions. "
            "The installer will not replace your Data Agent or create tables.",
            profile.fabric["dataAgentId"],
        )
    notebook_binding(fabric.definition(profile, "notebookId"), profile, None)
    pipeline_binding(fabric.definition(profile, "pipelineId"), None)
    activator_binding(fabric.definition(profile, "activatorId"), profile, enabled=None)


def notebook_binding(original, profile, storage_url):
    result = copy.deepcopy(original)
    part, notebook = decode_part(result, ".ipynb")
    matches = []
    for cell in notebook["cells"]:
        if cell.get("cell_type") != "code":
            continue
        source = "".join(cell["source"])
        try:
            tree = ast.parse(source)
        except SyntaxError as error:
            raise Blocked(
                "Fabric owner",
                "This notebook uses a custom cell language. Bind its ledger manually; "
                "the installer will not rewrite its code.",
                profile.items["notebookId"],
            ) from error
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "notebook_main"
                and node.args
                and isinstance(node.args[0], ast.Call)
                and isinstance(node.args[0].func, ast.Attribute)
                and isinstance(node.args[0].func.value, ast.Name)
                and node.args[0].func.value.id == "json"
                and node.args[0].func.attr == "loads"
                and len(node.args[0].args) == 1
                and isinstance(node.args[0].args[0], ast.Constant)
            ):
                literal = node.args[0].args[0]
                config = json.loads(literal.value)
                if any(config[key] != profile.fabric[key] for key in ("workspaceId", "databaseId")):
                    raise Blocked(
                        "Fabric owner", "The notebook belongs to another workspace/database."
                    )
                matches.append((cell, source, literal, config))
    if len(matches) != 1:
        raise Blocked(
            "Fabric owner",
            "Bind this notebook to the new ledger using your existing implementation. Automatic "
            "rebinding supports only the existing notebook_main(json.loads(...)) configuration.",
            profile.items["notebookId"],
        )
    cell, source, literal, config = matches[0]
    if storage_url and config.get("storageEndpoint", "").rstrip("/") != storage_url.rstrip("/"):
        config["storageEndpoint"] = storage_url
        lines = source.splitlines(keepends=True)
        start = sum(map(len, lines[: literal.lineno - 1])) + len(
            lines[literal.lineno - 1].encode()[: literal.col_offset].decode()
        )
        end = sum(map(len, lines[: literal.end_lineno - 1])) + len(
            lines[literal.end_lineno - 1].encode()[: literal.end_col_offset].decode()
        )
        source = source[:start] + repr(json.dumps(config)) + source[end:]
        cell["source"] = source.splitlines(keepends=True)
        encode_part(part, notebook)
    return result


def pipeline_binding(original, connection_id):
    result = copy.deepcopy(original)
    part, pipeline = decode_part(result, "pipeline-content.json")
    properties = pipeline["properties"]
    if set(properties.get("parameters", {})) != set(FIELDS):
        raise Blocked("Fabric owner", "Use the existing headless v0.2 dispatch parameter contract.")
    activities = properties.get("activities", [])
    if len(activities) != 1 or activities[0].get("type") != "WebActivity":
        raise Blocked(
            "Fabric owner",
            "Supply an existing dedicated one-Web-activity dispatch pipeline. "
            "Other activities will not be replaced or removed.",
        )
    activity = activities[0]
    props = activity.get("typeProperties", {})
    body = props.get("body", {}).get("value", "")
    if (
        props.get("method") != "POST"
        or "/api/coordinator/triggers/Fabric_readiness_transition/invoke"
        not in props.get("relativeUrl", "")
        or "HeadlessReadiness" not in body
        or "json(string(pipeline().parameters.readinessMissing))" not in body
    ):
        raise Blocked(
            "Fabric owner", "Align the existing callback with headless-readiness.schema.json."
        )
    reference = activity.get("externalReferences", {}).get("connection")
    if not reference:
        raise Blocked(
            "Fabric owner", "Bind an approved existing Web connection to the dispatch activity."
        )
    _, expected = decode_part(dispatch_pipeline_definition(reference), "pipeline-content.json")
    expected_body = expected["properties"]["activities"][0]["typeProperties"]["body"]
    if (
        props["body"] != expected_body
        or {name.lower(): value for name, value in props.get("headers", {}).items()}
        != {"content-type": "application/json"}
        or set(props) - {"method", "relativeUrl", "headers", "body", "turnOffAsync"}
    ):
        raise Blocked(
            "Fabric owner",
            "Use the existing headless-readiness envelope and connection-based authentication. "
            "Custom callback bodies/authentication are not overwritten or backed up.",
        )
    if connection_id:
        activity.setdefault("externalReferences", {})["connection"] = str(UUID(connection_id))
        props["relativeUrl"] = (
            "/api/coordinator/triggers/Fabric_readiness_transition/invoke?api-version=2022-05-01"
        )
        encode_part(part, pipeline)
    return result


def activator_binding(original, profile, *, enabled, start_time=None):
    result = copy.deepcopy(original)
    part, entities = decode_part(result, "ReflexEntities.json")
    sources = [row for row in entities if row["type"] == "kqlSource-v1"]
    actions = [row for row in entities if row["type"] == "fabricItemAction-v1"]
    rules = [row for row in entities if row["payload"].get("definition", {}).get("type") == "Rule"]
    if len(sources) != 1 or len(actions) != 1 or len(rules) != 2:
        raise Blocked(
            "Fabric owner", "Select an existing dedicated two-transition readiness Activator."
        )
    source = sources[0]["payload"]
    if (
        source["eventhouseItem"]["itemId"] != profile.fabric["databaseId"]
        or source["eventhouseItem"]["workspaceId"] != profile.fabric["workspaceId"]
        or "CloseoutFlowFactsV02" not in source["query"]["queryString"]
        or {row["payload"]["name"] for row in rules} != {"becomes-Blocked", "becomes-Ready"}
    ):
        raise Blocked(
            "Fabric owner", "Align this existing Activator with the selected readiness feed."
        )
    for rule in rules:
        instance = json.loads(rule["payload"]["definition"]["instance"])
        rows = [row for step in instance["steps"] for row in step["rows"]]
        conditions = [row for row in rows if row["kind"] == "TextChanges"]
        bindings = [row for row in rows if row["kind"] == "FabricItemInvocation"]
        state = rule["payload"]["name"].removeprefix("becomes-")
        if (
            len(conditions) != 1
            or {row["name"]: row["value"] for row in conditions[0]["arguments"]}
            != {"op": "ChangesTo", "value": state}
            or len(bindings) != 1
        ):
            raise Blocked("Fabric owner", "Use existing BECOMES Blocked/Ready transition rules.")
        arguments = {row["name"]: row for row in bindings[0]["arguments"]}
        if (
            arguments.get("workspaceId", {}).get("value") != profile.fabric["workspaceId"]
            or arguments.get("itemId", {}).get("value") != profile.items["pipelineId"]
        ):
            raise Blocked(
                "Fabric owner",
                "Align each existing readiness rule's pipeline action with the selected dispatch.",
                profile.items["activatorId"],
            )
    action = actions[0]["payload"]
    if (
        action["fabricItem"]["itemId"] != profile.items["pipelineId"]
        or action["fabricItem"]["workspaceId"] != profile.fabric["workspaceId"]
    ):
        raise Blocked(
            "Fabric owner", "Bind the selected Activator to the selected dedicated pipeline first."
        )
    if enabled is not None:
        source["runSettings"]["isStopped"] = not enabled
        for rule in rules:
            rule["payload"]["definition"]["settings"]["shouldRun"] = enabled
            rule["payload"]["definition"]["settings"]["shouldApplyRuleOnUpdate"] = False
        if start_time:
            query = re.sub(
                r"\n\| where timestamp >= datetime\([^)]+\)\s*$",
                "",
                source["query"]["queryString"],
            )
            if query.strip() != source_query():
                raise Blocked(
                    "Fabric owner",
                    "Set a current start watermark on this custom query before enablement. "
                    "The installer will not rewrite a custom source or replay its history.",
                )
            source["query"]["queryString"] = (
                query + f"\n| where timestamp >= datetime({start_time})"
            )
        encode_part(part, entities)
    return result


def choose_callback(progress, fabric, workflow_name):
    profile = progress.profile
    expected = f"https://{workflow_name}.azurewebsites.net;api://{profile.apiClientId}"
    if not profile.callbackConnectionId:
        rows = [
            row
            for row in fabric.rows("/connections")
            if row.get("connectionDetails", {}).get("type") == "WebForPipeline"
            and row["connectionDetails"].get("path") == expected
        ]
        if not rows:
            raise Blocked(
                "Fabric connection owner / Entra administrator",
                "Prepare an approved ServicePrincipal Web connection for this exact host/audience, "
                f"using client ID {profile.dispatchAppId} in tenant {profile.tenantId} "
                f"(service-principal object ID {profile.dispatchPrincipalId}). Keep its secret in "
                "Key Vault/Fabric credential storage. Existing connection URLs cannot be changed "
                "through the supported update API; no connection or credential was created.",
                expected,
            )
        profile.callbackConnectionId = str(UUID(select("Approved callback connection", rows)["id"]))
        progress.save_profile()
    connection = fabric.request("GET", f"/connections/{profile.callbackConnectionId}")
    if (
        connection["connectionDetails"].get("path") != expected
        or connection["connectionDetails"].get("type") != "WebForPipeline"
        or connection["credentialDetails"].get("credentialType") != "ServicePrincipal"
        or connection["credentialDetails"].get("skipTestConnection")
    ):
        raise Blocked(
            "Fabric connection owner", "Restore the exact approved callback binding.", expected
        )
    passwords = (
        az(
            "ad",
            "app",
            "show",
            "--id",
            profile.dispatchAppId,
            "--query",
            "passwordCredentials[].{id:keyId,expires:endDateTime}",
        )
        or []
    ) + (
        az(
            "ad",
            "sp",
            "show",
            "--id",
            profile.dispatchPrincipalId,
            "--query",
            "passwordCredentials[].{id:keyId,expires:endDateTime}",
        )
        or []
    )
    current = [
        row
        for row in passwords
        if datetime.fromisoformat(row["expires"].replace("Z", "+00:00")) > datetime.now(UTC)
    ]
    if not current:
        raise Blocked(
            "Entra administrator / Fabric connection owner",
            "Prepare or rotate an approved unexpired callback credential and update the Fabric "
            "connection's stored copy. No current password metadata is visible for the selected "
            "application/service principal. Never paste the credential into this installer.",
            profile.callbackConnectionId,
        )
    print(
        "Newest visible callback credential expiry: "
        + max(row["expires"] for row in current)
        + ". Its use by the Fabric connection still requires owner confirmation."
    )
    progress.approve(
        "callback-owner-confirmation",
        {
            "connection": profile.callbackConnectionId,
            "principal": profile.dispatchPrincipalId,
            "credentials": current,
        },
        "The connection API does not expose its stored credential identity/expiry. "
        f"Has its owner confirmed it uses {profile.dispatchPrincipalId}, has a current approved "
        "credential, and is dedicated to this target? This is owner confirmation, not a probe.",
    )


def bind_existing(progress, fabric, values):
    for key, transform in (
        (
            "notebookId",
            lambda value: notebook_binding(value, progress.profile, values["flow"]["storageUrl"]),
        ),
        (
            "pipelineId",
            lambda value: pipeline_binding(value, progress.profile.callbackConnectionId),
        ),
    ):
        before = fabric.definition(progress.profile, key)
        fabric.update(progress, key, before, transform(before))
        actual = fabric.definition(progress.profile, key)
        if transform(actual) != actual:
            raise Blocked("Fabric owner", "The requested binding did not persist.", key)


def schedule_change(progress, fabric, *, apply=False):
    profile = progress.profile
    path = fabric.item_path(profile, "notebookId") + "/jobs/RunNotebook/schedules"
    if not progress.data.get("scheduleId"):
        rows = fabric.rows(path)
        for row in rows:
            row["displayName"] = (
                f"{'Enabled' if row['enabled'] else 'Disabled'}, "
                f"ends {row['configuration'].get('endDateTime', 'unknown')}"
            )
        selected = select("Existing notebook schedule", rows)
        progress.data["scheduleId"] = str(UUID(selected["id"]))
        progress.save()
    path += "/" + progress.data["scheduleId"]
    current = fabric.request("GET", path)
    config = current["configuration"]
    if config.get("type") != "Cron" or not isinstance(config.get("interval"), int):
        raise Blocked("Fabric owner", "Use an existing bounded Cron notebook schedule.", path)
    expiry = datetime.fromisoformat(config["endDateTime"].replace("Z", "+00:00"))
    if current["enabled"] and expiry > datetime.now(UTC):
        progress.data["scheduleEndsAt"] = expiry.isoformat()
        progress.save()
        print(
            f"Existing notebook schedule ends at {expiry.isoformat()} (owner-delegated execution)."
        )
        return None
    desired = progress.data.get("scheduleChange")
    if not desired or datetime.fromisoformat(
        desired["configuration"]["endDateTime"].replace("Z", "+00:00")
    ) <= datetime.now(UTC):
        minutes = int(text("Run the existing projector every how many minutes?", "2", r"[1-9]\d?"))
        if minutes > 60:
            raise Blocked("Operator", "Choose a schedule interval between 1 and 60 minutes.")
        desired = {
            "enabled": True,
            "configuration": {
                **config,
                "interval": minutes,
                "startDateTime": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
                "endDateTime": (datetime.now(UTC) + timedelta(hours=8)).isoformat(),
                "localTimeZoneId": "UTC",
            },
        }
        progress.data["scheduleChange"] = desired
        progress.data["scheduleBefore"] = current
        progress.save()
    progress.data["scheduleEndsAt"] = desired["configuration"]["endDateTime"]
    progress.save()
    print(
        "Proposed notebook schedule ends at "
        + desired["configuration"]["endDateTime"]
        + f"; execution owner must remain {profile.projectorOwnerId}."
    )
    if apply:
        before = progress.data["scheduleBefore"]
        if any(current.get(key) != before.get(key) for key in ("enabled", "configuration")):
            raise Blocked(
                "Fabric owner", "The selected schedule changed while setup was paused.", path
            )
        atomic_json(
            progress.directory / "backups" / f"schedule-before-{digest(before)[:12]}.json",
            {key: before[key] for key in ("id", "enabled", "configuration") if key in before},
        )
        fabric.request("PATCH", path, desired)
    return path, desired

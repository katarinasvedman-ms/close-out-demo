"""Reconcile a project-managed-identity Fabric connection in the selected Foundry project."""

import re

import httpx

from agents.definitions import FabricSettings

API_VERSION = "2025-10-01-preview"
AUDIENCE = "https://api.fabric.microsoft.com"


def connection_properties(settings: FabricSettings) -> dict:
    return {
        "category": "RemoteTool",
        "authType": "ProjectManagedIdentity",
        "target": settings.mcp_url(),
        "audience": AUDIENCE,
        "isSharedToAll": True,
        "metadata": {"type": "custom_MCP"},
    }


def validate_connection(properties: dict, settings: FabricSettings) -> None:
    expected = connection_properties(settings)
    if any(
        properties.get(key) != expected[key]
        for key in ("category", "authType", "target", "audience")
    ) or properties.get("scopes") not in (None, []):
        raise ValueError("Existing Fabric connection has a different target or authentication")


def reconcile_connection(
    client: httpx.Client, project_id: str, settings: FabricSettings, *, create: bool = False
) -> dict:
    if not re.fullmatch(
        r"/subscriptions/[a-zA-Z0-9-]+/resourceGroups/[a-zA-Z0-9_.()-]+"
        r"/providers/Microsoft\.CognitiveServices/accounts/[a-zA-Z0-9-]+"
        r"/projects/[a-zA-Z0-9-]+",
        project_id,
        flags=re.IGNORECASE,
    ):
        raise ValueError("An explicit Foundry project ARM resource ID is required")
    connection_id = f"{project_id}/connections/{settings.connectionName}"
    url = f"https://management.azure.com{connection_id}?api-version={API_VERSION}"
    response = client.get(url)
    created = response.status_code == 404
    if created:
        if not create:
            raise ValueError(
                "Fabric connection is absent in the selected Foundry project; "
                "use --create-fabric-connection to authorize creating it"
            )
        response = client.put(
            url,
            json={"properties": connection_properties(settings)},
            headers={"If-None-Match": "*"},
        )
        response.raise_for_status()
        response = client.get(url)
    response.raise_for_status()
    validate_connection(response.json()["properties"], settings)
    return {
        "connectionId": connection_id,
        "target": settings.mcp_url(),
        "authType": "ProjectManagedIdentity",
        "created": created,
        "definitionsVerified": True,
        "retrievalVerification": "not-run",
        "unattendedVerification": "not-run",
    }


def ensure_connection(project_id: str, settings: FabricSettings, *, create: bool = False) -> dict:
    from azure.identity import AzureCliCredential

    with AzureCliCredential() as credential:
        token = credential.get_token("https://management.azure.com/.default")
        with httpx.Client(
            headers={"Authorization": f"Bearer {token.token}"},
            timeout=30,
            follow_redirects=False,
        ) as client:
            return reconcile_connection(client, project_id, settings, create=create)

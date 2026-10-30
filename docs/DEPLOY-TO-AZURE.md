# Fresh headless deployment

## Selected flow and deployment boundaries

The required deployment target is **playground validation agent -> its
trigger_fabric_eventstream OpenAPI publication tool -> existing Fabric readiness
and closeout-headless-v02-dispatch -> direct coordinator Logic App ->
close-out-coordinator -> raw response Blob**.
See [the exact flow and identity matrix](HEADLESS-FLOW.md#selected-playground-to-blob-flow).
The validation publisher Function remains; a coordinator Function is not in this path.

The `infra\fresh` template includes the selected Azure flow
foundation. Its output command publishes prompt/IQ definitions, the validation
OpenAPI tool and locked runtime/workflow packages. Agent definitions are Foundry
data-plane resources: Bicep passes the deployment settings to bootstrap rather
than inventing an ARM agent resource. Source prompts and tool definitions are
hashed, immutable versions are created/reused, and their definitions are read back.
Fabric is reused, never provisioned by ARM. Its grants, readiness/dispatch
bindings, document/evidence preparation and final enablement remain required
[manual steps](MANUAL_STEPS.md#playground-flow-deployment-checklist).
The button loads the published `main` revision; use that same revision for bootstrap.

**All-managed-identity delivery is blocked in the currently selected Fabric
environment:** its `WebForPipeline` and `RestService` capabilities do not list
`WorkspaceIdentity`, and the attempted connection was rejected. The working
callback uses an approved service-principal secret; it is not managed identity.
The existing scheduled Fabric projector uses its owner's delegated tokens, not
workspace MI. The current Azure Files host uses a Storage key held in Key Vault;
host access to that secret uses MI, but Azure Files authentication is not MI.
No new secret fallback, broad workspace access or anonymous callback may be
silently introduced to make a deployment appear complete.
Strict MI-only deployment must stop before provisioning until supported
alternatives are approved; documenting an exception does not approve it.

The selected Azure runtime now uses a direct, stateful coordinator workflow with
managed identity for Foundry and Blob. `infra/flow/main.bicep` declares its agent
settings and results-container grant. Its deployed health/definition read-back
does not prove a new invocation succeeds. The existing public button still
requires separately authorized publication of local changes to `main`.

## Azure foundation in the updated template

The README **Deploy to Azure** button opens a resource-group-scoped ARM template.
Choose your subscription and a new, dedicated resource group, and enter a globally
unique Foundry resource name. No existing Foundry account/project is required.
Also supply the four prepared external-binding parameters:
`flowApiClientId`, `fabricDispatchPrincipalId`, `eventstreamNamespace`,
and `eventstreamEntity`. Their preparation is in the manual checklist.
Resource-group location does not relocate the services:
Foundry, its regional Standard GPT-4o deployment, Blob Storage and
monitoring use Sweden Central; Basic AI Search uses France Central.

The template creates the account/project, model, managed identities, scoped RBAC,
private documents/evidence containers, Search, project connections, Log Analytics,
Application Insights, Logic Apps Standard and the Python publisher Function.
Both workflows start Disabled and the initial agent settings are explicitly
`pending-publication`; bootstrap replaces them with actual published versions
before deploying code. All new resources are in the selected group. There is no hosted
agent, application container, container registry or capability host.
Public endpoints require authentication; evidence/document Blob
anonymous access, shared keys and Foundry/Search local authentication are disabled.
Only the separate host Storage permits shared keys; its connection string is
in Key Vault, not a clear-text app setting.
Documents are shared synthetic operator knowledge, not per-user ACL-protected data.

This deploys the current headless increment, **not** the complete design-document
architecture. Fabric, Power Platform, upload APIs, governed actions,
Content Understanding, packages and Presenter hosting are not included.

## Prerequisites and portal deployment

Use an interactive target-tenant user with resource-creation
and role-assignment permissions in the group (for example Owner). The template
uses the deployer's object ID; no tenant, subscription or principal IDs are copied
from the development environment. Automation must set `operatorPrincipalType` to
`ServicePrincipal` and authenticate consistently for the completion phase.

The subscription needs the relevant providers registered:
`Microsoft.CognitiveServices`, `Microsoft.Search`, `Microsoft.Storage`,
`Microsoft.OperationalInsights` and
`Microsoft.Insights`, `Microsoft.Web`, `Microsoft.KeyVault`, and
`Microsoft.ManagedIdentity`. It also needs regional model quota (default capacity 30),
model/version availability, France Central Search capacity, and permission to use
the current preview IQ/agent APIs. Deployment can fail on quota, policy, naming or
feature availability; it does not switch regions, models or deployment SKUs.
Basic Search, WS1/EP1 plans, model inference and monitoring incur charges.

In the portal, review the template and parameters before deploying. Record the
successful deployment's **name** from the resource group's Deployments blade.
ARM success alone does not upload documents, create an IQ knowledge base or
publish agents. **Publishing definitions is a required second step**, independent
of optional document loading. Copy `publishDefinitionsCommand` from the deployment's
**Outputs** and run it from the repository root, as described below.

The button uses public `main/infra/fresh/azuredeploy.json`. That file is generated,
not manually maintained. A local badge cannot deploy unpublished changes:
the Bicep, generated JSON and application code must be committed and pushed
together before sharing the button. Publishing is a separate repository action.

## Publish agents, knowledge definitions and flow code without documents

Clone the same published revision and run from its root. Prerequisites are Python
3.11/uv, Azure CLI and PowerShell 7 (`pwsh`, for the existing ZIP packager). Authenticate Azure CLI to the intended tenant/subscription
yourself; the publishing command never starts login or changes Azure CLI's selected
subscription. No azd environment or container tooling is needed.

```powershell
uv sync --frozen --extra dev --extra agents --extra flow --python 3.11
az account show --query "{subscription:id,tenant:tenantId,user:user.name}"
# Substitute the selected group and the successful portal deployment name:
uv run --frozen --extra agents --extra flow python -m agents.bootstrap --resource-group "<resource-group>" --deployment "<deployment-name>" --create-fabric-connection --publish-flow
```

The command reads and checks the deployment outputs, configures the explicit
facts binding described below, registers both Foundry
prompt agents, then creates and reads back the shared native Blob IQ source/base
with Low retrieval reasoning. It does **not** generate/read/upload document assets,
wait for ingestion or invoke a model. A failed Blob upload cannot prevent agent
registration. With `--publish-flow`, it packages locked dependencies using
`scripts\package-flow.ps1`, sets the actual returned agent pins, deploys the
publisher and workflow ZIPs, and requires completed deployment responses.
It never invokes an agent or enables either workflow.
Omit that flag for definitions only; older foundation-only deployments remain
supported for definitions, but cannot publish flow code without the new resources.
No hosted agent is deployed and no Storage network or resource-group security
settings are changed.

The validation definition includes the target-local generated OpenAPI schema,
tool name `trigger_fabric_eventstream`, operation `publish_validation_result`,
and native `managed_identity` authentication with the exact API audience.
No API key or user token is embedded. The publication prompt is appended only
when this tool is configured; local validation without a publisher has no phantom
publication instruction. Unlike the earlier manually attached portal tool, this
definition uses inline MI audience configuration, so it needs no separate
publisher project connection. Native Fabric and document IQ still use their
own project connections. The coordinator retains the source
`closeout-fabric.txt` instructions and both native Fabric/document tools; the
publisher tool is never attached to it. Registration read-back accepts the
service's MCP whitelist normalization but rejects changed instructions or tools.

Configuration and receipts are isolated under ignored
`.local\fresh\<subscription-id>\<foundry-name>\`. `deployment.json` records successful
agent/IQ definition publication; `knowledge-definition-receipt.json` records the
source/base read-back. Neither receipt claims documents are indexed or retrieval
works. The base can appear in Foundry before it contains retrievable documents.
`flow-deployment.json` separately records completed ZIP publication and the
Disabled workflow states, not a successful live run. A failed runtime publication
does not turn a successful definition receipt into end-to-end success.
Rerunning `--publish-flow` disables both workflows again; complete the manual
checklist and explicitly re-enable the coordinator afterward.
Existing development configuration and azd environments are not overwritten.
Source synchronization still needs a permitted network/identity path to Storage;
an existing source definition does not prove that path is healthy.

## External Fabric configuration

Native Fabric IQ is the bootstrap default. Fabric itself remains external:
bootstrap does not create, publish, ingest or modify any Fabric item or capacity.
An interactive terminal prompts for six settings when no file is supplied.
Noninteractive runs require a file or explicit `--facts-mode stub`; they fail
before Azure calls if neither is supplied.

Keep the selected target in an ignored file, for example
`.local\fabric-settings.json`:

```json
{
  "workspaceId": "<existing workspace GUID>",
  "dataAgentId": "<published Data Agent GUID>",
  "databaseId": "<KQL database GUID>",
  "kustoEndpoint": "https://<cluster>.kusto.fabric.microsoft.com",
  "connectionName": "<connection name in the selected Foundry project>",
  "toolName": "DataAgent_<actual exposed tool name suffix>"
}
```

The database and Kusto endpoint are for independent fixture verification, not
an alternative agent tool. Native verification currently expects the
provisional contract fixtures described in [FOUNDRY-IQ.md](FOUNDRY-IQ.md#existing-fabric-for-local-verification).
An arbitrary published Data Agent is not automatically scenario-compatible.

```powershell
uv run --frozen --extra agents python -m agents.bootstrap --resource-group "<resource-group>" --deployment "<deployment-name>" --fabric-config .local\fabric-settings.json --create-fabric-connection
```

Omit `--create-fabric-connection` to require an already configured connection.
The connection uses `ProjectManagedIdentity` with audience
`https://api.fabric.microsoft.com`. It is created only when absent and explicitly authorized, and is
read back before registration. A different target, audience, delegated scope or
authentication type is rejected; 403/server errors are not treated as absence.
Creation requests include a conditional-create header; an API rejection is a
failure, not permission to overwrite. No role assignment, new application,
credential or consent grant is made. A reused connection is not rewritten.
`TODO(verify)`: fresh-target conditional creation/concurrent-create behavior is
not yet live-proven; do not run concurrent publishers for the same connection.

Both agents are pinned in the target-local config. The coordinator uses native
Fabric IQ plus document IQ, with no unresolved local facts function. The
`fabric-connection-receipt.json` records definition read-back, not successful
retrieval or unattended authentication. The optional verification requires both
actual Fabric and document retrieval receipts; stub receipts cannot pass it.

Older local fixture verification used a delegated `UserEntraToken` connection.
Bootstrap now requires project-managed-identity auth; it rejects rather than
overwrites older delegated connections. The Fabric owner must grant the selected
Foundry project identity Read on the published Data Agent and database Viewer on
its KQL source. These grants are not created by Azure RBAC or by merely creating
the Foundry connection. Query filters are not authorization.

For deliberately local stub facts, use `--facts-mode stub` instead of a Fabric
config and omit `--create-fabric-connection`. This preserves the old local-client
mode, not playground-executable facts.

## Optional: load documents and verify the demo

Only when document access is available, rerun with the explicit option:

```powershell
uv run --frozen --extra agents python -m agents.bootstrap --resource-group "<resource-group>" --deployment "<deployment-name>" --fabric-config .local\fabric-settings.json --seed-and-verify
```

This reuses the definitions, generates synthetic assets in the target directory,
uploads eight canonical text counterparts and waits for verified Blob/site/version
matches in the IQ-managed index. It then invokes both cases through the **local**
Agent Framework test client.
Model inference and IQ retrieval are live in Foundry. In native mode the agent
executes Fabric IQ using its configured project identity, and the signed-in CLI independently checks
the KQL fixture snapshot. In explicit stub mode the facts function runs
locally against the scoped synthetic MCP stub. The main case must return exactly
the three cited gaps; the Ready fallback must have no gaps/rework.
`completion.json` is written only after both checks pass. If document upload,
indexing or inference fails, the published agents/base and `deployment.json` remain;
there is no successful demo-completion receipt. Definition-only runs likewise do
not produce `completion.json`.

For infrastructure updates redeploy this same resource-group template; for
prompt/IQ updates rerun the publishing command. Unchanged prompt definitions are
reused. Stop on errors, allow for role propagation, resolve any reported policy or
quota issue, then retry. Completed phases may have incurred charges.
Rerunning uses the same target and verifies it again.

With a native binding, select the pinned coordinator version in the playground
and include the exact site, program and scenario. The explicit stub-mode
coordinator cannot execute its local function declaration in the playground.
Neither mode is an unattended Blob/Activator workflow or a hosted agent.

If external security controls disable Storage networking, obtain the target
owner's explicit approval before applying any `SecurityControl=Ignore` exception.
The portal template/publishing command do not change resource-group tags or bypass
policy. See `MANUAL_STEPS.md` for the diagnostic pattern; the development exception
does not authorize an exception in another subscription. Restore the declared
Storage network state through the target's approved process before retrying
document loading. This is not permission to weaken security merely to register
agents.

## Template maintenance and verification limits

The checked-in template uses Bicep **0.39.26**, also pinned in CI:

```powershell
az bicep build --file infra\fresh\main.bicep --outfile infra\fresh\azuredeploy.json
uv run --frozen --extra dev pytest tests\unit\test_bootstrap.py
```

CI recompiles it and checks freshness.
The user verified ARM resource creation in a fresh test group. The original
upload-first completion failed when Storage networking was disabled. The corrected
definition-only flow has focused offline regression coverage; its attempted live
run found that the selected test group no longer existed, so fresh-target
definition publication is not yet verified.
Compilation/offline completion tests do not prove a fresh subscription has quota,
feature access, usable model capacity or the required tenant permissions.
`TODO(verify)`: run definition publishing and optional seed verification in an
explicitly chosen fresh target, and repeat infrastructure deployment to establish fresh-cloud
idempotence. Existing shared IQ/agent live proofs are not a fresh-deployment proof.
No new target environment is created merely by adding these files.

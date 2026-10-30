# Fresh headless deployment

## Guided setup from a downloaded repository

Open PowerShell in the extracted repository and run:

```powershell
.\scripts\deploy-demo.ps1 -Environment demo-review
```

The launcher works from Windows PowerShell and hands off to PowerShell 7. If
PowerShell 7, Azure CLI or uv is missing, it prints the supported installation
instructions and the same rerun command. It does not bypass execution policy,
silently elevate, install a separate test toolchain or require Node/Docker/azd.
uv restores the existing locked agents/flow dependencies and obtains Python 3.11.

The guided setup first selects your subscription and dedicated resource group,
then deploys the Azure/Foundry foundation: Foundry project, model, Search,
document Storage, monitoring and their scoped permissions. This stage uses
`infra\fresh\foundation.bicep` and needs no application client IDs or Fabric
selections. It reports **infrastructure prepared**, not agents ready.

Next it reuses saved application registrations or offers to prepare dedicated
single-tenant registrations without secrets, with explicit approval and directory
permission. Choose **n** when asked whether to use an administrator-supplied
existing application to use automatic preparation. Choose **y** only when an
administrator has supplied a matching existing application client ID.
Azure Owner alone does not grant those directory permissions.

It then selects existing Fabric items by name. KQL database choices are limited to sources
used by both the selected Data Agent's draft and published stages. If an earlier
installer saved a mismatched database, rerunning offers a confirmed local
correction before secured runtime/agent publication, retaining Azure selections and a backup.
It never silently redirects an already-started deployment or edits the Data Agent
to match a wrong selection. It discovers the published Data Agent
tool and Eventstream coordinates. These are needed for the real agent tool
bindings and secured runtime, not for the initial Azure foundation.

The application steps are separate: **Application 1 of 2** identifies the
protected publisher/coordinator APIs; **Application 2 of 2** is Fabric's caller
identity for the coordinator. For an existing deployment, reuse its corresponding
saved application for each role, not the same ID twice. On the optional existing
application path, enter only the Application
(client) ID, without an `api://` prefix or any credential.
The identifier-URI and token-version confirmation configures the newly selected
dedicated API registration; it does not change a different demo's application.
Approve service-principal callback authentication only with target-owner approval.
That confirmation records approval; it creates no password or Fabric connection.

If setup reports `CapacityNotActive`, the existing Fabric capacity is inactive,
not necessarily missing permissions. Its administrator must open Azure portal >
Microsoft Fabric, select the workspace's capacity and choose **Resume**. This
restarts billing. Wait until it is active, then rerun the same installer command.
The installer does not resume Fabric capacity automatically.

If the supported demo policy disables the new Key Vault's public networking,
the installer asks for a separate, explicit policy-owner approval to set
`SecurityControl=Ignore` on the selected dedicated resource group. With approval
and permission it merges the tag, preserves existing tags, and retries the same
deployment once. The operator does not need to use the portal to set it. This exception
can affect other matching controls in that group; deployment/cost approval does
not authorize it. Unrecognized policies, custom tag values or missing permissions
remain owner handoffs. Existing exception tags are preserved, not re-applied.

After application and tool configuration, `infra\fresh\main.bicep` reuses the
same foundation resources and adds the secured runtime; resource names and
identities are retained. The full portal template still requires its API,
callback and Eventstream parameters up front. The installer separately records
foundation and full-runtime progress; a stopped or failed later stage does not
erase the foundation, saved registrations or original full-deployment state.

The installer publishes validation/coordinator prompt agents, a separate
read-only assistant with its own UserEntraToken Fabric connection, and the locked runtime,
loads synthetic documents using the existing indexing wait, registers PHOTO-20
and RFDS, and exports the exact registered playground image/message. It can
back up and narrowly update compatible existing notebook/pipeline/Activator
bindings and an existing bounded schedule, after approval. No Fabric items,
tables, connections, capacity or workspace are created. Custom/incompatible
definitions are left unchanged with an owner handoff.

Azure/Foundry publication and playground preparation complete before any Fabric
permission, Activator, notebook, pipeline or schedule changes. The installer
prints all agent versions and saves the playground handoff, then asks whether
to continue with Fabric setup. Answer **n** to retain the Azure setup without
changing an existing Fabric demo. Azure publication pauses only the selected
Azure target's workflows when needed, never the existing Fabric Activator.
An Azure-only preparation is not a connected or end-to-end verified flow; do
not send the playground message until Fabric setup and enablement are complete.

Its ignored `.local\installer\<environment>\profile.json` stores nonsecret
selections and approvals; `state.json` records setup progress. Existing
`.local\fresh\<subscription-id>\<foundry-name>\` remains the source of agent pins
and deployment receipts. Keep both directories and rerun the same command after
an interruption. An unchanged successful installation does not intentionally
redeploy Bicep, republish ZIPs or disable the coordinator. A needed update pauses
the target's Azure workflows and requires final enablement again. No automatic
rollback or deletion of already provisioned Azure resources is promised.

Some access cannot be prepared until the new identities exist. Missing access
produces the exact target, identity and required owner action in `handoff.txt`.
Direct Data Agent sharing remains an owner confirmation, not an API-proven grant.
The installer prints the actual workspace and agent, the delegated-user
requirement, the item-row **... -> Manage permissions** route and the
**Share** dialog alternative. These instructions are saved before it asks for
confirmation. Typing `y` does not create the grant.
The Fabric owner must provide a target-specific approved service-principal Web
connection for the printed Logic App host and API audience; the supported
connection-update API cannot change an existing connection URL. The installer
does not create passwords, connections or temporary probe pipelines.
Callback credential identity/expiry requires the connection owner's confirmation;
this is not an authentication probe.

Final enablement asks once before enabling the coordinator, the selected
Blocked/Ready Activator rules and any approved schedule renewal. The separate
Blob-validation workflow stays disabled. Schedule renewals are bounded to eight
hours; the existing schedule's end time is printed. Scheduled work and Azure
hosting incur charges. Never approve a shared item serving another deployment.

The final handoff prints the project URL, validation agent name/version,
`PHOTO-20.png` path and `message.json` path. Open the agent's playground, attach
the image, paste the message and send. Retries reuse the same publication ID;
do not manufacture new IDs for an unchanged retry.
For a genuinely new assessment, rerun with `-NewAssessment` and confirm new
run/batch/publication IDs. This retains the existing Azure deployment and
previously exported files; it does not submit the assessment for you.

**Connected runtime acceptance is separate:** focused installer unit tests,
setup safety checks and the document indexing wait do not prove the full flow.
The installer runs no model probe or end-to-end assessment and does not observe
Activator history or claim connected fresh-target runtime acceptance.
The advanced portal/bootstrap reference below remains available to administrators.

## Coordinator authentication handoff

The chosen **KQL-backed Fabric Data Agent does not support MI/SPN authentication**.
The first guided run can publish the agents, knowledge, registered inputs and
disabled baseline runtime; answer **n** at the Fabric continuation checkpoint.
It cannot enable that unsupported baseline. The assistant uses the local Azure
CLI user's delegation; the automatic coordinator needs a separately approved
renewable user credential. Neither is browser-user ACL isolation.

For a fresh target, obtain tenant/target-owner approval for delegated automation.
The authorized user needs Foundry project/model access, Data Agent Read and KQL
Viewer. Entra registration/consent and Key Vault management permissions are
separate from Azure deployment permission. The installer does not silently
create a public client, grant consent or authorize a user for this exception.

From the repository root after that first run:

```powershell
$profile = Get-Content .local\installer\demo-review\profile.json -Raw | ConvertFrom-Json
$client = ".local\installer\demo-review\coordinator-client.json"
uv run --frozen --extra agents python -m agents.coordinator_oauth register `
  --name "<approved-dedicated-public-app-name>" --tenant $profile.tenantId `
  --create --output $client

# Select the flow Key Vault in THIS target group; do not reuse another demo's vault.
az keyvault list --subscription $profile.subscriptionId `
  --resource-group $profile.resourceGroup --query "[].{name:name,id:id}" --output table
$vaultName = "<selected-flow-key-vault-name>"
$vaultId = az keyvault show --subscription $profile.subscriptionId `
  --resource-group $profile.resourceGroup --name $vaultName --query id --output tsv
$secretName = "<new-coordinator-credential-name>"
uv run --frozen --extra agents python -m agents.coordinator_oauth sign-in `
  --config $client --vault-resource-id $vaultId --secret-name $secretName

.\scripts\deploy-demo.ps1 -Environment demo-review `
  -CoordinatorClientConfig $client `
  -CoordinatorCredentialUri "https://$vaultName.vault.azure.net/secrets/$secretName"
```

Use the same environment name throughout. Registration metadata is nonsecret;
the helper uses public-client loopback/PKCE, keeps its token cache in memory
and writes the renewable credential directly to a **new Key Vault secret**.
It refuses an existing secret instead of overwriting one. Do not repeat
sign-in just to inspect a working credential. No password/client secret or
local token file is created.

The resumed installer validates tenant/credential ownership metadata without
reading its value. It shows a role-assignment what-if and asks before granting
the Logic App identity **Key Vault Secrets Officer on only that secret**.
It creates/reuses `close-out-coordinator-fabric-user`, publishes a new immutable
readable coordinator using native Fabric/document IQ, and renders the delegated
workflow into the ZIP. Token refresh/rotation precedes inference; runs are
serialized, acknowledgement asynchronous, and Blob writes remain MI.
The original structured config and validation publisher stay separate.
Read-only assistant publication creates no UI host.

Only the nonsecret client metadata and secret URI are retained in installer
state. Subsequent unchanged reruns can omit the two flags. A different
client/secret is not silently rebound onto saved target state. Tenant policy,
revocation or expiry can require an owner-managed reauthorization; uninterrupted
operation is not guaranteed. Callback app credentials, delegated notebook runs
and the Key Vault-held Azure Files Storage key remain separate dependencies.
Never describe this as all-MI.

Continue with the existing Fabric permission/binding checkpoint only after
approval. These checks and publication are **not fresh-target runtime proof**.
No inference is run by installation. Validate a real registered submission
separately before demonstrating the target. For local startup/configuration,
see [the README](../README.md#local-close-out-workbench).

## Selected flow and deployment boundaries

The required deployment target is **playground validation agent -> its
trigger_fabric_eventstream OpenAPI publication tool -> existing Fabric readiness
and closeout-headless-v02-dispatch -> direct coordinator Logic App ->
close-out-coordinator -> readable brief plus raw response Blob** when the
guided delegated configuration above is supplied. The advanced baseline below
still captures raw JSON only and remains disabled until its authentication gate
is resolved.
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
The advanced bootstrap requires project-managed-identity auth; it rejects rather
than overwrites older delegated connections. **This baseline is unsupported for
the selected KQL-backed Data Agent.** Project-MI Read/Viewer grants do not resolve
that authentication limitation. Use the guided installer's explicit
[delegated coordinator handoff](#coordinator-authentication-handoff), and grant
the authorized users Data Agent Read/KQL Viewer. Those grants are not created by
Azure RBAC or by merely creating a Foundry connection. Query filters are not
authorization.

For deliberately local stub facts, use `--facts-mode stub` instead of a Fabric
config and omit `--create-fabric-connection`. This preserves the old local-client
mode, not playground-executable facts.

## Optional: load documents and verify the demo

The following is the historical local verification interface, not a way to
enable KQL grounding with the unsupported MI connection. Use it only with an
independently supported binding and document access:

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

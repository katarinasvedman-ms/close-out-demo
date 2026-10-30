# Environment-specific setup

## Playground flow deployment checklist

This is the current new-target procedure. Older implementation records below are
history, not instructions to restore the removed coordinator Function hop.
Use the **same published source revision** for Bicep and bootstrap. A public
Deploy button cannot see local/uncommitted changes.

### Before deploying: approvals and existing external inputs

Reason: this template does not provision Entra application registrations or Fabric
item permissions, and the selected Fabric callback connector has no supported
workspace-identity credential. Do not silently substitute credentials.

| Boundary | Current authentication and manual requirement |
|---|---|
| Validation agent -> publisher | Foundry project MI. Bicep allows that exact object ID in Easy Auth and the publisher route; bootstrap attaches the native OpenAPI tool with the API audience. |
| Publisher -> Fabric Eventstream | Function MI. Fabric owner must explicitly approve/grant Contributor in the selected workspace; Azure RBAC does not grant this Fabric role. |
| Logic App -> coordinator/Blob | Logic App MI; project Foundry User and results-container Blob Contributor are in Bicep. No coordinator Function. |
| Coordinator -> Fabric/IQ | Foundry project MI, not Logic App MI. Native project connections are published; Fabric item/database grants below are manual. |
| Fabric dispatch -> Logic App | Current Web/REST connector lacks WorkspaceIdentity; the existing target uses a separately approved service-principal credential. Not MI. |
| Fabric readiness notebook | Existing test schedule executes with its owner's delegated Storage/Kusto tokens. Not workspace MI; its owner needs target-ledger read access. |
| Azure Files host storage | Host Storage key kept in Key Vault; a user-assigned MI reads that secret. The Files connection itself is not MI. Evidence/document storage remains keyless. |

**If strict MI-only authentication is required, stop here.** There is no portal
setting that fixes the unsupported callback or makes the current delegated
projector/Files connection MI. Obtain a supported replacement design or explicit
target-owner exceptions before provisioning. The prior development exceptions
do not authorize a new target. No secret is created by bootstrap.

1. In Microsoft Entra **App registrations -> New registration**, create a
   single-tenant API application, with no redirect URI. Record its application
   (client) ID as `flowApiClientId`. In **Expose an API**, set its identifier URI
   to `api://<client-id>`. In **Manifest**, set
   `api.requestedAccessTokenVersion` to `2`. Do not create a client secret for
   this API application. Easy Auth validates tokens and exact caller object IDs.
   The operator needs Entra application-registration permission separately from
   Azure resource-group Owner; Azure RBAC does not grant directory administration.
2. Record the approved Fabric callback caller's **service-principal object ID**
   as `fabricDispatchPrincipalId`, not its application ID, workspace ID or
   connection ID. For the currently supported connector this requires the
   separately approved credential exception above. Never enter an invented MI
   object ID merely to get ARM to succeed.
3. In the existing Fabric Eventstream, open the selected custom endpoint and copy
   its namespace FQDN and entity/Event Hub name into `eventstreamNamespace` and
   `eventstreamEntity`. Do not copy SAS keys or connection strings. Record the
   existing workspace, published Data Agent, KQL database and actual tool name
   in the six-field Fabric JSON from `DEPLOY-TO-AZURE.md`.
4. Review costs (including WS1/EP1) and the target's policy/network requirements.
   The present host template requires authenticated public connectivity and
   Azure Files host keys. Do not disable policy or add `SecurityControl=Ignore`
   to a new group without its owner's explicit approval.

### Deploy and publish the Azure side

Deploy `infra\fresh\azuredeploy.json` with those inputs, then run its
`publishDefinitionsCommand` output from the matching repository revision.
Prerequisites: Azure CLI logged into the selected tenant/subscription, Python
3.11/uv and PowerShell 7 (`pwsh`).

```powershell
uv sync --frozen --extra agents --extra flow --python 3.11
uv run --frozen --extra agents --extra flow python -m agents.bootstrap --resource-group "<group>" --deployment "<ARM-deployment>" --fabric-config .local\fabric-settings.json --create-fabric-connection --publish-flow
```

Use the target's output/state, never development IDs. Both workflows stay
Disabled. Registration includes `validation.txt` plus
`validation-publication.txt`, its generated publisher OpenAPI definition and MI
audience; the coordinator uses `closeout-fabric.txt` and native Fabric/document IQ.
The API registration is shared by the two protected hosts, not their identities.
Bootstrap creates no Entra application/secret and changes no Fabric item/role.
Its `flow-deployment.json` records ZIP publication only, not a successful run.
Rerunning `--publish-flow` disables both workflows again.

### Fabric owner: permissions, projection and callback

1. From ARM outputs record `projectPrincipalId`, `flow.adapterPrincipalId`,
   `flow.workflowPrincipalId`, host names and `flow.storageUrl`. Grant the
   **Function** identity Contributor in only the approved Fabric workspace.
   This broader-than-item role is required by the tested Eventstream endpoint;
   obtain explicit approval rather than substituting workspace access for reads.
2. Grant the **Foundry project** identity direct Read on the published Data Agent:
   **Manage permissions -> Direct access -> Add user**, select the exact project,
   additional permissions unchecked, email notification off. Grant it Viewer
   on the underlying KQL database. To resolve its application ID read-only:
   `az ad sp show --id "<projectPrincipalId>" --query appId --output tsv`.
   In the KQL database, an authorized owner can grant with
   `.add database ['<database-name>'] viewers ('aadapp=<project-app-id>;<tenant-id>')`
   and verify with `.show database ['<database-name>'] principals`.
   Use `.add`, not `.set`, which would replace other grants.
3. Reuse the existing isolated readiness projector, tables, Data Agent live-snapshot
   guidance and `closeout-headless-v02-dispatch`. Back up definitions before
   changing bindings; do not repoint another deployment's working items.
   The notebook must read registrations from the new `flow.storageUrl` ledger,
   not the old development account. Grant its actual execution owner/identity
   Blob Data Reader on that ledger through the target owner's approved process.
   In the notebook's schedule settings, check enabled state, execution owner and
   a future end time for the approved test window. The old bounded development
   schedule does not make a new target current automatically.
4. Keep the existing callback connection only for the target it actually describes.
   For an explicitly approved service-principal callback, configure a Fabric
   **Web** connection with the new Logic App host as base URL, the API audience
   `api://<flowApiClientId>`, and the approved tenant/client/secret. Keep that
   secret in Key Vault/Fabric credential storage only. Never use Anonymous or
   disable Easy Auth. Without an approved credential, leave dispatch disabled.
5. In the target-specific dispatch pipeline's Web activity, select that connection,
   POST the readiness envelope to
   `/api/coordinator/triggers/Fabric_readiness_transition/invoke?api-version=2022-05-01`,
   and preserve run/case/site/program, transition, facts revision and sourceRef.
   Preserve `json(string(pipeline().parameters.readinessMissing))` for its array.
   Read the actual contract in
   `contracts\messages\headless-readiness.schema.json`; do not reuse an old
   Dataverse callback shape or add a SAS signature.
6. Bind the target Activator rule to this pipeline and its current readiness feed.
   Verify that the Data Agent exposes this run's immutable `CloseoutFlowFactsV02`
   snapshot, not only static fixtures. Activator fires readiness transitions,
   not another observation of an unchanged Blocked value. Do not treat a manual
   pipeline retry as proof that Activator dispatched automatically.

Read-only checks, using actual outputs and approved callback binding:

```powershell
az functionapp function list --resource-group "<group>" --name "<flow.adapterName>" --query "[].name"
az webapp config appsettings list --resource-group "<group>" --name "<flow.workflowName>" --query "[?starts_with(name, 'FLOW_COORDINATOR_') || starts_with(name, 'Workflows.')].{name:name,value:value}"
.\scripts\verify-flow-callback.ps1 -WorkspaceId "<workspace-id>" -ConnectionId "<approved-connection-id>" -ExpectedPrincipalId "<fabricDispatchPrincipalId>" -WorkflowResourceId "<Logic-App-ARM-ID>" -ExpectedAudience "api://<flowApiClientId>" -OutputPath .local\callback-check.json
```

The callback script's connection mode verifies the supported **ServicePrincipal**
configuration, not MI. Without connection arguments it inventories workspace
identity capabilities and fails explicitly when unsupported. Also inspect the
Data Agent Direct access list, database principals, native connection's
`ProjectManagedIdentity` audience, and the projector's successful current jobs.
No configuration check proves inference or Blob capture.

### Documents, playground evidence and final enablement

Load synthetic document knowledge separately. Definition publication creates
the IQ source/base, not indexed documents:

```powershell
uv run --frozen python -m shared.local_demo
uv run --frozen --extra agents python -m agents.knowledge_setup --infra "<target-state>\infra.json" --site SE-STO-0042 --site SE-GOT-0017 --assets .local\demo --output "<target-state>\knowledge.json"
uv run --frozen --extra flow python -m agents.flow --storage-url "<flow.storageUrl>" upload --site SE-STO-0042 --evidence PHOTO-20 --assets .local\demo
```

The uploader registers and pins PHOTO-20 plus its required RFDS; record the
returned source run/batch UUIDs. With Blob validation Disabled, uploading does
not start a second assessment. Export the exact registered packet and bytes for
the playground (replace the three environment values first):

```powershell
$env:FLOW_STORAGE_URL = "<flow.storageUrl>"
$env:SOURCE_RUN_ID = "<uploaded runId>"
$env:SOURCE_BATCH_ID = "<uploaded batchId>"
@'
import json, os
from pathlib import Path
from uuid import uuid4
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
from agents.blob_sources import BlobSources, azure_downloader
run, batch, publication = os.environ["SOURCE_RUN_ID"], os.environ["SOURCE_BATCH_ID"], str(uuid4())
output = Path(".local") / "playground" / publication
output.mkdir(parents=True)
with AzureCliCredential() as credential, BlobServiceClient(os.environ["FLOW_STORAGE_URL"], credential=credential) as service:
    manifest = json.loads(service.get_blob_client("ledger", f"registrations/{run}/{batch}/manifest.json").download_blob().readall())
    inputs = service.get_container_client("inputs")
    sources = BlobSources(manifest, Path("contracts"), inputs.url, azure_downloader(inputs))
    packets, attachments = sources.validation_inputs(["PHOTO-20"])
    for identifier, data, media_type, receipt in attachments:
        (output / f"{identifier}.png").write_bytes(data)
    context = {"sourceRunId": run, "sourceBatchId": batch, "publicationId": publication}
    (output / "message.json").write_text(json.dumps({"evidence": packets, "publicationContext": context}, indent=2), encoding="utf-8")
print(output)
'@ | uv run --frozen --extra flow python -
```

Prepare the playground without submitting yet: select the **validation version in the target `config.json`**,
attach that exported PHOTO-20 PNG, and paste `message.json`; its packet includes
the registered RFDS text/checklist. Do not copy another target's publication IDs.
Retry unchanged input with the same publication ID; a new assessment needs a
fresh publication UUID. A successful tool call confirms publication only.

After all prerequisites are checked, explicitly enable only the coordinator:

```powershell
az webapp config appsettings set --resource-group "<group>" --name "<flow.workflowName>" --settings "Workflows.coordinator.FlowState=Enabled" "Workflows.validation.FlowState=Disabled" --output none
```

Now submit the prepared playground message and observe the actual tool call.
The expected result is a new Blob in private `results`:
`coordinator/<publicationId>/<caseId>/<readiness>/<transitionId>/<workflowRunId>.json`.
It contains the raw completed Foundry response, not an approved/validated package.
Allow for actual Fabric projection, Activator and background model processing.
List it read-only with
`az storage blob list --account-name "<flow-storage-name>" --container-name results --prefix "coordinator/<publicationId>/" --auth-mode login --query "[].name"`.
Only correlate actual publication, current Fabric revision, automatic dispatch,
Logic App execution and this new Blob before declaring end-to-end success.

## Validation publisher OpenAPI attachment

Manual fallback for an existing target. The fresh bootstrap now attaches this
tool reproducibly; do not attach a duplicate after publication. This procedure
is for the earlier operator-managed agent or recovery after a failed publication.

1. Deploy the flow adapter and set `publisherPrincipalId` to the selected Foundry
   project's managed-identity object ID. Preserve mandatory Easy Auth and the
   separate Logic Apps-only inference allowlist.
2. Generate the target-specific OpenAPI file using the command in
   `HEADLESS-FLOW.md`.
3. In the validation agent, add an OpenAPI tool, select **Managed identity**,
   create a connection with the Function host and its existing API audience,
   and paste the generated schema. Do not select `/api/validation-batch`:
   it invokes the agent and is not the publication tool.
4. Verify route discovery with `az functionapp function list`, and confirm an
   anonymous POST to `/api/publish-validation-result` returns 401. Submit an
   assessment using its original registration IDs and a fresh publication UUID;
   the response must contain published event IDs, not a readiness claim.

## Synthetic document Storage security-control exception

The development environment's external security controls disabled public network
access on the newly created knowledge Storage account. Upload returned
`AuthorizationFailure` despite the correct container-scoped Blob Data Contributor
role. The account had `publicNetworkAccess=Disabled`. The user explicitly
authorized the `SecurityControl=Ignore` resource-group exception if this occurred.

Apply only to the dedicated knowledge resource group, preserving other tags:

```powershell
az group update --name "<knowledge resource group>" --set tags.SecurityControl=Ignore
```

Re-run the knowledge Bicep deployment from `FOUNDRY-IQ.md` with the additional
parameter `securityControlIgnore=true`. This preserves the exception on later
deployments and restores the explicitly declared authenticated public endpoint.
Blob containers remain private; shared keys and anonymous access remain disabled.
Do not apply this exception to unrelated groups or infer approval for production.

Verify network state and run the actual upload/index verification script:

```powershell
az storage account show --ids "<storage ARM ID>" --query "{network:publicNetworkAccess,anonymous:allowBlobPublicAccess,sharedKeys:allowSharedKeyAccess}"
uv run --frozen --extra agents python -m agents.knowledge_setup --infra .local\foundry\knowledge-infra.json --site SE-STO-0042 --site SE-GOT-0017
```

Expected state is `Enabled`, `false`, `false`, followed by successful authenticated
uploads and verified canonical artifact/site/version text in the shared native
Blob source's generated index.

## Historical delegated external Fabric test connection

Earlier local-client verification used user delegation. New-target bootstrap
instead requires Project Managed Identity, as documented above.
An existing Fabric workspace/Data Agent is used for testing; it is
not provisioned or added to the fresh Azure deployment. User delegation is a
separate project connection because document IQ uses a different managed identity.
No stored access token, application secret or automatic consent grant is needed.
The signed-in user must already have project and Fabric data access.

For a fresh deployment, `agents.bootstrap --fabric-config <settings-file>`
automates the matching-connection check. Add `--create-fabric-connection` only
to authorize creation of an absent connection in that selected Foundry project.
An interactive bootstrap can prompt for settings instead. See
[deployment configuration](DEPLOY-TO-AZURE.md#external-fabric-configuration).
The manual equivalent below is retained for existing-project operators; it is
not an unattended runtime-authentication solution.

1. Select the authorized existing target and write the ignored native configuration
   described in [FOUNDRY-IQ.md](FOUNDRY-IQ.md#existing-fabric-for-local-verification).
   Confirm the actual exposed tool name, database and published agent.
2. Set `$projectId` to the verified existing Foundry project ARM ID. Inspect any
   existing connection at the URL below using `az rest --method get --url $url`.
   A first-time connection returns 404. Stop on other errors or an existing
   connection whose target/authentication differs; never overwrite it.
3. Generate the connection definition and PUT only the absent/matching named
   connection. This changes a Foundry project connection, not Fabric resources:

```powershell
$fabric = Get-Content .local\foundry\fabric-alignment\native.json -Raw | ConvertFrom-Json
$target = "https://api.fabric.microsoft.com/v1/mcp/workspaces/$($fabric.workspaceId)/dataagents/$($fabric.dataAgentId)/agent"
$url = "https://management.azure.com$projectId/connections/$($fabric.connectionName)?api-version=2025-10-01-preview"
$audience = "https://analysis.windows.net/powerbi/api"
$body = @{ properties = @{
    category = "RemoteTool"; authType = "UserEntraToken"; target = $target
    audience = $audience; scopes = @("$audience/DataAgent.Execute.All")
    isSharedToAll = $true
    metadata = @{ type = "generic_mcp"; audience = $audience }
} }
$body | ConvertTo-Json -Depth 6 | Set-Content .local\foundry\fabric-alignment\connection.json
az rest --method put --url $url --body "@.local\foundry\fabric-alignment\connection.json"
az rest --method get --url $url --query "properties.{target:target,authType:authType,audience:audience,scopes:scopes}"
```

Set `$url` as shown before the inspection in step 2. A project-shared connection
does not grant every user Fabric access: the downstream tool uses each caller's
delegated permissions. This is still an operator-only fixture demo, not a proof
of per-caller isolation. No roles, consent or network policies are changed here.
If the native call returns `CONSENT_REQUIRED`, follow the tenant administrator's
delegated-consent process for `DataAgent.Execute.All`; do not bypass it.

Register with `--fabric-config` and verify all three actual coordinator calls
from [FOUNDRY-IQ.md](FOUNDRY-IQ.md#existing-fabric-for-local-verification).
Successful saved receipts must contain both native `fabricRetrieval` and
`knowledgeRetrieval`, the exact current fixture `sourceRef`, RFDS/version
citations where relevant, and the expected gap sets. Merely creating the
connection or seeing an MCP tool declaration does not verify authentication.

## Headless flow preflight and managed-identity invocation

Run the read-only inventory/definition backup command in
[HEADLESS-FLOW.md](HEADLESS-FLOW.md#repeatable-read-only-preflight) before changing
the external test flow. The inspected existing Activator had no entities/rules;
the existing Eventstream and pipeline use the older flow and must not be
silently treated as the new contract/version-aware path.

Reuse the already working Fabric IQ tool and connection. Verify the direct
Logic Apps managed-identity call and actual tool execution as described in
[HEADLESS-FLOW.md](HEADLESS-FLOW.md#existing-connection-and-managed-identity-verification).
No authentication failure has been demonstrated for that call. Do not request
new credentials or add a local runner based solely on the earlier tests having
used a signed-in operator. New hosting/access still requires the appropriate
target approval. Resume of the existing assigned capacity was explicitly
authorized; it does not authorize scaling, workspace moves or a new capacity.

## Optional headless foundation and external plumbing

The optional `infra\flow\main.bicep` is not part of portal/bootstrap deployment.
The user authorized the selected `rg-close-out-demo-flow` target in Sweden Central.
The repaired deployment created WS1/EP1 hosts, two Storage accounts, monitoring,
Key Vault and a user-assigned host-secret identity. Both ZIPs are deployed.
These resources incur costs while remaining integration is blocked. Evidence
Storage disables keys and anonymous access. The host-storage secret supports
the current host/Azure Files setup; it is not an Eventstream credential.

**Resolved hosting gate:** the user explicitly applied `SecurityControl=Ignore`
to `rg-close-out-demo-flow` only. The repaired template restores approved public
networking, explicitly permits shared keys only for host Storage, and grants the
pre-created user-assigned identity Key Vault Secrets User before host creation.
Both hosts use `keyVaultReferenceIdentity` for their host-secret references;
runtime calls retain system identity. Never extend the exception to another group.

**Resolved publication gate:** a real Blob-triggered managed-identity Foundry
validation completed but initially failed Eventstream Send authorization.
The user explicitly approved the documented workspace Contributor grant for
the sending Function system identity. Read-back confirmed the grant; a marker
metadata-only retry published the seven cached events without repeating
inference. Eventhouse ingestion and the result Blob were verified. Do not
silently enable tenant settings, switch to SAS or grant other identities.
Native coordinator IQ remains untested, not an observed
failure. Validation version 4 corrects the observed azimuth-quality failure:
a fresh actual Blob batch rejected both originals and accepted RFDS, with
seven published events. Historical version-3 results remain unchanged.

**Original callback capability gate:** the selected tenant's `WebForPipeline`
and REST connection types do not advertise WorkspaceIdentity authentication.
Workspace GET also does not return the existing identity's principal. This is
separate from Logic Apps-to-Foundry managed identity and native Fabric IQ.
Do not silently substitute an anonymous connection or a client secret.
An owner must confirm a supported dispatch-authentication choice and exact
caller before enabling the callback. Verify the current tenant capabilities:

```powershell
.\scripts\verify-flow-callback.ps1 -WorkspaceId <selected-workspace-guid> -OutputPath .local\flow-deployment\callback-capabilities.json
```

The check is read-only and exits 2 while the required identity prerequisites
are absent; its receipt distinguishes this gate from untested native IQ.
An actual credential-creation probe also returned `UnsupportedCredentialType`;
no connection or secret was created.

**Authorized callback credential setup:** the user then explicitly authorized
the credentials for Fabric-to-Logic-Apps. The new app-only connection is
`closeout-headless-v02-callback`; only its dedicated service principal is allowed
through Logic Apps Easy Auth. The credential expires after 30 days (the current
receipt records November 28, 2026). It has no new Graph, Fabric workspace or
Azure resource roles. Existing Logic Apps-to-Foundry MI and Fabric IQ remain
unchanged.

For an explicitly selected target, create a nonsecret JSON config containing
`tenantId`, `workspaceId`, `apiClientId`, `audience`,
`workflowResourceId`, `vaultResourceId` and `connectionName`. The workflow and
vault must be in the same approved group; the existing API audience is
`api://<apiClientId>`. Then run:

```powershell
uv run --frozen --extra agents --extra flow python -m agents.flow_callback --config <callback-config.json> --output .local\flow-deployment --apply --probe
```

The helper reuses owned application/connection receipts, adds no default API
permissions, stores the secret in Key Vault and Fabric, and never prints/writes
the value locally. Failed Key Vault storage revokes the newly created password.
Recovery can temporarily grant the signed-in operator Secrets User on this one
secret; the CLI removes its own temporary assignment on exit. The actual probe
creates/deletes a temporary Fabric pipeline, calls the protected host root and
checks anonymous/spoofed-header denial. The selected host has no `/.auth/me`
endpoint. This does not invoke a model or prove Request-trigger delivery.

Read-only configuration verification:

```powershell
.\scripts\verify-flow-callback.ps1 -WorkspaceId <workspace-guid> -ConnectionId <connection-guid> -ExpectedPrincipalId <caller-object-guid> -WorkflowResourceId <workflow-arm-id> -ExpectedAudience api://<api-client-guid> -OutputPath .local\flow-deployment\callback-credential-readback.json
```

Nonsecret application/connection IDs, secret URI, expiry and successful job
receipts are in `fabric-callback-credentials.json` and
`fabric-callback-auth-probe.json`. For rotation, create a new password on this
dedicated application, update the existing Key Vault secret and Fabric
connection credential over HTTPS, rerun the probe, then revoke the old password.
Do not delete the setup receipt to rotate, expose a secret in CLI arguments, or
assume Fabric refreshes its encrypted credential copy from Key Vault.
The `enableCoordinatorDispatch` infrastructure flag stays false: credential
configuration must not implicitly start unfinished native coordinator inference.

**Automatic external-test projection:** a new isolated Fabric notebook now
reads the full Blob registrations and actual Eventhouse events using its
schedule owner's delegated platform tokens. Scheduled execution and immutable
snapshot read-back verified the main Blocked-to-Ready rehearsal, open rework,
Ready fallback and NotReady precedence. It uses no persisted user token or new
permission. The two-minute schedule expires at `2026-10-29T18:42:39Z`; it is not
the production fast path. The ignored `fabric-automation-acceptance.json` receipt
contains item/schedule IDs and exact GET/PATCH/read-back renewal instructions.
Use `agents.fabric_flow_automation --help` and `--verify` for re-verification.
New Activator source/rules remain stopped and the dispatch pipeline fails
closed. Live Data Agent exposure/native coordinator acceptance is still pending;
the existing fixture agent and working connection were not changed.

For another explicitly approved target, add its Function managed identity as
Contributor through the selected Fabric workspace's **Manage access**. Verify
the actual deployed system principal, not the host-secret user-assigned identity.
Read back the workspace assignment before retrying the same cached work:

```powershell
az rest --method get --resource https://api.fabric.microsoft.com --url "https://api.fabric.microsoft.com/v1/workspaces/$workspaceId/roleAssignments" --query "value[?principal.id=='$senderPrincipalId'].{principal:principal.id,role:role}"
```

The expected row is the selected Function principal with role `Contributor`.
This permission applies to the workspace, not only the isolated stream. Obtain
approval again for any other identity or workspace; the existing grant is not
blanket authorization.

1. Select the permitted target and existing Foundry project. Supply the approved
   API registration client ID/audience, exact Fabric dispatch principal, actual
   Eventstream protocol namespace/entity, pinned Foundry configuration, environment
   name and explicit network choice in ignored `.local\flow-parameters.json`.
   Do not create a registration, role grant or security exception by assumption.
2. With public access disabled, establish approved VNet/private-endpoint/DNS and
   deployment connectivity first. This optional template does **not** provide
   that networking foundation and must not be presented as a working isolated
   deployment by itself.
3. Compile and review the group what-if before an authorized deployment:

```powershell
az bicep build --file infra\flow\main.bicep --stdout
az deployment group what-if --resource-group "<selected-group>" --template-file infra\flow\main.bicep --parameters "@.local\flow-parameters.json"
.\scripts\package-flow.ps1 -OutputDirectory .local\flow-packages\review-01
```

4. After authorization, deploy only that optional target. Deploy the adapter ZIP
   with Linux Python 3.11 remote dependency build and the Standard workflow ZIP
   separately. See [Functions ZIP deployment](https://learn.microsoft.com/azure/azure-functions/deployment-zip-push)
   and [Standard workflow deployment](https://learn.microsoft.com/azure/logic-apps/devops-deployment-single-tenant-azure-logic-apps).
   Verify Key Vault references/host startup, enabled Easy Auth, exact caller
   allowlists, Blob-trigger output and runtime managed-identity retrieval. Local
   ZIP import discovery is not that proof.
5. In the approved external Fabric test workspace, preserve the static fixtures
   and backed-up older items. Isolated ingress/tables are now created by the
   explicit opt-in command below. The operator Entra probe was ingested once;
   runtime-identity publication remains unverified. Configure completed-batch
   projection and `CloseoutFlowFactsV02` snapshots with a `snapshot:dynamic` column
   matching `agents.flow_projection.project_run`. Arrange actual automatic
   refresh and persist the immutable revision before emitting readiness.
   The helper alone does not deploy this mechanism. Make the same revision
   available to the native Data Agent without silently replacing proven fixtures.

```powershell
uv run --frozen --extra agents --extra flow python -m agents.flow_setup --config .local\foundry\flow-config.json --output .local\flow-deployment --apply
```

The connection API returns credentials as well as coordinates; the setup script
persists only nonsecret coordinates. It does not configure automatic projection,
Activator, a workspace identity or an Azure Event Hubs service.

6. Configure a completed-readiness feed and case-scoped Activator BECOMES Blocked
   / BECOMES Ready rules. Use the supported Fabric pipeline parameter binding
   (Preview) and an authenticated Web activity connection to the deployed
   coordinator workflow. The exact authenticated connection has not been tested;
   never substitute an anonymous callback or expose a callback token in source.

The remaining portions of steps 5-6 are pending service
work, not automated success. Their manual boundary
exists because target/security approvals and the selected Fabric refresh/dispatch
configuration are unresolved. Do not invent a Reflex API payload. Capture the
actual supported configuration, selected identities and deployment receipts under
ignored `.local` before promoting them into repeatable setup code.

### Selected direct coordinator and Fabric identity grants

The selected playground-to-Blob path is described in
`HEADLESS-FLOW.md`, including the distinction between the publisher Function
and the removed coordinator Function hop. The Logic App directly starts/polls
the pinned Foundry agent and saves the completed raw response; it does not
perform independent Fabric or citation validation.

For each new target, the Fabric owner must give the **Foundry project** managed
identity direct **Read** access to the published Data Agent: item **Manage
permissions -> Direct access -> Add user**. Select the exact project identity,
leave additional permissions unchecked and disable email notification.
Give the same identity database Viewer/query access to the agent's KQL source.
Do not grant workspace-wide Contributor as a substitute for these read grants.
The supported public interfaces checked do not expose Data Agent item-only
sharing writes, so this remains an explicit owner-operated step.

The connection must use `ProjectManagedIdentity`, with audience
`https://api.fabric.microsoft.com`. This is not the Logic App or publisher
identity. The Logic App instead needs project-scoped Foundry User and Blob Data
Contributor on `results`; both are declared in `infra/flow`.

Read-only verification: inspect the Data Agent's Direct access list, the KQL
database principals, the connection ARM properties, Logic App identity roles,
and deployed coordinator definition/health. No agent invocation is required for
these configuration checks. Actual response capture still requires a separately
authorized/manual run.

The existing Fabric callback still uses its explicitly approved app-only secret.
Current Web/REST connector capabilities do not support workspace identity.
Do not claim an all-managed-identity Deploy-button flow until that gate and
the button's full runtime publishing are resolved.

Verification currently available: the inspection command above, the focused
`tests\unit\test_headless.py` offline suite, locked ZIP import/layout checks,
operator `agents.flow ... status`, and the coordinator's strict current-revision
KQL verifier. A credentialed unattended-flow verification command/report remains
to be implemented. Actual uploads, Fabric watermark/readiness changes, Activator
rule history, secured callback/workflow runs, native tool receipts and Blob results
must all be correlated before claiming end-to-end acceptance.

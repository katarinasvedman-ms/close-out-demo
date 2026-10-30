# Headless evidence flow

## Selected playground-to-Blob flow

```text
Foundry playground: close-out-validation (actual photo and its registered context)
  -> trigger_fabric_eventstream (publish-only OpenAPI tool)
  -> publisher Function /api/publish-validation-result
  -> existing Fabric Eventstream and Fabric-owned readiness projection
  -> Fabric readiness transition / Activator
  -> closeout-headless-v02-dispatch
  -> authenticated coordinator Logic App
  -> direct Foundry Responses API: close-out-coordinator
       -> native Fabric Data Agent and document IQ
  -> Logic App saves the completed, unmodified response JSON to Blob
```

The downstream agent is **close-out-coordinator**, not close-out-validation.
The publisher Function is still required by the validation agent's tool; no
Function participates between the coordinator Logic App and Foundry or Blob.
The generated OpenAPI operation is `publish_validation_result`; the tool can be
displayed as `trigger_fabric_eventstream` in the playground.

The stateful workflow acknowledges Fabric with HTTP 202, starts one stored
background response, then polls the same response ID every ten seconds, bounded
by 180 iterations and 30 minutes. Only `completed` responses are saved. Failed,
cancelled, incomplete and timed-out responses fail the workflow rather than
appearing as successful draft output. The initial POST is not automatically
retried, so a response timeout cannot silently start a second agent invocation.

The Blob is the **raw Foundry response**, including its final assistant output,
response ID and any returned tool records. It is not a contract-validated
GapAnalysis, an approval, or a compiled package. The workflow does not query
Kusto, recalculate readiness, inspect citations or reject a completed response
because of Fabric provenance. Existing Python verification remains available
only as a separate diagnostic path.

Results use the private `results` container, under
`coordinator/<runId>/<caseId>/<readiness>/<transitionId>/<workflowRunId>.json`.
Blob metadata also carries case, test-run and workflow-run IDs. A manual replay
creates a new workflow invocation and separate result; this capture-only path
does not claim exactly-once agent execution or write any system of record.

### Direct workflow configuration and identities

- `FLOW_FOUNDRY_PROJECT_ENDPOINT`: selected project endpoint, without a trailing slash.
- `FLOW_COORDINATOR_AGENT_NAME` and `FLOW_COORDINATOR_AGENT_VERSION`: explicit published agent binding.
- `FLOW_STORAGE_URL`: evidence Storage Blob endpoint, with its trailing slash.
- Logic App system identity: project-scoped **Foundry User**, and **Storage Blob
  Data Contributor** scoped only to the `results` container for output.
- Foundry-to-Fabric native tool: **Project Managed Identity**, audience
  `https://api.fabric.microsoft.com`. Grant the selected project identity Read
  on the published Data Agent and database Viewer on its KQL source. These are
  Fabric permissions, not Azure subscription RBAC.
- Foundry-to-publisher: the project identity is explicitly allowed by publisher
  Easy Auth and its route-specific allowlist. The publisher's own identity sends
  to the existing Eventstream; its selected-workspace Contributor requirement
  is broader than an individual stream and requires explicit approval.
- Fabric-to-Logic-App: preserve bearer-only ingress and its exact allowed caller.
  The current Fabric Web activity connection uses a service-principal credential.
  Its workspace-identity credential was rejected by the selected tenant's API.
  **This hop is not managed identity.** An all-managed-identity deployment remains
  gated until Fabric supports the required callback identity/connector path;
  never silently create a secret fallback and label it managed identity.
  The existing Fabric readiness notebook is also delegated-owner execution, not
  workspace MI; host Azure Files uses a Key Vault-held Storage key, not MI.

`infra/flow/main.bicep` declares the direct workflow settings and output-container
role. For the existing target, deploy the workflow package separately; do not redeploy the validation
publisher merely to update this coordinator workflow. The public Deploy button's
source and publication boundary is documented in `DEPLOY-TO-AZURE.md`.
The updated fresh template includes flow hosting and its output bootstrap command
publishes both agents, the validation OpenAPI tool and ZIPs with actual version
pins. Workflows start Disabled; complete
[the manual checklist](MANUAL_STEPS.md#playground-flow-deployment-checklist)
before enabling the coordinator. The public button loads `main`; use matching
published source for bootstrap. Fresh-cloud acceptance is not claimed.

## Earlier Blob-triggered validation path

```text
Registered evidence batch in a dedicated Blob input container
  -> Logic Apps Standard built-in Blob polling trigger
  -> actual image/PDF inference by the validation prompt agent
  -> durable result/outbox -> Fabric Eventstream
  -> Fabric evidence projection and versioned readiness snapshot
  -> Activator BECOMES Blocked or Ready, per caseId
  -> Fabric dispatch pipeline -> authenticated Logic Apps callback
  -> close-out prompt agent using native Fabric IQ and document IQ
  -> direct coordinator Logic App saves the raw completed Foundry response in Blob
```

No Event Grid, UI, Power Platform, hosted agent, application container or
business-system write belongs to this reduced test increment. Fabric is an
external test dependency, never part of Azure deployment. Operational work
receipts must not become a replacement Dataverse approval/process owner.

## Earlier implementation and service-verification history

The following records describe the earlier Function-mediated coordinator.
They do not define the selected direct coordinator workflow above.

### Earlier manual playground assessment publication

The deployed Function also exposes `POST /api/publish-validation-result` for a
manually attached validation-agent OpenAPI tool. It publishes one assessment
against an existing pinned photo registration; it does not call the validation
agent again. Submitted assessments have `agent-submitted-assessment` provenance,
not an independently verified inference receipt. Fabric still owns readiness.

Generate the target-specific OpenAPI import file:

```powershell
uv run --frozen --extra flow python -m agents.flow_publish --server-url https://<function-app>.azurewebsites.net --output .local\flow-deployment\validation-publisher.openapi.json
```

In Foundry, create an OpenAPI connection using **Managed identity**, the Function
host as **Target URL**, and the existing Function API audience as **Audience**.
Paste the generated JSON into the tool's OpenAPI schema field. The portable
example is `contracts\apim\validation-publisher.openapi.json`; its placeholder
server must be replaced. The tool is not automatically attached to any agent.

The request contains `sourceRunId`, `sourceBatchId`, a fresh `publicationId`
(canonical UUIDs), and one `assessment` from the validation output. Use the same
publication ID and unchanged assessment for retries. A changed assessment needs
a new publication ID. Only a photo from the source registration is accepted;
arbitrary image URLs or caller-invented site metadata are not accepted.

Easy Auth remains mandatory. The Foundry **project** managed identity is allowed
only by `FLOW_PUBLISHER_CALLER_OIDS`; existing inference routes retain their
Logic Apps-only allowlist. Set optional Bicep `publisherPrincipalId` to reproduce
this authorization. No additional Fabric role is needed for the project:
the existing Function identity publishes events.

The endpoint and scoped permissions are deployed, and the owned Fabric projector
bundle accepts this provenance. Native tool invocation is left to the user's
manual attachment/test. Activator remains stopped and coordinator disabled.

The Azure-side foundations are **deployed** to the selected test target.
A real Blob batch triggered Logic Apps and managed-identity Foundry validation.
After the user-approved sender Contributor grant, all seven cached events reached
Fabric without repeating inference and the validation result was saved to Blob.
The unattended flow is **not ready for manual end-to-end testing**.
Authenticated Fabric pipeline delivery to the real Logic Apps Request trigger
and its managed-identity call to the coordinator Function are now verified.
The coordinator successfully verifies the live Fabric snapshot, but Foundry's
native Fabric tool repeatedly fails under that cloud identity with HTTP 400:
`Streamable HTTP POST response completed without a reply to request with ID: 2`.
No completed cloud draft exists. Activator remains stopped, and the coordinator
workflow was disabled again after the targeted probe.

The same native connection retrieved the exact live revision and document IQ
under the signed-in operator. That diagnostic is not an unattended substitute:
its draft also failed strict citation-version validation and was not published.
Do not weaken the citation checks or inject direct KQL facts to force success.
The [Fabric IQ authentication documentation](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/fabric-iq#authentication-and-security)
describes user delegation/OAuth for Foundry connections and application-only
tokens for **direct MCP clients**. It does not establish a supported app-only
native Foundry path. Resolving this boundary requires an explicitly approved
authentication/integration choice, not another callback credential or ingress
relaxation. The exact service-side cause of the missing MCP reply is unresolved.

| Boundary | Current state |
|---|---|
| Agent deployment configuration | Bootstrap defaults to native Fabric, accepts a settings file or interactive prompts, validates the selected project connection and registers both native sources. Stub facts require explicit opt-in. |
| Connection setup | Matching delegated connections are read and reused; absent connections require explicit creation authorization. Mismatching connections fail before agent registration. Creation and fresh-target acceptance still need live verification. |
| Existing Fabric capacity | The user-authorized existing assigned F4 capacity was resumed and verified Active. No SKU or workspace assignment changed. |
| Existing facts | All 70 static fixture rows remain accessible. Their validation outcomes are seeded, not produced by a new upload. |
| Existing Eventstream | Custom endpoint feeds the old `Events` table. This is not the isolated, version-bound validation/batch projection required here. |
| Isolated test ingress | Explicit opt-in `agents.flow_setup` created `closeout-headless-v02`, `CloseoutFlowEventsV02`, its JSON mapping and `CloseoutFlowFactsV02` in the selected existing workspace. Actual managed-identity validation events were published and their ingestion verified. No SAS credential was used or persisted. |
| Activator | The older empty item is untouched. The isolated KQL source/rules remain stopped. The new authenticated dispatch pipeline reached the actual Request trigger; automatic Activator action execution remains unverified. Explicit enablement selects only Blocked and excludes snapshots before the enable timestamp. |
| Existing pipeline | Refreshes the old notebook and sends an older callback shape; it does not carry the planned immutable facts revision. It was backed up, not changed or run. |
| Cloud invocation identity | Both Logic Apps-to-Function routes were exercised with managed identity. Validation inference works. Coordinator Foundry invocation reaches the native Fabric tool but fails with the observed MCP HTTP 400; no cloud native retrieval receipt/draft was produced. |
| Batch contracts | Draft v0.2.0 adds pinned evidence registrations, a completion barrier and a revision-bound callback. Existing v0.1.0 seeded fixture provenance is preserved. |
| Upload and validation | `agents.flow` registers immutable upload intent, uploads/pins bytes and writes the completion marker last. Validation version 4 used actual Blob bytes, rejected both original photos and accepted the RFDS PDF. The numeric RFDS policy prevents invented tolerance from accepting a mismatched azimuth. |
| Recovery and publication | Blob ETag/CAS claims retained inference and seven outbox events across the initial Send denial. After the explicitly approved workspace Contributor grant, a metadata-only marker retry completed all seven publications with the original response ID. KQL found all seven distinct events and the result Blob exists. `azure-eventhub` is a protocol client only: **no Azure Event Hubs service** is created. |
| Azure workflows/runtime | Both hosts and ZIPs are deployed; both Function routes are discovered. Blob-triggered validation works and anonymous access returns 401. The dedicated Fabric credential delivered a bearer-only Request-trigger POST without SAS. Coordinator was enabled for that probe and then disabled again after native tool failure; ingress restrictions and identities are unchanged. |
| Fabric projection | The isolated scheduled notebook projects actual ingested events and Blob registrations into verified immutable snapshots. Existing Data Agent draft/published sources now additionally expose `CloseoutFlowFactsV02`; old static tables, examples and connection are retained. Actual operator-native retrieval returned the live sourceRef and fresh validation IDs. |
| Coordinator | Independently verifies the current revision, then invokes native tools and checks citations before saving results. Equivalent timestamp serializations are compared as instants, without relaxing revision hashes. Native cloud MCP failure and an operator draft citation-version rejection remain blocking; neither produced a result Blob. |
| Optional infrastructure | The user applied `SecurityControl=Ignore` to `rg-close-out-demo-flow` only. The repaired foundation and hosts deployed successfully. A pre-created user-assigned identity reads the Key Vault host secret; system identities handle runtime access. Shared keys are enabled only on host Storage; evidence containers remain private with keys disabled. Hosting resources incur costs while the remaining integration is blocked. |

Validation version 4 and coordinator version 14 have been published and pinned
in the ignored flow configuration, retaining the existing native Fabric/document
IQ bindings. Earlier versions remain available. Publication is not runtime or
end-to-end acceptance.

### Runtime publication recovery and remaining gates

The earlier Key Vault/network/bootstrap failures are resolved in the selected
group following explicit user approval of its security exception. Do not copy
that exception to any other group. The optional `uploadOperatorPrincipalId`
grants the selected user Blob Data Contributor on inputs/ledger and Blob Data
Reader on results; it is not a tenant-wide or Fabric permission.

The deployed Function identity was initially denied by the isolated Eventstream endpoint:
`Unauthorized access. 'Send' claim(s) are required`, with
`amqp:unauthorized-access`. This is publication authorization, **not** a failure
of the existing native Fabric IQ connection or validation's Foundry identity.
[Microsoft's managed-identity guide](https://learn.microsoft.com/fabric/real-time-intelligence/event-streams/connect-using-managed-identity)
requires workspace Contributor access for the sending identity. The user
explicitly approved that broader grant. Only the deployed Function system
identity received Contributor in the selected existing workspace; role read-back
and actual runtime publication succeeded. No SAS credential, tenant-wide setting
or native IQ change was added.

The registered validation receipt retains one Foundry response and seven cached
outbox events. Updating only completion-marker metadata retriggered the same
work; it completed with the same response ID, seven published events, seven
distinct Eventhouse rows and a persisted result Blob. Do not generate a new
batch merely to repair delivery.

The historical version-3 model-quality failure accepted
PHOTO-20's 127-degree azimuth against a 120-degree RFDS value by inventing an
acceptable tolerance. That receipt remains unchanged. Version 4 requires a
separate visible numeric observation. The adapter compares it with the exact
quoted RFDS sector direction; without a supplied tolerance, a mismatch cannot
be compliant. Missing observations cannot support compliance, and the original
model assessment object is not mutated. This remains model-based observation,
not independent image metrology.

A fresh immutable batch under version 4 completed through the deployed Blob
trigger and managed identities: both original photos were noncompliant and the
RFDS was compliant, with seven published events. Its receipt is
`.local\flow-deployment\validation-v04-receipt.json`. This verifies the quality
correction and validation delivery, not the remaining coordinator path.

### Fabric-to-Logic-Apps callback authentication

The selected tenant's supported-connection-types API currently lists
`OAuth2`, `Basic`, `Anonymous`, `Key` and `ServicePrincipal` for `WebForPipeline`,
but **not `WorkspaceIdentity`**. The REST alternative also lacks that credential
type. Workspace GET, including the workspace-specific endpoint, does not expose
the identity returned as already existing by provisioning. Do not infer a caller
principal or assume that a normal Azure Data Factory MSI example is supported
by this Fabric connector. The existing older callback connection is anonymous
and is not reused.

An actual CreateConnection request using the documented `WorkspaceIdentity`
credential was also rejected with HTTP 400 `UnsupportedCredentialType`; no
connection was created. Its nonsecret request/response persist under
`.local\flow-deployment\callback-identity-probe-*`. This is a demonstrated
Fabric callback-connector gate, not a Foundry managed-identity or native IQ
authentication failure.

Run the read-only capability check against the explicitly selected workspace:

```powershell
.\scripts\verify-flow-callback.ps1 -WorkspaceId <workspace-guid> -OutputPath .local\flow-deployment\callback-capabilities.json
```

In this legacy workspace-identity mode, exit code 2 means its prerequisites are absent. A successful
prerequisite check would still not prove callback delivery or native tool
execution. Keep coordinator ingress fail-closed until a supported, authorized
dispatch credential and exact caller are verified. No secret-based fallback,
delegated consent, anonymous callback or change to the working Foundry IQ
authentication is authorized by this check.

The user subsequently explicitly authorized the required callback credentials.
`agents.flow_callback` configures a dedicated single-tenant app/service principal
with no Graph, Fabric workspace or Azure resource-role grants. Its 30-day secret
is stored only in the selected Key Vault and Fabric's credential store, never
local configuration, app settings or workflow JSON. The new
`closeout-headless-v02-callback` connection uses `ServicePrincipal`, the existing
API audience and `skipTestConnection=false`. Logic Apps Easy Auth permits only
that exact principal; the invocation adapter still permits only Logic Apps MI.
This does not replace the working Fabric IQ connection or its authentication.

An actual temporary Fabric pipeline authenticated to the protected Logic Apps
host successfully; anonymous and principal-header-only calls returned 401.
The temporary pipeline was deleted. `/.auth/me` returns 404 on this selected
Standard host, so the proof uses the protected host root plus exact authorization
policy read-back, not an invented identity endpoint. That initial probe was host-authentication proof only. Subsequently, the isolated
dispatch pipeline used the same connection to POST to
`/api/coordinator/triggers/Fabric_readiness_transition/invoke?api-version=2020-05-01-preview`
without SAS. The actual workflow and coordinator Function ran. This proves
Request-trigger delivery, not successful native retrieval or a completed draft.
Nonsecret setup/expiry, historical failed probes and the successful job receipt
are under ignored `.local\flow-deployment\fabric-callback-*`.

The repeatable authorized setup/probe is:

```powershell
uv run --frozen --extra agents --extra flow python -m agents.flow_callback --config <callback-config.json> --output .local\flow-deployment --apply --probe
```

See `MANUAL_STEPS.md` for configuration, rotation and the read-only verification
command. Fabric holds an encrypted credential copy, not a live Key Vault
reference: rotate both before the recorded expiration. `connectionEncryption`
describes test-connection transport, not Fabric's encryption at rest.
`fabricDispatchPrincipalId` now protects ingress independently of the explicit
`enableCoordinatorDispatch` flag, which remains false until native coordinator
acceptance. The pipeline is configured with the authenticated Web activity, but
Activator stays stopped and the coordinator workflow stays disabled.

### Automatic Fabric projection and bounded test schedule

`agents.fabric_flow_automation` publishes only isolated external-test items.
The notebook reads full registered manifests from the private Blob ledger
and actual events from the isolated Eventhouse table. Fabric, not an Azure
Function or a laptop loop, executes the readiness projection. The Fabric-owned
schedule uses its operator owner's delegated platform Storage/Kusto tokens;
no user token is persisted. This is not a workspace-managed-identity runtime
or production per-caller authorization.

The notebook persists completed-boundary history and verifies each immutable
revision before a readiness observation can be consumed. Live scheduled jobs
verified original three gaps, replacements with open rework, sole rework blocker
after explicit fixture ticket closure, then Ready with no gaps after explicit
fixture rework closure. The independent fallback is Ready and Planned
ConstructionComplete remains NotReady. These closures are labelled synthetic
controls, never human approvals or business-system writes.

The two-minute test schedule expires at `2026-10-29T18:42:39Z`. It is not the
under-ten-second production fast path. `fabric-automation-acceptance.json` and
`fabric-main-ready-acceptance.json` under `.local\flow-deployment` contain
actual scheduled job IDs, revisions, timing, item IDs and the exact schedule
renewal API/request. Renewal requires backing up, explicitly updating and
reading back the existing schedule; `--apply` does not extend an enabled
schedule's expiry.

For the already selected target, read-only re-verification is:

```powershell
uv run --frozen --extra agents --extra flow python -m agents.fabric_flow_automation --config .local\foundry\fabric-config.json --output .local\flow-deployment --storage-endpoint https://<approved-account>.blob.core.windows.net --verify
```

The existing Data Agent was additively extended in both draft and published
definitions to select `CloseoutFlowFactsV02.snapshot` by exact run, site, program
and revision. Its old static table/examples and working native connection remain
available. Definition backups and decoded-JSON read-back persist under
`.local\flow-deployment\data-agent-*`. Operator-native retrieval of the live
revision succeeded, but cloud invocation and valid draft completion did not.
Direct KQL remains verification only, never agent input.

Nonsecret deployment/probe receipts are under ignored `.local\flow-deployment`;
`ingress-verification.json` records actual Eventhouse ingestion. This is an
infrastructure probe using the operator identity, not a validation/readiness
event. `validation-runtime-receipt.json` records actual managed-identity inference
and retained outbox; `runtime-eventstream-denial.json` records the earlier error.
`fabric-sender-role.json` records the approved grant and
`sender-grant-verification.json` records successful recovery/ingestion.

## Local implementation and packaging

Install the optional locked runtime dependencies and run the focused offline suite:

```powershell
uv sync --frozen --extra dev --extra agents --extra flow --python 3.11
uv run --frozen --extra dev --extra agents --extra flow pytest tests\unit\test_headless.py
```

Build into a **new** ignored directory. This does not deploy anything, register
agents or modify Fabric:

```powershell
.\scripts\package-flow.ps1 -OutputDirectory .local\flow-packages\review-01
az bicep build --file infra\flow\main.bicep --stdout
```

`adapter.zip` contains the Function entry point, Python modules, prompts, strict
contracts and requirements exported from `uv.lock`; its target needs Linux Python
3.11 remote dependency build. `workflows.zip` contains the Standard workflow
project. Neither package contains local settings, credentials or runtime receipts.
Regenerate packages after source changes; existing output directories are never
overwritten. Packaging/import discovery and Bicep compilation do not establish
deployability or a working service.

Once the selected target, identities and external plumbing are approved and
verified, the operator entry points are:

```powershell
# Generate synthetic source files locally; no upload or model call:
uv run --frozen python -m shared.local_demo
$run = [guid]::NewGuid().ToString()
$batch = [guid]::NewGuid().ToString()
# Persist these IDs for retries. Upload uses the operator's Azure CLI identity:
uv run --frozen --extra agents --extra flow python -m agents.flow --storage-url "https://<approved-account>.blob.core.windows.net" upload --site SE-STO-0042 --evidence PHOTO-20 --run-id $run --batch-id $batch
uv run --frozen --extra agents --extra flow python -m agents.flow --storage-url "https://<approved-account>.blob.core.windows.net" status --run-id $run --batch-id $batch
```

The upload command automatically includes the pinned RFDS PDF/text needed by
sector validation and manages its internal completion marker; the operator does
not create that marker manually. This currently exercises validation and scheduled
Fabric projection only, **not** a working end-to-end draft path.
`status` reports validation state, not coordinator completion.
Reusing the same run/batch with changed input is rejected.
A new batch is not an implicit rework/ticket closure. `emit-fixture --help`
describes explicitly labelled WorkOrderChanged, TicketChanged and MilestoneChanged
controls, with separate immutable operation IDs. These controls are synthetic
events, not approvals or governed system-of-record operations.

The validation workflow watches only `inputs` completion markers. Outputs and
operational receipts use separate `results`/`ledger` containers, not the IQ prefix.
The coordinator workflow returns 202 for a durable workflow run, **not** a
completed or validated draft; inspect the run and result ledger for its outcome.
After final workflow retry exhaustion, rerun/resubmit the same work with unchanged
identity to recover cached inference. No automatic dead-letter recovery is claimed.

## Unverified service details

- `TODO(verify)`: deploy and observe the built-in Blob trigger's actual body/path
  shape, including initial/pre-existing blobs and property-only changes. Its
  definition uses the documented `whenABlobIsAddedOrModified` operation.
- Easy Auth, the approved audience and actual bearer-only Request-trigger
  delivery pass for the exact dedicated principal. Native coordinator success
  is a separate, currently failing boundary.
- `TODO(verify)`: resolve the observed native Fabric MCP failure under the
  runtime managed identity and produce valid native Fabric/document receipts
  plus a cited draft. Host startup, Blob/Kusto access and validation inference
  have already worked; do not relabel this as a generic hosting failure.
- Fabric ingestion, scheduled refresh and immutable snapshot persistence are
  verified; automatic Activator action execution remains unverified. The
  projector's `evaluatedAt` is its source-event watermark; scheduling/ingestion
  timestamps and delay must be recorded separately.
- `TODO(verify)`: Activator transition rules and pipeline parameter binding
  (Preview), including initial-state reset and authenticated callback delivery.
- The public 2016 workflow JSON schema does not describe Standard
  `ServiceProvider` triggers or current managed-identity authentication. It
  cannot validate these definitions unchanged; do not misreport that check as
  passing. Deployment/runtime validation remains a separate gate.

## Existing connection and managed-identity verification

The Fabric Data Agent is already connected as a native Fabric IQ tool and has
returned real fixture facts. Do not recreate or replace that connection merely
because the caller changes from the local test client to Logic Apps.

The connection uses `UserEntraToken`; successful fixture/live retrieval used the
signed-in operator. A real Logic Apps-triggered managed-identity invocation now
fails at native Fabric MCP with the HTTP 400 recorded above. The
[Fabric IQ documentation](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/fabric-iq)
and [delegation documentation](https://learn.microsoft.com/azure/foundry/agents/how-to/configure-user-delegation)
inform diagnostics; the observed transport error alone does not prove a specific
missing permission or justify modifying the connection.

Verify the intended path before proposing changes:

1. Invoke the selected Foundry coordinator from the Logic Apps runtime using
   managed identity and its configured Foundry access.
2. Verify actual native Fabric IQ tool execution and returned fixture/source
   provenance, together with the required document IQ retrieval.
3. If the call fails, identify the exact failing boundary and service error.
   Only then determine whether a configuration or permission change is needed.

No local coordinator runner or Blob job mailbox is part of this design.
Blob retains evidence, results and recoverable delivery receipts, not an
unnecessary handoff to a laptop. Do not introduce stored user tokens, new
credentials, consent grants, direct-KQL agent facts or silent stub fallback.

## Repeatable read-only preflight

Use the six-field external settings file from
[deployment instructions](DEPLOY-TO-AZURE.md#external-fabric-configuration):

```powershell
uv run --frozen --extra agents python -m agents.fabric_setup --config .local\fabric-settings.json --output .local\flow-preflight --inspect-only
```

This command reads workspace/capacity assignment and paginated item inventory,
reads table metadata, and backs up the selected Data Agent plus existing
Eventstream, Activator and pipeline definitions. The `.show tables` request uses
the KQL management endpoint but does not mutate tables. It does not retrieve
ingress secrets, start a pipeline/rule, ingest events or republish definitions.
Timestamped backups and `inspection.json` are ignored runtime receipts.
Failure is not a successful inspection; inspect the command exit and receipt
timestamp. Inventory/read-back is not end-to-end verification.

Only an explicitly authorized Azure operator should resume the assigned
capacity if it is paused. Resume that exact existing capacity without scaling or
reassigning the workspace. An active capacity incurs its existing charges.

## Remaining acceptance

The user narrowed this increment to **one** fresh PHOTO-20 submission producing
a real cited Blocked draft through the unattended cloud chain. First resolve the
native cloud tool boundary and strict citation-version rejection. Then enable
the isolated Blocked rule/coordinator, submit fresh evidence and prove automatic
Activator delivery and a persisted draft before handing over a manual command.
The existing original/Ready/rework projection history is supporting evidence,
not a requirement to repeat a broad scenario matrix. Fresh-tenant/deployment-button
acceptance, an arbitrary-photo inbox and hardening remain deferred.

Measure Blob polling, inference, Fabric ingestion/readiness and
Activator/pipeline/coordinator latency separately. The polling trigger is
best-effort; neither this reduced flow nor local fixture tests establish the
full design's event-to-Dataverse latency acceptance.

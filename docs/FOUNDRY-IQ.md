# Foundry IQ document knowledge

The document path is private Blob Storage in **Sweden Central** -> one native
**Azure Blob knowledge source** in France Central Search -> one shared
**Foundry IQ knowledge base** -> the coordinator's native MCP tool.
Default facts/readiness use the explicit local stub. An optional native Fabric IQ
binding uses an existing external Data Agent for local synthetic-flow verification;
see [the Fabric test path](#existing-fabric-for-local-verification).

## What IQ manages

`close-out-documents` is a knowledge source with `kind: azureBlob`, scoped to
the `documents` container's `iq/` prefix. IQ creates and manages its data source,
indexer, skillset and index. `agents.knowledge_setup` declares the source and
knowledge base; it does not create or edit those generated indexing resources.
This replaces the previous two per-site Search-index knowledge sources.

`close-out-demo` is the single shared knowledge base. Both synthetic sites use
the same coordinator version, MCP URL and project-managed-identity connection.
In Foundry, refresh **Build -> Knowledge**, open this base and inspect its
`close-out-documents` source: the source type is **Azure Blob Storage**, not
Azure AI Search index. No browser automation is required.

Setup uploads eight canonical text documents: RFDS, as-built, integration report
and draft acceptance certificate for each selected site. The generated seed PDFs
are raster images, so minimal extraction uses their exact **text counterparts**,
not PDF OCR. Photo packs are excluded. The separately labelled operator refresh
note below is not case evidence. Arbitrary PDF ingestion, OCR and Content
Understanding are not implemented.

Native ingestion extracts/chunks text and synchronizes every five minutes.
Synchronization is asynchronous, not an instant or sub-ten-second promise.
Setup waits for the expected Blob URLs and artifact/site/version text to appear,
not merely for successful source creation. Minimal text extraction needs no
embedding or enrichment model. Retrieval separately uses **Low reasoning effort**
with the existing regional GPT-4o deployment for one LLM query-planning pass.
No new model deployment is required. This adds model usage and latency compared
with the previous Minimal retrieval setting. Medium effort is not currently
listed as supported in France Central.

## Scope and citations

This is an **operator-only synthetic demo, not per-user ACL enforcement**.
The project identity can retrieve both sites. Including the current site in
queries improves relevance; it does not authorize or isolate data. Other-site
results are ignored for case citations and counted in retrieval receipts.

Knowledge-base retrieval instructions include the exact site ID in site-specific
searches, prioritize RFDS for planned values and acceptance documents for
requirements, respect requested versions and avoid substituting other sites.
These are query-planning guidance, not access controls or proof of retrieval.

Receipts verify the actual returned Blob origin, filename artifact ID, Site
line and Version line against current case facts. Wrong versions, foreign
origins and document citations absent from retrieval are rejected. Both native
MCP references and Foundry's normalized document references are supported;
model-authored answers are not proof of retrieval. This adapter is verified for
the short generated text documents whose chunks retain their identity headers,
not arbitrary long documents.

Document user-token forwarding is not configured. Do not
put real multi-customer data in this shared source on the strength of a prompt
or site allowlist. The optional Fabric tool below separately uses delegated user
authentication; it does not change document permissions.

Search Basic uses one replica/partition, Entra-only authentication and the free
semantic allowance. It is billable and not an HA configuration. Search's managed
identity has container-scoped Blob Data Reader; the project identity has Search
Index Data Reader. Search also has **Cognitive Services User**, scoped to the
existing Foundry account, to invoke the configured retrieval model through
managed identity. Keys, anonymous Blob access and shared-key Storage
authentication remain disabled. The HNS-enabled account is accessed through its
Blob interface (`isADLSGen2: false`); ACL ingestion is not enabled.

## Provision and populate

IQ definitions do not require document uploads. Add `--definitions-only` to
`agents.knowledge_setup` to create/read back the native source and knowledge base
without asset files, Blob uploads, ingestion polling or model inference.
`knowledge-definition-receipt.json` records that configuration-only result; it is
not evidence of successful ingestion. The fresh-project bootstrap uses this mode
by default, with document loading and smoke checks opt-in via `--seed-and-verify`.
Without `--definitions-only`, the standalone knowledge setup below also populates
and verifies the synthetic documents as before.

Use the authenticated target subscription and verified project, not copied
development IDs. This Bicep entry point attaches knowledge to an existing project;
the fresh-project portal path is documented in [DEPLOY-TO-AZURE.md](DEPLOY-TO-AZURE.md).
Neither entry point deploys a hosted agent.

```powershell
uv sync --frozen --extra dev --extra agents --python 3.11
$projectId = "<verified target Foundry project ARM ID>"
$operatorId = az ad signed-in-user show --query id -o tsv
$environment = "<target environment>"
$modelDeployment = "<existing regional model deployment>"
$modelName = "<supported model name>"
az deployment sub what-if --name close-out-knowledge --location swedencentral --template-file infra\knowledge\main.bicep --parameters environmentName=$environment projectResourceId=$projectId operatorPrincipalId=$operatorId knowledgeBaseName=close-out-demo knowledgeModelDeploymentName=$modelDeployment knowledgeModelName=$modelName
az deployment sub create --name close-out-knowledge --location swedencentral --template-file infra\knowledge\main.bicep --parameters environmentName=$environment projectResourceId=$projectId operatorPrincipalId=$operatorId knowledgeBaseName=close-out-demo knowledgeModelDeploymentName=$modelDeployment knowledgeModelName=$modelName --query properties.outputs -o json | Set-Content .local\foundry\knowledge-infra.json
uv run --frozen --extra agents python -m agents.knowledge_setup --infra .local\foundry\knowledge-infra.json --site SE-STO-0042 --site SE-GOT-0017
```

Generate assets with `python -m shared.local_demo` first if absent. Setup checks
seed hashes before upload, targets the same named source/base and overwrites
only selected synthetic blobs. It does not delete unrelated documents.
Receipts/configuration stay under ignored `.local\foundry`.
The generated `knowledgeModel` infrastructure output supplies the actual model
endpoint, deployment and name to setup. Old infrastructure outputs without this
binding are rejected before uploads; redeploy the template instead of silently
restoring Minimal retrieval. Allow for managed-identity role propagation.

If external security controls disable Storage networking, use only an explicitly
approved exception as documented in [MANUAL_STEPS.md](MANUAL_STEPS.md). The current
development group requires `securityControlIgnore=true` on subsequent deployments.

The template creates `close-out-knowledge-search` for portal Search discovery
and `close-out-knowledge` for project-managed-identity access to the exact shared
base MCP endpoint. A connection to the service root previously returned HTTP 405.
Native MCP uses preview API `2026-08-01-preview`.
The template also manages Search's account-scoped retrieval-model role assignment.

Register the shared prompt binding for use with the local verification client:

```powershell
uv run --frozen --extra agents python -m agents register --endpoint "<target project endpoint>" --deployment "<existing deployment>" --model "<model name>" --model-version "<version>" --knowledge-config .local\foundry\knowledge.json
```

Development coordinator version **9** is shared by both sites; validation
remains version **1**. There is no `closeoutBySite` binding. The optional
`knowledge` configuration explicitly selects live IQ; omitting it selects
the existing local-document mode rather than silently falling back.
Knowledge-base reasoning/instruction changes take effect at the same endpoint;
they do not require a new coordinator version.

## Demonstrate managed refresh

Upload the labelled note without touching the generated Search pipeline:

```powershell
$infra = Get-Content .local\foundry\knowledge-infra.json -Raw | ConvertFrom-Json
$storageAccount = ([Uri]$infra.storageEndpoint.value).Host.Split('.')[0]
az storage blob upload --account-name $storageAccount --container-name $infra.containerName.value --name iq/demo/managed-ingestion-proof.txt --file examples\knowledge-refresh.txt --auth-mode login --overwrite
```

Wait for source synchronization, then ask the knowledge base for
`IQ-MANAGED-BLOB-REFRESH-1 managed ingestion confirmation`. Inspect the returned
`iq/demo/managed-ingestion-proof.txt` source and marker, not just model prose.
To repeat with a changed marker, edit a local copy and upload it to the same
Blob name. Do not change RFDS or canonical seed facts just to demonstrate refresh.
Use the knowledge-base retrieval experience for this note. The coordinator's
synthetic-facts function is executed only by the local client, not the playground.

## Verification and limitations

```powershell
uv run --frozen --extra dev --extra agents pytest tests\unit\test_agents.py
az bicep build --file infra\knowledge\main.bicep --stdout
uv run --frozen --extra agents python -m agents gaps --site SE-STO-0042
uv run --frozen --extra agents python -m agents gaps --site SE-GOT-0017 --scenario fallback-ready
```

Check `modes.foundryIq=live`, shared-operator scope, actual `knowledgeRetrieval`
receipts, RFDS artifact/version citations and the empty-gap Ready fallback.
RFDS text supplies Sector B's planned 120 degrees; do not require a particular
model phrasing as a proxy for provenance. Facts/readiness remain a stub.

The local client verified the three cited gaps using shared prompt version **9**
with Low retrieval reasoning. Direct retrieval returned
`modelQueryPlanning` activity for the bound model and `agenticReasoning` at Low,
not just a saved configuration value. The Ready fallback returned no gaps against
that same prompt version. Adding the operator note was verified through native MCP after
managed synchronization; local receipts are in `.local\foundry`.
The superseded per-site bases, Search-index sources, manual pipelines and
RemoteTool connections have been retired. Historical agent versions and
original Blob files are retained, but are not the current demo path.

Cross-region ingestion moves document text into approved France Central Search;
Blob/model resources stay in Sweden Central. No Fabric resource, Dataverse write,
approval, business action or package generation is introduced.

## Existing Fabric for local verification

**Fabric is an external test dependency, never part of portal/Bicep/bootstrap
deployment.** The opt-in `agents.fabric_setup` utility can align an explicitly
authorized existing Data Agent/KQL database with provisional v0.1.0 fixtures.
It backs up the agent definition, refuses unrelated owned-table replacements,
and proves independently computed KQL readiness against all 70 scenario/site
rows before publishing the materialized facts. It does not modify the existing
notebook, pipeline, semantic model or old tables, or create a workspace/capacity.
Do not run it against an unapproved target.

The Data Agent must select `CloseoutContractFactsV01` and its actual columns in
both draft and published definitions. Base-row lookup must filter `siteId`,
`program` and `scenario` before interpreting child arrays. Empty blocker arrays
do not mean a missing site. The real contract fields are
`workOrders[].workOrderType/status/caseId` and
`photos[].isCurrent/replacesPhotoId`; invented fields and blocker-only inner
joins previously caused false "no facts" answers for Ready and rework scenarios.

Use the signed-in operator's existing Fabric permissions and the documented
[`UserEntraToken` delegation](https://learn.microsoft.com/azure/foundry/agents/how-to/configure-user-delegation)
for a project `RemoteTool` connection to the exact published Data Agent MCP URL.
The audience is `https://analysis.windows.net/powerbi/api` and delegated scope is
`https://analysis.windows.net/powerbi/api/DataAgent.Execute.All`. See
[manual connection setup](MANUAL_STEPS.md#external-fabric-test-connection).
Fresh bootstrap can instead accept the same settings through `--fabric-config`,
validate the selected Foundry project connection and, with explicit
`--create-fabric-connection`, create it when absent. Interactive bootstrap
prompts for the settings; local stub facts require `--facts-mode stub`.
This binds an Azure connection to external Fabric; it does not deploy Fabric.
The development connection was exercised without creating an application,
client secret or granting consent. Other tenants may require administrator
consent: an authentication/consent failure is a failure, never a stub fallback.

Keep the selected target in ignored `.local\foundry\fabric-alignment\native.json`:

```json
{
  "workspaceId": "<existing workspace GUID>",
  "dataAgentId": "<existing published Data Agent GUID>",
  "databaseId": "<existing KQL database GUID>",
  "kustoEndpoint": "https://<existing cluster>.kusto.fabric.microsoft.com",
  "connectionName": "<existing Foundry project connection name>",
  "toolName": "DataAgent_<actual exposed tool name suffix>"
}
```

Register a separate pinned local configuration, preserving the default stub
configuration and document IQ connection:

```powershell
uv run --frozen --extra agents python -m agents register --endpoint "<project endpoint>" --deployment "<existing deployment>" --model "<model name>" --model-version "<version>" --knowledge-config .local\foundry\knowledge.json --fabric-config .local\foundry\fabric-alignment\native.json --config .local\foundry\fabric-config.json
uv run --frozen --extra agents python -m agents gaps --config .local\foundry\fabric-config.json --site SE-STO-0042 --program CUST-A-5G-ROLLOUT --scenario initial-blocked
uv run --frozen --extra agents python -m agents gaps --config .local\foundry\fabric-config.json --site SE-GOT-0017 --program CUST-A-5G-ROLLOUT --scenario fallback-ready
uv run --frozen --extra agents python -m agents gaps --config .local\foundry\fabric-config.json --site SE-STO-0042 --program CUST-A-5G-ROLLOUT --scenario replacements-reviewed-rework-open
```

The new immutable coordinator version replaces **all local function declarations**
with `fabric_iq_preview` and retains native document MCP. Validation remains on
its existing version; neither inference models nor document resources change.
In the coordinator playground select the native version and include, for example,
`siteId SE-STO-0042, program CUST-A-5G-ROLLOUT, scenario initial-blocked`.
Playground answers alone do not satisfy the deterministic verification below.

Expected CLI outcomes are exactly three initial gaps
(`CHK-A-AZ-B`, `CHK-A-GROUNDING`, `PL-0042-01`) with current photo/latest-result
citations, retrieved RFDS/version and a proposed rework; no gaps/rework for the
Ready fallback; and only `RW-0042-01`, without another proposed rework, after
accepted replacements while active-case rework is still open.

Native Data Agent output is synthesized prose, **not** the stub JSON contract.
The local verifier separately reads the actual materialized KQL row, checks its
identity/record schemas, and compares final gap IDs and citations against that
snapshot. This read is not supplied as agent facts: the agent must execute the
native Fabric tool and document IQ itself. `fabricRetrieval` receipts require an
actual matching native tool result, explicit scope and exact `sourceRef`;
`knowledgeRetrieval` still verifies Blob/site/version provenance. Successful
receipts disclose `native-fabric-iq-synthetic-fixtures` and the separate KQL
verification mode. Missing/duplicate rows, source failures, invalid citations or
wrong drafts fail closed. Native fixture mode supports `gaps --program` only:
local validation overlays and the stub-based `smoke` command are rejected.

These are manually refreshed synthetic facts with **seeded validations**, not
fresh photo inference, human approvals, production Fabric readiness/Eventstream/
Activator wiring, Dataverse updates or packages. No denied-user test is claimed.
The local site allowlist and query filters are not production access controls.

The [headless Blob/Activator plan](HEADLESS-FLOW.md) reuses the existing native
Fabric IQ tool with a Logic Apps managed-identity invocation of Foundry.
Verify that exact call and actual tool results before declaring an identity
blocker. No new authentication, local runner or alternative tool is assumed.

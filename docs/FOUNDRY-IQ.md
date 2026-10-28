# Foundry IQ document knowledge

The document path is private Blob Storage in **Sweden Central** -> one native
**Azure Blob knowledge source** in France Central Search -> one shared
**Foundry IQ knowledge base** -> the coordinator's native MCP tool.
Fabric facts/readiness remain explicitly stubbed.

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

Native permission enforcement/user-token forwarding is not configured. Do not
put real multi-customer data in this shared source on the strength of a prompt
or site allowlist. Future Fabric facts still require a real authenticated Data
Agent tool; connecting IQ does not make the entire coordinator code-free.

Search Basic uses one replica/partition, Entra-only authentication and the free
semantic allowance. It is billable and not an HA configuration. Search's managed
identity has container-scoped Blob Data Reader; the project identity has Search
Index Data Reader. Search also has **Cognitive Services User**, scoped to the
existing Foundry account, to invoke the configured retrieval model through
managed identity. Keys, anonymous Blob access and shared-key Storage
authentication remain disabled. The HNS-enabled account is accessed through its
Blob interface (`isADLSGen2: false`); ACL ingestion is not enabled.

## Provision and populate

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

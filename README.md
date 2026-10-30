# Close-out demo - Azure/Foundry track

Start with `docs\close-out-demo-definition-en.md` and `docs\IMPLEMENTATION-NOTES.md`.

The implementation provides **real Foundry validation and close-out prompt agents**, Blob-backed Foundry IQ, a runnable offline seed/stub demonstration, a minimal React console, and provisional cross-track contracts with generated types. Evidence is synthetic; document retrieval is live IQ when bound. Facts use either the scoped MCP stub or an explicit native Fabric IQ binding to existing external test fixtures. Fabric is not deployed by this repository. Upload APIs, business actions, package compilation, a complete Azure foundation and production Dataverse/Fabric flows are not implemented.

**Fabric contract review:** start with [`contracts/REVIEW-DECISIONS.md`](contracts/REVIEW-DECISIONS.md), then the referenced schemas and examples. The provisional v0.1.0 shared baseline now has draft v0.2.0 additive headless-test transports; neither is an approved release or a complete end-to-end integration.

## Deploy the playground flow

From the downloaded repository, run:

```powershell
.\scripts\deploy-demo.ps1 -Environment demo-review
```

The guided installer explains missing prerequisites, guides sign-in and target
selection, prepares nonsecret settings, and reuses the existing Bicep, agent
publication and document/evidence helpers. It does not require a configuration
file prepared in advance. Keep the same environment name when rerunning; state
and exact playground inputs are kept under ignored `.local\installer\`.

The Azure/Foundry foundation is deployed **before application client IDs or
Fabric selections are requested**. Saved application registrations are reused;
otherwise dedicated secret-free registrations are prepared with approval.
Entering an existing client ID is an optional administrator-supplied path.
The foundation milestone is infrastructure only, not agent publication.
For a new target, answer **n** to using an administrator-supplied application,
then approve preparation of the new dedicated registrations and API settings.
Confirm the callback authentication approach only with target-owner approval;
this does not create a password or Fabric connection.

Azure infrastructure, both Foundry agents, native document IQ, runtime packages
and playground inputs are prepared **before Fabric permission or binding changes**.
The installer prints the project and agent versions, then asks whether to continue
with Fabric setup. Answer **n** to stop there and preserve your existing Fabric demo.
Do not submit the playground message until Fabric setup and runtime enablement finish.

**Fabric is not provisioned.** Select existing compatible Fabric items. Only
explicitly approved bindings, permissions and schedule updates are applied;
incompatible or unsupported changes are handed to the owner rather than guessed.
For example: "Grant this Foundry project identity direct Read on this Data Agent,
then rerun the same command." New Azure identities are displayed after provisioning.
The existing Fabric callback needs an approved target-specific Web connection;
the installer does not create its credential or silently repoint a shared connection.

After setup, confirm runtime enablement, open the printed Foundry project, select
the exact validation agent version, attach the exported image and paste the
prepared message. The handoff is saved in `.local\installer\<environment>\handoff.txt`.
Installation is **not end-to-end proof**: connected runtime acceptance remains
separate from Azure preparation. See [deployment details](docs/DEPLOY-TO-AZURE.md)
and the [owner-only steps](docs/MANUAL_STEPS.md#playground-flow-deployment-checklist).

### Advanced: portal deployment

The portal template includes the Azure foundation for the
[playground-to-Blob flow](docs/HEADLESS-FLOW.md#selected-playground-to-blob-flow).
**ARM, publication and manual Fabric setup are separate required phases.**
The public button loads `main`; use the matching published source revision
for the publication command. It cannot deploy uncommitted code.
Strict all-managed-identity operation is not currently supported by the selected
Fabric callback/projector and Azure Files hosting configuration. Review the
[manual checklist and identity limitations](docs/MANUAL_STEPS.md#playground-flow-deployment-checklist)
before provisioning; no credential exception is automatically approved.

[![Deploy to Azure](https://aka.ms/deploytoazurebutton)](https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2Fkatarinasvedman-ms%2Fclose-out-demo%2Fmain%2Finfra%2Ffresh%2Fazuredeploy.json)

Select your subscription/resource group and enter a Foundry name, the prepared
Entra API registration client ID, exact Fabric dispatch principal object ID,
and existing Eventstream namespace/entity. No existing Foundry project is needed;
Fabric remains external. The template creates Foundry/IQ resources, the publisher
Function and direct coordinator Logic App, their identities and Azure permissions.
WS1/EP1 hosting incurs charges. Copy `publishDefinitionsCommand` from deployment
**Outputs**, or run from the same source revision:

```powershell
uv sync --frozen --extra agents --extra flow --python 3.11
uv run --frozen --extra agents --extra flow python -m agents.bootstrap --resource-group "<resource-group>" --deployment "<deployment-name>" --create-fabric-connection --publish-flow
```

Publication requires Azure CLI, uv/Python 3.11 and PowerShell 7 (`pwsh`).
It registers the validation agent with its target-specific
`trigger_fabric_eventstream` OpenAPI tool and publication instructions, retains
native Fabric/document IQ on the coordinator, pins the actual returned versions
and publishes the runtime/workflow ZIPs. Both workflows remain **Disabled** until
the checklist's grants, documents, external bindings and coordinator enablement
are complete. The separate Blob-validation workflow stays disabled for this path.

The command defaults to **native Fabric IQ** and prompts in an interactive
terminal for the existing external Fabric target. For automation, supply
`--fabric-config "<fabric-settings.json>"`; see the six-field example in
[deployment details](docs/DEPLOY-TO-AZURE.md#external-fabric-configuration).
It validates a **Project Managed Identity** Fabric connection in the selected
Foundry project, using audience `https://api.fabric.microsoft.com`. The
command explicitly authorizes creating an absent connection; omit
`--create-fabric-connection` to require an existing connection instead.
It never overwrites a mismatching existing connection or deploys Fabric.
Use `--facts-mode stub` explicitly, without the connection-creation flag, if you
want the local synthetic-facts client instead. Native configuration never
silently falls back to it.

**No document upload, generated assets or inference is required for publication.**
Document loading, indexing checks and local-client smoke tests are
optional: rerun with `--seed-and-verify` when Storage access is available.
[Deployment details](docs/DEPLOY-TO-AZURE.md) explain the two phases and receipts.
The button alone still does not publish agents or knowledge-base definitions.
No hosted agent, application container or registry is part of this solution.
This is not the full planned Azure/Fabric/Power Platform solution.
The existing development [Blob/Activator headless flow](docs/HEADLESS-FLOW.md) reuses the existing
Fabric IQ tool, with Logic Apps invoking Foundry using managed identity.
Pinned upload contracts, Blob adapters/outbox, workflow/Function definitions and
optional flow Bicep are deployed to the selected test target. An actual Blob
upload triggered Logic Apps and managed-identity Foundry validation. After the
approved sender Contributor grant, the cached outbox published all seven events
and their ingestion was verified without repeating inference. Validation v4
rejects both original photos and accepts replacements using actual Blob bytes.
A Fabric-owned scheduled notebook now projects events into immutable readiness
snapshots; original gaps, open rework, Ready fallback and NotReady precedence
were verified. After Fabric rejected WorkspaceIdentity, the user authorized a
dedicated app-only callback credential, now protected in Key Vault and Fabric.
An actual authenticated Fabric Web activity reached the Logic Apps Request
trigger; anonymous access is denied. The Data Agent additively exposes live
snapshots. After changing its Foundry connection to Project Managed Identity
and granting the project identity Read/database Viewer, the native Fabric call
succeeded. The old coordinator Function subsequently rejected provenance.
The replacement coordinator workflow calls Foundry directly, waits for its
background response and saves unmodified completed response JSON to Blob.
Its deployed definition and health are verified, but a live invocation of the
replacement and a fresh unattended proof have not been performed.
The template includes that Azure hosting foundation; it adds no
Azure Event Hubs service. Fresh-target runtime acceptance remains unverified.
**The button requires the template and code to be published to `main`; local
unpublished changes are not deployable through the link.**

## Local setup

Prerequisites: Python 3.11 (uv can obtain it), uv, Node.js 22.12 or newer within Node 22, and npm.

Run from the workspace root in PowerShell:

```powershell
uv sync --frozen --extra dev --python 3.11
npm ci
```

`uv.lock` and `package-lock.json` are the reproducible dependency sources. The local `.venv` and `node_modules` directories are ignored.

## Checks

```powershell
uv run --frozen --extra dev ruff check .
uv run --frozen --extra dev ruff format --check .
uv run --frozen --extra dev --extra agents --extra flow pytest -m "not integration"
uv run --frozen --extra dev python -m shared.contract_validation
npm run contracts:check
npm run lint
npm run typecheck
npm test
npm run build
```

Single tests:

```powershell
uv run --frozen --extra dev pytest tests\contracts\test_contract_validation.py::test_ready_signal_cannot_contain_unmet_conditions
npm test --workspace @close-out/presenter -- tests/contracts.test.ts -t "rejects Ready"
```

The Python `integration` marker is reserved for tests requiring real dev Dataverse and other explicitly configured services; none exist yet. Current checks are offline and do not certify cloud integration.

## Real Foundry agents

The supplied project contains two active, service-managed prompt agents:

| Agent | Implementation |
|---|---|
| `close-out-validation` | Inspects attached PNG/PDF bytes against scoped checklist/RFDS inputs; produces contract-validated ValidationResult records. |
| `close-out-coordinator` | Uses read-only facts/documents to draft cited GapAnalysis and proposed rework; never approves or changes records. |

Both use `close-out-gpt-4o`: GPT-4o `2024-11-20`, regional **Standard**, capacity 30, in **Sweden Central**, with `Microsoft.DefaultV2` filtering and automatic model upgrades disabled. No global/data-zone deployment was created. [Foundry IQ](docs/FOUNDRY-IQ.md) uses one shared knowledge base and one native **Azure Blob knowledge source**, with France Central Search managing ingestion from private Sweden Central Blob Storage. **Low retrieval reasoning** reuses the existing regional model through managed identity, with site/version-aware retrieval instructions and extractive output. Both sites use coordinator version **9** and the same managed-identity MCP connection; validation remains version **1**. This synthetic operator demo does not enforce per-user document ACLs.

The stored definitions and response schemas are built from `src\agents\definitions.py` and versioned prompt files. `agent-framework-foundry` connects to pinned Foundry prompt-agent versions. The default local configuration retains coordinator version **9** with a client-executed facts stub. The optional native Fabric test binding replaces that function with the authenticated Fabric IQ tool while preserving document IQ; no hosted wrapper is used. See [external Fabric testing](docs/FOUNDRY-IQ.md#existing-fabric-for-local-verification) for registration, explicit site/program/scenario prompts and the three verification commands. Native versions can retrieve both sources without local function execution; deterministic result verification still runs in the CLI.

For local use, an authenticated identity with project/model access is required. Registration uses the existing Azure CLI login; stub-mode invocation uses `DefaultAzureCredential`. Native Fabric invocation and its independent KQL verifier use the same Azure CLI user. These CLI commands do not initiate login or assign roles. Install the optional, locked SDK dependencies:

```powershell
uv sync --frozen --extra dev --extra agents --python 3.11
# Generate local evidence first if it does not exist:
uv run --frozen python -m shared.local_demo
# Actual model inference; incurs Azure token charges:
uv run --frozen --extra agents python -m agents validate --site SE-STO-0042 --evidence PHOTO-19 --evidence PHOTO-20
uv run --frozen --extra agents python -m agents gaps --site SE-STO-0042
uv run --frozen --extra agents python -m agents gaps --site SE-GOT-0017 --scenario fallback-ready
```

`.local\foundry\config.json` holds this machine's endpoint, deployment, actual model/version and pinned agent versions; it is ignored by Git. To register in a selected existing project, supply its configuration explicitly:

```powershell
uv run --frozen --extra agents python -m agents register --endpoint $env:AZURE_AI_PROJECT_ENDPOINT --deployment $env:AZURE_AI_MODEL_DEPLOYMENT --model gpt-4o --model-version 2024-11-20
```

Set those environment variables to your selected project endpoint and existing model deployment first. Add `--knowledge-config .local\foundry\knowledge.json` to retain live IQ bindings; omitting it deliberately registers the local-document mode. Names can be overridden with `--validation-name` and `--closeout-name`. Identical definitions/model versions reuse the existing version; changed definitions create immutable versions. Registration refuses to overwrite an unrelated agent. After changing a prompt/schema, register again before invoking it.

`infra\models.bicep` reproducibly deploys only a regional Standard model on an existing account. Required parameters are `accountName`, `deploymentName`, `modelName` and `modelVersion`; `capacity` defaults to 30. Compile with `az bicep build --file infra\models.bicep --stdout`. Use `az deployment group what-if` before `az deployment group create`, supplying the selected resource group/template/parameters. Verify the existing account is in Sweden Central; this template neither creates nor relocates it. `infra\fresh\main.bicep` adds a new account/project and native IQ infrastructure for the portal deployment, not the full Azure foundation.

The executable live smoke scenario checks rejected originals, accepted replacements, an RFDS PDF, a cited blocked-case rework draft and the Ready fallback:

```powershell
uv run --frozen --extra agents python -m agents smoke
# Offline boundary, provenance, scope and MCP regression checks:
uv run --frozen --extra dev pytest tests\unit\test_agents.py
```

Receipts, response IDs, token usage, file hashes, actual model/agent versions and the smoke report persist under ignored `.local\foundry\results`. The smoke command passes **actual photo validation results** to the real stdio MCP stub through a fixed, contract-validated local overlay. `agents gaps --validation-results <photo-results-json>` can reuse that overlay. Wrong-site/case evidence, forged human review, reused IDs and results older than fixture history are rejected. Overlay changes affect only a cloned fixture snapshot, not seed files or Dataverse. Without an overlay, the coordinator explicitly uses seeded validation facts.

Model inference is live; structured facts/readiness are a local MCP stub. IQ-bound coordinators retrieve indexed synthetic text counterparts from Blob through native Foundry IQ; without a knowledge binding they use hash-checked local text. Validation attaches actual image/PDF bytes. Content Understanding, approvals, package rendering and live business-system writes are **not connected**. The Ready result is an empty gap list, not a fabricated package manifest. Confidence is uncalibrated model self-assessment; below-threshold results become `NeedsReview`, never `Confirmed`. Synthetic smoke success is not a field-photo evaluation or a production quality gate. A broader generated Foundry evaluation suite is deferred.

## Run the offline demonstration

```powershell
uv run --frozen python -m shared.local_demo
```

This generates `.local\demo\report.json`, canonical/mock-system/Fabric JSON extracts, 203 labelled PNG schematics, 55 synthetic input PDFs with text counterparts, an asset/hash manifest and an isolated adversarial text fixture. Generated files and local event receipts are ignored by Git.

The replay demonstrates `SE-STO-0042`: **Blocked** by PHOTO-19, PHOTO-20 and PL-0042-01; still **Blocked** after accepted replacements while active-case rework is open; then **Ready** after that fixture rework is Closed. `SE-GOT-0017` remains the Ready fallback. Validation outcomes are seeded, not produced by an AI model.

The local readiness rule applies current photos/latest validation, Confirmed or sufficiently confident Auto review, program-specific mandatory evidence and thresholds (0.75/0.85), blocking tickets and active-case rework. It emits only Blocked/Ready transitions. Missing-condition IDs are checklist/requirement/ticket/work-order IDs; the accompanying photo gaps identify rejected photos.

The event sink persists schema-valid events in SQLite with atomic eventId deduplication. Re-running the same seed against the same directory adds no duplicate events. The readiness tracker reconstructs each local replay and writes its signals to a file; it does not deliver external notifications. Equal-time conflicting snapshots and competing replacement photos fail rather than guessing an order. This provisional ordering policy is not a verified live-platform guarantee.

After changing seed payloads, use a fresh output directory (for example `--output .local\demo-v2`); reusing an existing eventId with changed content is deliberately rejected rather than silently replacing a receipt.

```powershell
# Replay without regenerating evidence:
uv run --frozen python -m shared.local_demo --no-files
# Focused local-stub suite:
uv run --frozen --extra dev pytest tests\unit
# Single readiness boundary:
uv run --frozen --extra dev pytest tests\unit\test_seed_and_readiness.py::test_program_b_uses_its_own_threshold_and_extra_evidence
```

`seed\seed.json` is the master recipe for program requirements, site IDs, ID prefixes, photo slots and scenario values. `shared.seed` expands it into logical contract records and checks references and expected initial readiness. `seed\defects.yaml` is applied only to the Fabric extract: duplicate site, stale milestone, wrong GPS, orphan ticket and subcontractor spellings. Mock-system exports remain canonical. The adversarial document is not a normal artifact or indexed input.

Blob URLs use the reserved `.invalid` fixture host and are never fetched; generated URL paths map to local files. PDFs are synthetic input documents, not a compiled close-out package. Scenario changes are fixture edits, not approvals, governed actions, or Dataverse case transitions. There is no runtime Dataverse fake: the ETag fake lives only in `tests\conftest.py`.

## Local Data Agent MCP stub

```powershell
uv run --frozen python -m shared.stub_mcp --site SE-STO-0042 --site SE-GOT-0017
```

Configure that command as a **stdio MCP server**, not an HTTP endpoint. It exposes the provisional read-only `get_site_facts(site_id, scenario)` tool. It returns structured seed facts, requirements, current/superseded photos, validations, readiness and source references for the contract question scenarios. It does not run a language model or mimic an unverified Fabric tool response.

The launch-time `--site` allowlist cannot be expanded by a tool call. This is a local fixture boundary, not Entra authentication or production role enforcement. A scenario name selects synthetic data; it does not grant authorization. Starting the process from a terminal waits for an MCP client; normal stdout is reserved for the protocol.

`READINESS_SOURCE=stub`, `DATA_AGENT_MODE=stub`, and `EVENT_SINK=stub` are the offline defaults. Invalid values fail; selecting `fabric`, `live`, or `eventstream` fails explicitly because live adapters do not exist. The local report displays dependency modes and `dataverse: not-connected`. The Presenter is not yet wired to these stubs.

## Presenter skeleton

```powershell
npm run dev --workspace @close-out/presenter
```

Use the localhost URL printed by Vite. The page deliberately reports that no integrations are connected; no demo state or successful live behavior is fabricated.

## Contracts

`contracts\VERSION` is currently `0.1.0`, a review draft, not an approved release. `contracts\REVIEW-DECISIONS.md` is the focused review packet for the Power Platform, Fabric and Azure/Foundry track owners. It covers proposed field nullability, mappings, repeated transitions, closure authorization, review ordering and output storage.

The draft covers the shared objects/code lists, seven events, normalized Dataverse message, readiness signals, APIM OpenAPI, agent/package outputs and Data Agent questions. The raw Dataverse endpoint format, physical mappings, and real MCP tool schemas are explicitly unverified.

`contracts\examples\manifest.json` associates JSON examples (including JSON Pointer-selected objects) with root or named schemas. Validation checks formats, all named-definition example coverage, mapping coverage and self-contained OpenAPI without fetching remote schemas. These fixtures are not a synthetic seed database.

After changing a schema:

```powershell
npm run contracts:generate
npm run contracts:check
```

Never edit `src\shared\contracts\models.py`, `models.ts` or `openapi.json` directly. Python/TypeScript types are tolerant reader projections, not strict producer validators; use original schemas for producer validation. The Python boundary helper logs unknown codes and ignores extra fields while retaining structural checks. Generated models alone cannot enforce every schema/business constraint.

Compatibility checks compare against the latest released `contracts-vX.Y.Z` Git tag. Before the first approved release they explicitly report draft-only checking. A local baseline can be supplied with `uv run --frozen --extra dev python -m shared.contract_compatibility --baseline <contracts-directory>`. Structural checks enforce version/changelog changes conservatively; owner review remains necessary for semantic changes.

The user authorized local implementation against this provisional baseline without waiting for cross-track sign-off, then supplied a Foundry project and authorized needed model deployments. Owner decisions remain relevant before live cross-track integration or an approved release; CI does not substitute for approval, and no release tag is created automatically. See `docs\IMPLEMENTATION-GATES.md` for remaining external prerequisites.

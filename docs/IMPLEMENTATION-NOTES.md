# Implementation notes – Azure/Foundry track

> For GitHub Copilot. Read together with `docs/close-out-demo-definition-en.md` (the demo definition). Store this file as `docs/IMPLEMENTATION-NOTES.md` and reference both from `.github/copilot-instructions.md`.
> The definition says **what** we build and **who** owns what. This file says **how** we build the Azure/Foundry track: contracts, automation and the rules that keep it repeatable.

---

## Current execution mode

The guided Windows entry point is `scripts\deploy-demo.ps1 -Environment <name>`.
It prepares nonsecret target settings interactively and orchestrates the existing
fresh Bicep/bootstrap, synthetic document and pinned playground-evidence paths.
It deploys `infra\fresh\foundation.bicep` before application-registration prompts
or Fabric selections. Saved registrations are reused; dedicated secret-free
registrations are prepared with approval by default, with an optional existing
client-ID path. The full `infra\fresh\main.bicep` composes that same foundation
and adds the secured runtime without renaming resources or weakening authentication.
Foundation and full-runtime progress/pending deployments are tracked separately.
Foundation-only completion never claims that agents have been published.
Azure/Foundry publication and playground preparation precede Fabric grants and
binding changes. An explicit checkpoint prints both agent versions and allows
the operator to stop with the Azure setup retained and existing Fabric untouched.
Publication can pause the selected Azure workflows, but not the Fabric Activator.
It can update only explicitly selected compatible existing Fabric bindings and
schedules; it does not provision Fabric or create callback credentials/connections.
Ignored installer progress is separate from target-local bootstrap agent pins.
No-op resume avoids intentional runtime republication/disablement. Final
enablement is explicit. Connected fresh-target acceptance remains separate;
focused installer unit checks and owner-confirmed grants do not prove runtime access.
For the supported Key Vault networking policy, the installer can merge an
explicitly policy-owner-approved `SecurityControl=Ignore` tag onto its dedicated
resource group and retry deployment once. Approval is separate from cost/target
approval; no exception is inferred or extended to another group.
Fresh and resumed deployments check the knowledge and playground document Storage
accounts for networking drift. With the dedicated group's approved
`SecurityControl=Ignore` tag present, setup restores their declared public endpoints
and verifies the result, without enabling anonymous Blob or shared-key access.
Without that tag, disabled networking pauses setup for the policy owner.

The selected reduced flow now starts in the validation playground: its publish-only
OpenAPI tool sends an assessment through the publisher Function to existing Fabric;
Fabric dispatch calls Logic Apps, which directly invokes the coordinator, waits,
and saves its raw completed response to Blob. No coordinator Function participates.
The fresh template includes Azure hosting; its bootstrap output
publishes both versioned prompt definitions, target-specific validation OpenAPI
and ZIPs. Publication leaves workflows Disabled. Fabric sharing, callback,
projector/document readiness and enablement are explicit manual steps in
`MANUAL_STEPS.md`. The public button and bootstrap must use the same published
revision; fresh-target acceptance is not claimed. Fabric callback credentials,
delegated notebook execution and Azure
Files host keys mean the current implementation is not all-managed-identity.
The following paragraphs retain earlier implementation history; see
`HEADLESS-FLOW.md` for the selected path.

The user authorized implementation against provisional v0.1.0 contracts without waiting for cross-track sign-off; full end-to-end integration may not be needed. Local seed expansion, synthetic evidence, readiness/event replay and a scoped stdio Data Agent MCP stub now run through `uv run --frozen python -m shared.local_demo`. See README for the implemented commands and limitations. The architecture and build order below remain targets for live delivery, not a claim that those components exist.

The current deployment publishes Foundry prompt agents and native IQ, not a hosted
agent or application container. The default local client supplies stub facts.
An explicit native Fabric IQ test configuration instead uses the existing external
Data Agent, with a separate KQL snapshot for deterministic output verification.
Fabric is not part of deployment; its manually refreshed test fixtures use seeded
validation outcomes, not production event/readiness or Dataverse integration.
See `FOUNDRY-IQ.md` for the verified three-case local test path.
The portal template and completion command are described in `DEPLOY-TO-AZURE.md`.
The broader azd project/workflows below remain implementation targets.

The reduced headless test increment has local draft v0.2.0 evidence/callback
contracts, Blob upload/validation and coordinator adapters, durable outbox,
Standard workflow definitions, ZIP packaging and opt-in `infra/flow` Bicep.
It omits Event Grid, Service Bus, Power Platform and UI; the Event Hubs SDK sends
directly to Fabric Eventstream's compatible endpoint, not a new Event Hubs service.
The authorized optional foundation and workflow/Function code are deployed after
the user applied the selected group's security exception and host-secret identity
ordering was repaired. A real Blob upload triggered managed-identity Foundry
validation, caching inference/outbox across retries. The user-approved sender
Contributor grant resolved Eventstream authorization; the same inference's seven
events were published and their actual ingestion verified. Validation v4 fixes
the observed azimuth tolerance failure using actual numeric observations and an
RFDS policy check. A Fabric-owned scheduled notebook now reads registrations
and events and persists immutable readiness history. The main Blocked-to-Ready,
open-rework, fallback and NotReady paths were verified through actual scheduled
jobs. After the callback connector rejected WorkspaceIdentity, the user
authorized a dedicated app-only credential, now stored in Key Vault/Fabric.
Actual Fabric Web activity authentication and anonymous denial are verified.
Activator/dispatch stays stopped pending workflow-trigger delivery, live native
Data Agent exposure and coordinator acceptance. Existing MI/IQ bindings are
unchanged. See `HEADLESS-FLOW.md` for schedule limits,
identity distinctions and the exact remaining gates.

Unresolved physical mappings, platform payloads, approval provenance and MCP authentication remain isolated from the local stubs. Revisit them before connecting live services. This execution choice does not authorize cloud provisioning or waive the process decision rights.

---

## 1. Scope of this repository

This repo contains the **Azure/Foundry track** only. It integrates with two tracks built elsewhere:

| Track | Built in | Integrates via |
|---|---|---|
| Power Platform | Power Platform solution | Dataverse tables, Dataverse → Service Bus, approval flows, custom connector to API Management |
| Fabric | Fabric workspace (Git-connected) | Eventstream custom endpoint, Activator, Data Agent (MCP), OneLake shortcut to Blob, Link to Fabric |

**In scope here**
- Blob Storage for synthetic photos and documents, and the upload API
- Event Grid, Service Bus
- Logic Apps Standard workflows (short, idempotent runs per event)
- Foundry project, model deployments, agents (validation, close-out, site assistant), Content Understanding, Foundry IQ
- API Management: business actions, policies, MCP server, custom connector definition
- Mock systems of record: Sitetracker and ticketing
- Presenter console (web app)
- Observability, evaluations, cost tagging
- Contracts, seed data, stubs and test harness for the other tracks
- All infrastructure as code and all deployments through GitHub Actions

**Out of scope here** (but we provide contracts and stubs for them): Dataverse schema and flows, Power Apps, Power Pages, Fabric items, ontology, Activator rules.

---

## 2. Non-negotiable principles

1. **Everything is automated and repeatable.** No portal clicks in any environment except where a platform has no API; those steps are scripted or documented in `docs/MANUAL_STEPS.md` with a script to verify them.
2. **Tenant-agnostic.** The solution will be consolidated into one tenant later. No tenant ID, subscription ID, object ID, domain, endpoint or resource name is hardcoded. Everything comes from parameters, environment files or outputs.
3. **One command per environment.** `azd up` (or the deploy workflow) creates a complete working environment from an empty subscription, given the parameters and prerequisites in section 7.
4. **Idempotent deployments.** Running the same deployment twice changes nothing.
5. **Contracts first, and contracts change.** All cross-track interfaces live in `contracts/`, are versioned, generate code, and are tested in CI (section 4).
6. **Every component runs in two modes:** `live` (real cloud services) and `stub` (local or in-cloud stubs for the other tracks). The demo can fall back to stubs.
7. **Synthetic data only.** Sweden Central, with a temporary approved exception for Azure AI Search in France Central because Sweden Central has no capacity. Other components remain in Sweden Central.
8. **Do not invent platform APIs.** If unsure whether a feature exists, is GA or works as assumed, add `TODO(verify)` and list it in the PR description.

---

## 3. Repository structure

```
/
├── .github/
│   ├── copilot-instructions.md
│   ├── CODEOWNERS                    # contracts/ requires review from all track owners
│   └── workflows/
│       ├── ci.yml                    # lint, tests, contract tests, bicep build + what-if, evals
│       ├── deploy.yml                # OIDC → azd provision + deploy per environment
│       ├── contracts.yml             # validate schemas, regenerate types, check breaking changes
│       ├── seed.yml                  # load seed data into an environment (manual trigger)
│       └── teardown.yml              # delete an environment (manual trigger, protected)
├── azure.yaml                        # azd project
├── infra/
│   ├── main.bicep                    # subscription scope: resource group + modules
│   ├── main.parameters.json          # azd parameter mapping from environment variables
│   ├── modules/                      # one module per service
│   └── abbreviations.json            # naming
├── contracts/
│   ├── VERSION                       # semver of the contract set
│   ├── CHANGELOG.md
│   ├── codelists/*.yaml              # enumerations (section 4.2)
│   ├── events/*.schema.json          # Eventstream event schemas
│   ├── messages/*.schema.json        # Service Bus messages from Dataverse
│   ├── dataverse/*.schema.json       # tables/columns we read and write
│   ├── apim/openapi.yaml             # business actions incl. get-site
│   ├── activator/*.schema.json       # payloads Activator sends (for reference/stubs)
│   ├── mcp/data-agent-tests.yaml     # test questions + expected answers for the Data Agent
│   └── examples/                     # one valid example per schema
├── seed/
│   ├── seed.json                     # single source of truth for synthetic master data
│   ├── defects.yaml                  # deliberate defects, applied only for the Fabric extract
│   ├── generate_files.py             # creates synthetic photos and documents
│   └── load_*.py                     # load into mock SoR, Blob, (optionally) Dataverse
├── src/
│   ├── api/                          # upload API, get-site, presenter API (Functions)
│   ├── logicapps/                    # Logic Apps Standard project (workflows as JSON)
│   ├── agents/                       # Agent Framework agents + prompts + tool definitions
│   ├── mock_sor/                     # mock Sitetracker + ticketing (Functions)
│   ├── presenter/                    # Presenter console (static web app)
│   └── shared/                       # generated contract types, telemetry, idempotency
├── stubs/
│   ├── data_agent_mcp/               # stub MCP server answering contracts/mcp tests
│   ├── activator/                    # script that emits readiness signals like Activator
│   ├── dataverse/                    # local fake for unit tests only
│   └── eventstream_replay/           # replay example events to Eventstream
├── evals/                            # evaluation datasets and thresholds per agent
├── scripts/
│   ├── bootstrap-oidc.sh             # one-time: app registration + federated credential for GitHub
│   ├── verify-environment.py         # end-to-end health and timing check
│   └── postprovision.*               # azd hooks
└── docs/
    ├── close-out-demo-definition-en.md
    ├── IMPLEMENTATION-NOTES.md
    ├── MANUAL_STEPS.md
    └── status-labels.yaml            # GA / Preview / Custom per component (single source)
```

---

## 4. Contracts – built for change

The contract **will change a lot** during implementation. Design for that.

### 4.1 Rules
- `contracts/` is the **single source of truth** for every cross-track interface: names, codes, schemas, payloads, test questions.
- **Semantic versioning** in `contracts/VERSION`:
  - *Patch:* descriptions, examples.
  - *Minor:* new optional fields, new event types, new code values.
  - *Major:* renamed or removed fields, changed meaning, new required fields.
- Every change updates `CHANGELOG.md` with what changed, why and which track is affected.
- `CODEOWNERS`: changes under `contracts/` require approval from all three track owners (configure the appropriate owner teams; `TODO` until known).
- **Generate code from contracts.** Python (Pydantic) and TypeScript types are generated into `src/shared/contracts/` by `contracts.yml`. Never hand-edit generated files.
- **Tolerant reader.** Consumers ignore unknown fields and accept unknown enum values by logging them, not by crashing.
- **Envelope on every event and message:**
  ```json
  {
    "schemaVersion": "1.2.0",
    "eventType": "ValidationResultCreated",
    "eventId": "uuid",
    "caseId": "uuid | null",
    "siteId": "string",
    "occurredAt": "ISO 8601",
    "producer": "azure.validation | azure.mock-sor | ...",
    "data": { }
  }
  ```
  `eventId` is used for deduplication; `caseId` for correlation.
- **Breaking-change check in CI:** `contracts.yml` compares schemas with the last tagged version and fails on a breaking change without a major version bump.
- **Contract tests:** every producer validates its output against the schema in unit tests; every consumer is tested against `contracts/examples/`.
- **Mapping layer:** code never uses raw contract payloads deep inside business logic. Map to internal models at the edge so contract changes touch one place.

### 4.2 Initial contract content (v0.1.0, derived from the definition, section 4)
- **Code lists:** artifactType, checklistItemCode, milestoneType, workOrderType, workOrder status, ticketType, ticket status, artifact status, reviewStatus, readiness, close-out status and status reasons, actorType.
- **Events to Eventstream (produced by this repo):** `ValidationResultCreated`, `ValidationResultReviewed`, `PhotoUploaded`, `ArtifactUploaded`, `WorkOrderChanged`, `TicketChanged`, `MilestoneChanged`.
- **Readiness signal (produced by Activator, consumed by Power Automate):** reference schema only, used by the Activator stub.
- **Service Bus messages from Dataverse:** close-out record change with `caseId`, `siteId`, `status`, `statusReason`, `rowVersion`. `TODO(verify)` the exact message format of the Dataverse service endpoint and map it in one adapter.
- **Dataverse columns** read and written by Logic Apps (CloseOutCase, Photo, AcceptanceArtifact, ValidationResult, DecisionLogEntry). Logical names `TODO` until the Power Platform owner publishes them; use a mapping file `contracts/dataverse/mapping.yaml` so names change in one place.
- **API Management OpenAPI:** `get-site`, `create-rework-work-order`, `close-rework-work-order`, `close-punch-list-item`, `submit-closeout`, optional `record-pac`. Headers: `Idempotency-Key`, `x-case-id`, `If-Match` / `expectedVersion`.
- **Data Agent test questions:** 10–15 questions with expected facts, e.g. "Why is SE-STO-0042 blocked?" → PHOTO-19, PHOTO-20, PL-0042-01.

### 4.3 Seed data
- `seed/seed.json` is the **only** source of synthetic master data and IDs (projects, requirements, checklist items, sites, milestones, subcontractors, work orders, tickets, photos, documents).
- The Fabric extract is generated from it **with** `defects.yaml` applied. The mock SoR is loaded **without** defects. Photo and document files are generated from it.
- Seed changes follow the same review rule as contracts.

---

## 5. Infrastructure as code

### 5.1 Tooling
- **Bicep** for all Azure resources, organised as modules, orchestrated by **azd** (`azure.yaml`).
- **GitHub Actions** with **OIDC** (federated credentials, no secrets in GitHub) for all deployments.
- Use **Azure Verified Modules** where they fit; otherwise local modules.

### 5.2 Resources (all in Bicep)
Resource group, Log Analytics, Application Insights, Key Vault, Storage (ADLS Gen2, hierarchical namespace on; containers `photos`, `documents`, `packages`), Event Grid system topic + subscriptions, Service Bus namespace + queues/topics, Function apps (API, mock SoR), Logic Apps Standard (plan, app, storage), API Management (APIs, products, policies, named values, backends, MCP server where supported), Foundry account and project, model deployments, Content Understanding, AI Search (Foundry IQ), Static Web App (Presenter console), managed identities and **all role assignments**, diagnostic settings, budget and cost tags.

### 5.3 Rules
- **Naming** from `abbreviations.json` + environment name + a short unique suffix. No resource names hardcoded in code; code reads them from app settings populated by Bicep outputs.
- **Identity:** managed identities everywhere; role assignments in Bicep with built-in role definition IDs. No keys or connection strings where an identity works. Anything that must be a secret goes to Key Vault and is referenced, never stored in app settings in clear text.
- **Parameters** per environment in azd environments (`.azure/<env>/.env`, not committed) and GitHub environment variables. Required inputs: location (default `swedencentral`), environment name, principal IDs for admins, Dataverse environment URL, Fabric workspace and Eventstream connection details, Teams/Power Platform values where needed.
- **Search region exception:** when infrastructure is implemented, give the Search module its own `searchLocation` parameter, supplied through `AZURE_SEARCH_LOCATION` with the temporary value `francecentral`. Keep the main location `swedencentral`; do not move Blob, Foundry or model deployments along with Search. Verify the cross-region indexing/retrieval, identity/network path, latency and any feature co-location constraints before deployment. This parameter is planned, not an existing azd setting.
- **Cross-track identities are parameters**, e.g. the Fabric workspace identity object ID (granted Storage Blob Data Reader) and the Dataverse application user. Never assume they exist in the same tenant as today.
- **App code is deployed by azd** (`azd deploy`): Functions, Logic Apps workflows (zip deploy of the Logic Apps project), Static Web App, agents (see 5.4).
- **APIM content as code:** OpenAPI from `contracts/apim/openapi.yaml`, policies from files in `infra/modules/apim/policies/`.
- **Logic Apps:** workflows are JSON files in `src/logicapps/`, connections parameterised (`connections.json` with app settings), managed identity for Service Bus, Storage, APIM and Dataverse where supported. `TODO(verify)` Dataverse connector authentication with managed identity in Logic Apps Standard; fall back to app registration with secret in Key Vault.
- **`what-if` in CI** on every PR touching `infra/`.
- **Teardown:** `azd down --purge` via `teardown.yml` (protected environment).

### 5.4 Foundry and agents
- Foundry account, project, model deployments and connections in Bicep.
- Agents (Agent Framework) are **code in `src/agents/`**, deployed by a script or azd service in the pipeline. Agent definitions, instructions, tool bindings (Data Agent MCP, Foundry IQ, APIM MCP) and model versions are versioned in Git. No portal-only agents.
- Model names and versions are parameters; record the model and version used in every decision log entry.
- Guardrails (content filters, Prompt Shields) configured as code. `TODO(verify)` which settings are Bicep-deployable versus SDK.
- Foundry IQ knowledge-source/base definitions as code; use the native Azure Blob source so IQ manages its generated data source, indexer, skillset and index. Do not manually edit generated ingestion resources. Shared operator-demo retrieval does not imply per-user ACL enforcement.

### 5.5 Things that may not be fully automatable
List each in `docs/MANUAL_STEPS.md` with: why it is manual, the exact steps, and a verification script. Expected candidates (all `TODO(verify)`):
- Entra app registrations and app roles (prefer the Microsoft Graph Bicep extension or a script in the pipeline).
- GitHub OIDC bootstrap (one-time, `scripts/bootstrap-oidc.sh`).
- Dataverse application user and security role for our managed identity or app registration (Power Platform admin; can be scripted with PAC CLI).
- Dataverse service endpoint to Service Bus (owned by Power Platform; we provide the Service Bus details as outputs).
- Fabric workspace identity and OneLake shortcut to our storage (owned by Fabric; we grant the role via a parameter).
- Teams connector consent in Power Automate (owned by Power Platform).

---

## 6. CI/CD

| Workflow | Trigger | Does |
|---|---|---|
| `ci.yml` | PR, push | Lint, unit tests, contract tests, generated types up to date, `bicep build`, `what-if` (if infra changed), agent evals against thresholds (gate) |
| `contracts.yml` | Change in `contracts/` | Validate schemas and examples, breaking-change check, regenerate types, require `VERSION` and `CHANGELOG` update |
| `deploy.yml` | Merge to `main` → `dev`; manual approval → `demo` | OIDC login, `azd provision`, `azd deploy`, post-deploy `verify-environment.py` |
| `seed.yml` | Manual | Generate files, load seed into mock SoR and Blob, optionally replay events |
| `teardown.yml` | Manual, protected | `azd down --purge` |

**Environments:** `dev` (continuous), `demo` (frozen 48 hours before the session). Later: the consolidated tenant is just another azd environment with its own parameters.

---

## 7. Prerequisites per environment (document, do not hardcode)

- Azure subscription and permission to create role assignments.
- GitHub environment with OIDC federated credential to that subscription.
- Region quota for the chosen models in Sweden Central.
- Dataverse environment URL and an application user for this solution.
- Fabric workspace ID, Eventstream custom endpoint details, workspace identity object ID.
- Teams/Power Platform values needed by approval flows (owned by Power Platform).

---

## 8. Stubs and the `stub` mode

Each integration point has a stub so this track can be built and demoed independently:

| Dependency | Stub | Switch |
|---|---|---|
| Fabric rule + Activator | `stubs/activator`: evaluates the close-out rule on our own events and calls the same Power Automate/Dataverse target, or updates the record directly in stub mode | `READINESS_SOURCE=fabric|stub` |
| Data Agent (MCP) | `stubs/data_agent_mcp`: MCP server answering from `seed.json` and the events | `DATA_AGENT_MODE=live|stub` |
| Eventstream | Events written to a local file / Event Hubs in stub mode | `EVENT_SINK=eventstream|stub` |
| Dataverse | Local fake for unit tests only; integration tests use the real dev environment | test configuration |

The Presenter console shows which mode each dependency runs in.

---

## 9. Robustness and traceability (applies to every run)

- **Idempotency key** `caseId` + transition on every Logic Apps run and every APIM business action; the mock SoR stores receipts.
- **Optimistic concurrency** on every Dataverse write (row version / ETag).
- **Retries** with exponential backoff inside a run; on final failure write `ActionFailed` to the record and log it.
- **No run holds state**; any run can be restarted from the record.
- **Correlation:** `caseId` as OpenTelemetry baggage on every outbound call, in every event envelope, every APIM call (`x-case-id`) and every decision log entry. Every decision log entry carries the trace ID.
- **Latency target:** event → status on the Dataverse record < 10 seconds; measured by `verify-environment.py`.

---

## 10. Build order

| Milestone | Content | Done when |
|---|---|---|
| M0 | Repo skeleton, azd, Bicep for core resources, OIDC, CI, `contracts/` v0.1.0, `seed.json` | `azd up` creates an empty working environment; CI green |
| M1 | Upload API → Blob → Event Grid → Logic Apps → validation agent → ValidationResult to Dataverse + Eventstream | A photo upload produces a valid event in Eventstream and a row in Dataverse |
| M2 | Mock SoR + APIM business actions + Service Bus trigger from Dataverse → Logic Apps | A status change on the record executes a business action idempotently |
| M3 | Close-out agent with Data Agent (stub then live) and Foundry IQ; package generation | Gap list and package with citations written to the record |
| M4 | Presenter console, decision log view, traces, cost, evals as CI gate | End-to-end run visible for one `caseId` |
| M5 | Governance moments: 403 per role, injection blocked, Purview Audit | All three demonstrable on demand |
| M6 | Hardening: `verify-environment.py`, timings, reset, fallback site, teardown and redeploy from scratch | Fresh environment from zero passes verification |

---

## 11. Conventions for Copilot

- Python 3.11 (Functions, agents, scripts), TypeScript for the Presenter console. Ruff, pytest, ESLint, Vitest.
- Small PRs, one milestone item each; every PR states which contract version it implements.
- Never hardcode IDs, names, URLs, tenant or subscription values.
- Never edit generated contract types; change the schema and regenerate.
- When a contract is unclear or missing, propose the change as a PR to `contracts/` first, then implement.
- Keep `docs/status-labels.yaml` updated when a platform component is added (GA / Preview / Custom).
- `TODO(verify)` instead of guessing.

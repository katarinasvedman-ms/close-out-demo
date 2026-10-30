# Repository guidance

## Authoritative documents and current state

Read these together before implementing changes:
- `docs/close-out-demo-definition-en.md` defines the process, shared data model, decision rights, status transitions, and cross-track interfaces.
- `docs/IMPLEMENTATION-NOTES.md` defines the Azure/Foundry implementation, contracts, automation, and delivery milestones.

The repository contains provisional contracts/generated types, deterministic seed/evidence, offline readiness/event replay, a scoped stdio Data Agent MCP stub, real Foundry validation/close-out prompt agents, Python tests and a fixture-backed React/Vite workbench with an optional separate read-only Foundry assistant. Run its loopback API with `python -m agents.workbench`; case cards are local seed fixtures, not live process state. Publish the separate assistant with `python -m agents.assistant --endpoint <project>`; native chat requires active external Fabric and both IQ receipts, with no silent seed fallback. Model, existing-project Blob-backed Foundry IQ and fresh-project portal Bicep entry points exist. The solution excludes hosted agents, application containers and registries. The default client executes synthetic-facts tools; optional native Fabric IQ retrieves external test fixtures with a separate KQL snapshot verifier. Fabric is never part of deployment. Upload APIs, governed actions, package rendering, the full Azure foundation and production cross-track flows are not implemented. The repository is `https://github.com/katarinasvedman-ms/close-out-demo.git`. See README, `docs/DEPLOY-TO-AZURE.md`, `docs/FOUNDRY-IQ.md` and implementation gates.

## Build, test, lint, and deployment

Use Python 3.11 through uv and Node.js 22.12+ within Node 22. From the workspace root:
- Install locked dependencies: `uv sync --frozen --extra dev --python 3.11` and `npm ci`.
- Python lint/format: `uv run --frozen --extra dev ruff check .` and `uv run --frozen --extra dev ruff format --check .`.
- Python suite: `uv run --frozen --extra dev pytest -m "not integration"`.
- Single Python test: `uv run --frozen --extra dev pytest tests\contracts\test_contract_validation.py::test_ready_signal_cannot_contain_unmet_conditions`.
- Contract schema/example validation: `uv run --frozen --extra dev python -m shared.contract_validation`.
- Regenerate types/OpenAPI: `npm run contracts:generate`. Validate examples, freshness and release compatibility: `npm run contracts:check`.
- Run the offline seed/stub demo: `uv run --frozen python -m shared.local_demo`; outputs and the durable event ledger are under ignored `.local/demo/`. `--no-files` replays without regenerating evidence.
- Local seed/stub suite: `uv run --frozen --extra dev pytest tests\unit`. Single boundary: `uv run --frozen --extra dev pytest tests\unit\test_seed_and_readiness.py::test_program_b_uses_its_own_threshold_and_extra_evidence`.
- Stdio Data Agent stub: `uv run --frozen python -m shared.stub_mcp --site SE-STO-0042`. Launch-time site allowlist is a local fixture scope, not production authentication.
- Optional Foundry dependencies: `uv sync --frozen --extra dev --extra agents --python 3.11`. Register agent versions with `python -m agents register` using explicit endpoint/deployment/model/version arguments; invoke through `uv run --frozen --extra agents python -m agents`.
- Live synthetic agent smoke: `uv run --frozen --extra agents python -m agents smoke` (Azure token charges). Offline agent suite: `uv run --frozen --extra dev pytest tests\unit\test_agents.py`. Receipts/configuration are under ignored `.local/foundry/`.
- External Fabric local tests: register with both `--knowledge-config` and `--fabric-config`, writing a separate pinned config. Use `gaps --config <native-config> --site <site> --program <program> --scenario <scenario>`. Native mode has no local function tools or validation overlays; it requires actual Fabric and IQ receipts and checks the real KQL snapshot. Keep Fabric provisioning out of portal/Bicep/bootstrap; Azure project connection binding is allowed. `agents.fabric_setup --inspect-only` reads inventory/table metadata and backs up definitions without publishing. See `docs/FOUNDRY-IQ.md`.
- Model-only Bicep compile: `az bicep build --file infra\models.bicep --stdout`; review a group what-if before an authorized deployment.
- Fresh portal deployment: `infra\fresh\main.bicep` includes Foundry/IQ and the playground flow hosts. It requires prepared Entra API/Fabric callback/Eventstream bindings. Compile `infra\fresh\azuredeploy.json` with Bicep 0.39.26. The output command runs `agents.bootstrap --create-fabric-connection --publish-flow`: native project-MI Fabric connection, both prompt agents, target-specific validation OpenAPI and locked ZIPs, without inference. PowerShell 7 is needed for packaging. Actual agent versions replace pending-publication settings; both workflows stay Disabled until the manual checklist is completed. Omit --publish-flow for definitions only; old foundation-only outputs cannot deploy a runtime. No automatic Fabric role/item, secret fallback, policy exception or hosted agent. See docs/DEPLOY-TO-AZURE.md and docs/MANUAL_STEPS.md. Fresh-cloud acceptance and remote source publication are separate.
- Knowledge Bicep: `az bicep build --file infra\knowledge\main.bicep --stdout`. Follow `docs/FOUNDRY-IQ.md` and `register --knowledge-config` to retain live IQ. Use one shared native `azureBlob` source/base and one exact MCP connection for both sites. Low retrieval reasoning uses the existing regional model binding from infrastructure outputs and site/version guidance; keep extractive output and the Bicep-managed Search-to-model role. IQ owns the generated data source/indexer/skillset/index; do not edit those objects. Shared operator retrieval is not per-user ACL enforcement; case citations still require current-site/version provenance.
- Presenter checks/build: `npm run lint`, `npm run typecheck`, `npm test`, `npm run build`.
- Guided setup: `scripts\deploy-demo.ps1 -Environment <name>` also publishes the read-only assistant but never hosts the UI. KQL-backed Fabric MI/SPN grounding is unsupported; Fabric continuation/enablement requires explicit `-CoordinatorClientConfig` metadata and `-CoordinatorCredentialUri`. The target owner registers/authorizes the public client separately; opt-in resume validates secret ownership, requests secret-only rotation access, and packages the delegated readable coordinator. No automatic consent or fresh-target runtime proof. See docs/DEPLOY-TO-AZURE.md.
- Single Presenter test: `npm test --workspace @close-out/presenter -- tests/contracts.test.ts -t "rejects Ready"`.

The user authorized local implementation against provisional v0.1.0 without waiting for cross-track sign-off; full end-to-end integration may not be required. Do not block seed/stub development on external review. Resolve relevant `contracts/REVIEW-DECISIONS.md` items before live integration or an approved release. Checks do not prove owner sign-off; before the first release tag they explicitly report draft-only status. Do not create a release tag or silently invent physical mappings.

The specified stack is Python 3.11 for Functions, agents, and scripts, and TypeScript for the Presenter console. Ruff/pytest and ESLint/Vitest are configured. `infra/models.bicep` targets an existing account; the full Bicep/azd foundation is still planned.

The user supplied an existing Foundry project and authorized needed models and Blob-backed Foundry IQ in France Central. Hosted agents are excluded. The selected path is validation playground -> publish-only Function tool -> existing Fabric readiness/dispatch -> direct coordinator Logic App -> Foundry -> raw response Blob. No coordinator Function participates. Source publication uses native MI OpenAPI and conditional validation-publication instructions; the coordinator retains native Fabric/document IQ. Default bootstrap still validates ProjectManagedIdentity, which does not establish supported KQL-backed Data Agent authentication. The existing target has an explicitly approved dedicated public OAuth client, Key Vault-held renewable user credential, secret-scoped MI read/rotation and separate UserEntraToken coordinator connection; actual delegated callback grounding/capture is verified. Do not silently apply this exception to fresh targets. Fabric permissions remain explicit and external. Default local facts/stub verification remain separate. IQ documents are synthetic text counterparts, not OCR. The current callback uses an approved service-principal credential, the scheduled projector uses its owner's delegated tokens, and Azure Files hosting uses a Key Vault-held Storage key; never claim all-MI delivery or guaranteed week-long token validity. The validation agent receives actual bytes, never seeded outcomes. Do not claim approvals, packages, Dataverse writes, production Fabric or fresh-target proof from compilation.

The documented deployment interface, once the azd project and infrastructure exist, is:
- `azd up`: provision and deploy a complete environment.
- `azd provision` and `azd deploy`: separate provisioning and application deployment, also used by GitHub Actions.
- `azd down --purge`: destructive teardown, intended for a manually triggered, protected workflow.

Planned CI gates include lint, unit and contract tests, generated-type freshness, Bicep build, infrastructure what-if on infrastructure changes, and agent evaluation thresholds. Post-deployment verification must cover environment health and event-to-record latency below 10 seconds.

## Architecture and ownership

This repository implements only the Azure/Foundry track: upload and presenter APIs, Blob files, Event Grid, Service Bus, Logic Apps Standard, Foundry agents, Content Understanding, Foundry IQ/AI Search, API Management, mock Sitetracker/ticketing, the Presenter web console, infrastructure, and integration stubs.

Power Platform and Fabric are built elsewhere. Power Platform owns the Dataverse process instance, apps, status and approval flows, approval policy, reminders, and escalation. Fabric owns structured site data, ontology/semantic model, close-out readiness evaluation, Eventstream, Activator, and the Data Agent MCP interface. Provide contracts and stubs for those tracks rather than implementing their production responsibilities here.

The end-to-end paths are:
- Upload through API Management and Functions to Blob, creating Photo/AcceptanceArtifact metadata in Dataverse. Blob events trigger short Logic Apps runs and validation agents; results go to both Dataverse and Eventstream.
- Fabric evaluates readiness from events. Activator signals readiness transitions to Power Automate, which updates the Dataverse CloseOutCase.
- Dataverse record changes reach Logic Apps through Service Bus. Runs obtain facts from the Fabric Data Agent via MCP and document knowledge from Foundry IQ, then write drafts or packages back to the record.
- After approval, Logic Apps invokes governed business actions through API Management against mock systems of record, then writes results to Dataverse and Eventstream.

The Dataverse CloseOutCase is the process instance; workflows do not retain process state or wait for long-running approvals. Files belong in Blob, structured data and validation metadata in Fabric, and document knowledge in Foundry IQ. Fabric may reference blob URLs but must not store the photos or documents themselves. Eventstream is the fast path for rule evaluation; Link to Fabric serves history, decision logs, and KPIs.

## Process invariants

- Readiness is `NotReady` until ConstructionComplete is Actualized; otherwise it is `Blocked` until all mandatory program artefacts validate, no blocking punch-list tickets remain open, and all rework orders for the active case are Closed; then it is `Ready`.
- For photo requirements, use current photos and their latest validation results. A compliant photo counts only when reviewStatus is Confirmed, or Auto with confidence at least the program threshold. Do not treat every compliant result as accepted evidence.
- Fabric computes readiness; Dataverse displays it. Activator emits on transitions to Blocked or Ready, not on every evaluation.
- Preserve the single writer for each status transition in section 5 of the demo definition. Power Automate owns status/approval flows; Logic Apps owns its draft, business-action, and package-result transitions; Power Pages owns customer acceptance/return.
- Agents propose; people or approval policy decide. Construction Manager approves rework, Coordinator submits, and Customer PM accepts PAC.
- Every Dataverse write uses row version/ETag. On conflict, re-read and reconsider the transition.
- Use `caseId` + transition as the idempotency key for Logic Apps runs and APIM business actions; mock systems retain receipts. Deduplicate incoming events by `eventId`.
- Retry with exponential backoff within a run. On final business-action failure, record `ActionFailed` and a decision-log entry; recovery starts a new run from the record.
- Carry the same `caseId` through events, records, runs, OpenTelemetry baggage, APIM's `x-case-id`, and append-only decision logs. Decision entries include trace ID and the model name/version when used.

## Contracts and synthetic data

- Establish cross-track interfaces in `contracts/` before implementing consumers or producers. Preserve the shared object names and codes from the demo definition. If an interface is missing or unclear, propose the contract change first.
- The planned contract set uses `contracts/VERSION` and `contracts/CHANGELOG.md`: patch for descriptions/examples, minor for optional fields/event types/code values, major for removals, renames, semantic changes, or new required fields. Each change records why and which track is affected, and requires review from all three track owners.
- Generate Python Pydantic and TypeScript types into `src/shared/contracts/`; never edit generated types. Map external payloads to internal models at the boundary.
- Code lists are authoritative JSON Schemas under `contracts/codelists/`, referenced by the other schemas. Generated Python/TypeScript types are tolerant reader projections; producer validation uses the original strict schemas. `shared.contract_reading.read_model` logs unknown enum codes, preserves structural validation and ignores unknown model fields. Models alone do not enforce every JSON Schema condition or business invariant.
- Consumers ignore unknown fields and log unknown enum values rather than crashing. Producers validate outputs against schemas; consumers are tested against `contracts/examples/`.
- Every event/message envelope contains `schemaVersion`, `eventType`, `eventId`, `caseId` (nullable), `siteId`, `occurredAt`, `producer`, and `data`.
- Keep Dataverse logical-name mapping in `contracts/dataverse/mapping.yaml`; do not scatter provisional column names through application code.
- APIM action contracts include `Idempotency-Key`, `x-case-id`, and `If-Match`/`expectedVersion`.
- `seed/seed.json` is the master recipe for synthetic data/IDs. `shared.seed` expands profiles, ID prefixes and photo slots to contract-valid logical records. Fabric extracts alone receive `seed/defects.yaml`; canonical/mock-system data remains clean. `shared.seed_files` creates explicitly labelled PNG schematics and PDF/text inputs, not real photos, model outcomes or compiled packages. The adversarial fixture is isolated from normal artifact metadata. Live handoff of seed/contract changes still requires cross-track review.
- Preserve the demo evidence scenario: SE-STO-0042 is blocked by PHOTO-19, PHOTO-20, and PL-0042-01; SE-GOT-0017 is the Ready fallback. See the definition for replacement photos and per-program requirements/thresholds.

## Infrastructure, integrations, and delivery conventions

- Automate infrastructure with modular Bicep orchestrated by azd, preferring Azure Verified Modules where suitable. Deploy applications, workflow JSON, agents/prompts/tool bindings, search indexes, APIM OpenAPI/policies, identities, role assignments, diagnostics, and guardrails as code.
- Use GitHub Actions OIDC for deployments and managed identities where supported. Required secrets belong in Key Vault; never place them in clear-text app settings.
- Keep deployments tenant-agnostic and idempotent. Runtime IDs, domains, names, URLs, model versions, and cross-track identities come from parameters/environment configuration or infrastructure outputs, not hardcoded values. Resource naming follows the planned abbreviations + environment + unique suffix scheme.
- Use synthetic data and Sweden Central, except Azure AI Search: the user approved France Central (`francecentral`) temporarily because Sweden Central has no capacity. Knowledge Bicep has an independent `searchLocation` parameter; cross-region Blob text indexing and native retrieval have been verified. This is not a broader residency or feature approval. The dedicated development knowledge group required the explicitly approved `SecurityControl=Ignore` exception; preserve its flag on redeployment, never apply it to unrelated groups. azd environment files under `.azure/<env>/.env` are not committed.
- `READINESS_SOURCE=fabric|stub`, `DATA_AGENT_MODE=live|stub`, and `EVENT_SINK=eventstream|stub` are validated; offline defaults are stub. Live selections fail explicitly until implemented, never silently fall back. The CLI report displays modes; the workbench uses a separate labelled seed case view and optional native assistant, not a live process adapter. The Dataverse ETag fake exists only in `tests/conftest.py`; there is no deployable fake adapter or runtime switch. Integration tests require real dev Dataverse.
- Route all writes to systems of record through APIM, with schema, idempotency, concurrency, and role enforcement. Match access scopes across APIM, Dataverse, and Fabric: subcontractors see assigned work-order sites; customers see only their own programs and submitted packages.
- Use `TODO(verify)` for unverified APIs, feature availability, or authentication capabilities, and list uncertainties in the PR description. In particular, do not assume the Dataverse service-endpoint payload, logical column names, or Logic Apps managed-identity connector support is settled.
- Record unavoidable manual setup in `docs/MANUAL_STEPS.md` with the reason, exact steps, and a verification script. Maintain component availability labels in `docs/status-labels.yaml` when those components are added.
- Keep PRs scoped to one implementation milestone item and state the contract version implemented. Follow the build order in implementation notes section 10: skeleton/contracts, validation path, governed actions, close-out agent, presenter/observability, governance, then hardening.

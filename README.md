# Close-out demo - Azure/Foundry track

Start with `docs\close-out-demo-definition-en.md` and `docs\IMPLEMENTATION-NOTES.md`.

The implementation provides **real Foundry validation and close-out prompt agents**, a runnable offline seed/stub demonstration, a minimal React console, and provisional cross-track contracts with generated types. The agents run against synthetic local evidence and a scoped Data Agent MCP stub. Upload APIs, business actions, package compilation, a complete Azure foundation and live Dataverse/Fabric integrations are not implemented.

**Fabric contract review:** start with [`contracts/REVIEW-DECISIONS.md`](contracts/REVIEW-DECISIONS.md), then the referenced schemas and examples. This is a provisional v0.1.0 review baseline, not an approved release or a complete end-to-end integration.

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
uv run --frozen --extra dev pytest -m "not integration"
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

The supplied project now contains two active, service-managed prompt agents, both version 1:

| Agent | Implementation |
|---|---|
| `close-out-validation` | Inspects attached PNG/PDF bytes against scoped checklist/RFDS inputs; produces contract-validated ValidationResult records. |
| `close-out-coordinator` | Uses read-only facts/documents to draft cited GapAnalysis and proposed rework; never approves or changes records. |

Both use `close-out-gpt-4o`: GPT-4o `2024-11-20`, regional **Standard**, capacity 30, in **Sweden Central**, with `Microsoft.DefaultV2` filtering and automatic model upgrades disabled. No global/data-zone deployment or new container hosting was created. AI Search remains undeployed by this implementation; its approved location is France Central.

The stored definitions and response schemas are built from `src\agents\definitions.py` and versioned prompt files. `agent-framework-foundry` connects to pinned Foundry agent versions. The Python client supplies the coordinator's function implementations; only their declarations live in Foundry. Inspect the agents in the project's Agents view, but use this CLI to execute their local tools. This is not a deployed hosted Python application.

An authenticated Azure CLI identity with project/model access is required. No keys, interactive login or automatic role assignments are used. Install the optional, locked SDK dependencies:

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

Set those environment variables to your selected project endpoint and existing model deployment first. Names can be overridden with `--validation-name` and `--closeout-name`. Identical definitions/model versions reuse the existing version; changed definitions create immutable versions. Registration refuses to overwrite an unrelated agent. After changing a prompt/schema, register again before invoking it.

`infra\models.bicep` reproducibly deploys only a regional Standard model on an existing account. Required parameters are `accountName`, `deploymentName`, `modelName` and `modelVersion`; `capacity` defaults to 30. Compile with `az bicep build --file infra\models.bicep --stdout`. Use `az deployment group what-if` before `az deployment group create`, supplying the selected resource group/template/parameters. Verify the existing account is in Sweden Central; this template neither creates nor relocates it. A full azd environment is still a future milestone.

The executable live smoke scenario checks rejected originals, accepted replacements, an RFDS PDF, a cited blocked-case rework draft and the Ready fallback:

```powershell
uv run --frozen --extra agents python -m agents smoke
# Offline boundary, provenance, scope and MCP regression checks:
uv run --frozen --extra dev pytest tests\unit\test_agents.py
```

Receipts, response IDs, token usage, file hashes, actual model/agent versions and the smoke report persist under ignored `.local\foundry\results`. The smoke command passes **actual photo validation results** to the real stdio MCP stub through a fixed, contract-validated local overlay. `agents gaps --validation-results <photo-results-json>` can reuse that overlay. Wrong-site/case evidence, forged human review, reused IDs and results older than fixture history are rejected. Overlay changes affect only a cloned fixture snapshot, not seed files or Dataverse. Without an overlay, the coordinator explicitly uses seeded validation facts.

Model inference is live; structured facts/readiness are a local MCP stub; document retrieval is hash-checked local text, while validation attaches the actual image/PDF bytes. Content Understanding, Foundry IQ/Search, approvals, package rendering and live business-system writes are **not connected**. The Ready result is an empty gap list, not a fabricated package manifest. Confidence is uncalibrated model self-assessment; below-threshold results become `NeedsReview`, never `Confirmed`. Synthetic smoke success is not a field-photo evaluation or a production quality gate. A broader generated Foundry evaluation suite is deferred.

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

`contracts\VERSION` is currently `0.1.0`, a review draft, not an approved release. `contracts\REVIEW-DECISIONS.md` is the focused review packet for Henrik, Lamin and Katarina. It covers proposed field nullability, mappings, repeated transitions, closure authorization, review ordering and output storage.

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

# Contract review packet - v0.1.0 draft

The user authorized using this draft as a provisional baseline for local seed/stub implementation without waiting for cross-track review. It is not owner-approved for live integration or release. It covers the 14 shared objects, code lists, seven event types, normalized Dataverse changes, readiness signals, upload/read/business-action OpenAPI, cited agent/package outputs, and 12 Data Agent questions.

## Lamin: stage 1 Azure/Fabric review

**Goal:** agree the contracts for a headless integration between Azure/Foundry and Fabric. Scripts and CLI calls can exercise this stage; UI integration is not required.

| Direction | Stage 1 boundary |
|---|---|
| Azure -> Fabric | Ingest photo/artifact metadata, validation results and milestone/ticket/work-order changes. Fabric owns the structured model and readiness calculation. |
| Fabric -> Azure/Foundry | Return scoped facts, readiness and source references through the actual Data Agent interface so the close-out agent can explain gaps and propose rework. |

Real Foundry inference and local stubs exist. **Live Eventstream ingestion and the real Fabric Data Agent adapter are not connected yet.** This review agrees what those adapters must exchange; it is not a request to approve a finished integration.

### Review table

Lamin: replace `Pending` with **Accept**, **Change** or **Defer** and add a comment or issue/PR link. For Change, show the proposed field/payload or behavior. For Defer, identify the reason and whether it blocks stage 1. File links below are relative to this packet.

| Item | Files to review | What Lamin needs to confirm or propose | Decision / comment |
|---|---|---|---|
| L01 - Shared objects (D01 subset) | [models.schema.json](models.schema.json), [code lists](codelists/codes.schema.json) | Confirm IDs/joins for programs, requirements/checklists, sites, photos/artifacts, validations, milestones, tickets and work orders; required/null fields; status codes; opaque string versions; and how the active case is identified. Preserve source IDs/versions in evidence references. Fabric stores structured metadata and Blob references, not photo/document bytes. | Pending |
| L02 - Event payloads and ingestion | [event schemas](events/), [event examples](examples/events.json) | Confirm the common envelope and full-snapshot proposal, nullable caseId, and rejection of mismatched envelope/body identities. Note the reviewed-result wrapper described below. Identify the supported Eventstream ingestion protocol/endpoint, payload wrapping/batching and connection requirements; these are not established by the JSON schemas. | Pending |
| L03 - Duplicates, ordering and replacements (D06) | [models.schema.json](models.schema.json), [event schemas](events/) | Confirm eventId deduplication, how late events are ordered, and what resolves equal timestamps. Define current/superseded photo handling and latest-result selection, including a review whose validatedAt is unchanged. An older event must not roll back newer facts. Coordinate review-writer semantics with Henrik; do not infer them from the stub. | Pending |
| L04 - Readiness and missing conditions (D02) | [readiness signal](activator/readiness-signal.schema.json), [models.schema.json](models.schema.json) | Confirm the rule and scenarios below. readinessMissing contains checklistItemId/requirementId/ticketId/workOrderId values, not rejected photo IDs; photo IDs belong in supporting evidence. Confirm how the result and evaluation timestamp can be read back for freshness checks. | Pending |
| L05 - Actual Data Agent interface (D08) | [12 Data Agent cases](mcp/data-agent-tests.yaml) | Supply actual tool names, input/output schemas and sanitized request/response examples. Confirm facts, source IDs/versions and current/latest evidence needed for the questions can be returned. Identify any missing fields or response normalization needed; our local get_site_facts tool and scenario argument are fixture conventions, not a claimed Fabric API. | Pending |
| L06 - Authentication, scope and failures (D08) | [Data Agent cases](mcp/data-agent-tests.yaml), connection requirements from L02/L05 | Confirm supported transport/authentication and required identity/permissions for the Azure client; do not assume managed identity or app-only access is supported. Confirm site/program visibility, no evidence leakage on denied access, missing-data/error behavior, and query freshness. Share configuration requirements and sanitized examples, never credentials. | Pending |
| L07 - Seed and conformance (D01/D08) | [seed recipe](../seed/seed.json), [Fabric-only defects](../seed/defects.yaml), [Data Agent cases](mcp/data-agent-tests.yaml) | Confirm master IDs, program-specific requirements/thresholds and expected scenario facts/references. Distinguish clean canonical records from deliberately defective raw Fabric input. Review all 12 questions, not just the happy path. | Pending |

The event set is `PhotoUploaded`, `ArtifactUploaded`, `ValidationResultCreated`, `ValidationResultReviewed`, `TicketChanged`, `WorkOrderChanged` and `MilestoneChanged`. Every envelope includes `schemaVersion`, `eventType`, `eventId`, `caseId`, `siteId`, `occurredAt`, `producer` and `data`. `ValidationResultReviewed.data` contains `validationResult` plus `decisionEntryId`; it is not the same data shape as `ValidationResultCreated`. A real review-producing flow may be deferred, with reviewed-event behavior tested using clearly labelled fixtures instead.

### Readiness and joint acceptance checks

ConstructionComplete must be Actualized before readiness can be anything other than `NotReady`. After that, all mandatory program evidence must validate, no blocking ticket may remain open, and every rework order for the active case must be Closed.

For photos, use current photos and their latest validation results. A compliant photo counts only when reviewStatus is `Confirmed`, or `Auto` with confidence at least the program threshold. Confidence is on a 0-1 scale; the seed thresholds are 0.75 for program A and 0.85 for program B. Compliant-but-unaccepted evidence does not satisfy the requirement.

| Check | Expected outcome |
|---|---|
| Initial SE-STO-0042 | Blocked, with supporting references to PHOTO-19, PHOTO-20 and PL-0042-01. Missing-condition IDs identify the checklist items and ticket, not the photos. |
| Accepted replacements and closed blocking ticket, but active-case rework remains open | Still Blocked by the open rework. |
| All mandatory evidence accepted, blocking tickets closed and active-case rework Closed | Ready; current replacement evidence resolves to PHOTO-19b and PHOTO-20b. |
| SE-GOT-0017 fallback | Ready, without fabricated blockers. |
| Mandatory photo has only compliant Auto evidence below the program threshold | That evidence does not count as accepted. |
| ConstructionComplete is Planned | NotReady, even if other evidence is complete. |
| Duplicate or out-of-order events | No duplicate logical effects; older events do not replace newer facts under the agreed ordering rule. |
| Caller lacks site access | No unauthorized facts or evidence references returned. |
| Foundry coordinator uses real Fabric | Gap explanations cite resolvable Fabric source IDs/versions rather than silently using stub facts. |

The current Activator schema describes **Fabric -> Power Automate**, not an existing Azure callback. For stage 1, readiness can be checked through Fabric/Data Agent output, subject to L04/L05 confirmation. If push notifications into Azure are needed, agree that receiver's transport/authentication separately rather than silently repurposing the Power Automate contract. Activator emits transitions to Blocked or Ready, not every evaluation and not NotReady notifications.

Generate the integration data from the repository root with `uv run --frozen python -m shared.local_demo` after installing the locked Python dependencies described in README. The output includes `.local/demo/canonical.json` and `.local/demo/fabric-extract.json`; the latter deliberately contains the defects listed in defects.yaml. Use the clean data for the first shared happy path and the defective extract for separate cleansing tests. Defective raw rows are not valid producer-event examples. Contract examples illustrate alternative IDs/states and must not be loaded together as one master dataset.

### Expected handoff and exclusions

Return the completed review table in a PR, with proposed corrections and sanitized Eventstream/Data Agent examples plus endpoint/protocol, identity/permission and freshness requirements. Identify which cases are supported now, which need an adapter, and which are deferred. Keep live connection configuration outside committed examples.

**Not required for this stage:** UI wiring, physical Dataverse mappings (D03), business-action/approval execution (D04/D05/D09), Dataverse draft/package storage (D07), or package rendering (D10). These responsibilities are deferred, not reassigned to Fabric or agents. This section focuses on D01's shared-data subset, D02, D06 and D08; Lamin's review alone does not resolve other owners' decisions or approve a released contract baseline.

## Decisions to confirm

| ID | Owner | Proposed contract and required decision |
|---|---|---|
| D01 | Henrik + Lamin + Katarina | Confirm shared field requiredness/nullability and opaque string versions in `models.schema.json`. `close_out_package` is included because the definition uses it in CaseHasPackage even though section 4.2 omits it from the initial artifact code list. EvidenceRef and ApprovalDecision structures are proposals. |
| D02 | Lamin | Keep `readinessMissing` as checklistItemId/requirementId/ticketId/workOrderId strings, not rejected photo IDs. Confirm ID resolution and whether a typed object is needed instead. Activator signals only Blocked/Ready transitions; Dataverse stores but does not calculate readiness. |
| D03 | Henrik | Provide one sanitized real Dataverse service-endpoint message and confirm rowVersion extraction. `messages/close-out-case-changed.schema.json` is our normalized internal envelope, not the raw platform format. Fill the null logical/entity-set/column and choice mappings in `dataverse/mapping.yaml`; do not invent schema prefixes. |
| D04 | Henrik + Lamin + Katarina | Preserve caseId + transition idempotency. For repeated customer-return/rework cycles, propose a stable transitionOccurrenceId assigned by the transition writer, reused on retries; agree how occurrence and action identity qualify the key. It is optional in the draft and is NOT an assumed Dataverse column. Resolve before execution consumers are implemented. |
| D05 | Henrik + Katarina | Confirm who/policy authorizes closing rework and punch-list items, what triggers closure, and which trusted approval/evidence record the executor re-reads. A closing photo or request body alone is not authorization. Do not invent a new case status for these actions. |
| D06 | Henrik + Lamin | Confirm atomic replacement-photo currentness, latest-result ordering, concurrent uploads and review ordering. Which row/version orders a reviewed result when validatedAt is unchanged? Confirm whether review updates the same result or creates a new result and how Azure publishes ValidationResultReviewed with a decision reference. |
| D07 | Henrik | Specify storage for gap analysis, rework proposal, package manifest/files and citations. Confirm whether decision objects are JSON, related rows or mapped columns. These result locations are null in the mapping until agreed. Dataverse updates use If-Match, and conflicts require re-read/reconsideration. |
| D08 | Lamin | Review `mcp/data-agent-tests.yaml`: 12 questions, scenario states, expected facts and references. Publish actual MCP tool input/output schemas, transport/auth and access-scope behavior. The questions do not assume a platform tool name or JSON response shape. |
| D09 | Katarina + Henrik | Confirm APIM routes, error/status semantics, multipart upload deduplication and expectedVersion target (especially create-rework). Body expectedVersion is required for actions; optional If-Match must agree with it. x-case-id must match the body. Entra audiences/scopes/roles remain environment configuration, not hardcoded issuers. |
| D10 | Henrik + Katarina | Confirm DOCX/PDF rendition expectations and conversion/residency approach. PackageManifest lists Blob files and versioned evidence, not embedded documents. Manifest validity alone does not establish readiness or accepted evidence. |

## Review boundaries

- Producers validate against strict JSON Schemas. Generated Pydantic/TypeScript models are reader projections: unknown fields are ignored by Python models; enum fields accept future codes, and `shared.contract_reading.read_model` logs unknown codes while retaining structural validation. Types alone do not enforce every schema condition, approval, role or readiness rule.
- Code lists are JSON Schema rather than duplicated YAML lists so models and schemas can reference one authoritative set directly.
- Event `data` contains full logical snapshots in this proposal. Schema checks cannot compare duplicated envelope/body identities: adapters must reject mismatched caseId/siteId. Confirm snapshot versus delta semantics and event ordering with Lamin.
- Examples under `examples/` illustrate different scenario states. Placeholder IDs/URLs/models are synthetic interface fixtures, not a seed master, deployed endpoints, or simultaneous database contents.
- Required confirmation from all three owners is not inferred from passing checks. Owner GitHub handles and enforcement of all-three-owner approval still need configuration; CODEOWNERS alone is insufficient.

## Baseline and handoff

Run `npm run contracts:check`, Python/TypeScript suites, lint and typecheck. Local seed/stub work may proceed now. Review schemas, examples and the decision table before live adapters are connected; record resolutions here and in CHANGELOG and regenerate types after schema changes. Only after owner sign-off should an approved contract set be tagged `contracts-v0.1.0`. No full end-to-end integration is assumed as a prerequisite for the offline demo.

Later changes use VERSION/CHANGELOG: patch for descriptions/examples, minor for optional fields/events/codes, major for removals/renames/new required fields/semantic changes. The structural checker is deliberately conservative; humans must still identify semantic changes it cannot infer. No released tag currently exists, so CI explicitly reports draft-only compatibility rather than pretending a release baseline has been checked.

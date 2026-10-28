# Contract review packet - v0.1.0 draft

The user authorized using this draft as a provisional baseline for local seed/stub implementation without waiting for cross-track review. It is not owner-approved for live integration or release. It covers the 14 shared objects, code lists, seven event types, normalized Dataverse changes, readiness signals, upload/read/business-action OpenAPI, cited agent/package outputs, and 12 Data Agent questions.

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

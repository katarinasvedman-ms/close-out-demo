# Contract changelog

## 0.3.0 - Draft authenticated assessment publication

Added a publish-only OpenAPI tool for one registered synthetic photo assessment.
EvidenceBatchCompleted additionally permits `agent-submitted-assessment`;
existing v0.2.0 transport payloads and live-inference behavior remain valid.
This provenance identifies authenticated submitted assessments, not independently
verified model responses or proof that the playground attached the pinned bytes.
The publisher does not claim a model/version, approval or readiness decision.
Affected tracks: Azure/Foundry and the isolated external Fabric test projector.
Static fixtures and production status writers are unchanged. This draft is not
cross-track owner approval.

## 0.2.0 - Draft headless test transports

Added evidence-batch registrations with pinned Blob hashes/ETags, an
EvidenceBatchCompleted event listing all prerequisite event IDs, and a distinct
HeadlessReadiness callback carrying an immutable facts revision and transition
identity. These additive synthetic-test interfaces affect Azure/Foundry and
external Fabric test adapters. They do not change production Dataverse writer
rights, approval policy, existing event meanings or the v0.1.0 fixture tables.

Registrations are immutable within runId/batchId. Retries reuse their event IDs;
changed input requires a new batch. Callback retries reuse transitionId; an
explicit isolated replay uses a new runId. A new transition occurrence must come
from Fabric/Activator, not an unchanged-state evaluation. Operational failures are
ledger receipts, never empty Ready GapAnalysis results. Document validation only
projects Validated for compliant Confirmed or sufficiently confident Auto results;
NeedsReview is never accepted. This remains provisional, not owner sign-off.

## 0.1.0 - Draft, not approved for integration

Expanded the unreleased initial proposal into the cross-track contract review set, derived from demo definition sections 4-5 and 9 and implementation notes section 4.

Affected tracks: Azure/Foundry, Fabric, Power Platform.

Added shared objects and code lists, all seven event payload schemas, the normalized Dataverse change message, APIM upload/read/action OpenAPI, agent gap/rework/package outputs, explicit unresolved Dataverse mappings, and 12 Data Agent questions. These make the interfaces reviewable without inventing platform wire formats or logical column names.

Added targeted examples, generated Pydantic/TypeScript reader types and self-contained producer OpenAPI, strict producer/schema checks, tolerant reader boundary tests, generated-type freshness and conservative version compatibility checks. Code lists use directly referenceable JSON Schema rather than maintaining a second YAML representation.

This is a draft, not a released baseline. The same unreleased 0.1.0 proposal is being completed; no existing release is being changed. The user authorized local seed/stub implementation against this provisional baseline without waiting for all-three-owner sign-off. Relevant `REVIEW-DECISIONS.md` items remain to be resolved before live integrations or the first approved release tag. No owner approval is inferred from generated files or local checks; full end-to-end integration is not assumed necessary for the offline demo.

The envelope permits unknown fields for forward compatibility and requires its documented fields, UUID event/case IDs, nonempty site/producer identifiers, and a timestamp with timezone. The readiness signal permits only the Blocked/Ready transition notifications specified by the definition, not NotReady notifications. Missing-condition IDs are proposed as strings pending agreement on their detailed shape. Photo-related missing conditions refer to checklist item IDs, not rejected photo IDs; the illustrative checklist IDs in the example are not an approved seed baseline.

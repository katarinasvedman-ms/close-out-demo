/* Generated from contracts; do not edit. Reader codes are forward-compatible strings. */

/**
 * Draft body of the Activator to Power Automate signal, not the event/message envelope.
 *
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "ReadinessSignal".
 */
export type ReadinessSignal = {
  [k: string]: unknown;
} & {
  caseId: string;
  siteId: string;
  readiness: string;
  /**
   * Proposed condition ID list; detailed shape requires cross-track agreement.
   */
  readinessMissing: string[];
  evaluatedAt: string;
  [k: string]: unknown;
};
/**
 * Proposed agent outputs are recommendations/manifests, never approval or execution authority.
 *
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "AgentOutput".
 */
export type AgentOutput = GapAnalysis | PackageManifest;
/**
 * Proposed APIM boundaries. Inputs are untrusted: the executor must re-read trusted approval/evidence and target versions.
 *
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "BusinessAction".
 */
export type BusinessAction =
  | CreateReworkRequest
  | CloseReworkRequest
  | ClosePunchListRequest
  | SubmitCloseoutRequest
  | ActionReceipt
  | ActionError
  | SiteView
  | UploadMetadata;
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "CreateReworkRequest".
 */
export type CreateReworkRequest = ActionContext & {
  subcontractorId: string;
  reason: string;
  validationResultIds: string[];
  ticketIds: string[];
  approvalEntryId: string;
  [k: string]: unknown;
};
/**
 * Closure authorization source/trigger is unresolved; evidence alone is not approval.
 *
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "CloseReworkRequest".
 */
export type CloseReworkRequest = ActionContext & {
  workOrderId: string;
  /**
   * @minItems 1
   */
  evidenceRefs: [EvidenceRef, ...EvidenceRef[]];
  [k: string]: unknown;
};
/**
 * Closing photo must be authorized, current and accepted; closure actor/policy needs owner agreement.
 *
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "ClosePunchListRequest".
 */
export type ClosePunchListRequest = ActionContext & {
  ticketId: string;
  closingPhotoId: string;
  [k: string]: unknown;
};
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "SubmitCloseoutRequest".
 */
export type SubmitCloseoutRequest = ActionContext & {
  packageArtifactId: string;
  approvalEntryId: string;
  [k: string]: unknown;
};
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "Readiness".
 */
export type Readiness = string;
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "MilestoneType".
 */
export type MilestoneType = string;
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "MilestoneStatus".
 */
export type MilestoneStatus = string;
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "WorkOrder".
 */
export type WorkOrder = {
  [k: string]: unknown;
} & {
  workOrderId: string;
  siteId: string;
  subcontractorId: string;
  workOrderType: WorkOrderType;
  status: WorkOrderStatus;
  caseId: string | null;
  reason: string;
  createdAt: string;
  dueDate: string | null;
  closedAt: string | null;
  version: string;
  [k: string]: unknown;
};
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "WorkOrderType".
 */
export type WorkOrderType = string;
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "WorkOrderStatus".
 */
export type WorkOrderStatus = string;
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "TicketType".
 */
export type TicketType = string;
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "TicketStatus".
 */
export type TicketStatus = string;
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "ChecklistItemCode".
 */
export type ChecklistItemCode = string;
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "ArtifactType".
 */
export type ArtifactType = string;
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "ArtifactStatus".
 */
export type ArtifactStatus = string;
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "ValidationResult".
 */
export type ValidationResult = {
  validationResultId: string;
  photoId?: string;
  artifactId?: string;
  checklistItemCode: ChecklistItemCode | null;
  compliant: boolean;
  confidence: number;
  findings: string[];
  rfdsArtifactId: string | null;
  rfdsReference: string | null;
  reviewStatus: ReviewStatus;
  validatedBy: string;
  model: string;
  modelVersion: string;
  validatedAt: string;
  caseId: string | null;
  [k: string]: unknown;
} & (
  | {
      photoId: string;
      [k: string]: unknown;
    }
  | {
      artifactId: string;
      [k: string]: unknown;
    }
);
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "ReviewStatus".
 */
export type ReviewStatus = string;
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "CloseOutStatus".
 */
export type CloseOutStatus = string;
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "StatusReason".
 */
export type StatusReason = string;
/**
 * Metadata only; bytes are a separate multipart field. Uploader identity comes from the authenticated caller, never this body.
 *
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "UploadMetadata".
 */
export type UploadMetadata = {
  [k: string]: unknown;
} & {
  siteId: string;
  caseId: string | null;
  requirementId: string;
  artifactType: ArtifactType;
  checklistItemCode?: ChecklistItemCode;
  sector?: string | null;
  latitude?: number;
  longitude?: number;
  capturedAt?: string;
  replacesPhotoId?: string | null;
  [k: string]: unknown;
};
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "ArtifactUploaded".
 */
export type ArtifactUploaded = EventEnvelope & {
  eventType?: "ArtifactUploaded";
  data?: AcceptanceArtifact;
  [k: string]: unknown;
};
/**
 * Synthetic test ingestion barrier. Fabric must observe every listed eventId with matching run/batch/case/site identity before projecting this batch. Arrival of this marker alone is not readiness.
 *
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "EvidenceBatchCompleted".
 */
export type EvidenceBatchCompleted = EventEnvelope & {
  schemaVersion?: "0.2.0";
  eventType?: "EvidenceBatchCompleted";
  caseId?: string;
  data?: BatchCompletion;
  [k: string]: unknown;
};
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "MilestoneChanged".
 */
export type MilestoneChanged = EventEnvelope & {
  eventType?: "MilestoneChanged";
  data?: Milestone;
  [k: string]: unknown;
};
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "PhotoUploaded".
 */
export type PhotoUploaded = EventEnvelope & {
  eventType?: "PhotoUploaded";
  data?: Photo;
  [k: string]: unknown;
};
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "TicketChanged".
 */
export type TicketChanged = EventEnvelope & {
  eventType?: "TicketChanged";
  data?: Ticket;
  [k: string]: unknown;
};
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "ValidationResultCreated".
 */
export type ValidationResultCreated = EventEnvelope & {
  eventType?: "ValidationResultCreated";
  data?: ValidationResult;
  [k: string]: unknown;
};
/**
 * Proposed immutable reviewed snapshot plus a decision-log reference; review actor/provenance needs Power Platform confirmation.
 *
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "ValidationResultReviewed".
 */
export type ValidationResultReviewed = EventEnvelope & {
  eventType?: "ValidationResultReviewed";
  data?: {
    validationResult: ValidationResult;
    decisionEntryId: string;
    [k: string]: unknown;
  };
  [k: string]: unknown;
};
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "WorkOrderChanged".
 */
export type WorkOrderChanged = EventEnvelope & {
  eventType?: "WorkOrderChanged";
  data?: WorkOrder;
  [k: string]: unknown;
};
/**
 * Internal normalized envelope, NOT an assertion about the raw Dataverse service-endpoint wire format. TODO(verify): capture the external message and map it in one adapter.
 *
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "CloseOutCaseChanged".
 */
export type CloseOutCaseChanged = EventEnvelope & {
  eventType?: "CloseOutCaseChanged";
  caseId?: string;
  data?: CloseOutCaseChange;
  [k: string]: unknown;
};
/**
 * External Fabric test callback, not a Dataverse change. transitionId identifies one actual BECOMES occurrence; retries preserve it. runId scopes an explicit test reset. Blocked-to-Blocked is not a new transition.
 *
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "HeadlessReadiness".
 */
export type HeadlessReadiness = EventEnvelope & {
  schemaVersion?: "0.2.0";
  eventType?: "HeadlessReadiness";
  caseId?: string;
  data?: HeadlessReadinessData;
  [k: string]: unknown;
};
/**
 * Draft logical objects, not physical Dataverse column names. Lifecycle nullability and auxiliary structures require owner review.
 *
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "SharedObject".
 */
export type SharedObject =
  | CustomerProject
  | ArtifactRequirement
  | ChecklistItem
  | Site
  | Milestone
  | Subcontractor
  | WorkOrder
  | Ticket
  | AcceptanceArtifact
  | Photo
  | ValidationResult
  | CloseOutCase
  | DecisionLogEntry
  | EntityMatchCandidate;
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "ActorType".
 */
export type ActorType = string;
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "BatchEvidence".
 */
export type BatchEvidence = {
  [k: string]: unknown;
} & {
  evidenceId: string;
  kind: string;
  metadata: {
    [k: string]: unknown;
  };
  blob: PinnedBlob;
  text?: PinnedBlob1;
};

export interface ContractTypes {
  ReadinessSignal?: ReadinessSignal;
  AgentOutput?: AgentOutput;
  BusinessAction?: BusinessAction;
  CodeLists?: CodeLists;
  ArtifactUploaded?: ArtifactUploaded;
  EventEnvelope?: EventEnvelope;
  EvidenceBatchCompleted?: EvidenceBatchCompleted;
  MilestoneChanged?: MilestoneChanged;
  PhotoUploaded?: PhotoUploaded;
  TicketChanged?: TicketChanged;
  ValidationResultCreated?: ValidationResultCreated;
  ValidationResultReviewed?: ValidationResultReviewed;
  WorkOrderChanged?: WorkOrderChanged;
  CloseOutCaseChanged?: CloseOutCaseChanged;
  HeadlessReadiness?: HeadlessReadiness;
  SharedObject?: SharedObject;
  EvidenceBatch?: EvidenceBatch;
}
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "GapAnalysis".
 */
export interface GapAnalysis {
  caseId: string;
  siteId: string;
  generatedAt: string;
  model: string;
  modelVersion: string;
  gaps: Gap[];
  proposedRework: ReworkProposal | null;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "Gap".
 */
export interface Gap {
  conditionType: string;
  conditionId: string;
  reason: string;
  /**
   * @minItems 1
   */
  evidenceRefs: [EvidenceRef, ...EvidenceRef[]];
  [k: string]: unknown;
}
/**
 * Proposed version-aware citation, not a SAS token or embedded document.
 *
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "EvidenceRef".
 */
export interface EvidenceRef {
  objectType: string;
  id: string;
  version?: string;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "ReworkProposal".
 */
export interface ReworkProposal {
  reason: string;
  subcontractorId?: string;
  validationResultIds: string[];
  ticketIds: string[];
  [k: string]: unknown;
}
/**
 * Manifest must be checked against current accepted evidence before PackageReady. File conversion and Dataverse storage are not yet agreed.
 *
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "PackageManifest".
 */
export interface PackageManifest {
  artifactId: string;
  caseId: string;
  siteId: string;
  createdAt: string;
  model: string;
  modelVersion: string;
  /**
   * @minItems 1
   */
  evidenceRefs: [EvidenceRef, ...EvidenceRef[]];
  /**
   * @minItems 1
   */
  files: [PackageFile, ...PackageFile[]];
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "PackageFile".
 */
export interface PackageFile {
  format: string;
  blobUrl: string;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "ActionContext".
 */
export interface ActionContext {
  caseId: string;
  siteId: string;
  transition: string;
  /**
   * Opaque target SoR version, not the Dataverse rowVersion. Target for creation needs owner agreement.
   */
  expectedVersion: string;
  /**
   * Optional proposal for repeated transitions; key composition requires all-track review.
   */
  transitionOccurrenceId?: string;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "ActionReceipt".
 */
export interface ActionReceipt {
  receiptId: string;
  idempotencyKey: string;
  caseId: string;
  siteId: string;
  action: string;
  targetId: string;
  targetVersion: string;
  completedAt: string;
  evidenceRefs: EvidenceRef[];
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "ActionError".
 */
export interface ActionError {
  code: string;
  message: string;
  traceId: string;
  retryable: boolean;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "SiteView".
 */
export interface SiteView {
  site: Site;
  milestones: Milestone[];
  workOrders: WorkOrder[];
  tickets: Ticket[];
  artifacts: AcceptanceArtifact[];
  photos: Photo[];
  validations: ValidationResult[];
  activeCase: CloseOutCase | null;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "Site".
 */
export interface Site {
  siteId: string;
  siteName: string;
  sourceSiteIds: string[];
  projectId: string;
  market: string;
  latitude: number;
  longitude: number;
  siteType: string;
  currentPhase: string;
  readiness: Readiness;
  readinessMissing: string[];
  readinessEvaluatedAt: string | null;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "Milestone".
 */
export interface Milestone {
  milestoneId: string;
  siteId: string;
  projectId: string;
  milestoneType: MilestoneType;
  status: MilestoneStatus;
  plannedDate: string | null;
  actualDate: string | null;
  sourceUpdatedAt: string;
  isStale: boolean;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "Ticket".
 */
export interface Ticket {
  ticketId: string;
  siteId: string;
  ticketType: TicketType;
  blocking: boolean;
  status: TicketStatus;
  description: string;
  checklistItemCode: ChecklistItemCode | null;
  openedAt: string;
  closedAt: string | null;
  closingPhotoId: string | null;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "AcceptanceArtifact".
 */
export interface AcceptanceArtifact {
  artifactId: string;
  siteId: string;
  requirementId: string | null;
  artifactType: ArtifactType;
  status: ArtifactStatus;
  blobUrl: string | null;
  version: string;
  submittedBy: string | null;
  submittedAt: string | null;
  caseId: string | null;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "Photo".
 */
export interface Photo {
  photoId: string;
  artifactId: string;
  siteId: string;
  checklistItemCode: ChecklistItemCode;
  sector: string | null;
  blobUrl: string;
  latitude: number;
  longitude: number;
  capturedAt: string;
  uploadedBy: string;
  replacesPhotoId: string | null;
  isCurrent: boolean;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "CloseOutCase".
 */
export interface CloseOutCase {
  caseId: string;
  siteId: string;
  status: CloseOutStatus;
  statusReason: StatusReason;
  bpfStage: string;
  reworkDecision: ApprovalDecision | null;
  reworkPolicyVersion: string | null;
  submissionDecision: ApprovalDecision | null;
  packageArtifactId: string | null;
  createdAt: string;
  submittedAt: string | null;
  acceptedAt: string | null;
  slaDueAt: string | null;
  [k: string]: unknown;
}
/**
 * Proposed logical decision structure; physical Dataverse mapping and trusted provenance require Henrik's confirmation.
 *
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "ApprovalDecision".
 */
export interface ApprovalDecision {
  outcome: string;
  actorType: string;
  actorId: string;
  decidedAt: string;
  policyVersion: string;
  comment?: string;
  [k: string]: unknown;
}
/**
 * Known producer codes. Generated reader types accept future string codes and boundary tooling logs them.
 *
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "CodeLists".
 */
export interface CodeLists {
  ArtifactType: string;
  ChecklistItemCode: string;
  MilestoneType: string;
  MilestoneStatus: string;
  WorkOrderType: string;
  WorkOrderStatus: string;
  TicketType: string;
  TicketStatus: string;
  ArtifactStatus: string;
  ReviewStatus: string;
  Readiness: string;
  CloseOutStatus: string;
  StatusReason: string;
  ActorType: string;
  [k: string]: unknown;
}
/**
 * Draft common envelope; event-specific data must also validate against its own schema.
 *
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "EventEnvelope".
 */
export interface EventEnvelope {
  schemaVersion: string;
  eventType: string;
  eventId: string;
  caseId: string | null;
  siteId: string;
  occurredAt: string;
  producer: string;
  data: {
    [k: string]: unknown;
  };
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "BatchCompletion".
 */
export interface BatchCompletion {
  runId: string;
  batchId: string;
  program: string;
  manifestSha256: string;
  /**
   * @minItems 2
   */
  eventIds: [string, string, ...string[]];
  /**
   * Agent-submitted assessments are authenticated tool inputs, not independently verified model-response receipts.
   */
  validationSource: {
    [k: string]: unknown;
  };
}
export interface CloseOutCaseChange {
  caseId: string;
  siteId: string;
  status: CloseOutStatus;
  statusReason: StatusReason;
  rowVersion: string;
  /**
   * Optional proposed occurrence discriminator; not yet agreed or a deployed Dataverse field.
   */
  transitionOccurrenceId?: string;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "HeadlessReadinessData".
 */
export interface HeadlessReadinessData {
  runId: string;
  transitionId: string;
  program: string;
  factsRevision: string;
  sourceRef: string;
  signal: ReadinessSignal;
}
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "CustomerProject".
 */
export interface CustomerProject {
  projectId: string;
  programCode: string;
  customerName: string;
  customerId: string;
  market: string;
  lowConfidenceThreshold: number;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "ArtifactRequirement".
 */
export interface ArtifactRequirement {
  requirementId: string;
  projectId: string;
  artifactType: ArtifactType;
  mandatory: boolean;
  phase: string;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "ChecklistItem".
 */
export interface ChecklistItem {
  checklistItemId: string;
  requirementId: string;
  itemCode: ChecklistItemCode;
  description: string;
  mandatory: boolean;
  rfdsCheck: boolean;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "Subcontractor".
 */
export interface Subcontractor {
  subcontractorId: string;
  name: string;
  normalizedName: string;
  sourceNames: string[];
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "DecisionLogEntry".
 */
export interface DecisionLogEntry {
  entryId: string;
  caseId: string;
  seq: number;
  timestamp: string;
  actorType: ActorType;
  actorId: string;
  role: string;
  step: string;
  inputs: {
    [k: string]: unknown;
  };
  evidenceRefs: EvidenceRef[];
  model: string | null;
  modelVersion: string | null;
  confidence: number | null;
  ruleResult: string | null;
  outcome: string;
  traceId: string;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "EntityMatchCandidate".
 */
export interface EntityMatchCandidate {
  candidateId: string;
  entityType: string;
  /**
   * @minItems 2
   */
  candidateIds: [string, string, ...string[]];
  matchConfidence: number;
  matchReasons: string[];
  status: string;
  decidedBy: string | null;
  decidedAt: string | null;
  survivorId: string | null;
  [k: string]: unknown;
}
/**
 * Isolated synthetic test registration, not a Dataverse upload or an approval. Register before uploading; pin each immutable input with its SHA256 and If-Match ETag.
 *
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "EvidenceBatch".
 */
export interface EvidenceBatch {
  schemaVersion: "0.2.0";
  runId: string;
  batchId: string;
  caseId: string;
  siteId: string;
  program: string;
  registeredAt: string;
  /**
   * Explicit synthetic baseline used for validation context, never a substitute for the coordinator's native Fabric tool. Seeded validations are not uploaded-inference results.
   */
  facts: {
    [k: string]: unknown;
  };
  /**
   * @minItems 1
   * @maxItems 100
   */
  members: [BatchEvidence, ...BatchEvidence[]];
}
/**
 * This interface was referenced by `ContractTypes`'s JSON-Schema
 * via the `definition` "PinnedBlob".
 */
export interface PinnedBlob {
  url: string;
  etag: string;
  sha256: string;
  versionId?: string;
  mediaType: string;
}
/**
 * Optional pinned synthetic text counterpart. It does not imply PDF OCR or document IQ ingestion.
 */
export interface PinnedBlob1 {
  url: string;
  etag: string;
  sha256: string;
  versionId?: string;
  mediaType: string;
}

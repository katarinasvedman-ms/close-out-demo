export interface CaseView {
  siteId: string;
  siteName: string;
  caseId: string;
  program: string;
  customer: string;
  readiness: "Blocked" | "Ready" | "NotReady";
  scenario: string;
  evaluatedAt: string;
  threshold: number;
  missing: string[];
  constructionComplete: boolean;
  photos: { id: string; item: string; current: boolean }[];
  validations: {
    id: string; photoId: string | null; compliant: boolean; confidence: number;
    reviewStatus: string; findings: string[]; validatedAt: string;
  }[];
  documents: { id: string; type: string; status: string; version: string }[];
  tickets: { id: string; description: string; status: string; blocking: boolean }[];
  workOrders: { id: string; type: string; status: string; reason: string }[];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function record(value: unknown): Record<string, unknown> {
  if (!isRecord(value)) {
    throw new Error("The presenter API returned an invalid object.");
  }
  return value;
}

function text(value: unknown): string {
  if (typeof value !== "string") throw new Error("The presenter API returned invalid text.");
  return value;
}

function number(value: unknown): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error("The presenter API returned an invalid number.");
  }
  return value;
}

function boolean(value: unknown): boolean {
  if (typeof value !== "boolean") throw new Error("The presenter API returned an invalid flag.");
  return value;
}

function array(value: unknown): unknown[] {
  if (!Array.isArray(value)) throw new Error("The presenter API returned an invalid list.");
  return value;
}

export function parseCases(value: unknown): CaseView[] {
  return array(value).map((value) => {
    const row = record(value), site = record(row.site), project = record(row.project);
    if (row.mode !== "stub" || row.validationSource !== "seed") {
      throw new Error("This case view only supports explicitly labelled seed fixtures.");
    }
    const readiness = row.readiness;
    if (readiness !== "Blocked" && readiness !== "Ready" && readiness !== "NotReady") {
      throw new Error("Unknown readiness state; no default applied.");
    }
    return {
      siteId: text(site.siteId), siteName: text(site.siteName), caseId: text(row.caseId),
      program: text(project.programCode), customer: text(project.customerName),
      readiness, scenario: text(row.scenario), evaluatedAt: text(site.readinessEvaluatedAt),
      threshold: number(project.lowConfidenceThreshold),
      missing: array(row.readinessMissing).map(text),
      constructionComplete: boolean(row.constructionComplete),
      photos: array(row.photos).map((value) => {
        const row = record(value);
        return { id: text(row.photoId), item: text(row.checklistItemCode),
          current: boolean(row.isCurrent) };
      }),
      validations: array(row.validations).map((value) => {
        const row = record(value);
        return { id: text(row.validationResultId),
          photoId: row.photoId == null ? null : text(row.photoId),
          compliant: boolean(row.compliant), confidence: number(row.confidence),
          reviewStatus: text(row.reviewStatus), findings: array(row.findings).map(text),
          validatedAt: text(row.validatedAt) };
      }),
      documents: array(row.artifacts).map((value) => {
        const row = record(value);
        return { id: text(row.artifactId), type: text(row.artifactType),
          status: text(row.status), version: text(row.version) };
      }),
      tickets: array(row.tickets).map((value) => {
        const row = record(value);
        return { id: text(row.ticketId), description: text(row.description),
          status: text(row.status), blocking: boolean(row.blocking) };
      }),
      workOrders: array(row.workOrders).map((value) => {
        const row = record(value);
        return { id: text(row.workOrderId), type: text(row.workOrderType),
          status: text(row.status), reason: text(row.reason) };
      }),
    };
  });
}

export interface AgentStatus {
  configured: boolean;
  name: string | null;
  version: string | null;
}

export function parseStatus(value: unknown): AgentStatus {
  const row = record(value), configured = boolean(row.configured);
  const agent = configured ? record(row.agent) : null;
  return { configured, name: agent ? text(agent.name) : null,
    version: agent ? text(agent.version) : null };
}

export interface Answer {
  text: string;
  responseId: string;
  retrieval: { source: string; name: string; callId: string }[];
  citations?: Citation[];
}

export interface Citation {
  start: number;
  end: number;
  url: string;
  title: string;
  excerpt: string;
}

export function safeSourceUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password;
  } catch {
    return false;
  }
}

export function parseAnswer(value: unknown): Answer {
  const row = record(value);
  const content = text(row.text);
  let end = 0;
  const citations = row.citations === undefined ? undefined : array(row.citations).map((value) => {
    const citation = record(value), start = number(citation.start), next = number(citation.end);
    const url = text(citation.url);
    if (!Number.isInteger(start) || !Number.isInteger(next) || start < end
      || next <= start || next > Array.from(content).length || !safeSourceUrl(url)) {
      throw new Error("The presenter API returned an invalid citation.");
    }
    end = next;
    return { start, end: next, url, title: text(citation.title), excerpt: text(citation.excerpt) };
  });
  return {
    text: content, responseId: text(row.responseId), citations,
    retrieval: array(row.retrieval).map((value) => {
      const row = record(value);
      return { source: text(row.source), name: text(row.name), callId: text(row.callId) };
    }),
  };
}

export async function fetchJson(url: string, init?: RequestInit): Promise<unknown> {
  let response: Response;
  try {
    response = await fetch(url, init);
  } catch {
    throw new Error("Cannot reach the local workbench API. Check that it is running.");
  }
  const value: unknown = await response.json();
  if (!response.ok) {
    const body = record(value);
    throw new Error(typeof body.error === "string" ? body.error : `API error ${response.status}`);
  }
  return value;
}

export interface BriefSummary {
  id: string;
  siteId: string;
  readiness: "Blocked" | "Ready";
  savedAt: string;
}

export interface Brief extends BriefSummary {
  markdown: string;
}

function briefSummary(value: unknown, siteId: string): BriefSummary {
  const row = record(value), readiness = row.readiness;
  if (row.siteId !== siteId || (readiness !== "Blocked" && readiness !== "Ready")) {
    throw new Error("The brief does not belong to the selected site or has invalid readiness.");
  }
  const savedAt = text(row.savedAt), id = text(row.id);
  if (!id || !Number.isFinite(Date.parse(savedAt))) {
    throw new Error("The brief has an invalid identifier or timestamp.");
  }
  return { id, siteId, readiness, savedAt };
}

export function parseBriefs(value: unknown, siteId: string): BriefSummary[] {
  return array(value).map((row) => briefSummary(row, siteId));
}

export function parseBrief(value: unknown, siteId: string, briefId: string): Brief {
  const summary = briefSummary(value, siteId);
  const markdown = text(record(value).markdown);
  if (summary.id !== briefId || !markdown.startsWith("# Close-out review")) {
    throw new Error("The saved brief does not match the selected document.");
  }
  return { ...summary, markdown };
}

export function label(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function acceptedPhotos(site: CaseView): number {
  return site.photos.filter((photo) => {
    if (!photo.current) return false;
    const latest = site.validations.filter((row) => row.photoId === photo.id)
      .sort((a, b) => Date.parse(b.validatedAt) - Date.parse(a.validatedAt))[0];
    return latest?.compliant && (latest.reviewStatus === "Confirmed"
      || (latest.reviewStatus === "Auto" && latest.confidence >= site.threshold));
  }).length;
}

export interface LiveAssessment {
  evidenceId: string;
  compliant: boolean;
  confidence: number;
  findings: string[];
  rfdsReference: string | null;
  observedAzimuthDegrees: number | null;
}

export interface ValidationRun {
  publicationId: string;
  createdAt: number;
  status: "Preparing" | "Starting" | "Running" | "Completed" | "Failed" | "Unknown";
  responseId: string | null;
  assessment: LiveAssessment | null;
  publication: {
    status: "Published" | "NotConfirmed" | "Unknown";
    eventIds: string[];
    error: string | null;
  };
  error: string | null;
}

const nullableText = (value: unknown) => value === null ? null : text(value);

export function parseValidationRun(value: unknown): ValidationRun {
  const row = record(value), publication = record(row.publication);
  const status = row.status, published = publication.status;
  if (status !== "Preparing" && status !== "Starting" && status !== "Running"
    && status !== "Completed" && status !== "Failed" && status !== "Unknown") {
    throw new Error("Unknown validation execution state.");
  }
  if (published !== "Published" && published !== "NotConfirmed" && published !== "Unknown") {
    throw new Error("Unknown publication state.");
  }
  const assessment = row.assessment === null ? null : record(row.assessment);
  return {
    publicationId: text(row.publicationId), createdAt: number(row.createdAt),
    status, responseId: nullableText(row.responseId),
    error: nullableText(row.error),
    publication: { status: published, eventIds: array(publication.eventIds).map(text),
      error: nullableText(publication.error) },
    assessment: assessment ? {
      evidenceId: text(assessment.evidenceId), compliant: boolean(assessment.compliant),
      confidence: number(assessment.confidence), findings: array(assessment.findings).map(text),
      rfdsReference: nullableText(assessment.rfdsReference),
      observedAzimuthDegrees: assessment.observedAzimuthDegrees === null ? null
        : number(assessment.observedAzimuthDegrees),
    } : null,
  };
}

export type ValidationTarget = { configured: false } | {
  configured: true;
  siteId: string;
  evidenceId: string;
  agent: { name: string; version: string };
  sourceRunId: string;
  sourceBatchId: string;
  sha256: string;
  latest: ValidationRun | null;
};

export function parseValidationTarget(value: unknown): ValidationTarget {
  const row = record(value);
  if (!boolean(row.configured)) return { configured: false };
  const agent = record(row.agent);
  return {
    configured: true, siteId: text(row.siteId), evidenceId: text(row.evidenceId),
    agent: { name: text(agent.name), version: text(agent.version) },
    sourceRunId: text(row.sourceRunId), sourceBatchId: text(row.sourceBatchId),
    sha256: text(row.sha256), latest: row.latest === null ? null : parseValidationRun(row.latest),
  };
}

export function validationActive(run: ValidationRun | null): boolean {
  return run !== null && ["Preparing", "Starting", "Running", "Unknown"].includes(run.status);
}

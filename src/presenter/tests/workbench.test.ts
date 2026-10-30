import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ValidationOutcome } from "../src/ValidationPanel";
import { AssistantAnswer, linkedAnswer } from "../src/AssistantAnswer";
import { BriefDocument } from "../src/BriefPanel";
import {
  acceptedPhotos, label, parseAnswer, parseCases, parseStatus, parseValidationRun,
  parseValidationTarget, validationActive,
  parseBriefs, parseBrief,
} from "../src/model";
import type { Brief, CaseView } from "../src/model";

describe("workbench boundaries", () => {
  it("parses scoped saved briefs and rejects swapped sites, documents and invalid states", () => {
    const row = { id: "coordinator/run/case/Blocked/transition/brief.md",
      siteId: "SE-STO-0042", readiness: "Blocked", savedAt: "2026-10-30T16:00:00+00:00" };
    const value = { ...row, markdown: "# Close-out review - SE-STO-0042\n\nReview first." };
    expect(parseBriefs([row], row.siteId)).toEqual([row]);
    expect(parseBrief(value, row.siteId, row.id)).toEqual(value);
    expect(parseBriefs([], row.siteId)).toEqual([]);
    expect(() => parseBriefs([row], "SE-GOT-0017")).toThrow("selected site");
    expect(() => parseBriefs([{ ...row, readiness: "Accepted" }], row.siteId)).toThrow();
    expect(() => parseBriefs([{ ...row, savedAt: "not-a-date" }], row.siteId)).toThrow();
    expect(() => parseBrief(value, row.siteId, "another-document")).toThrow("selected document");
    expect(() => parseBrief({ ...value, markdown: '{"gaps":[]}' }, row.siteId, row.id)).toThrow();
    expect(() => parseBriefs({ configured: false }, row.siteId)).toThrow();
  });

  it("renders a readable brief without executing HTML, images or unsafe links", () => {
    const brief: Brief = { id: "brief.md", siteId: "SE-STO-0042", readiness: "Blocked",
      savedAt: "2026-10-30T16:00:00Z",
      markdown: "# Close-out review - SE-STO-0042\n\n## What needs attention\n\n"
        + "**Grounding label** needs review.\n\n1. Coordinator: confirm requirements.\n\n"
        + "[RFDS](https://documents.example.test/rfds.txt)\n\n"
        + "<script>alert(1)</script>\n\n![tracker](https://example.test/image)\n\n"
        + "[unsafe](javascript:alert)\n\n[credentials](https://user:password@example.test/doc)" };
    const html = renderToStaticMarkup(createElement(BriefDocument, { brief }));
    expect(html).toContain("<h1>Close-out review - SE-STO-0042</h1>");
    expect(html).toContain("<strong>Grounding label</strong>");
    expect(html).toContain("<ol>");
    expect(html).toContain("Draft recommendation");
    expect(html).toContain('href="https://documents.example.test/rfds.txt"');
    expect(html).toContain('rel="noopener noreferrer"');
    expect(html).not.toMatch(/<script|<img|javascript:|user:password|Fabric|Foundry/);
  });

  it("does not mistake a configured agent for successful retrieval", () => {
    expect(parseStatus({ configured: false, agent: null })).toEqual({
      configured: false, name: null, version: null,
    });
    expect(parseStatus({ configured: true, agent: { name: "assistant", version: "1" } }))
      .toEqual({ configured: true, name: "assistant", version: "1" });
    expect(() => parseStatus({ configured: "true" })).toThrow();
  });

  it("rejects malformed, unlabelled or live cases rather than marking them Ready", () => {
    expect(() => parseCases([{ mode: "live", site: {}, project: {} }])).toThrow("seed");
    expect(() => parseCases([{ mode: "stub", validationSource: "seed", site: {},
      project: {}, readiness: "future-state" }])).toThrow("Unknown readiness");
    expect(() => parseCases(null)).toThrow();
  });

  it("requires response IDs and real retrieval receipt shapes", () => {
    expect(parseAnswer({ text: "answer", responseId: "resp-1",
      retrieval: [{ source: "fabric-facts", name: "retrieve", callId: "call-1" }] }).responseId)
      .toBe("resp-1");
    expect(() => parseAnswer({ text: "success" })).toThrow();
  });

  it("renders Markdown and deduplicated clickable sources using Unicode-safe offsets", () => {
    const marker = "\u30106:1\u2020source\u3011";
    const content = `\u{1f4ce} **RFDS** requires \`120 degrees\` ${marker}. Again ${marker}.`;
    const start = Array.from(content.slice(0, content.indexOf(marker))).length;
    const next = Array.from(content.slice(0, content.lastIndexOf(marker))).length;
    const source = { url: "https://documents.example.test/rfds.txt", title: "rfds.txt",
      excerpt: "SYNTHETIC INPUT\nSector B azimuth 120 degrees" };
    const answer = parseAnswer({ text: content, responseId: "resp_test", retrieval: [],
      citations: [{ ...source, start, end: start + marker.length },
        { ...source, start: next, end: next + marker.length }] });
    const { markdown, sources } = linkedAnswer(answer);
    expect(sources).toHaveLength(1);
    expect(markdown).not.toContain(marker);
    expect(markdown.match(/\[1\]\(#source-resp_test-1\)/g)).toHaveLength(2);
    const html = renderToStaticMarkup(createElement(AssistantAnswer,
      { answer, siteId: "SE-STO-0042" }));
    expect(html).toContain("<strong>RFDS</strong>");
    expect(html).toContain("<code>120 degrees</code>");
    expect(html).toContain('href="#source-resp_test-1"');
    expect(html).toContain('id="source-resp_test-1"');
    expect(html).toContain("Document extract used for this answer");
    expect(html).toContain("Sector B azimuth 120 degrees");
    expect(html).toContain("permission to open the original");
    expect(html).not.toMatch(/Document IQ|Blob permissions|Foundry|Fabric/);
    expect(html.match(/<summary>/g)).toHaveLength(1);
  });

  it("preserves cited words, and renders ordinary answers without source metadata", () => {
    const answer = parseAnswer({ text: "The RFDS requirement.", responseId: "resp_words",
      retrieval: [], citations: [{ start: 4, end: 8, title: "rfds.txt",
        url: "https://documents.example.test/rfds.txt", excerpt: "Requirement" }] });
    expect(linkedAnswer(answer).markdown).toContain("The RFDS[1](");
    expect(linkedAnswer({ ...answer, citations: [] }).markdown).toBe(answer.text);
    expect(linkedAnswer({ ...answer, citations: undefined }).sources).toEqual([]);
  });

  it("rejects unsafe URLs and invalid citation spans and never embeds HTML or images", () => {
    const raw = { text: "A source", responseId: "resp_test", retrieval: [],
      citations: [{ start: 2, end: 8, title: "source",
        url: "https://documents.example.test/source.txt", excerpt: "<script>alert(1)</script>" }] };
    for (const url of ["javascript:alert(1)", "data:text/html,test", "http://example.test",
      "https://user:password@example.test", "//example.test"]) {
      expect(() => parseAnswer({ ...raw, citations: [{ ...raw.citations[0], url }] })).toThrow();
    }
    for (const [start, end] of [[-1, 3], [1, 100], [3, 2], [1.5, 3]]) {
      expect(() => parseAnswer({ ...raw,
        citations: [{ ...raw.citations[0], start, end }] })).toThrow();
    }
    expect(() => parseAnswer({ ...raw, citations: [...raw.citations, ...raw.citations] })).toThrow();
    const answer = parseAnswer({ ...raw, text: raw.text
      + "\n<script>alert(2)</script>\n![track](https://example.test/image)\n[bad](javascript:alert)" });
    const html = renderToStaticMarkup(createElement(AssistantAnswer,
      { answer, siteId: "SE-STO-0042" }));
    expect(html).not.toContain("<script");
    expect(html).not.toContain("<img");
    expect(html).not.toContain("javascript:");
    expect(html).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");
  });

  it("counts only current photos with the latest accepted validation at the exact threshold", () => {
    const site: Pick<CaseView, "photos" | "validations" | "threshold"> = {
      threshold: 0.75,
      photos: [{ id: "p1", item: "grounding", current: true }],
      validations: [{ id: "v1", photoId: "p1", compliant: true, confidence: 0.75,
        reviewStatus: "Auto", findings: [], validatedAt: "2026-10-27T09:00:00Z" }],
    };
    const view: CaseView = { ...site, siteId: "site", siteName: "Site", caseId: "case",
      program: "program", customer: "customer", readiness: "Blocked", scenario: "fixture",
      evaluatedAt: "2026-10-27T09:00:00Z", missing: [], constructionComplete: true,
      documents: [], tickets: [], workOrders: [] };
    expect(acceptedPhotos(view)).toBe(1);
    view.validations[0].confidence = 0.749;
    expect(acceptedPhotos(view)).toBe(0);
    view.validations[0].reviewStatus = "Confirmed";
    expect(acceptedPhotos(view)).toBe(1);
    view.validations.push({ ...view.validations[0], id: "v2", compliant: false,
      validatedAt: "2026-10-28T09:00:00Z" });
    expect(acceptedPhotos(view)).toBe(0);
    view.validations.pop();
    view.photos[0].current = false;
    expect(acceptedPhotos(view)).toBe(0);
  });

  it("formats only display labels, not IDs", () => {
    expect(label("acceptance_certificate")).toBe("Acceptance Certificate");
  });

  it("keeps agent execution, assessment and publication distinct", () => {
    const raw = { publicationId: "test-id", createdAt: 1793354884, status: "Completed", responseId: "resp-1",
      assessment: { evidenceId: "PHOTO-20", compliant: false, confidence: 0.9,
        findings: ["Azimuth mismatch"], rfdsReference: "RFDS v1", observedAzimuthDegrees: 127 },
      publication: { status: "NotConfirmed", eventIds: [], error: null }, error: null };
    const run = parseValidationRun(raw);
    expect(run.assessment?.compliant).toBe(false);
    expect(run.publication.status).toBe("NotConfirmed");
    expect(validationActive(run)).toBe(false);
    for (const status of ["Preparing", "Starting", "Running", "Unknown"]) {
      expect(validationActive(parseValidationRun({ ...raw, status }))).toBe(true);
    }
    expect(() => parseValidationRun({ ...raw, status: "Ready" })).toThrow();
    expect(() => parseValidationRun({ ...raw, publication: { ...raw.publication,
      status: "FabricReady" } })).toThrow();
    expect(() => parseValidationRun({ ...raw, assessment: { ...raw.assessment,
      compliant: "false" } })).toThrow();
  });

  it("requires explicit validation configuration and pinned target references", () => {
    expect(parseValidationTarget({ configured: false })).toEqual({ configured: false });
    expect(() => parseValidationTarget({ configured: true, siteId: "SE-STO-0042" })).toThrow();
    expect(validationActive(null)).toBe(false);
  });

  it("explains running, review and delivery states without claiming downstream completion", () => {
    const raw = { publicationId: "test-id", createdAt: 1793354884, status: "Running",
      responseId: "resp-1", assessment: null,
      publication: { status: "NotConfirmed", eventIds: [], error: null }, error: null };
    const render = (value: unknown) => renderToStaticMarkup(createElement(ValidationOutcome,
      { run: parseValidationRun(value), elapsedSeconds: 73 }));
    const running = render(raw);
    expect(running).toContain("Checking your photo...");
    expect(running).toContain("1m 13s elapsed");
    expect(running).toContain("updates appear here automatically");
    expect(running).not.toContain("<strong>Assessment submitted</strong>");
    expect(running).toContain("Photo received");
    expect(running).toContain("Evidence checked");
    expect(running).toContain("Result submitted");
    const paused = renderToStaticMarkup(createElement(ValidationOutcome,
      { run: parseValidationRun(raw), elapsedSeconds: 73, updatesPaused: true }));
    expect(paused).toContain("Automatic updates paused");
    expect(paused).not.toContain("updates appear here automatically");
    const finished = { ...raw, status: "Completed",
      assessment: { evidenceId: "PHOTO-20", compliant: false, confidence: 0.95,
        findings: ["Azimuth differs"], rfdsReference: "Sector B azimuth 120 degrees",
        observedAzimuthDegrees: 127 },
      publication: { status: "Published", eventIds: ["one", "two", "three"], error: null } };
    const reviewed = render(finished);
    expect(reviewed).toContain("Photo needs review");
    expect(reviewed).toContain("Assessment submitted");
    expect(reviewed).toContain("does not confirm that the site is ready for close-out");
    expect(reviewed).toContain("Confirm the applicable program tolerance");
    expect(reviewed).not.toContain("elapsed");
    expect(reviewed).not.toContain("<details open");
    expect(reviewed).toContain("What happens next?");
    expect(reviewed).toContain("approved site requirements");
    expect(reviewed).toContain("replacement evidence");
    expect(reviewed).toContain("Other outstanding issues must also be cleared");
    expect(reviewed).not.toMatch(/Fabric|Foundry|publisher|event IDs|Technical details|Publication ID/);
    expect(reviewed).not.toContain("resp-1");
    expect(reviewed).not.toContain("test-id");
    expect(reviewed).not.toContain("model confidence");
    const unconfirmed = render({ ...finished, publication: raw.publication });
    expect(unconfirmed).toContain("Submission not yet confirmed");
    expect(unconfirmed).toContain("Do not start another photo check to resend this result");
    expect(unconfirmed).not.toContain("<strong>Assessment submitted</strong>");
    const unknown = render({ ...raw, status: "Unknown" });
    expect(unknown).toContain("Submission needs checking");
    expect(unknown).not.toContain("updates appear here automatically");
    const failed = render({ ...raw, status: "Failed" });
    expect(failed).toContain("The photo check could not finish");
    expect(failed).toContain("contact support before submitting the photo again");
    const compliant = render({ ...finished, assessment: { ...finished.assessment, compliant: true } });
    expect(compliant).toContain("Photo check complete");
    expect(compliant).toContain("does not approve the site for close-out");
    expect(compliant).toContain("Any required approvals still apply");
    expect(compliant).not.toContain("replacement evidence or corrective work");
    const missing = render({ ...finished, assessment: null });
    expect(missing).toContain("Result unavailable");
    expect(missing).not.toContain("Photo needs review");
    expect(missing).not.toContain("Photo check complete");
    const errors = render({ ...raw, status: "Failed", error: "Foundry response failed",
      publication: { ...raw.publication, error: "Fabric publisher failed" } });
    expect(errors).toContain("The photo check reported a problem");
    expect(errors).toContain("assessment could not be confirmed as submitted");
    expect(errors).not.toMatch(/Fabric|Foundry|publisher/);
  });
});

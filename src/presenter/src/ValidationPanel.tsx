import { useEffect, useState } from "react";
import {
  fetchJson, parseValidationRun, parseValidationTarget, validationActive,
} from "./model";
import type { ValidationRun, ValidationTarget } from "./model";

const inProgress = (run: ValidationRun | null) =>
  run !== null && ["Preparing", "Starting", "Running"].includes(run.status);

export function ValidationOutcome({ run, elapsedSeconds, updatesPaused = false }: {
  run: ValidationRun | null; elapsedSeconds: number; updatesPaused?: boolean;
}) {
  const assessment = run?.assessment;
  const published = run?.publication.status === "Published";
  const interrupted = run?.status === "Unknown" || run?.status === "Failed";
  const working = !run || inProgress(run);
  useEffect(() => {
    if (run?.error) console.error("Photo check failed", run.error);
    if (run?.publication.error) console.error("Assessment submission failed", run.publication.error);
  }, [run?.error, run?.publication.error]);
  const title = !run ? "Sending your photo..."
    : run.status === "Unknown" ? "Submission needs checking"
    : run.status === "Failed" ? "The photo check could not finish"
    : run.status === "Preparing" ? "Preparing your photo..."
    : run.status === "Starting" ? "Sending your photo..."
    : run.status === "Running" ? "Checking your photo..."
    : !assessment ? "Result unavailable"
    : assessment.compliant ? "Photo check complete" : "Photo needs review";
  const description = interrupted
    ? "Refresh status before starting another check. Any confirmed submission is shown separately below."
    : working
      ? updatesPaused
        ? "Automatic updates paused. Your photo may still be being checked. Refresh status to reconnect to the same submission."
        : run?.status === "Running"
          ? "Your photo is being compared with the checklist and RFDS. This can take a few minutes; updates appear here automatically."
          : "Preparing and submitting your photo for checking. This can take a few minutes; updates appear here automatically."
      : !assessment ? "No assessment is available. Refresh status before making a decision."
      : assessment.compliant
        ? "The photo was assessed as meeting the checked requirements. Review the result before accepting the evidence; this does not approve the site for close-out."
        : "The check flagged a difference. Review the photo and RFDS before deciding whether replacement evidence or rework is needed.";
  const steps = [
    { title: "Photo received", state: run?.responseId ? "done" : interrupted ? "attention" : "current",
      detail: run?.responseId ? "Submitted for checking" : interrupted ? "Check submission status" : "Submitting securely" },
    { title: "Evidence checked", state: assessment ? "done" : interrupted || !working ? "attention"
      : run?.status === "Running" ? "current" : "waiting",
      detail: assessment ? assessment.compliant ? "Check complete" : "Checked - review needed"
        : interrupted || !working ? "Result unavailable" : "Compare photo, checklist and RFDS" },
    { title: "Result submitted", state: published ? "done" : run && !inProgress(run) ? "attention" : "waiting",
      detail: published ? "Submission confirmed" : "Confirmation pending" },
  ];
  return <div className="validation-result">
    <div className={`validation-summary ${interrupted ? "issue" : working ? "working"
      : assessment?.compliant ? "checked" : "review"}`} role="status">
      <div className="validation-summary-heading">
        <h3>{working && <span className="validation-spinner" aria-hidden="true" />}{title}</h3>
        {working && <span className="elapsed-time">
          {elapsedSeconds < 60 ? `${elapsedSeconds}s elapsed`
            : `${Math.floor(elapsedSeconds / 60)}m ${elapsedSeconds % 60}s elapsed`}
        </span>}
      </div>
      <p>{description}</p>
    </div>
    <ol className="validation-progress" aria-label="Photo validation progress">
      {steps.map((step, index) => <li className={step.state} key={step.title}
        aria-current={step.state === "current" ? "step" : undefined}>
        <span className="validation-step-number" aria-hidden="true">{index + 1}</span>
        <div><strong>{step.title}</strong><small>{step.detail}</small></div>
      </li>)}
    </ol>
    {assessment && <div className="assessment-result">
      {(assessment.observedAzimuthDegrees !== null || assessment.rfdsReference) &&
        <dl className="observation-grid">
          {assessment.observedAzimuthDegrees !== null && <div>
            <dt>Observed in the photo</dt><dd>{assessment.observedAzimuthDegrees}&deg;</dd>
          </div>}
          {assessment.rfdsReference && <div>
            <dt>RFDS reference</dt><dd>{assessment.rfdsReference}</dd>
          </div>}
        </dl>}
      {!assessment.compliant && <p className="review-guidance">
        Confirm the applicable program tolerance before making a rework decision.
        A flagged difference is not an instruction to start corrective work.
      </p>}
      <details className="agent-findings"><summary>Read the findings</summary>
        <ul>{assessment.findings.map((finding, index) => <li key={index}>{finding}</li>)}</ul>
      </details>
    </div>}
    {run && !working && <div className={`publication-notice ${published ? "delivered" : "unconfirmed"}`}>
      <strong>{published ? "Assessment submitted" : "Submission not yet confirmed"}</strong>
      <p>{published
        ? "The photo-check result has been submitted. This result alone does not confirm that the site is ready for close-out."
        : "Refresh status to check whether the result was submitted. Do not start another photo check to resend this result."}</p>
    </div>}
    {run?.error && <p role="alert" className="error-box">
      The photo check reported a problem. Refresh status or contact support before trying again.
    </p>}
    {run?.publication.error && <p role="alert" className="error-box">
      The assessment could not be confirmed as submitted. Refresh status before taking further action.
    </p>}
    <div className="validation-next">
      <h3>What happens next?</h3>
      {interrupted || (!working && !assessment)
        ? <p>Refresh status before starting another check. If the result remains unavailable,
          contact support before submitting the photo again.</p>
        : working ? <p>When the check is complete, review the finding against the approved site requirements.</p>
        : assessment?.compliant
          ? <p>Review the result and clear any other outstanding issues before submitting
            the site for close-out review. Any required approvals still apply.</p>
          : <p>Check the finding against the approved site requirements. If replacement evidence
            or corrective work is needed, resolve it before submitting the site for close-out
            review. Other outstanding issues must also be cleared.</p>}
    </div>
  </div>;
}

export function ValidationPanel({ siteId }: { siteId: string }) {
  const [target, setTarget] = useState<ValidationTarget | null>(null);
  const [run, setRun] = useState<ValidationRun | null>(null);
  const [posting, setPosting] = useState(false);
  const [error, setError] = useState("");
  const [photoError, setPhotoError] = useState(false);
  const [reload, setReload] = useState(0);
  const [uncertainPost, setUncertainPost] = useState(false);
  const [submittedAt, setSubmittedAt] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const working = posting || inProgress(run);

  useEffect(() => {
    if (!working) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [working]);

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const info = parseValidationTarget(await fetchJson("/api/presenter/validation"));
        let latest = info.configured ? info.latest : null;
        if (latest && !["Preparing", "Starting", "Running"].includes(latest.status)
          && latest.publication.status !== "Published") {
          latest = parseValidationRun(await fetchJson(
            `/api/presenter/validation/run?publicationId=${encodeURIComponent(latest.publicationId)}`));
        }
        if (active) {
          setTarget(info); setRun(latest);
          setError(""); setUncertainPost(false);
        }
      } catch (error) {
        console.error("Photo check status could not be loaded", error);
        if (active) setError("Photo-check status could not be loaded. Refresh status before submitting again.");
      }
    }
    void load();
    return () => { active = false; };
  }, [reload]);

  useEffect(() => {
    if (!run || !["Preparing", "Starting", "Running"].includes(run.status)) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const id = run.publicationId;
    async function poll() {
      try {
        const next = parseValidationRun(await fetchJson(
          `/api/presenter/validation/run?publicationId=${encodeURIComponent(id)}`));
        if (!active) return;
        setRun(next); setError("");
        if (["Preparing", "Starting", "Running"].includes(next.status)) {
          timer = setTimeout(() => { void poll(); }, 2500);
        }
      } catch (error) {
        console.error("Photo check updates paused", error);
        if (active) setError("Updates paused. Refresh status to reconnect to the same submission.");
      }
    }
    timer = setTimeout(() => { void poll(); }, 1500);
    return () => { active = false; clearTimeout(timer); };
  }, [run?.publicationId, run?.status, reload]);

  async function submit() {
    if (!target?.configured || posting || validationActive(run) || uncertainPost) return;
    const started = Date.now();
    setSubmittedAt(started); setNow(started);
    setPosting(true); setRun(null); setError("");
    try {
      const state = parseValidationRun(await fetchJson("/api/presenter/validation", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ siteId, evidenceId: target.evidenceId,
          publicationId: crypto.randomUUID() }),
      }));
      setRun(state);
    } catch (error) {
      console.error("Photo submission could not be confirmed", error);
      setUncertainPost(true);
      setError("Submission could not be confirmed. Refresh status before considering another submission.");
    } finally { setPosting(false); }
  }

  const available = target?.configured && target.siteId === siteId;
  const started = posting ? submittedAt : run ? run.createdAt * 1000 : null;
  const elapsed = started === null ? 0 : Math.max(0, Math.floor((now - started) / 1000));
  return <section className="card live-validation" aria-labelledby="validation-heading">
    <div className="section-heading"><h2 id="validation-heading">Photo check</h2>
      <span className="live-source-label">Evidence review</span></div>
    {!target && !error && <p className="validation-note" role="status">Loading photo...</p>}
    {target && !available && <p className="validation-note">
      {target.configured ? "Photo checking is not available for this site."
        : "No photo is available for checking."}</p>}
    {available && target.configured && <>
      <div className="validation-input">
        <div className="photo-preview">{photoError
          ? <p role="alert">The photo could not be loaded. Refresh the page to try again.</p>
          : <img src="/api/presenter/validation/photo" alt={`Demo photo ${target.evidenceId}`}
            onError={() => setPhotoError(true)} />}</div>
        <div className="validation-context">
          <span className="fixture-tag">Demo photo</span>
          <h3>{target.evidenceId}</h3>
          <p>Compare this photo with the checklist and RFDS to identify anything that needs attention.</p>
          <button className="primary-button" disabled={posting || validationActive(run) || uncertainPost}
            onClick={() => void submit()}>
            {posting ? "Submitting photo..." : validationActive(run)
              ? run?.status === "Unknown" ? "Refresh status before retrying" : "Photo check in progress..."
              : run ? "Run another photo check" : "Submit for validation"}
          </button>
          <p className="validation-note">Checking may take a few minutes. The assessment does not
            approve the photo or change this demo case overview.</p>
        </div>
      </div>
      {(run || posting) && <ValidationOutcome run={posting ? null : run}
        elapsedSeconds={elapsed} updatesPaused={Boolean(error)} />}
    </>}
    {error && <p role="alert" className="error-box">{error}</p>}
    <button className="text-button" disabled={posting} onClick={() => setReload((value) => value + 1)}>
      Refresh status
    </button>
  </section>;
}

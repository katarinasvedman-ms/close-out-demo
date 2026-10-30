import { StrictMode, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { createRoot } from "react-dom/client";
import { acceptedPhotos, fetchJson, label, parseAnswer, parseCases, parseStatus } from "./model";
import type { AgentStatus, Answer, CaseView } from "./model";
import { ValidationPanel } from "./ValidationPanel";
import { AssistantAnswer } from "./AssistantAnswer";
import { BriefPanel } from "./BriefPanel";
import "./styles.css";

type IconName = "grid" | "spark" | "arrow" | "file" | "check" | "clock" | "site" | "shield";
const paths: Record<IconName, string> = {
  grid: "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
  spark: "m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z",
  arrow: "M5 12h14 m-6-6 6 6-6 6",
  file: "M14 2H5v20h14V7z M14 2v6h5 M8 13h8 M8 17h6",
  check: "m5 12 4 4L19 6",
  clock: "M12 8v5l3 2 M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0",
  site: "M4 21V7h10v14 M14 12h6v9 M8 11h2 M8 15h2 M2 21h20",
  shield: "m12 2 9 4v6c0 5-9 10-9 10S3 17 3 12V6z m-5 10 3 3 5-6",
};
function Icon({ name }: { name: IconName }) {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"
    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]} /></svg>;
}

const date = (value: string) => new Date(value).toLocaleString(undefined,
  { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

interface Message {
  role: "user" | "assistant";
  content: string;
  answer?: Answer;
  saved?: boolean;
}

function Assistant({ site, status }: { site: CaseView; status: AgentStatus | null }) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [question, setQuestion] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const query = new URLSearchParams(window.location.search);
  const savedResponseId = query.get("siteId") === site.siteId ? query.get("responseId") : null;
  const [restoring, setRestoring] = useState(savedResponseId !== null);
  const bottom = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (savedResponseId === null) return;
    let active = true;
    const query = new URLSearchParams({ responseId: savedResponseId, siteId: site.siteId });
    void fetchJson(`/api/presenter/response?${query}`).then(parseAnswer).then((answer) => {
      if (active) setMessages([{ role: "assistant", content: answer.text, answer, saved: true }]);
    }).catch((error: unknown) => {
      console.error("Saved answer could not be opened", error);
      if (active) setError("This saved answer could not be opened. Check your link or try again.");
    }).finally(() => { if (active) setRestoring(false); });
    return () => { active = false; };
  }, [savedResponseId, site.siteId]);
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [messages, pending]);

  async function send(content: string) {
    if (!content.trim() || pending || restoring || !status?.configured) return;
    const history: Message[] = [...messages, { role: "user", content: content.trim() }];
    setMessages(history);
    setQuestion("");
    setPending(true);
    setError("");
    try {
      const answer = parseAnswer(await fetchJson("/api/presenter/chat", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ siteId: site.siteId,
          messages: history.slice(-11).map(({ role, content }) => ({ role, content })) }),
      }));
      setMessages([...history, { role: "assistant", content: answer.text, answer }]);
    } catch (error) {
      console.error("Close-out assistant request failed", error);
      setError("The assistant could not return an answer. Wait before sending the same question again, or contact support.");
    } finally {
      setPending(false);
    }
  }
  function submit(event: FormEvent) { event.preventDefault(); void send(question); }
  const prompts = [site.readiness === "Blocked" ? "Why is this site blocked?"
    : "What is this site's readiness?", "What does the RFDS require?", "What should I do next?"];
  return <aside className={`assistant-panel${messages.length === 0 ? " assistant-empty" : ""}`}
    aria-label="Close-out assistant">
    <div className="assistant-heading">
      <span className="assistant-icon"><Icon name="spark" /></span>
      <div><h2>Close-out assistant</h2><p>Your evidence, connected.</p></div>
    </div>
    <div className="assistant-scope"><span className="status-dot" />{site.siteId}
      <span className="mini-badge">Read only</span></div>
    <div className="chat-scroll">
      {messages.length === 0 && !restoring && <div className="assistant-welcome">
        <div className="welcome-mark"><Icon name="spark" /></div>
        <h3>A little clarity.<br />A clear next step.</h3>
        <p>Ask about this site's evidence, acceptance requirements or next steps.</p>
        <div className="source-list"><span>Site records <small>Evidence and related work</small></span>
          <span>Requirements &amp; documents <small>RFDS and acceptance</small></span></div>
        <div className="suggestions">{prompts.map((prompt) =>
          <button key={prompt} disabled={!status?.configured} onClick={() => void send(prompt)}>
            {prompt}<Icon name="arrow" /></button>)}</div>
      </div>}
      <div className="messages" role="log" aria-label="Assistant conversation" aria-live="polite">
        {messages.map((message, index) => <div key={index} className={`message ${message.role}`}>
          <span className="message-author">{message.role === "user" ? "You" : "Close-out assistant"}</span>
          {message.saved && <p className="saved-answer-label">Previously saved answer</p>}
          {message.answer ? <AssistantAnswer answer={message.answer} siteId={site.siteId} />
            : <div className="message-text">{message.content}</div>}
          {message.answer && <div className="retrieval-receipt">
            {[...new Set(message.answer.retrieval.map((row) => row.source))].map((source) =>
              <span key={source}><Icon name="check" />{source === "documents"
                ? "Requirements & documents" : "Site records"}</span>)}
            {message.answer.retrieval.length === 0 && <span>No source information available</span>}
            <a className="saved-answer-link" href={`?${new URLSearchParams({ siteId: site.siteId,
              responseId: message.answer.responseId })}`}>Open saved answer</a>
          </div>}
        </div>)}
        {pending && <p className="thinking"><span className="pulse" />Consulting your sources...</p>}
        {restoring && <p className="thinking">Opening saved answer...</p>}
      </div>
      {error && <div role="alert" className="error-box">{error}</div>}
      <div ref={bottom} />
    </div>
    <form className="composer" onSubmit={submit}>
      <label className="sr-only" htmlFor="question">Ask the close-out assistant</label>
      <textarea id="question" value={question} onChange={(event) => setQuestion(event.target.value)}
        maxLength={3000} rows={4} placeholder="Ask about this site..."
        disabled={!status?.configured || pending || restoring}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void send(question); }
        }} />
      <div className="composer-bottom"><small>{status?.configured
        ? "Ready to help" : "Assistant unavailable"}</small>
        <button className="send-button" type="submit" aria-label="Send message"
          disabled={!question.trim() || pending || restoring || !status?.configured}><Icon name="arrow" /></button></div>
    </form>
    <p className="assistant-footer">Suggestions, not approvals. Always review the evidence.</p>
  </aside>;
}

function Lifecycle({ site }: { site: CaseView }) {
  const stages = ["Construction", "Evidence review", "Rework", "Ready", "Submitted", "Accepted"];
  const current = !site.constructionComplete ? 0 : site.readiness === "Ready" ? 3 : 1;
  return <section className="card lifecycle" aria-labelledby="lifecycle-heading">
    <div className="section-heading"><h2 id="lifecycle-heading">Close-out lifecycle</h2>
      <span className="muted">Demo case overview</span></div>
    <ol className="stage-list">{stages.map((stage, index) => <li key={stage}
      className={index === current ? "current" : index === 0 && site.constructionComplete ? "complete" : ""}>
      <span className="stage-number">{index === 0 && site.constructionComplete
        ? <Icon name="check" /> : `0${index + 1}`}</span><span>{stage}</span>
      {index === current && <small>Current focus</small>}
    </li>)}</ol>
    <p className="lifecycle-note">Resolve outstanding evidence and work before submitting the site for acceptance.</p>
  </section>;
}

function CaseDetail({ site, ask }: { site: CaseView; ask: () => void }) {
  const [selectedEvidence, setSelectedEvidence] = useState<string | null>(null);
  const rejected = site.validations.filter((row) => row.photoId && !row.compliant);
  const currentPhotos = site.photos.filter((row) => row.current);
  return <>
    <section className="case-hero">
      <div className="eyebrow">{site.siteId}<span className={`badge ${site.readiness.toLowerCase()}`}>
        <span className="status-dot" />{site.readiness === "NotReady" ? "Not ready" : site.readiness}</span>
        <span className="fixture-tag">Demo case</span></div>
      <div className="hero-body"><div><h1>{site.readiness === "Blocked"
        ? "A few gaps. A clear path forward." : site.readiness === "Ready"
          ? "Evidence complete. Ready for review." : "Awaiting construction completion."}</h1>
        <p>{site.siteName} <span className="separator">/</span> {site.customer}</p></div>
        <button className="primary-button" onClick={ask}><Icon name="spark" />Ask assistant</button></div>
      <div className="case-reference"><span>Case</span><code>{site.caseId}</code></div>
    </section>
    <div className="metric-grid">
      <div className="metric"><span>Accepted photos</span><strong>{acceptedPhotos(site)}
        <small> / {currentPhotos.length}</small></strong><div className="progress-track">
        <div style={{ width: `${acceptedPhotos(site) / Math.max(currentPhotos.length, 1) * 100}%` }} /></div></div>
      <div className="metric"><span>Outstanding issues</span><strong>{site.missing.length}
        <small>{site.missing.length ? " need attention" : " outstanding"}</small></strong>
        <p>{site.missing.length ? "Evidence and blocking work" : "No outstanding issues in this overview"}</p></div>
      <div className="metric"><span>Program threshold</span><strong>{Math.round(site.threshold * 100)}
        <small>% confidence</small></strong><p>Auto-reviewed compliant photos</p></div>
    </div>
    <Lifecycle site={site} />
    <ValidationPanel siteId={site.siteId} />
    <BriefPanel key={site.siteId} siteId={site.siteId} />
    <div className="detail-grid">
      <section className="card basic-info"><div className="section-heading"><h2>Basic information</h2><Icon name="site" /></div>
        <dl><div><dt>Site</dt><dd>{site.siteName}</dd></div><div><dt>Program</dt><dd>{site.program}</dd></div>
          <div><dt>Construction complete</dt><dd>{site.constructionComplete ? "Actualized" : "Planned"}</dd></div>
          <div><dt>Customer</dt><dd>{site.customer}</dd></div>
          <div><dt>Case snapshot</dt><dd>{date(site.evaluatedAt)}</dd></div>
          <div><dt>Overview</dt><dd>Illustrative demo case</dd></div></dl>
      </section>
      <section className="card activity" id="activity"><div className="section-heading"><h2>Your next steps</h2><Icon name="clock" /></div>
        <div className="timeline"><div className="timeline-item"><span className="timeline-dot" />
          <small>Current focus</small><h3>{!site.constructionComplete ? "Complete construction first"
            : site.readiness === "Ready" ? "Prepare for close-out review" : "Resolve outstanding issues"}</h3>
          <p>{!site.constructionComplete ? "Construction must be completed before the site can be ready for close-out."
            : site.readiness === "Ready" ? "Review the evidence and required approvals before submitting the close-out package."
              : "Review the flagged evidence and blocking work before submitting the site for close-out review."}</p></div>
          <div className="timeline-item"><span className="timeline-dot hollow" /><small>Evidence review</small>
            <h3>Confirm the requirements</h3><p>Use the assistant to compare findings with the approved RFDS
              and acceptance requirements.</p></div>
          <div className="timeline-item"><span className="timeline-dot hollow" /><small>Decision rights</small>
            <h3>Review before acting</h3><p>The Construction Manager approves rework.
              The Coordinator submits the close-out package; the Customer PM accepts it.</p></div>
        </div>
      </section>
      <section className="card evidence" id="evidence"><div className="section-heading"><h2>Evidence &amp; related work</h2>
        <span className="count-badge">{rejected.length + site.tickets.length}</span></div>
        {rejected.map((row) => <button className="evidence-row" key={row.id}
          aria-expanded={selectedEvidence === row.id}
          onClick={() => setSelectedEvidence(selectedEvidence === row.id ? null : row.id)}>
          <span className="evidence-icon warning"><Icon name="file" /></span>
          <span><strong>{row.photoId}</strong><small>{row.findings.join(". ")}</small></span>
          <span className="row-status">Review<Icon name="arrow" /></span>
        </button>)}
        {selectedEvidence && <div className="evidence-detail">
          <strong>Assessment from the demo case overview</strong>
          {site.validations.filter((row) => row.id === selectedEvidence).map((row) =>
            <dl key={row.id}><div><dt>Validation ID</dt><dd>{row.id}</dd></div>
              <div><dt>Finding</dt><dd>{row.findings.join(". ")}</dd></div>
              <div><dt>Confidence / review</dt><dd>{Math.round(row.confidence * 100)}% / {row.reviewStatus}</dd></div></dl>)}
        </div>}
        {site.tickets.map((ticket) => <div className="evidence-row" key={ticket.id}>
          <span className="evidence-icon"><Icon name="shield" /></span><span><strong>{ticket.id}</strong>
            <small>{ticket.description}</small></span><span className="row-status">{ticket.status}</span></div>)}
        {site.workOrders.map((order) => <div className="evidence-row" key={order.id}>
          <span className="evidence-icon"><Icon name="grid" /></span><span><strong>{order.id}</strong>
            <small>{order.type} / {order.reason}</small></span><span className="row-status">{order.status}</span></div>)}
        <details className="document-list"><summary>Program documents <span>{site.documents.length}</span></summary>
          {site.documents.map((doc) => <div key={doc.id}><Icon name="file" /><span><strong>{label(doc.type)}</strong>
            <small>{doc.id} / v{doc.version}</small></span><span className="row-status">{doc.status}</span></div>)}</details>
        <div className="unavailable-action">
          <small>New photo assessments appear in the photo-check panel above.
            This demo case overview does not update automatically.</small></div>
      </section>
    </div>
  </>;
}

function App() {
  const [cases, setCases] = useState<CaseView[]>([]);
  const [selected, setSelected] = useState("");
  const [status, setStatus] = useState<AgentStatus | null>(null);
  const [error, setError] = useState("");
  const [queue, setQueue] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const [caseData, agentData] = await Promise.all([
          fetchJson("/api/presenter/cases"), fetchJson("/api/presenter/status"),
        ]);
        const rows = parseCases(caseData), agent = parseStatus(agentData);
        const requestedSite = new URLSearchParams(window.location.search).get("siteId");
        if (requestedSite && !rows.some((row) => row.siteId === requestedSite)) {
          throw new Error("The saved answer references an unconfigured site.");
        }
        if (active) { setCases(rows); setSelected(requestedSite ?? rows[0]?.siteId ?? "");
          setStatus(agent); setError(""); }
      } catch (error) {
        console.error("Workbench could not be loaded", error);
        if (active) setError("The workbench could not be loaded. Check your link or try again.");
      }
    }
    void load();
    return () => { active = false; };
  }, [attempt]);
  const site = cases.find((row) => row.siteId === selected);
  return <div className="app-shell">
    <nav className="nav-rail" aria-label="Workbench navigation">
      <a className="brand-mark" href="#" aria-label="Close-out Workbench"><Icon name="shield" /></a>
      <div className="rail-links"><button className={queue ? "active" : ""} title="Cases" aria-label="Case queue"
        onClick={() => setQueue(true)}><Icon name="grid" /></button>
        <button className={!queue ? "active" : ""} title="Site 360" aria-label="Site 360"
          onClick={() => setQueue(false)}><Icon name="site" /></button>
        <button title="Evidence" aria-label="Evidence" onClick={() => {
          setQueue(false); requestAnimationFrame(() => document.getElementById("evidence")?.scrollIntoView({ behavior: "smooth" }));
        }}><Icon name="file" /></button></div>
      <span className="rail-avatar" title="Coordinator demo">CO</span>
    </nav>
    {site && <Assistant key={site.siteId} site={site} status={status} />}
    <div className="workspace">
      <header className="topbar"><div className="wordmark">Close-out<span>Workbench</span></div>
        <div className="topbar-right"><span className="synthetic-badge"><span className="status-dot" />Demo / synthetic data</span>
          <span className="persona"><span className="avatar">CO</span>Coordinator<small>Site operations</small></span></div></header>
      <main className="main-content">
        <div className="breadcrumb"><button onClick={() => setQueue(true)}>Cases</button><span>/</span>
          <span>{queue ? "Work queue" : site?.siteId ?? "Loading"}</span><span className="breadcrumb-end">Site workspace</span></div>
        {error && <div role="alert" className="error-box">{error}
          <button onClick={() => setAttempt((value) => value + 1)}>Retry connection</button></div>}
        {!site && !error && <p role="status">Loading the workbench...</p>}
        {site && (queue ? <section className="case-queue"><div className="queue-heading"><div>
          <p className="eyebrow">YOUR WORKSPACE</p><h1>Every site. One clear next step.</h1></div>
          <span className="fixture-tag">{cases.length} demo sites</span></div>
          {cases.map((row) => <button key={row.siteId} className="queue-row" onClick={() => {
            setSelected(row.siteId); setQueue(false);
          }}><span className="evidence-icon"><Icon name="site" /></span><span><strong>{row.siteName}</strong>
            <small>{row.siteId} / {row.program}</small></span><span className={`badge ${row.readiness.toLowerCase()}`}>
              {row.readiness}</span><Icon name="arrow" /></button>)}</section>
          : <CaseDetail key={site.siteId} site={site} ask={() => document.getElementById("question")?.focus()} />)}
      </main>
      <footer className="workspace-footer"><span>Close-out Workbench / demonstration</span>
        <span>Approvals and case updates are not available here.</span></footer>
    </div>
  </div>;
}

const root = document.getElementById("root");
if (!root) throw new Error("Missing Presenter root element");
createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

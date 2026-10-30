import { useEffect, useState } from "react";
import Markdown from "react-markdown";
import { fetchJson, parseBrief, parseBriefs, safeSourceUrl } from "./model";
import type { Brief, BriefSummary } from "./model";

const savedDate = (value: string) => new Date(value).toLocaleString(undefined,
  { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });

export function BriefDocument({ brief }: { brief: Brief }) {
  return <article className="brief-document" aria-label="Saved close-out brief">
    <p className="brief-saved-date">Saved {savedDate(brief.savedAt)} · Draft recommendation</p>
    <Markdown skipHtml disallowedElements={["img"]}
      urlTransform={(url) => safeSourceUrl(url) ? url : ""}
      components={{ a: ({ children, href }) => href
        ? <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>
        : <span>{children}</span> }}>{brief.markdown}</Markdown>
  </article>;
}

export function BriefPanel({ siteId }: { siteId: string }) {
  const [briefs, setBriefs] = useState<BriefSummary[]>([]);
  const [selected, setSelected] = useState("");
  const [brief, setBrief] = useState<Brief | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const [opening, setOpening] = useState(false);
  const [listError, setListError] = useState("");
  const [readError, setReadError] = useState("");

  useEffect(() => {
    let active = true;
    setLoading(true);
    setListError("");
    const query = new URLSearchParams({ siteId });
    void fetchJson(`/api/presenter/briefs?${query}`).then((value) => parseBriefs(value, siteId))
      .then((rows) => {
        if (!active) return;
        setBriefs(rows);
        setSelected((previous) => rows.some((row) => row.id === previous)
          ? previous : rows[0]?.id ?? "");
      }).catch((error: unknown) => {
        console.error("Could not list close-out briefs", error);
        if (active) setListError("Saved briefs could not be loaded. Use Refresh to try again.");
      }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [siteId, refresh]);

  useEffect(() => {
    if (!selected) return;
    let active = true;
    setOpening(true);
    setReadError("");
    setBrief(null);
    const query = new URLSearchParams({ siteId, briefId: selected });
    void fetchJson(`/api/presenter/brief?${query}`)
      .then((value) => parseBrief(value, siteId, selected))
      .then((value) => { if (active) setBrief(value); })
      .catch((error: unknown) => {
        console.error("Could not open close-out brief", error);
        if (active) setReadError("This brief could not be opened. Use Refresh to try again.");
      }).finally(() => { if (active) setOpening(false); });
    return () => { active = false; };
  }, [siteId, selected, refresh]);

  return <section className="card briefs-panel" id="briefs" aria-labelledby="briefs-heading">
    <div className="section-heading"><div><h2 id="briefs-heading">Close-out briefs</h2>
      <p className="muted">Outstanding issues, evidence and recommended next steps.</p></div>
      <button className="brief-refresh" onClick={() => setRefresh((value) => value + 1)}
        disabled={loading || opening}>Refresh</button></div>
    <p className="brief-boundary">Saved briefs refer to a particular evidence snapshot and may not include newer photo checks.
      They are not approvals and do not change the case overview.</p>
    {loading && <p role="status">Loading saved briefs...</p>}
    {listError && <p className="error-box" role="alert">{listError}</p>}
    {!loading && !listError && briefs.length === 0 &&
      <p className="brief-empty">No close-out briefs have been saved for this site yet.</p>}
    {!loading && !listError && briefs.length > 0 && <div className="brief-layout">
      <nav className="brief-list" aria-label="Saved briefs">{briefs.map((row) =>
        <button key={row.id} className={row.id === selected ? "selected" : ""}
          aria-current={row.id === selected ? "true" : undefined}
          onClick={() => setSelected(row.id)}>
          <strong>Close-out review</strong><time dateTime={row.savedAt}>{savedDate(row.savedAt)}</time>
          <span>{row.readiness === "Ready" ? "Ready for review" : "Needs attention"}</span>
        </button>)}</nav>
      <div className="brief-reader" aria-live="polite">
        {opening && <p role="status">Opening brief...</p>}
        {readError && <p className="error-box" role="alert">{readError}</p>}
        {!opening && !readError && brief && brief.id === selected && <BriefDocument brief={brief} />}
      </div>
    </div>}
  </section>;
}

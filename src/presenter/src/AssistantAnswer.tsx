import { useEffect, useState } from "react";
import Markdown from "react-markdown";
import { fetchJson, parseAnswer, safeSourceUrl } from "./model";
import type { Answer, Citation } from "./model";

export function linkedAnswer(answer: Answer) {
  const characters = Array.from(answer.text);
  const sources: (Citation & { href: string; number: number })[] = [];
  let markdown = "", cursor = 0;
  for (const citation of answer.citations ?? []) {
    let source = sources.find((row) => row.url === citation.url);
    if (!source) {
      source = { ...citation, number: sources.length + 1,
        href: `#source-${encodeURIComponent(answer.responseId)}-${sources.length + 1}` };
      sources.push(source);
    }
    const span = characters.slice(citation.start, citation.end).join("");
    markdown += characters.slice(cursor, citation.start).join("");
    // Backend offsets use Unicode code points, not JavaScript UTF-16 units.
    if (!/^\u3010[^\u3011]*\u2020source\u3011$/u.test(span)) markdown += span;
    markdown += `[${source.number}](${source.href})`;
    cursor = citation.end;
  }
  return { markdown: markdown + characters.slice(cursor).join(""), sources };
}

export function AssistantAnswer({ answer, siteId }: { answer: Answer; siteId: string }) {
  const [restored, setRestored] = useState<Answer | null>(null);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  useEffect(() => {
    if (answer.citations !== undefined) return;
    let active = true;
    const query = new URLSearchParams({ responseId: answer.responseId, siteId });
    void fetchJson(`/api/presenter/response?${query}`).then(parseAnswer).then((value) => {
      if (active) setRestored(value);
    }).catch((error: unknown) => {
      console.error("Document sources could not be loaded", error);
      if (active) setError("Try opening the saved answer again.");
    });
    return () => { active = false; };
  }, [answer, siteId]);
  const { markdown, sources } = linkedAnswer(restored ?? answer);
  return <div className="assistant-answer">
    <Markdown skipHtml disallowedElements={["img"]}
      urlTransform={(url) => sources.some((source) => source.href === url)
        || safeSourceUrl(url) ? url : ""}
      components={{ a: ({ href, children }) => {
        const source = sources.find((source) => source.href === href);
        return source ? <a href={href} className="citation-link"
          aria-label={`Source ${source.number}: ${source.title}`}
          onClick={() => setExpanded(source.href)}>{children}</a>
          : href ? <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>
            : <span>{children}</span>;
      } }}>{markdown}</Markdown>
    {sources.length > 0 && <div className="answer-sources" aria-label="Document sources">
      <h3>Sources</h3>
      {sources.map((source) => <details key={source.href} id={source.href.slice(1)}
        open={expanded === source.href}
        onToggle={(event) => {
          if (event.currentTarget.open) setExpanded(source.href);
          else setExpanded((current) => current === source.href ? null : current);
        }}>
        <summary><span className="source-number">{source.number}</span>{source.title}</summary>
        <p>Document extract used for this answer, not the full document.
          Review it against the finding before making a decision.</p>
        <pre>{source.excerpt}</pre>
        <a href={source.url} target="_blank" rel="noopener noreferrer">Open original document</a>
        <p>You may need permission to open the original.</p>
      </details>)}
    </div>}
    {error && <p role="alert" className="error-box">Source links could not be loaded: {error}</p>}
  </div>;
}

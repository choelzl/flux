// Flux web, results (D955, D1095): the library's digests a loop's record holds -- each paper, who digested it, the
// text the prompts read.

import { Empty, html } from "../view.js";
import { plural } from "../format.js";
import { RefreshCard } from "../components/RefreshCard.js";

const base = p => String(p).split("/").pop();
/** results-32: the loop's own notes are digested too, but they are notes, not papers. */
export const isNote = d => /^notes?\.(md|txt)$/i.test(base(d.source));

/** results-32: what was digested and by which model, once ("8 papers · 1 note · qwen3"); each row its size. */
export function librarySaid(digests) {
  const notes = digests.filter(isNote).length, papers = digests.length - notes;
  const models = [...new Set(digests.map(d => d.model).filter(Boolean))];
  return [papers ? plural(papers, "paper") : "", notes ? plural(notes, "note") : "", models.length === 1 ? models[0] : ""].filter(Boolean).join(" · ");
}

export const Library = ({ data, refresh }) => {
  const one = new Set(data.digests.map(d => d.model).filter(Boolean)).size === 1;
  return html`<${RefreshCard} rawTitle="Library digests" said=${librarySaid(data.digests)} refresh=${refresh} raw=${data} cls="library-view">
  ${data.digests.length ? data.digests.map(d => html`<details key=${d.source} class="digest"><summary><strong>${base(d.source)}</strong>${" "}
      <small class="muted digest-meta">${[one ? "" : d.model || "?", `${Number(d.chars || 0).toLocaleString()} chars`, d.reused ? "reused" : ""].filter(Boolean).join(" · ")}</small></summary>
    <p class="muted small mono">${d.source}</p><pre class="digest-text">${d.digest}</pre></details>`)
  : html`<${Empty} text="No digests yet: each setup digests up to 8 papers from library/." />`}<//>`;
};

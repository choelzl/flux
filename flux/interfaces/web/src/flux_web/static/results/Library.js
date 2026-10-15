// Flux web, results (D955, D1095): the library's digests a loop's record holds -- each paper, who digested it, the
// text the prompts read.

import { Empty, html } from "../view.js";
import { plural } from "../format.js";
import { RefreshCard } from "../components/RefreshCard.js";

const base = p => String(p).split("/").pop();

export const Library = ({ data, refresh }) => html`<${RefreshCard} title="Library" rawTitle="Library digests" said=${`${plural(data.digests.length, "paper")} digested`} refresh=${refresh} raw=${data} cls="library-view">
  ${data.digests.length ? data.digests.map(d => html`<details key=${d.source} class="digest"><summary><strong>${base(d.source)}</strong>${" "}
      <small class="muted">${[d.model || "?", `${Number(d.chars || 0).toLocaleString()} chars read`, d.reused ? "kept from before" : ""].filter(Boolean).join(" · ")}</small></summary>
    <p class="muted small mono">${d.source}</p><pre class="digest-text">${d.digest}</pre></details>`)
  : html`<${Empty} text="No digests yet: with the library on, each Setup digests up to 8 papers of the loop's library/ folder." />`}<//>`;

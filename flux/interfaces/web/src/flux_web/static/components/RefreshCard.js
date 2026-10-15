// Flux web (D955, D1042, D1095): a card of what a loop's record holds, read on asking -- a line saying what it holds,
// Refresh, Raw (the answer as it came) and Fullscreen over its content.

import { Card, html } from "../view.js";
import { Act } from "./Act.js";
import { ViewerFrame, ViewerTools, useViewer } from "../viewer.js";

/** `said` the line; `refresh()`; `raw` the answer (shown as JSON, `rawTitle` its title); `cls` the content's class. */
export function RefreshCard({ title, said, refresh, raw, rawTitle = title, cls, children }) {
  const view = useViewer();
  return html`<${Card} title=${title}><div class="bar"><span class="muted">${said}</span>
      <${Act} cls="small" onClick=${refresh}>Refresh<//><${ViewerTools} v=${view} title=${rawTitle} rawText=${JSON.stringify(raw, null, 2)} /></div>
    <${ViewerFrame} v=${view} title=${rawTitle}><div class=${cls}>${children}</div><//><//>`;
}

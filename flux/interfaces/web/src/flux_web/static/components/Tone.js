// Flux web (D1102): one vocabulary for a state wherever it is drawn -- five tones, one colour and one glyph each:
// run (a turning ring: running, reading, reconnecting), ok ✓ (done, accepted), warn ! (stopped, rejected, out of
// date), bad ✗ (failed, could not be read), none ○ (idle, pending, nothing yet). A pill says it in a line, a notice
// (view.js) in a block, Failed a read that failed; a Fact is a value with its label, at three sizes.

import { Glyph, Notice, cx, html } from "../view.js";
import { Act } from "./Act.js";

/** A state said inside a sentence or a row: its glyph and its words, in its tone. */
export const Said = ({ tone = "none", cls = "", children, ...rest }) => html`<span ...${rest} class=${cx("said", `t-${tone}`, cls)}><${Glyph} tone=${tone} />${children}</span>`;

/** A read that failed: what could not be read (bad -- nothing to show; `stale` warn -- what came before stays), why,
    and Retry at the end. */
export const Failed = ({ what, why, retry, stale = false, cls = "" }) => html`<${Notice} tone=${stale ? "warn" : "bad"} cls=${cx("failed", cls)} what=${what} why=${why}
  action=${retry ? html`<${Act} cls="small" onClick=${retry}>Retry<//>` : null} />`;

/** A state in a line: its glyph and its words (`children`); `icon` the glyph alone, the words its label. */
export const Pill = ({ tone = "none", small = false, icon = false, cls = "", title, children, ...rest }) => icon
  ? html`<span ...${rest} class=${cx("pill icon", `t-${tone}`, small && "small", cls)} role="img" aria-label=${title} title=${title}><${Glyph} tone=${tone} /></span>`
  : html`<span ...${rest} class=${cx("pill", `t-${tone}`, small && "small", cls)} title=${title || null}><${Glyph} tone=${tone} />${children}</span>`;

/** A value with its label, a tile: `size` "headline" (the Overview, the machine) or "wide" for a long value
    on its own line; `tone` when the value is a verdict; then `children` (a meter, a text) and `sub`, a muted line. */
export const Fact = ({ label, value, tone, size = "", sub, onClick, cls = "", children, ...rest }) => html`<div ...${rest}
  class=${cx("fact", size, tone && `t-${tone}`, onClick && "clickable", cls)} onClick=${onClick}><small>${label}</small>${
  value != null ? html`<span class=${cx("v", size === "headline" && "big")}>${value}</span>` : ""}${children}${sub ? html`<div class="sub">${sub}</div>` : ""}</div>`;

/** Tiles in a row, from [label, value] pairs. */
export const Facts = ({ list, cls = "" }) => list.length
  ? html`<div class=${cx("facts", cls)}>${list.map(([k, v]) => html`<${Fact} key=${k} label=${k} value=${String(v)} />`)}</div>` : null;

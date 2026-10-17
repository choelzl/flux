// Flux web: one status pill for a design, a check and an idea -- its word, lowercase, in its tone (D1102), wherever
// it is said: the results table, a design's panel, Compare, the checked designs, the ideas notebook, the Overview.

import { Glyph, cx, html } from "../view.js";
import { Pill } from "./Tone.js";

/** A status's tone: done well ok, a rejection or an interruption warn, a failure bad, the rest (pending, proposed,
    measured: evidence, not that it helped) none. */
const TONE = { accepted: "ok", passed: "ok", met: "ok", ok: "ok", rejected: "warn", interrupted: "warn", failed: "bad", missed: "bad" };
export const statusTone = status => TONE[status] || "none";

/** `status` the word (or `children` the words said for it: "rejected · critic"); `small` in a dense row; `bare` its
    glyph alone, in its tone, the word its label and tooltip -- a table's narrow status column (the designs, Best 3). */
export const Status = ({ status, small = false, bare = false, title, cls = "", children }) => bare
  ? html`<span class=${cx("status-bare", `t-${statusTone(status)}`, cls)} role="img" aria-label=${status} title=${title || status}><${Glyph} tone=${statusTone(status)} /></span>`
  : html`<${Pill} tone=${statusTone(status)} small=${small} cls=${cls} title=${title}>${children ?? status}<//>`;

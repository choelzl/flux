// Flux web: one status pill for a design, a check and an idea -- its word, lowercase, in its tone (D1102), wherever
// it is said: the results table, a design's panel, Compare, the checked designs, the ideas notebook, the Overview.

import { html } from "../view.js";
import { Pill } from "./Tone.js";

/** A status's tone: done well ok, a rejection or an interruption warn, a failure bad, the rest (pending, proposed,
    measured: evidence, not that it helped) none. */
const TONE = { accepted: "ok", passed: "ok", met: "ok", ok: "ok", rejected: "warn", interrupted: "warn", failed: "bad", missed: "bad" };
export const statusTone = status => TONE[status] || "none";

/** `status` the word (or `children` the words said for it: "rejected · critic"); `small` in a dense row. */
export const Status = ({ status, small = false, title, cls = "", children }) => html`<${Pill} tone=${statusTone(status)} small=${small} cls=${cls} title=${title}>${children ?? status}<//>`;

// Flux web, configure (D1117): the document as the form writes it, beside the step -- `problem.yaml` highlighted, the
// lines the step writes lit (and brought into view); Copy and Download.

import { cx, html, useEffect, useRef } from "../view.js";
import { tokens } from "../highlight.js";
import { toast } from "../ui.js";
import { Head, Tool } from "../components/Head.js";

// the steps that write a key: top-level keys, then `flow`'s boxes
const TOP = { statement: ["prompt"], contract: ["prompt"], language: ["prompt"], objectives: ["objective"], budget: ["graph", "extra"],
  baseline: ["extra"], parts: ["extra"], params: ["extra"], flow: ["checks", "graph"] };
const FLOW = { test: ["checks"], measure: ["checks"], select: ["checks", "graph"], orchestrate: ["graph", "extra"], knowledge: ["graph"] };

/** The document's lines, each with the steps that write it (a key's lines go with it). */
export function linesOf(yaml) {
  let top = [], box = null;
  return String(yaml || "").replace(/\n$/, "").split("\n").map(text => {
    let m;
    if ((m = /^([A-Za-z_][\w-]*):/.exec(text))) { top = TOP[m[1]] || []; box = null; }
    else if (/^#/.test(text) || !text.trim()) return { text, steps: text.trim() ? [] : top };
    else if (top.includes("checks") && (m = /^ {2}"?([A-Za-z_][\w-]*)"?:/.exec(text))) box = FLOW[m[1]] || ["graph"];
    const steps = box || top;
    return { text, steps: box && /\bcritic:/.test(text) && !steps.includes("graph") ? [...steps, "graph"] : steps };
  });
}

function copyText(text) {
  if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(text).then(() => toast("Copied", "ok"), () => toast("The browser did not copy it: select the text", "warn"));
  toast("The browser does not copy here: select the text", "warn");
}
/** `text` saved by the browser as `file`'s name. */
export function download(text, file) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type: "text/yaml" }));
  a.download = file.split("/").pop();
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

/** The document: `step` the one whose lines are lit; `card` false for one inside a fold (Save's). */
export function DocView({ yaml, file, step = "", card = true }) {
  const pre = useRef(null);
  useEffect(() => {                                 // the step's first line in view, inside the document's own scroll
    const el = pre.current, lit = el?.querySelector(".lit");
    if (el && lit && el.scrollHeight > el.clientHeight) el.scrollTop = Math.max(0, lit.offsetTop - 24);              // the pre is the lines' offset parent
  }, [step]);
  const body = html`<pre class="cfg-yaml hl" ref=${pre} aria-label=${file}>${linesOf(yaml).map(l => html`<div class=${cx(step && l.steps.includes(step) && "lit")}>${
    tokens(l.text || " ", "yaml").map(([c, s]) => c ? html`<span class=${"tk-" + c}>${s}</span>` : s)}</div>`)}</pre>`;
  const acts = html`<button type="button" class="small" onClick=${() => copyText(yaml)}>Copy</button>
    <${Tool} icon="download" label=${`Download ${file.split("/").pop()}`} onClick=${() => download(yaml, file)} />`;
  if (!card) return html`<div class="cfg-doc-acts">${acts}</div>${body}`;
  return html`<section class="card cfg-doc"><${Head} title=${file} mono actions=${acts} />${body}</section>`;
}

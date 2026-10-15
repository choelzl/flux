// LiveAlt's inspector (D1095): its building blocks -- the state pill, a step's row, a fold that stays as the
// person left it, a block of fields, a text that keeps its place (and follows its end while it grows).

import { html, useLayoutEffect, useRef } from "../../view.js";
import { dur } from "../../format.js";
import { restoreScroll, scrollState } from "../../scroll.js";
import { colorOf, running, stateOf, summaryOf, taskLabel, tidyCommand } from "./parts.js";

/** What the person did in a task's panel -- which folds they opened, where they left each text -- kept when
    they look at another task and back (D731), forty tasks at most. */
const memory = new Map();
export function memoryOf(id) {
  if (!memory.has(id)) { memory.set(id, { folds: new Map(), scroll: new Map(), panel: null }); if (memory.size > 40) memory.delete(memory.keys().next().value); }
  return memory.get(id);
}

export const Pill = ({ n }) => { const [cls, text] = stateOf(n);
  return html`<span class=${`pill ${cls}`}>${running(n) ? html`<span class="alt-spin" aria-hidden="true" />` : ""}${text}</span>`; };

/** A step under a task: its mark, its name, what it gave, its time; a click opens it. */
export function StepRow({ x, a, N }) {
  const s = summaryOf(x), crit = /^\s*critique\s*:/i.test(String(x.name));
  return html`<button type="button" class="alt-step-row" style=${`--k:${x.failed ? "var(--bad)" : colorOf(x)}`} data-task=${String(x.id)} onClick=${() => a.select(x.id)}>
    ${running(x) ? html`<span class="alt-spin" aria-hidden="true" />` : html`<span class=${`alt-task-state ${x.failed ? "failed" : x.rejected ? "partial" : "done"}`}>${x.failed ? "✗" : x.rejected ? "!" : "✓"}</span>`}
    <span class="alt-task-name" title=${taskLabel(x)}>${N.shortAll(taskLabel(x))}${s ? html`<span class="muted">${` — ${s}`}</span>` : ""}
      ${crit ? html`<span class="muted">${` — ${/^(rejected|sent back)/i.test(String(x.output?.verdict ?? "")) ? "rejected" : x.output?.verdict ? "no objection" : ""}`}</span>` : ""}</span>
    <span class="mono muted small">${dur(x.seconds ?? (x.t1 ?? Date.now() / 1000) - x.t0)}</span></button>`;
}

/** A fold, open as the person left it in this task. */
export function Fold({ k, title, summary, mem, cls = "", children }) {
  return html`<details class=${`alt-fold-block ${cls}`} data-fold=${k} open=${mem.folds.get(k) || false} onToggle=${e => mem.folds.set(k, e.currentTarget.open)}>
    <summary><b>${title}</b>${summary ? html`<span class="alt-fold-summary">${summary}</span>` : ""}</summary>${children}</details>`;
}

const atEnd = el => el.scrollHeight - el.scrollTop - el.clientHeight < 24;
const visible = el => el.getClientRects().length && !el.closest("details:not([open])");
/** A text that keeps its place in this task; `follow`: a text that grows stays at its end while one reads
    its end (a new one opens there) -- not a prompt, not what it was given. */
export function Pre({ k, text, cls = "val", mem, follow = true }) {
  const ref = useRef(null), end = useRef(true);
  if (ref.current && visible(ref.current)) end.current = atEnd(ref.current);   // as it stood before this draw
  useLayoutEffect(() => {
    const el = ref.current, saved = mem?.scroll.get(k);
    const place = () => { if (saved) restoreScroll(el, saved, { follow }); else if (follow) el.scrollTop = el.scrollHeight; };
    if (visible(el)) place(); else el.closest("details")?.addEventListener("toggle", () => { if (visible(el)) place(); }, { once: true });
    return () => { if (mem && visible(el)) mem.scroll.set(k, scrollState(el)); };
  }, []);
  useLayoutEffect(() => { if (follow && end.current && visible(ref.current)) ref.current.scrollTop = ref.current.scrollHeight; });
  return html`<pre class=${cls} data-k=${k} ref=${ref}>${text}</pre>`;
}

/** A task's fields as name and value -- a long value its own text; the Input's command as a person reads it (D976). */
export function Block({ title, obj, mem }) {
  if (!obj || !Object.keys(obj).length) return null;
  return html`<div class="blk"><h3>${title}</h3>${Object.entries(obj).map(([k, v]) => {
    let text = typeof v === "string" ? v : JSON.stringify(v, null, 1);
    if (title === "Input" && k === "command" && typeof v === "string") text = tidyCommand(v, obj.folder);   // Raw keeps it whole
    const long = text.length > 120 || text.includes("\n"), key = k === "prompt" && title === "Input" ? "prompt" : `field:${title}:${k}`;
    return html`<div class="kv" key=${k}><div class="k">${k}</div>${long ? html`<${Pre} k=${key} text=${text} mem=${mem} follow=${title !== "Input"} />` : html`<div class="val mono">${text}</div>`}</div>`;
  })}</div>`;
}

/** Facts as small labelled values. */
export const Facts = ({ list, cls = "facts" }) => list.length ? html`<div class=${cls}>${list.map(([k, v]) => html`<div class="fact"><small>${k}</small><span class="mono">${String(v)}</span></div>`)}</div>` : null;

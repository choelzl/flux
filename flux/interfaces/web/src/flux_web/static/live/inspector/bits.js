// Live's inspector (D1095): its building blocks -- the state pill, a step's row, a fold that stays as the
// person left it, a block of fields, a text that keeps its place (and follows its end while it grows).

import { Glyph, html, useLayoutEffect, useRef, useState } from "../../view.js";
import { bytes, dur } from "../../format.js";
import { restoreScroll, scrollState, shown as visible, useFollow } from "../../scroll.js";
import { colorOf, running, stateOf, summaryOf, taskLabel, tidyCommand } from "./parts.js";
import { useLoad } from "../../components/Load.js";
import { Fact, Pill } from "../../components/Tone.js";

/** What the person did in a task's panel -- which folds they opened, where they left each text -- kept when
    they look at another task and back (D731), forty tasks at most. */
const memory = new Map();
export function memoryOf(id) {
  if (!memory.has(id)) { memory.set(id, { folds: new Map(), scroll: new Map(), panel: null }); if (memory.size > 40) memory.delete(memory.keys().next().value); }
  return memory.get(id);
}

export const TaskPill = ({ n }) => { const [tone, text] = stateOf(n); return html`<${Pill} tone=${tone}>${text}<//>`; };

/** A step under a task: its mark, its name, what it gave, its time; a click opens it. */
export function StepRow({ x, a, N }) {
  const s = summaryOf(x), crit = /^\s*critique\s*:/i.test(String(x.name));
  return html`<button type="button" class="alt-step-row" style=${`--k:${x.failed ? "var(--bad)" : colorOf(x)}`} data-task=${String(x.id)} onClick=${() => a.select(x.id)}>
    <${Glyph} tone=${running(x) ? "run" : x.failed ? "bad" : x.rejected ? "warn" : "ok"} />
    <span class="alt-task-name" title=${taskLabel(x)}>${N.shortAll(taskLabel(x))}${s ? html`<span class="muted">${` — ${s}`}</span>` : ""}
      ${crit ? html`<span class="muted">${` — ${x.rejected ? "rejected" : x.output?.verdict ? "no objection" : ""}`}</span>` : ""}</span>
    <span class="mono muted small">${dur(x.seconds ?? (x.t1 ?? Date.now() / 1000) - x.t0)}</span></button>`;
}

/** A fold, open as the person left it in this task. */
export function Fold({ k, title, summary, mem, cls = "", children }) {
  return html`<details class=${`alt-fold-block ${cls}`} data-fold=${k} open=${mem.folds.get(k) || false} onToggle=${e => mem.folds.set(k, e.currentTarget.open)}>
    <summary><b>${title}</b>${summary ? html`<span class="alt-fold-summary">${summary}</span>` : ""}</summary>${children}</details>`;
}

/** A text that keeps its place in this task; `follow`: a text that grows stays at its end while one reads
    its end (a new one opens there) -- not a prompt, not what it was given. */
export function Pre({ k, text, cls = "val", mem, follow = true }) {
  const ref = useRef(null);
  useFollow(ref, follow);                                     // first: a place kept below overrides it
  useLayoutEffect(() => {
    const el = ref.current, saved = mem?.scroll.get(k), place = () => restoreScroll(el, saved, { follow });
    if (saved) { if (visible(el)) place(); else el.closest("details")?.addEventListener("toggle", () => { if (visible(el)) place(); }, { once: true }); }
    return () => { if (mem && visible(el)) mem.scroll.set(k, scrollState(el)); };
  }, []);
  return html`<pre class=${cls} data-k=${k} ref=${ref}>${text}</pre>`;
}

const SLICE = 65536;
/** D1098: a tool's stdout or stderr -- the end its task kept (`text`), or, when its run wrote it whole (`file`,
    `size` bytes), read from that file: its last part first, each earlier part as one scrolls to the top, the
    place kept as it comes. `read(file, start, size)` reads a slice (env.output). */
export function Stream({ k, text, file, size, read, mem, cls = "val astream-body" }) {
  const ref = useRef(null), [above, setAbove] = useState(null), busy = useRef(false), grew = useRef(null);
  const last = useLoad(() => file && read ? read(file, -1, SLICE) : null, file || "").got?.d;     // its end first
  const got = last ? (above && above.file === file ? { start: above.start, text: above.text + last.text } : last) : null;
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (grew.current != null) { el.scrollTop += el.scrollHeight - grew.current; grew.current = null; return; }   // what came above: the place kept
    const saved = mem?.scroll.get(k), place = () => { if (saved) restoreScroll(el, saved, { follow: true }); else el.scrollTop = el.scrollHeight; };   // read at its end: at the new end
    if (visible(el)) place(); else el.closest("details")?.addEventListener("toggle", () => { if (visible(el)) place(); }, { once: true });   // in a closed fold: once opened
  }, [last, above]);
  const scrolled = e => {
    const el = e.currentTarget;
    if (mem) mem.scroll.set(k, scrollState(el));
    if (!got || got.start <= 0 || busy.current || el.scrollTop > 40) return;
    busy.current = true;
    const from = Math.max(0, got.start - SLICE);
    read(file, from, got.start - from).then(g => { grew.current = el.scrollHeight; setAbove(x => ({ file, start: g.start, text: g.text + (x && x.file === file ? x.text : "") })); }, () => {})
      .finally(() => { busy.current = false; });
  };
  if (!file || !read) return html`<${Pre} k=${k} cls=${cls} text=${text} mem=${mem} />`;
  return html`<pre class=${cls} data-k=${k} ref=${ref} onScroll=${scrolled}>${got && got.start > 0 ? html`<span class="muted stream-earlier">${`… ${bytes(got.start)} earlier of ${bytes(size)}: scroll up for more\n`}</span>` : ""}${got ? got.text : text}</pre>`;
}

/** A task's fields as tiles (D1102) -- a long value a wide one, its own text; the Input's command as a person reads it (D976). */
export function Block({ title, obj, mem }) {
  if (!obj || !Object.keys(obj).length) return null;
  return html`<div class="blk"><h3>${title}</h3><div class="facts">${Object.entries(obj).map(([k, v]) => {
    let text = typeof v === "string" ? v : JSON.stringify(v, null, 1);
    if (title === "Input" && k === "command" && typeof v === "string") text = tidyCommand(v, obj.folder);   // Raw keeps it whole
    const long = text.length > 120 || text.includes("\n"), key = k === "prompt" && title === "Input" ? "prompt" : `field:${title}:${k}`;
    return long ? html`<${Fact} key=${k} label=${k} size="wide"><${Pre} k=${key} text=${text} mem=${mem} follow=${title !== "Input"} /><//>`
      : html`<${Fact} key=${k} label=${k} value=${text} />`;
  })}</div></div>`;
}


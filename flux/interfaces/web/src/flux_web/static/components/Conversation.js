// Flux web (D1095): an agent's conversation (D712) as components -- drawn inside Live's chat (live/inspector/Chat.js, D1144) -- what it did, in order: its words as
// text, its thinking and each tool call (input and output) folded, opened on a click and kept open across
// redraws (D1030: the work between two answers one dashed line, opened to its steps; the answers in view).

import { Empty, cx, Glyph, html } from "../view.js";
import { Markdown } from "./Markdown.js";

const convOpen = new Set();          // which steps a person opened, by key: kept across redraws and tasks
const preview = (t, n = 140) => { const x = String(t || "").replace(/\s+/g, " ").trim(); return x.length > n ? x.slice(0, n) + "…" : x; };
const took = s => { s = Math.max(0, Math.round(s)); return s >= 60 ? `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s` : `${s}s`; };
const remember = id => e => { if (e.currentTarget.open) convOpen.add(id); else convOpen.delete(id); };

function Step({ st, id, live }) {
  if (st.k === "think") {
    const t = String(st.text || "").trim();
    return html`<details class="cv-step cv-think" open=${convOpen.has(id)} onToggle=${remember(id)}>
      <summary><span class="cv-kind">Thinking</span><span class="cv-sum muted">${t ? preview(t) : `redacted, about ${Number(st.redacted || 0).toLocaleString()} tokens`}</span>
        ${live ? html`<span class="cv-live">…</span>` : ""}</summary>
      ${t ? html`<pre class="cv-body cv-thought" data-k=${`${id}:thinking`}>${t}</pre>` : ""}</details>`;
  }
  const state = st.out != null ? (st.error ? "failed" : "") : live ? "running" : "";
  const input = st.input && typeof st.input === "object" ? st.input : st.input ? { input: String(st.input) } : {};
  return html`<details class=${`cv-step cv-${st.k}${st.error ? " bad" : ""}`} open=${convOpen.has(id)} onToggle=${remember(id)}>
    <summary><span class="cv-kind">${st.name || "tool"}</span><code class="cv-sum">${preview(String(st.call || "").replace(/^[^:]*:\s*/, ""), 160)}</code>
      ${state ? html`<span class=${`cv-state ${state === "failed" ? "bad" : "live"}`}>${state}</span>` : ""}</summary>
    ${Object.entries(input).map(([k, v]) => { const t = String(v ?? "");
      return html`<div class="cv-io"><small>${k || "input"}</small>${t.includes("\n") || t.length > 90 ? html`<pre class="cv-body" data-k=${`${id}:input:${k}`}>${t}</pre>` : html`<div><code class="cv-arg">${t}</code></div>`}</div>`; })}
    ${st.out != null ? html`<div class="cv-io"><small>${st.error ? "error" : "output"}</small><pre class=${cx("cv-body", st.error && "err")} data-k=${`${id}:output`}>${String(st.out).trim() || "(nothing)"}</pre></div>` : ""}</details>`;
}

/** D1033: the conversation draws its own "working" line -- a stretch of work at its end, live. */
export const liveWork = (steps, live) => !!live && steps.length > 0 && steps[steps.length - 1].k !== "text";

/** `offset`: how many earlier steps are not in `steps` (a live row sends its latest); `quiet`: no line saying so. */
export function Conversation({ steps, k = "", offset = 0, live = false, quiet = false }) {
  const items = [];
  if (offset > 0 && !quiet) items.push(html`<p class="muted small">${`${offset} earlier step${offset === 1 ? "" : "s"} hidden`}</p>`);
  for (let i = 0; i < steps.length;) {
    const st = steps[i];
    if (st.k === "text") {
      if (String(st.text || "").trim()) items.push(html`<div class="cv-text" key=${`t${offset + i}`}><${Markdown} text=${String(st.text).trim()} /></div>`);
      i += 1;
      continue;
    }
    if (st.k === "said") {             // (D1247) what Flux told the agent to go on -- a nudge, an answer -- on the right, as the prompt
      const t = String(st.text || ""), id = `${k}:${offset + i}`;
      items.push(html`<details key=${`s${offset + i}`} class="alt-bubble me cv-said" open=${convOpen.has(id)} onToggle=${remember(id)}>
        <summary><small>${`Flux · ${preview(t, 90)}`}</small></summary><pre class="cv-body">${t}</pre></details>`);
      i += 1;
      continue;
    }
    let j = i;
    while (j < steps.length && steps[j].k !== "text" && steps[j].k !== "said") j += 1;
    const work = steps.slice(i, j), now = live && j === steps.length, first = i;     // the stretch it is working on
    const cmds = work.filter(x => x.k !== "think").length, failed = work.filter(x => x.error).length;
    const end = now ? Date.now() / 1000 : (steps[j]?.at ?? work[work.length - 1].at);
    const secs = work[0].at != null && end != null ? end - work[0].at : null;
    // the line neutral, only its failed commands red: a turn that went on after one failed is not a failure
    const said = [`${work.length} step${work.length === 1 ? "" : "s"}${now ? " so far" : ""}`,
      cmds ? `${cmds} command${cmds === 1 ? "" : "s"}` : "", failed ? html`<span class="bad">${`${failed} failed`}</span>` : "", secs != null ? took(secs) : ""]
      .filter(Boolean).flatMap((x, n) => n ? [" · ", x] : [x]);
    // D1126: a stretch is known by where it ends (the answer after it), the one at work as "live" -- not by its first
    // step, which a live window rolls away with every new step (the box one opened closed on each update)
    const live_ = `${k}:wlive`, id = now ? live_ : `${k}:w${offset + j}`;
    if (!now && convOpen.has(live_) && !steps.slice(j).some(x => x.k !== "text")) { convOpen.add(id); convOpen.delete(live_); }   // it ended: still open
    // open while a step in it is open
    const open = convOpen.has(id) || work.some((_x, n) => convOpen.has(`${k}:${offset + first + n}`));
    items.push(html`<details key=${id} class=${cx("cv-work", now && "live", failed && "bad")} open=${open} onToggle=${remember(id)}>
      <summary title="Thinking and commands"><span class="cv-work-said">${now ? html`<${Glyph} tone="run" />` : ""}${now ? "working · " : ""}${said}</span></summary>
      <div class="cv-work-steps">${work.map((x, n) => html`<${Step} key=${`${k}:${offset + first + n}`} st=${x} id=${`${k}:${offset + first + n}`} live=${live && first + n === steps.length - 1} />`)}</div></details>`);
    i = j;
  }
  return html`<div class="cv" data-k=${`cv-${k}`}>${items.length ? items : html`<${Empty} text="Nothing yet." />`}</div>`;
}

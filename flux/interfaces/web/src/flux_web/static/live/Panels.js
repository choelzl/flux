// Live (D1095): the inspector's panels this page draws itself -- a box of a pass (its runs, its notes,
// what it is set to until it runs), a pass at a glance, and where a task's panel (inspector/) is kept.

import { html, useLayoutEffect, useRef } from "../view.js";
import { WORK_COLORS as COLORS, workLabel } from "../work_style.js";
import { passName, taskLabel } from "../live_views.js";
import { PassSummary } from "./inspector/Inspector.js";
import { asYaml, noteName, noteSaid } from "./notes.js";

const said = v => typeof v === "object" ? JSON.stringify(v) : String(v);

/** D1059: a box of a pass -- its state as its box says it, its runs to open, or its notes, or what the
    document sets it to (as YAML, D1094) while it has not run. */
export function BoxPanel({ v, a, box }) {
  const { b, boxes, config, took } = box, later = b && boxes.slice(boxes.indexOf(b) + 1).some(x => x.runs.length);
  const pill = b?.done ? "done" : b?.runs.length || b?.notes?.length ? { ok: "done", bad: "failed", part: "partly failed", run: "running" }[b.state] || "done" : later ? "not recorded" : "waiting";
  return html`<div class="detail alt-detail alt-box-detail" data-box=${v.boxView.key} data-pass=${String(v.boxView.p ?? "setup")} data-drawn=${v.drawn}>
    <div class="detail-head alt-ihead"><span class="alt-stripe" style=${`background:${COLORS[b?.kind] || COLORS.setup}`} />
      <h2>${b?.title || workLabel(v.boxView.key)}</h2><span class="pill">${pill}</span></div>
    <p class="muted small">${passName(v.boxView.p)}</p>
    ${b?.runs.length ? html`<div class="alt-steps">${b.runs.map(n => html`<button key=${n.id} type="button" class="alt-step-row" data-task=${String(n.id)} onClick=${() => a.select(n.id)}>${taskLabel(n)}</button>`)}</div>`
      : b?.notes?.length === 1 ? html`<div><p>${noteSaid(b.notes[0], took)}</p>
          ${Object.entries(b.notes[0].output || {}).filter(([k]) => k !== "scopes").map(([k, x]) => html`<div class="kv"><div class="k">${k}</div><div class="val">${said(x)}</div></div>`)}</div>`
      : b?.notes?.length ? html`<div class="alt-steps">${b.notes.map(n => html`<div class="alt-step-row note"><span>${noteName(n)}</span>
          ${Object.keys(n.output || {}).length ? html`<span class="mono muted small">${Object.entries(n.output).map(([k, x]) => `${k} ${said(x)}`).join(" · ")}</span>` : ""}</div>`)}</div>`
      : html`<p class="muted">${b?.done ? "Baseline stored as a reference." : later ? "Nothing was recorded for it in this pass." : "Not started yet."}</p>`}
    ${v.boxView.key === "record" && b?.done ? html`<${PassSummary} p=${v.boxView.p} nodes=${box.nodes} a=${a} N=${v.N} head=${false} />` : ""}
    ${config != null && !b?.runs.length && !b?.notes?.length ? html`<section><div class="alt-label">${`Set to · ${v.problem?.document || "problem.yaml"}`}</div>
        <pre class="val tall" data-k="configuration">${typeof config === "string" ? config : asYaml(config)}</pre></section>`
      : v.notes[v.boxView.key] ? html`<p class="mono">${v.notes[v.boxView.key]}</p>` : ""}</div>`;
}

/** D982: a pass in the inspector -- its steps, where its time went. */
export const PassPanel = ({ p, nodes, a, N }) => html`<div class="detail alt-detail alt-pass-detail" data-pass=${String(p ?? "setup")}><${PassSummary} p=${p} nodes=${nodes} a=${a} N=${N} /></div>`;

/** The inspector's own panel (a task's), kept where it is drawn -- not taken back from Fullscreen (D987). */
export function TaskPanel({ detail }) {
  const ref = useRef(null);
  useLayoutEffect(() => {
    if (detail.parentNode !== ref.current && !detail.closest("dialog.fullscreen-view")) ref.current.replaceChildren(detail);
  });
  return html`<span ref=${ref} style="display:contents" />`;
}

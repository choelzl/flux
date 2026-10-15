// LiveAlt (D1095): the lines rows.js works out, drawn -- the tree, and a graph box's list.

import { html } from "../view.js";
import { WORK_COLORS as COLORS } from "../work_style.js";
import { taskState as state } from "../live_alt_model.js";

const Segment = ({ s }) => typeof s === "string" ? s
  : s.b != null ? html`<b>${s.b}</b>`
  : s.dots ? html`<span class="alt-dots">${s.dots.map(c => html`<i class=${c} />`)}</span>`
  : html`<span class=${s.cls} title=${s.title || null}>${s.text}</span>`;

const Mark = ({ m }) => m === "spin" ? html`<span class="alt-spin" role="img" aria-label="running" />`
  : html`<span class=${`alt-task-state ${m.cls}`} aria-hidden="true" title=${m.title || null}>${m.glyph}</span>`;

/** What a line's button does, as its attributes: open a task (D980), open or fold, show a pass, a box. */
function actOf(l, a) {
  const key = (e, f) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); f(); } };
  if (l.act.task) {
    const n = l.act.task, go = () => a.select(n.id);
    return { "data-task": String(n.id), tabindex: "0", role: "button", "aria-label": `${n.name} · ${state(n)}`, "aria-pressed": String(!!l.picked), onClick: go, onKeyDown: e => key(e, go) };
  }
  if (l.act.toggle != null) return { onClick: () => a.toggleFold(l.act.toggle, l.act.collapsed) };
  if (l.act.pass !== undefined) return { "data-pass": String(l.act.pass ?? "setup"), onClick: () => a.showPass(l.act.pass) };
  if (l.act.box) return { "data-box": l.act.box.key, onClick: () => a.showBox(l.act.box.p, l.act.box.key) };
  return {};
}

function Line({ l, a, located }) {
  if (l.type === "more") return html`<button type="button" class="alt-more-tries" style=${`--depth:${l.depth}`} data-more=${l.key} onClick=${() => a.toggleTries(l.key)}>${l.text}</button>`;
  if (l.type === "skip") return html`<div class="alt-tree-row step quiet skipped" style=${`--depth:${l.depth};--k:${COLORS.check}`}>
    <span class="alt-fold-space" /><span class="alt-task" title="not run: a check before it failed"><span class="alt-task-state muted">○</span><span class="alt-task-name muted">${l.text}</span></span></div>`;
  if (l.type === "wait") return html`<div class="alt-tree-row box wait" style=${`--depth:${l.depth};--k:${l.k}`} aria-label=${`${l.title}: still to come`}>
    <span class="alt-fold-space" /><span class="alt-task"><span class="alt-task-state wait">·</span><span class="alt-task-name"><b>${l.title}</b></span></span></div>`;
  const f = l.fold, here = l.act.task && located != null && String(l.act.task.id) === String(located);
  return html`<div class=${`alt-tree-row ${l.cls}`} style=${`--depth:${l.depth}` + (l.k ? `;--k:${l.k}` : "")}>
    ${f ? html`<button type="button" class="alt-fold" data-fold=${String(f.key)} aria-label=${`${f.collapsed ? "Expand" : "Collapse"} ${f.label}`} aria-expanded=${String(!f.collapsed)}
        onClick=${e => { e.stopPropagation(); a.toggleFold(f.key, f.collapsed); }}>${f.collapsed ? "▸" : "▾"}</button>` : html`<span class="alt-fold-space" />`}
    <button type="button" class=${`alt-task ${l.btnCls || ""}${l.picked ? " sel" : ""}${here ? " alt-located" : ""}`} title=${l.title || null} ...${actOf(l, a)}>
      <${Mark} m=${l.mark} />
      <span class="alt-task-name">${l.name.filter(s => s !== "" && s != null).map(s => html`<${Segment} s=${s} />`)}</span>
      ${l.pills.map(p => html`<span class=${p.cls} title=${p.title || null}>${p.text}</span>`)}
      ${l.noTime ? "" : html`<span class="mono muted small">${l.time}</span>`}
    </button></div>`;
}

/** Lines as the tree draws them; `a` the page's actions, `located` the task just located (its flash). */
export const Lines = ({ lines, a, located }) => lines.map(l => html`<${Line} key=${`${l.type}:${l.key}`} l=${l} a=${a} located=${located} />`);

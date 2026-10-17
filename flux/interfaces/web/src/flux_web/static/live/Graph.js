// Live (D1095): the Graph -- the chain of passes, and the pass shown as its flow of boxes (D978), a box
// opening on its list (D1044) -- in the timeline's words and colours.

import { cx, Empty, html } from "../view.js";
import { plural } from "../format.js";
import { WORK_COLORS as COLORS } from "./work_style.js";
import { byPass, currentTask, taskState as state } from "./model.js";
import { agentsOf, flowBoxes, isDraft, passName, passShort, repaired, triesOf, waitWord } from "./views.js";
import { CRIT, soleOf } from "./rules.js";
import { lines } from "./rows.js";
import { Lines } from "./Lines.js";
import { SvgPill, pillWidth } from "../components/SvgPill.js";

const W = 300, X = 26, BW = W - 2 * X, GAP = 16, RH = 28;

/** D1094: the chain is where one is -- the first (Setup), the pass before the one shown, it, the one after, the
    latest; a gap between ("…": the chips' own numbers say how many) -- the Pass menu has every one. */
function Chain({ v, a, passes, shown }) {
  const idx = Math.max(0, passes.indexOf(shown)), keep = new Set([0, idx - 1, idx, idx + 1, passes.length - 1]);
  const before = v.model.before || 0, seq = [];
  for (let i = 0, prev = -1; i < passes.length; i++) {
    if (!keep.has(i)) continue;
    const skipped = i - prev - 1 + (prev === 0 && passes[0] == null ? before : 0);   // the passes not loaded sit after the start's Setup
    if (skipped > 0) seq.push({ gap: skipped });
    seq.push(passes[i]); prev = i;
  }
  if (before && !(passes[0] == null && passes.length > 1)) seq.unshift({ gap: before });
  return html`<div class="alt-chain" role="group" aria-label="Passes">${seq.map((p, i) => typeof p === "object" && p !== null
    ? html`<span key=${`gap${i}`} class="alt-chain-more muted small" title=${`${plural(p.gap, "pass", "passes")} between -- in the Pass menu${before ? ", and under Earlier passes" : ""}`}>…</span>`
    : (() => {
      const rows = v.scopeRows.filter(r => (v.membership.get(r.node.id) ?? null) === p);
      const st = v.J.passState(p, rows.map(r => r.node), rows, v.passNotes(p));   // D1031; D1078: as the tree says it
      return html`<button key=${String(p)} type="button" class=${`alt-chip ${st}${p === shown ? " sel" : ""}`} data-chain=${String(p ?? "S")} aria-pressed=${String(p === shown)}
        title=${passName(p)} onClick=${() => a.chain(p)}>${passShort(p)}</button>`;
    })())}</div>`;
}

/** One box of the flow: its title, note, pills, steps' bars, its mark; pressing it opens its task or its list. */
function Box({ b, i, y, BH, note, shown, v, a, task, isOpen, listed, wait }) {
  const c = COLORS[b.kind] || COLORS.setup, kids = [];
  if (note) kids.push(html`<title>${`${b.title}: ${note}`}</title>`);
  if (b.state === "run") kids.push(html`<rect x=${X - 5} y=${y - 5} width=${BW + 10} height=${BH + 10} rx="10" style=${`fill:${c}`} class="alt-glow" />`);
  kids.push(html`<rect x=${X} y=${y} width=${BW} height=${BH} rx="7" class=${`alt-box ${b.state}`} style=${`--k:${c}`} />`,
    html`<rect x=${X} y=${y} width="5" height=${BH} rx="2" style=${`fill:${c}`} />`,
    html`<text x=${X + 14} y=${y + (note || b.steps ? 15 : BH / 2 + 4)} class="alt-box-title">${b.title}</text>`);
  let right = X + BW - 10;
  const pill = (text, kind) => { const w = pillWidth(text); right -= w; kids.push(html`<${SvgPill} x=${right} y=${y + 6} w=${w} text=${text} kind=${kind} />`); right -= 6; };
  if (wait) { kids.push(html`<text x=${right} y=${y + (note || b.steps ? 15 : BH / 2 + 4)} class="alt-box-wait">${wait}</text>`); right -= wait.length * 6 + 6; }   // as the tree and the inspector say it
  if (b.who) pill(b.who, "ai");
  if (b.critic) pill("critic", "critic");                   // D1034: a critic judged this box's work -- no box of its own
  if (b.count > 1) pill(`×${b.count}`, "count");
  // D1089: a check at work says how far it is -- "2 of 5 checks"
  const ofChecks = b.kind === "check" && b.state === "run" && b.steps ? /^(\d+) check/.exec(note) : null;
  const shownNote = ofChecks ? `${b.steps.filter(s => s === "ok" || s === "bad" || s === "part").length} of ${ofChecks[1]} checks` : note;
  if (note) kids.push(html`<foreignObject x=${X + 14} y=${y + 22} width=${BW - 28} height="16" pointer-events="none"><div class="alt-box-note">${shownNote}</div></foreignObject>`);
  // D1064: a check that stopped at a failure -- the checks it never reached, skipped; D1089: at work -- those to come, waiting
  const declared = b.kind === "check" ? Number((/^(\d+) check/.exec(note) || [])[1] || 0) : 0;
  let steps = b.steps;
  if (steps && steps.includes("bad") && declared > steps.length) steps = [...steps, ...Array(declared - steps.length).fill("skip")];
  else if (steps && b.state === "run" && declared > steps.length) steps = [...steps, ...Array(declared - steps.length).fill("wait")];
  if (steps) { const w = (BW - 24) / steps.length; steps.forEach((s, k) => kids.push(html`<rect x=${X + 14 + k * w} y=${y + BH - 11} width=${w - 3} height="5" rx="2" class=${`alt-step ${s}`} style=${`--k:${c}`} />`)); }
  // D1053: a box at work says so in its corner, as a finished one does
  if (b.state === "run") kids.push(html`<circle cx=${X + BW - 2} cy=${y + 2} r="6" class="alt-done run" />`);
  if (["ok", "bad", "part"].includes(b.state)) kids.push(html`<circle cx=${X + BW - 2} cy=${y + 2} r="7" class=${`alt-done ${b.state}`} />`,
    html`<text x=${X + BW - 2} y=${y + 5.5} class="alt-done-mark">${b.state === "ok" ? "✓" : b.state === "part" ? "!" : "✗"}</text>`);
  const picked = v.boxView ? v.boxView.p === shown && v.boxView.key === b.key : b.ids.has(v.selected);
  const here = v.located != null && (v.boxView ? picked : task && String(task.id) === String(v.located));
  const me = `${shown}:${b.key}`;
  const activate = e => {
    e.currentTarget.focus({ preventScroll: true });
    if (task) { a.openBox(v.openBox !== me ? me : null); a.select(task.id); } else a.showBox(shown, b.key);
  };
  return html`<g class=${cx("alt-flow-box", picked && "sel", isOpen && "open", b.state === "wait" && "wait", here && "alt-located")}
    data-kind=${b.kind} data-box=${b.key} aria-pressed=${String(picked)} tabindex="0" role="button"
    aria-label=${`${b.title} · ${task ? state(task) : b.done || b.notes?.length ? "done" : wait || "no task recorded"}`}
    data-task=${task ? String(task.id) : null} aria-expanded=${task ? String(!!isOpen) : null} data-open=${me}
    onClick=${activate} onKeyDown=${e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); activate(e); } }}>${kids}</g>`;
}

export function Graph({ v, a }) {
  const passes = [...new Set(v.scopeRows.map(r => v.membership.get(r.node.id) ?? null))].sort(byPass);   // D1181: in pass order
  const selPass = v.membership.get(v.selected) ?? null;
  const shown = v.pass === "all" ? (passes.includes(v.chainPass) ? v.chainPass : passes.includes(selPass) ? selPass : passes.at(-1)) : passes.at(-1);
  const inPass = v.scopeRows.filter(r => (v.membership.get(r.node.id) ?? null) === shown);
  const boxes = flowBoxes(inPass, shown, inPass.some(r => r.node.t1 == null), v.passNotes(shown)), back = repaired(boxes);
  const g = [], at = {};
  let y = 6;
  boxes.forEach((b, i) => {
    const own = inPass.filter(r => b.ids.has(r.node.id));
    // D1056; D1059; D1089: what its list would hold -- a design's tries, a check's or a measure's one run's steps, else its runs
    const oneSteps = b.kind !== "design" && b.runs.length === 1 && ["check", "measure"].includes(b.kind) ? (b.runs[0].kids || []).length : 0;
    const listed = b.kind === "design" ? triesOf(b.runs) : oneSteps > 1 ? oneSteps : b.runs.length;
    // a box at work opens its current task; a finished one its latest run (D1066: its own work, not the critique of it)
    const lastRun = [...b.runs].reverse().find(n => !CRIT.test(String(n.name))) || b.runs.at(-1);
    const lastDraft = b.kind === "design" && lastRun ? [...(lastRun.kids || [])].reverse().find(isDraft) : null;
    const items = b.kind === "design" ? [] : b.runs;
    const chosen = own.some(r => r.node.t1 == null) ? currentTask(own) : lastDraft || (listed === 1 && items.length === 1 ? items[0] : lastRun || currentTask(own));
    const task = soleOf(chosen) || chosen;
    if (i) g.push(html`<path key=${`e${i}`} d=${`M${W / 2} ${y - GAP} V${y - 2}`} class="alt-flow-edge" marker-end="url(#alt-arrow)" />`);
    // D1075: a selection inside one of its tries opens the list, so it is in sight
    const holdsSel = v.selected != null && v.selected !== task?.id && own.some(r => r.node.id === v.selected);
    const isOpen = (v.openBox === `${shown}:${b.key}` || holdsSel) && listed > 1;
    const note = (b.kind === "design" && agentsOf(b.runs).join(", ")) || v.notes[b.key] || "";   // D1181: who drafted, as the tries say
    const BH = note ? (b.steps ? 52 : 44) : 34;
    at[b.key] = y + BH / 2;
    g.push(html`<${Box} key=${b.key} b=${b} i=${i} y=${y} BH=${BH} note=${note} shown=${shown} v=${v} a=${a} task=${task} isOpen=${isOpen} listed=${listed}
      wait=${b.state === "wait" && !b.runs.length && !b.done && !b.notes?.length ? waitWord(boxes, b) : ""} />`);
    y += BH;
    // D1044 (G1): a box opens on its list, attached under it in its colour
    if (isOpen) {
      const list = lines(own, v).boxList(b.runs, `box:${shown}:${b.key}`, b.kind);
      if (list.length) {
        const lh = list.filter(l => l.type !== "more").length * RH + list.filter(l => l.type === "more").length * 22 + 8;
        g.push(html`<foreignObject key=${`list:${b.key}`} x=${X} y=${y} width=${BW} height=${lh}>
          <div class="alt-box-tasks" role="group" aria-label=${`${b.title}: its tries`} style=${`--k:${COLORS[b.kind] || COLORS.setup}`}><${Lines} lines=${list} a=${a} located=${v.located} /></div></foreignObject>`);
        y += lh;
      }
    }
    y += GAP;
  });
  if (back && at.check != null && at.design != null)   // the way back, from Check to Design
    g.push(html`<path key="back" d=${`M${X + BW} ${at.check} H${W - 8} V${at.design} H${X + BW + 2}`} class="alt-flow-back" marker-end="url(#alt-arrow)" />`);
  const H = y - GAP + 8;
  return html`<div class="alt-graph" role="group" aria-label="The pass as a flow">
    ${v.pass === "all" && passes.length > 1 ? html`<${Chain} v=${v} a=${a} passes=${passes} shown=${shown} />` : ""}
    ${boxes.length ? html`<svg viewBox=${`0 0 ${W} ${H}`} class="alt-flow" style=${`max-width:${W}px`}>
      <defs><marker id="alt-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0 L10 5 L0 10 z" class="alt-arrow-head" /></marker></defs>
      ${g}</svg>` : html`<${Empty} text="No work in this pass yet." />`}</div>`;
}

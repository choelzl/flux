// Flux web, configure (D1118, D1128): the loop drawn as it runs -- five steps on a ring (Search, Design, Check, Measure,
// Choose); around it what feeds them: Validate, Plan, the search policy and your notes into Search, the Library (what
// every prompt reads, its papers digested, its lessons: D1136) into Design, Calibrate beside Measure; the record below
// the ring. A design its
// checks refuse goes back to Design. Each box wears its kind's colour (Live's), an "AI" pill when a model or an agent
// works it, a "critic" pill when a critic judges it; a step that is off is dashed. A click (Enter, Space) opens it.

import { cx, html } from "../view.js";
import { plural } from "../format.js";
import { WORK_COLORS } from "../live/work_style.js";
import { agentWord, isAgent, whoOn, whoOptions } from "./who.js";

const CX = 400, CY = 270, R = 150;
const RING = [["orchestrate", -162], ["generate", -90], ["test", -18], ["measure", 54], ["select", 126]];
const P = Object.fromEntries(RING.map(([k, a]) => [k, [CX + R * Math.cos(a * Math.PI / 180), CY + R * Math.sin(a * Math.PI / 180)]]));
/** Each box's kind of work, as Live colours it. */
const KIND = { validate: "setup", plan: "plan", dse: "search", orchestrate: "search", feedback: "setup", generate: "design",
  test: "check", measure: "measure", calibrate: "measure", select: "choose", knowledge: "setup", records: "setup" };
const f1 = x => x.toFixed(1);

function head(x1, y1, x2, y2, back) {
  const a = Math.atan2(y2 - y1, x2 - x1), l = 8, s = 0.42;
  return html`<path class=${cx("ah", back && "back")} d=${`M${f1(x2)} ${f1(y2)}L${f1(x2 - l * Math.cos(a - s))} ${f1(y2 - l * Math.sin(a - s))}L${f1(x2 - l * Math.cos(a + s))} ${f1(y2 - l * Math.sin(a + s))}Z`} />`;
}
const line = (x1, y1, x2, y2, soft) => html`<path class=${cx("e", soft && "soft")} d=${`M${f1(x1)} ${f1(y1)}L${f1(x2)} ${f1(y2)}`} />${head(x1, y1, x2, y2)}`;
const cut = (s, n = 16) => s.length > n ? s.slice(0, n - 1) + "…" : s;

/** What works a box, in a word, and whether it is off or AI: [word, "off" | "ai" | ""]. */
function workerOf(M, state, box, agent) {
  const f = state.flow || {};
  if (box === "test") return [state.checks.length ? plural(state.checks.length, "check") : "no checks", ""];
  if (box === "measure") return [state.stages.length ? plural(state.stages.length, "measurement") : "none", ""];
  if (box === "records") return ["", ""];
  if (box === "orchestrate" && M.parameterSearch(state)) return ["settings", ""];
  if (box === "generate" && M.paramOnly(state)) return ["off", "off"];
  if (box === "dse") { const p = f.dse === "none" ? "adaptive" : f.dse; return isAgent(p) ? [cut(agentWord(M, p)), "ai"] : [cut(p), M.halfOf(state, "dse") === "model" ? "ai" : ""]; }
  if (box === "knowledge") {                     // AI: a model or an agent sums up its papers (and an agent may write lessons)
    if (f.knowledge === "none") return f.lessons && f.lessons !== "off" ? ["lessons only", isAgent(f.lessons) ? "ai" : ""] : ["off", "off"];
    const n = M.list(state.knowledgeFiles).length;
    return [isAgent(f.digest) ? cut(agentWord(M, f.digest)) : n ? plural(n, "file") : "", "ai"];
  }
  const v = f[box], opts = whoOptions(M, box, v, agent), o = opts.find(x => x.k === whoOn(opts, v)) || {};
  return [o.cls === "agent" ? cut(o.label) : o.cls === "model" ? "" : cut(o.label || String(v)), o.cls === "off" ? "off" : ["model", "agent"].includes(o.cls) ? "ai" : ""];
}

/** `sel` the box opened; `pick(box)`. */
export function Cycle({ M, state, agent, sel, pick }) {
  const crit = M.criticsOf(state);
  const node = (k, cx0, cy0, w = 124, title = M.BOXES[k]?.title || k) => {
    const [word, how] = workerOf(M, state, k, agent), x = cx0 - w / 2, y = cy0 - 20;
    const c = ["orchestrate", "test", "select", "measure"].includes(k) && crit[k] && crit[k] !== "off" ? crit[k] : null;
    const pills = [how === "ai" ? ["AI", "ai"] : null, c ? ["critic", "critic"] : null].filter(Boolean);
    const key = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(k); } };
    let px = x + w - 6;
    return html`<g class=${cx("hit", sel === k && "sel")} data-node=${k} tabindex="0" role="button" aria-pressed=${String(sel === k)}
        aria-label=${`${title}${word ? `: ${word}` : ""}${how === "ai" ? ", AI" : ""}${c ? ", a critic" : ""}`} onClick=${() => pick(k)} onKeyDown=${key}>
      <rect class=${cx("bx", how === "off" && "off")} style=${`--k:${WORK_COLORS[KIND[k]] || "var(--muted)"}`} x=${x} y=${y} width=${w} height="40" rx="8" />
      <text class="t" x=${x + 10} y=${cy0 + (word ? -3 : 4)}>${title}</text>
      ${word ? html`<text class=${cx("w", how)} x=${x + 10} y=${cy0 + 12}>${word}</text>` : ""}
      ${pills.map(([t, cls]) => { const pw = t.length * 6 + 10; px -= pw; const at = px; px -= 3;
        return html`<g class=${cx("pill", cls)}><rect x=${at} y=${y + 5} width=${pw} height="14" rx="7" /><text x=${at + pw / 2} y=${y + 15.5} text-anchor="middle">${t}</text></g>`; })}</g>`;
  };
  // the ring: one arc from each step to the next, clear of both boxes, its head where it arrives (D1133)
  const gap = deg => (Math.abs(Math.sin(deg * Math.PI / 180)) > 0.8 ? 18 : 26);       // a box is wider than it is tall
  const arcs = RING.map(([, deg], i) => {
    const next = RING[(i + 1) % RING.length][1] + (i === RING.length - 1 ? 360 : 0);
    const a0 = (deg + gap(deg)) * Math.PI / 180, a1 = (next - gap(next)) * Math.PI / 180;
    const x0 = CX + R * Math.cos(a0), y0 = CY + R * Math.sin(a0), x1 = CX + R * Math.cos(a1), y1 = CY + R * Math.sin(a1);
    return html`<path class="e ring-e" d=${`M${f1(x0)} ${f1(y0)} A${R} ${R} 0 0 1 ${f1(x1)} ${f1(y1)}`} />${head(x1 + 8 * Math.sin(a1), y1 - 8 * Math.cos(a1), x1, y1)}`;
  });
  const G = P.generate, T = P.test, O = P.orchestrate, Me = P.measure, Ch = P.select, off = b => ["off", "none"].includes((state.flow || {})[b]);
  return html`<svg class="cfg-cycle" viewBox="0 0 790 520" role="group" aria-label="The loop: each box is one step">
    ${arcs}
    <path class="e back" d=${`M${f1(T[0] - 30)} ${f1(T[1] + 20)} Q${f1(CX + 30)} ${f1(CY - 60)} ${f1(G[0] + 12)} ${f1(G[1] + 20)}`} />${head(G[0] + 16, G[1] + 31, G[0] + 12, G[1] + 20, true)}
    ${[["validate", 50], ["plan", 112], ["dse", 174], ["feedback", 236]].map(([k, y]) => html`${line(148, y, O[0] - 62, O[1] + (y - 143) * 0.12, off(k))}${node(k, 86, y)}`)}
    ${line(498, 50, G[0] + 50, G[1] - 20, true)}${node("knowledge", 560, 40, 124, "Library")}
    ${line(Me[0] + 160, Me[1], Me[0] + 62, Me[1], true)}${node("calibrate", Me[0] + 222, Me[1])}
    ${line(Ch[0] - 62, Ch[1] + 8, 210, 468, false)}${node("records", 148, 480)}
    ${RING.map(([k]) => node(k, P[k][0], P[k][1]))}</svg>`;
}

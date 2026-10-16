// Flux web, configure (D1118): the loop drawn as it runs -- six steps on a ring (Orchestrate, Design, Check, Measure,
// Choose, Record); around it what feeds them: Validate, Plan, the search policy and your notes into Orchestrate, the
// knowledge and the digested papers into Design, Calibrate beside Measure, Lessons from the record. A critic is a tag
// on the step it judges; a design sent back to be repaired or improved goes back to Design. Each box says who works it,
// tinted by its kind; a click (Enter, Space) opens it.

import { cx, html } from "../view.js";
import { plural } from "../format.js";
import { agentWord, isAgent, whoOn, whoOptions } from "./who.js";

const CX = 400, CY = 286, R = 158;
const RING = [["orchestrate", -150], ["generate", -90], ["test", -30], ["measure", 30], ["select", 90], ["records", 150]];
const P = Object.fromEntries(RING.map(([k, a]) => [k, [CX + R * Math.cos(a * Math.PI / 180), CY + R * Math.sin(a * Math.PI / 180)]]));
const f1 = x => x.toFixed(1);

function head(x1, y1, x2, y2, back) {
  const a = Math.atan2(y2 - y1, x2 - x1), l = 7, s = 0.45;
  return html`<path class=${cx("ah", back && "back")} d=${`M${x2} ${y2}L${f1(x2 - l * Math.cos(a - s))} ${f1(y2 - l * Math.sin(a - s))}L${f1(x2 - l * Math.cos(a + s))} ${f1(y2 - l * Math.sin(a + s))}Z`} />`;
}
const line = (x1, y1, x2, y2, soft) => html`<path class=${cx("e", soft && "soft")} d=${`M${f1(x1)} ${f1(y1)}L${f1(x2)} ${f1(y2)}`} />${head(x1, y1, x2, y2)}`;
const cut = (s, n = 18) => s.length > n ? s.slice(0, n - 1) + "…" : s;

/** Who works a box, in a word, and its kind (the box's tint). */
export function workerOf(M, state, box, agent) {
  const f = state.flow || {};
  if (box === "test") return [state.checks.length ? plural(state.checks.length, "check") : "no checks yet", "fixed"];
  if (box === "measure") return [state.stages.length ? plural(state.stages.length, "measurement") : "none yet", "fixed"];
  if (box === "records") return ["always", "fixed"];
  if (box === "orchestrate" && M.parameterSearch(state)) return ["the search", "rules"];
  if (box === "generate" && M.paramOnly(state)) return ["not run", "off"];
  if (box === "dse") { const h = M.halfOf(state, "dse"); return [f.dse === "none" ? "adaptive" : cut(isAgent(f.dse) ? agentWord(M, f.dse) : f.dse), h]; }
  if (box === "digest" && f.knowledge === "none") return ["off", "off"];
  const v = f[box], opts = whoOptions(M, box, v, agent), o = opts.find(x => x.k === whoOn(opts, v)) || {};
  return [cut(o.label || String(v)), o.cls || "rules"];
}

/** `sel` the box opened; `pick(box)`. */
export function Cycle({ M, state, agent, sel, pick }) {
  const crit = M.criticsOf(state);
  const node = (k, cx0, cy0, w = 124, title = M.BOXES[k]?.title || k) => {
    const [who, kind] = workerOf(M, state, k, agent), x = cx0 - w / 2, y = cy0 - 21;
    const judged = ["orchestrate", "test", "select"].includes(k) || (k === "measure" && crit.measure !== "off");
    const c = judged ? crit[k] : null, ck = c ? (c === "off" ? "off" : isAgent(c) ? "agent" : "model") : null;
    const key = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(k); } };
    return html`<g class=${cx("hit", sel === k && "sel")} data-node=${k} tabindex="0" role="button" aria-pressed=${String(sel === k)}
        aria-label=${`${title}: ${who}${c ? `, critic ${c === "off" ? "off" : ck === "agent" ? agentWord(M, c) : "a model"}` : ""}`} onClick=${() => pick(k)} onKeyDown=${key}>
      <rect class=${cx("bx", kind)} x=${x} y=${y} width=${w} height="42" rx="8" />
      <text class="t" x=${cx0} y=${cy0 - 3} text-anchor="middle">${title}</text>
      <text class=${cx("w", kind)} x=${cx0} y=${cy0 + 12} text-anchor="middle">${who}</text>
      ${c ? html`<rect class=${cx("bx crit", ck)} x=${cx0 - 38} y=${y - 13} width="76" height="15" rx="7.5" />
        <text class=${cx("w crit", ck)} x=${cx0} y=${y - 2.5} text-anchor="middle">${`critic · ${c === "off" ? "off" : ck === "agent" ? cut(agentWord(M, c), 9) : "model"}`}</text>` : ""}</g>`;
  };
  const arcs = RING.map(([, deg], i) => {
    const a0 = (deg + 19) * Math.PI / 180, a1 = (RING[(i + 1) % 6][1] - 19) * Math.PI / 180;
    const x0 = CX + R * Math.cos(a0), y0 = CY + R * Math.sin(a0), x1 = CX + R * Math.cos(a1), y1 = CY + R * Math.sin(a1);
    return html`<path class="e" d=${`M${f1(x0)} ${f1(y0)} A${R} ${R} 0 0 1 ${f1(x1)} ${f1(y1)}`} />${head(x1 - 6 * Math.sin(a1), y1 + 6 * Math.cos(a1), x1, y1)}`;
  });
  const G = P.generate, T = P.test, Me = P.measure, O = P.orchestrate, Rc = P.records, off = b => (state.flow || {})[b] === "off" || (state.flow || {})[b] === "none";
  return html`<svg class="cfg-cycle" viewBox="0 0 790 500" role="group" aria-label="The loop: each box is one step">
    <circle class="ring" cx=${CX} cy=${CY} r=${R} />
    ${arcs}
    <path class="e back" d=${`M${f1(T[0] - 44)} ${f1(T[1] + 10)} Q${CX + 70} ${CY - 110} ${f1(G[0] + 22)} ${f1(G[1] + 22)}`} />${head(G[0] + 30, G[1] + 30, G[0] + 22, G[1] + 22, true)}
    <text class="el back" x=${CX + 36} y=${CY - 96}>repair</text>
    <path class="e back" d=${`M${f1(Me[0] - 60)} ${f1(Me[1] - 12)} Q${CX + 110} ${CY - 20} ${f1(G[0] + 4)} ${f1(G[1] + 22)}`} />${head(G[0] + 8, G[1] + 32, G[0] + 4, G[1] + 22, true)}
    <text class="el back" x=${CX + 84} y=${CY + 30}>improve</text>
    ${[["validate", 64], ["plan", 136], ["dse", 208], ["feedback", 280]].map(([k, y]) => html`${line(150, y, O[0] - 62, O[1] - 8 + (y - 172) * 0.08, off(k) || k === "plan" && state.flow.plan === "off")}${node(k, 86, y, 128)}`)}
    ${line(492, 52, G[0] + 62, G[1] - 6, true)}${node("knowledge", 556, 52, 128)}
    ${line(650, 52, 620, 52, true)}${node("digest", 714, 52, 128)}
    ${line(654, Me[1], Me[0] + 62, Me[1], true)}${node("calibrate", 714, Me[1], 120)}
    ${line(Rc[0] - 40, Rc[1] + 21, 120, 449, off("lessons"))}${node("lessons", 86, 470, 128)}
    ${RING.map(([k]) => node(k, P[k][0], P[k][1]))}</svg>`;
}

// Flux web, configure (D1118, D1128, D1139): the loop drawn as it runs -- five steps on one circle (Search, Design,
// Check, Measure, Choose), an arrowhead half way between each two, clockwise; left of it one column: the Setup box
// (Validate, Plan, Knowledge -- skills, the library, lessons, the workbench digest, D1136, D1152 -- and notes) feeding Search, and the record Choose
// writes, each link one straight horizontal arrow. The search policy is Search's, calibration Measure's. A design its
// checks refuse goes back to Design. Each box wears its kind's colour (Live's), an "AI" pill when a model or an agent
// works it, a "critic" pill when a critic judges it; a step that is off is dashed. A click (Enter, Space) opens it.

import { cx, html } from "../view.js";
import { plural } from "../format.js";
import { WORK_COLORS } from "../live/work_style.js";
import { agentWord, isAgent } from "./who.js";
import { SvgPill, pillWidth } from "../components/SvgPill.js";

const CX = 450, CY = 192, R = 150;
const RING = [["orchestrate", -162], ["generate", -90], ["test", -18], ["measure", 54], ["select", 126]];
const P = Object.fromEntries(RING.map(([k, a]) => [k, [CX + R * Math.cos(a * Math.PI / 180), CY + R * Math.sin(a * Math.PI / 180)]]));
/** Each box's kind of work, as Live colours it. */
const KIND = { validate: "setup", plan: "plan", orchestrate: "search", feedback: "setup", generate: "design",
  test: "check", measure: "measure", select: "choose", knowledge: "setup", records: "setup" };
const SETUP = [["validate"], ["plan"], ["knowledge"], ["feedback"]];        // D1152: Knowledge -- skills, library, lessons, workbench
const BIG = 124, SMALL = 140, COL = 102;                  // box widths; the column's centre
const f1 = x => x.toFixed(1);

function head(x1, y1, x2, y2, back) {
  const a = Math.atan2(y2 - y1, x2 - x1), l = 8, s = 0.42;
  return html`<path class=${cx("ah", back && "back")} d=${`M${f1(x2)} ${f1(y2)}L${f1(x2 - l * Math.cos(a - s))} ${f1(y2 - l * Math.sin(a - s))}L${f1(x2 - l * Math.cos(a + s))} ${f1(y2 - l * Math.sin(a + s))}Z`} />`;
}
/** A horizontal arrow at `y`, from x1 to x2. */
const across = (x1, x2, y, soft) => html`<path class=${cx("e", soft && "soft")} d=${`M${f1(x1)} ${f1(y)}L${f1(x2)} ${f1(y)}`} />${head(x1, y, x2, y)}`;
const cut = (s, n = 16) => s.length > n ? s.slice(0, n - 1) + "…" : s;
const AI = ["model", "agent"];

/** What works a box, in a word, and whether it is off or AI: [word, "off" | "ai" | ""]. */
function workerOf(M, state, box, agent) {
  const f = state.flow || {};
  const who = (bx, v = f[bx]) => M.whoWorks(bx, v, agent);            // the model's, the docs' drawing's too (D1146)
  if (box === "test") return [state.checks.length ? plural(state.checks.length, "check") : "no checks", ""];
  if (box === "measure") return [state.stages.length ? plural(state.stages.length, "measurement") : "none", ""];
  if (box === "records") return ["", ""];
  if (box === "orchestrate") {                   // its policy; AI when a model or an agent picks the policy or the work
    const p = f.dse, word = !p || p === "none" ? "default" : isAgent(p) ? cut(agentWord(M, p)) : cut(p);
    const ai = p === "model" || isAgent(p) || (!M.parameterSearch(state) && AI.includes(who("orchestrate").cls));
    return [word, ai ? "ai" : ""];
  }
  if (box === "generate" && M.paramOnly(state)) return ["off", "off"];
  if (box === "knowledge") {                     // D1152: AI when a model or an agent works a section (papers, lessons, workbench)
    const lib = f.knowledge !== "none", wb = f.workbench !== "off", ls = f.lessons && f.lessons !== "off";
    if (!lib && !wb && !ls && state.skillsOn === false) return ["off", "off"];
    return ["", lib || wb || isAgent(f.lessons) ? "ai" : ""];
  }
  const o = who(box);
  return [o.cls === "agent" ? cut(o.label) : cut(o.label || String(f[box])), o.cls === "off" ? "off" : AI.includes(o.cls) ? "ai" : ""];
}

/** `sel` the box opened; `pick(box)`. */
export function Cycle({ M, state, agent, sel, pick }) {
  const crit = M.criticsOf(state);
  /** A box centred at (cx0, cy0): a step (its word under its title) or, `small`, a title alone. */
  const node = (k, cx0, cy0, { w = BIG, small = false, title = M.BOXES[k]?.title || k } = {}) => {
    const [word, how] = workerOf(M, state, k, agent), h = small ? 30 : 40, x = cx0 - w / 2, y = cy0 - h / 2;
    const c = ["orchestrate", "test", "select", "measure"].includes(k) && crit[k] && crit[k] !== "off" ? crit[k] : null;
    const pills = [how === "ai" ? ["AI", "ai"] : null, c ? ["critic", "critic"] : null].filter(Boolean);
    const key = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(k); } };
    const shown = small ? "" : word;
    let px = x + w - 6;
    return html`<g class=${cx("hit", sel === k && "sel")} data-node=${k} tabindex="0" role="button" aria-pressed=${String(sel === k)}
        aria-label=${`${title}${word ? `: ${word}` : ""}${how === "ai" ? ", AI" : ""}${c ? ", critic" : ""}`} onClick=${() => pick(k)} onKeyDown=${key}>
      <rect class=${cx("bx", how === "off" && "off")} style=${`--k:${WORK_COLORS[KIND[k]] || "var(--muted)"}`} x=${x} y=${y} width=${w} height=${h} rx="8" />
      <text class="t" x=${x + 10} y=${cy0 + (shown ? -3 : 4)}>${title}</text>
      ${shown ? html`<text class=${cx("w", how)} x=${x + 10} y=${cy0 + 12}>${shown}</text>` : ""}
      ${pills.map(([t, cls]) => { px -= pillWidth(t); const at = px; px -= 3; return html`<${SvgPill} x=${at} y=${small ? cy0 - 7 : y + 5} text=${t} kind=${cls} />`; })}</g>`;
  };
  // the circle: one line behind the steps (their fill hides it), an arrowhead half way between each two (D1139)
  const heads = RING.map(([, deg], i) => {
    const next = RING[(i + 1) % RING.length][1] + (i === RING.length - 1 ? 360 : 0), m = (deg + next) / 2 * Math.PI / 180;
    const x = CX + R * Math.cos(m), y = CY + R * Math.sin(m), tx = -Math.sin(m), ty = Math.cos(m);
    return head(x - 6 * tx, y - 6 * ty, x + 4 * tx, y + 4 * ty);
  });
  // left: the Setup box centred on Search's height, the record at Choose's: both links straight across
  const O = P.orchestrate, Ch = P.select, G = P.generate, T = P.test, gap = 36, gh = 26 + SETUP.length * gap + 4, gt = O[1] - gh / 2;
  const gx = COL - SMALL / 2 - 12, gw = SMALL + 24;
  return html`<svg class="cfg-cycle" viewBox="0 0 670 350" role="group" aria-label=${M.WORDS.graph.aria}>
    <circle class="e ring-line" cx=${CX} cy=${CY} r=${R} /><g class="ring-heads">${heads}</g>
    <path class="e back" d=${`M${f1(T[0] - 30)} ${f1(T[1] + 20)} Q${f1(CX + 30)} ${f1(CY - 50)} ${f1(G[0] + 12)} ${f1(G[1] + 20)}`} />${head(G[0] + 16, G[1] + 31, G[0] + 12, G[1] + 20, true)}
    <g class="grp"><rect x=${gx} y=${gt} width=${gw} height=${gh} rx="12" /><text class="lane" x=${gx + 10} y=${gt + 16}>SETUP</text></g>
    ${SETUP.map(([k, title], i) => node(k, COL, gt + 41 + i * gap, { w: SMALL, small: true, title }))}
    ${across(gx + gw, O[0] - BIG / 2, O[1], true)}
    ${node("records", COL, Ch[1], { w: SMALL, small: true })}${across(Ch[0] - BIG / 2, COL + SMALL / 2, Ch[1])}
    ${RING.map(([k]) => node(k, P[k][0], P[k][1]))}</svg>`;
}

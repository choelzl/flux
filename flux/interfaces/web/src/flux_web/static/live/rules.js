// Live (D1095): how a task, a try, a pass ended -- the rules every view judges by (the tree's lines,
// the graph's boxes, the chain's chips), in one place. Plain functions of the task model, no page.

import { boxOf } from "./facts.js";
import { designOf, flowBoxes, stepOf, taskLabel } from "./views.js";
import { isTool } from "./inspector/parts.js";

export const CRIT = /^\s*critique\s*:/i;
export const STAGE = /^\s*(simulation|analytical)\s*:/i;
export const isCritique = n => CRIT.test(String(n.name));

/** D1082: a design that got past its failures -- finished well, its last step ended well (not one cut off). */
const getsPast = p => {
  if (boxOf(p) !== "design" || p.t1 == null || p.failed || p.timedOut || p.interrupted) return false;
  const last = [...(p.kids || [])].sort((a, b) => a.t0 - b.t0).at(-1);
  return !last || (last.t1 != null && !last.failed && !last.interrupted && !last.timedOut);
};
/** D1031: a list of tries or passes is judged by how it ended -- its last one failed is ✗. */
export const ended = nodes => { const last = [...nodes].sort((a, b) => (a.t1 ?? a.t0) - (b.t1 ?? b.t0)).at(-1); return !!last?.failed; };
/** D1044: a critique that rejected what it judged; D1075: guidance, "!" -- not a failure. */
export const rejectedBy = n => isCritique(n) && /^(rejected|sent back|objection)/i.test(String(n.output?.verdict ?? n.fields?.verdict ?? ""));

/** The judge of one render: the model's passes read once (D1089), what each design run's answer is. */
export function judge(model, membership) {
  const later = new Map();
  // D1089: a design run that failed (out of time) when a later run of the design in its pass got past it
  const laterPasses = n => {
    if (boxOf(n) !== "design") return false;
    if (!later.has(n.id)) later.set(n.id, [...(n.parent ? n.parent.kids : model.roots || [])]
      .some(k => k !== n && k.t0 > n.t0 && boxOf(k) === "design" && (membership.get(k.id) ?? null) === (membership.get(n.id) ?? null) && getsPast(k)));
    return later.get(n.id);
  };
  const recovered = n => { if (laterPasses(n)) return true; for (let p = n.parent; p; p = p.parent) if (getsPast(p) || laterPasses(p)) return true; return false; };
  const hardFail = n => n.failed && !recovered(n);
  // D995: what holds a failure under it ("!"); D1075: a rejection under it, "!" too
  const below = new Set(), soft = new Set();
  for (const n of model.nodes.values()) {
    if (n.failed) for (let p = n.parent; p && !getsPast(p); p = p.parent) below.add(p.id);
    else if (n.rejected) for (let p = n.parent; p; p = p.parent) soft.add(p.id);
  }
  const marked = id => below.has(id) || soft.has(id);
  /** A set of a pass's tasks as one mark: running, failed (it ended so, or the box it ended on failed, D1078),
      partly ("!": a step under it failed, ran out of time or was rejected), or done. */
  const passState = (p, nodes, rows, notes) => {
    if (nodes.some(n => n.t1 == null)) return "running";
    const top = nodes.filter(n => n.parent == null || (membership.get(n.parent.id) ?? null) !== p);
    const lastBad = p != null && flowBoxes(rows, p, false, notes).filter(b => b.runs.length).at(-1)?.state === "bad";
    if (ended(top.length ? top : nodes) || lastBad) return "failed";
    return nodes.some(n => hardFail(n) || n.timedOut && !recovered(n) || n.rejected || marked(n.id)) ? "part" : "done";
  };
  return { laterPasses, recovered, hardFail, below, soft, marked, passState };
}

/** D1061: a step that is only its one step of the same kind (a choice and its pick) is that step; D1066: a
    choice opens its pick beside a critique of it too; a command is its step's (D1034). */
export const soleOf = n => {
  const k = (n?.kids || []).filter(x => !(boxOf(n) === "choose" && isCritique(x)));
  return k.length === 1 && boxOf(k[0]) === boxOf(n) && !(k[0].kids || []).length && !/^\s*tool\s*:/i.test(String(k[0].name)) ? k[0] : null;
};
/** What follows a label's kind word ("Check · ut" -> "ut"). */
export const rest = n => { const l = taskLabel(n), at = l.indexOf(" · "); return at < 0 ? "" : l.slice(at + 3); };
/** D1051: a check said by its own word ("check", "gate", a scope check). */
export const checkWord = n => /^\s*test\s*:\s*(?:fast )?check\b/i.test(String(n.name)) ? "check" : rest(n).split(" · ")[0] || "check";
/** D1063: which of a check's tests failed first, and how many failing. */
export const firstFailing = n => {
  const bad = (n.kids || []).find(k => k.failed);
  if (!bad) {     // D1235: a check that took its one command in -- the check it stopped at, as the engine says it
    if (!n.failed || !isTool(n)) return "";
    const f = n.output?.failing; return `${n.tags?.["failed at"] || stepOf(n) || "a test"}${f ? ` ${f} failing` : " failed"}`;
  }
  const f = bad.output?.failing; return `${rest(bad).split(" · ")[0] || "a test"}${f ? ` ${f} failing` : " failed"}`;
};
/** D1046: what a stage measured, as it said it -- its first numbers, the goals' first (D1068). */
export const numbersOf = (n, goals = []) => {
  const line = String(n.output?.measured ?? n.fields?.measured ?? "").split("\n")[0], at = line.indexOf(": ");
  if (at < 0) return "";
  // key=value pairs whose value is a plain number; a dictionary's {...} or a list is skipped whole (D1051)
  const pairs = [...line.slice(at + 2).matchAll(/([A-Za-z][\w.]*)=(\{[^}]*\}|\[[^\]]*\]|[^,]+)/g)];
  const g = goals.map(x => x.metric), rank = k => { const i = g.findIndex(m => k === m || k.startsWith(m + ".")); return i < 0 ? g.length : i; };
  return pairs.filter(([, k, v]) => !k.startsWith("_") && /^-?[\d.]+(e-?\d+)?$/i.test(v.trim())).sort((a, b) => rank(a[1]) - rank(b[1]))
    .slice(0, 3).map(([, k, v]) => `${k} ${v.trim()}`).join(" · ");
};
/** The design a try made: its draft's own, the check after it, or the design it was asked for. */
export const madeOf = (t, opens = t.lead) => designOf(t.lead) || t.steps.map(designOf).find(Boolean)
  || (/\S+#\S+/.exec(String(t.lead.params?.["turn why"] || opens.why || t.lead.why || "")) || [""])[0] || "";

/** Designs said by their id (D1059): only the loop's own name goes ("#4") -- a part's keeps its part
    ("lo#1"), a baseline is "baseline", the parts' composition (named after the loop) "whole" (D1094). */
export function namer(names) {
  const loopNamed = w => names.includes(w);
  const idOf = (pre, id) => id === "#baseline" ? "baseline" : !pre || loopNamed(pre) ? id : pre + id;
  const short = c => loopNamed(String(c)) ? "whole" : String(c).replace(/^(.*?)(#[^#\s]+)$/, (_, pre, id) => idOf(pre, id));
  const shortAll = t => typeof t === "string" ? t.replace(/([^\s·#]+)(#[^#\s]+)/g, (_, pre, id) => idOf(pre, id))
    .replace(/(^|[\s★(])([^\s·#()]+)(?=$|[\s·,)])/g, (all, pre, w) => loopNamed(w) ? `${pre}whole` : all) : t;
  return { short, shortAll, loopNamed };
}

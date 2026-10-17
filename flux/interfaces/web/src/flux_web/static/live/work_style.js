// Shared timeline colours: work keeps its colour, agent activity is an overlay.
// D978: palette A4 -- one meaning per colour in the tree, the graph and the timeline; the agent keeps its pink.
// D1107: the colours are the stylesheet's tokens (--work-*, --agent: a value per theme), named here for a style.
// a critic is a role, not a kind of work: its tag is neutral, the AI pill the only pink (no --work-critic)
import { boxOf } from "./facts.js";
export { aiWorking, boxOf } from "./facts.js";

const KINDS = ["setup", "plan", "search", "design", "check", "measure", "choose"];
export const WORK_COLORS = Object.fromEntries(KINDS.map(k => [k, `var(--work-${k})`]));
export const AGENT_COLOR = "var(--agent)";
export const workLabel = kind => kind[0].toUpperCase() + kind.slice(1);
/** A timeline tick's words, `at` seconds into a chart `span` seconds wide: milliseconds under a second
    ("+25 ms"), else `dur`'s words ("+3m 01s"). */
export const tickText = (at, span, dur) => span < 1 ? `+${Math.round(at * 1000)} ms` : `+${dur(at) || "0s"}`;

/** A task's own time, as Live draws it: from its start to its end less the time its children
    ran (side by side counted once); a task with no children is its whole span. `end(n)` is a
    task's end, or now while it runs. */
export function ownSpans(node, end) {
  const t1 = end(node), spans = [];
  let cursor = node.t0;
  for (const kid of node.kids.slice().sort((a, b) => a.t0 - b.t0)) {
    const a = Math.max(node.t0, kid.t0), b = Math.min(t1, end(kid));
    if (a > cursor) spans.push([cursor, a]);
    cursor = Math.max(cursor, b);
  }
  if (cursor < t1 || !node.kids.length) spans.push([cursor, t1]);
  return spans;
}

/** D1182: where a pass's time went -- its span (first start to last end, as the tree's line says it), each
    kind's own time, and the time none of its tasks ran (`idle`: passes side by side take turns), so the parts
    add up to the span. `end(n)`: a task's end, or now while it runs. */
export function passTime(nodes, end) {
  const t0 = Math.min(...nodes.map(n => n.t0)), t1 = Math.max(...nodes.map(end)), kinds = {}, all = [];
  for (const n of nodes) for (const [s0, s1] of ownSpans(n, end)) { const k = boxOf(n); kinds[k] = (kinds[k] || 0) + (s1 - s0); all.push([s0, s1]); }
  let busy = 0, at = -Infinity;
  for (const [s0, s1] of all.sort((a, b) => a[0] - b[0])) { const a = Math.max(s0, at); if (s1 > a) { busy += s1 - a; at = s1; } }
  return { t0, t1, kinds, idle: Math.max(0, t1 - t0 - busy) };
}

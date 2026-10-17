// Shared timeline colours: work keeps its colour, agent activity is an overlay.
// D978: palette A4 -- one meaning per colour in the tree, the graph and the timeline; the agent keeps its pink.
// D1107: the colours are the stylesheet's tokens (--work-*, --agent: a value per theme), named here for a style.
// a critic is a role, not a kind of work: its tag is neutral, the AI pill the only pink (no --work-critic)
const KINDS = ["setup", "plan", "search", "design", "check", "measure", "choose"];
export const WORK_COLORS = Object.fromEntries(KINDS.map(k => [k, `var(--work-${k})`]));
export const AGENT_COLOR = "var(--agent)";
export const workLabel = kind => kind[0].toUpperCase() + kind.slice(1);
/** A timeline tick's words, `at` seconds into a chart `span` seconds wide: milliseconds under a second
    ("+25 ms"), else `dur`'s words ("+3m 01s"). */
export const tickText = (at, span, dur) => span < 1 ? `+${Math.round(at * 1000)} ms` : `+${dur(at) || "0s"}`;

// D977: the kind of work a phase names, as Live's timeline has it (flux_web/timeline.py `kind_of`,
// kept equal by tests/unit/test_work_style.py): null when it names none of its own.
export function kindOf(name, why = "") {
  name = String(name).toLowerCase();
  const at = name.indexOf(":"), head = (at < 0 ? name : name.slice(0, at)).trim(), rest = at < 0 ? "" : name.slice(at + 1).trim();
  // D996: orchestrating decides what to search and design -- the Search box's, by a model or an agent
  if (head === "orchestrate" || head === "agent" && String(why || "").trim().toLowerCase().split(/\s+/)[0] === "orchestrate") return "search";
  // D1143: an agent digesting the library or the workbench is the Setup's -- a top task in a journal written before
  // its quiet step held it (the workbench's README was an agent's: Design)
  if (head === "agent" && String(why || "").trim().toLowerCase().split(/\s+/)[0] === "digest") return "setup";
  if (head === "agent" || head === "llm" || name.startsWith("model")) return null;    // workers inherit the work they assist
  if (head === "tool") {
    const word = String(why || "").toLowerCase().split(/\s+/).filter(Boolean)[0] || "";
    if (word === "generate") return "design";
    if (word === "stage" || word === "estimate") return /^\s*stage\s+gate\b/i.test(String(why)) ? "check" : "measure";   // D1094
    if (["test", "lint", "golden", "build", "compile"].includes(word)) return "check";
    return null;
  }
  if (["knowledge", "records", "feedback", "extract", "validate", "setup"].includes(head)) return "setup";
  if (head === "propose" && rest.startsWith("decompose") || head === "gate" && (rest.startsWith("tools") || rest.startsWith("the problem"))) return "setup";
  if (head === "build" || head === "generation" && rest.startsWith("build ")) return "check";
  if (["test", "gate", "judge", "admitted", "verify", "probe"].includes(head)) return "check";
  // D1031: `evaluation` is the climb through the stages (compose, measure, cutoffs, routing) -- the Measure box's
  // D1094: the built-in gate stage of a loop with no measure stage counts the checks' failures -- it is the checks', not a measurement
  if (["simulation", "analytical"].includes(head) && rest.trim() === "gate") return "check";
  if (["simulation", "physical", "analytical", "measure", "screen", "confirm", "estimate", "calibrate", "evaluation"].includes(head)) return "measure";
  if (["generation", "generate", "llm-gen", "template-fill", "template", "prototype", "patch", "repair", "rewrite",
    "design", "oracle", "invent", "compute"].includes(head)) return "design";
  if (head === "propose" && rest.startsWith("brief")) return "design";        // D1029: the design's brief, not a choice
  if (head === "propose" && rest.startsWith("plan")) return "plan";           // D1038: the menu plan, not a choice
  if (["propose", "decide", "frontier", "decision", "select"].includes(head)) return "choose";
  // D1034: a critique is in the box of what it judges -- the split (setup), the decision (choose), the numbers (measure, D1040), a design (check)
  if (head === "critique") return rest.startsWith("decomposition") ? "setup" : rest.startsWith("decision") ? "choose" : rest.startsWith("measurement") ? "measure" : "check";
  if (head === "dse") return "search";
  if (head === "plan" || head === "search") return head;
  return null;
}

/** A task's kind: its own name's, else its nearest ancestor's; under an agent with none, design. */
export function phaseKind(node) {
  let agent = false;
  for (let p = node; p; p = p.parent) {
    const k = kindOf(p.name, p.why);
    if (k) return k;
    if (/^\s*agent\s*:/i.test(String(p.name))) agent = true;
  }
  return agent ? "design" : "setup";
}

/** True when an agent or the model works in this task or one it is under (D978: the overlay). D1050: a wrapper
    that took in its one turn (a draft, a critique) is that turn -- `params.turn` says so from the turn's start. */
export function aiWorking(node) {
  for (let p = node; p; p = p.parent) if (/^\s*(agent|llm|model)\b/i.test(String(p.params?.turn || p.name))) return true;
  return false;
}

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
  for (const n of nodes) for (const [s0, s1] of ownSpans(n, end)) { const k = phaseKind(n); kinds[k] = (kinds[k] || 0) + (s1 - s0); all.push([s0, s1]); }
  let busy = 0, at = -Infinity;
  for (const [s0, s1] of all.sort((a, b) => a[0] - b[0])) { const a = Math.max(s0, at); if (s1 > a) { busy += s1 - a; at = s1; } }
  return { t0, t1, kinds, idle: Math.max(0, t1 - t0 - busy) };
}

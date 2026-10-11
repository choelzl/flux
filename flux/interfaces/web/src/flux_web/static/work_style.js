// Shared timeline colours: work keeps its colour, agent activity is an overlay.
export const WORK_COLORS = { setup: "#8f9aa6", plan: "#c98a56", search: "#d9b440", design: "#5b8def",
  check: "#4fb286", measure: "#48b3c9", choose: "#e8804f", critic: "#a3c956" };
export const AGENT_COLOR = "#d45eae";
export const workLabel = kind => kind[0].toUpperCase() + kind.slice(1);

// D977: the kind of work a phase names, as Live's timeline has it (flux_web/timeline.py `kind_of`,
// kept equal by tests/unit/test_work_style.py): null when it names none of its own.
export function kindOf(name, why = "") {
  name = String(name).toLowerCase();
  const at = name.indexOf(":"), head = (at < 0 ? name : name.slice(0, at)).trim(), rest = at < 0 ? "" : name.slice(at + 1).trim();
  if (head === "agent" || head === "llm" || name.startsWith("model")) return null;    // workers inherit the work they assist
  if (head === "tool") {
    const word = String(why || "").toLowerCase().split(/\s+/).filter(Boolean)[0] || "";
    if (word === "generate") return "design";
    if (word === "stage" || word === "estimate") return "measure";
    if (["test", "lint", "golden", "build", "compile"].includes(word)) return "check";
    return null;
  }
  if (["knowledge", "records", "feedback", "extract", "validate", "setup"].includes(head)) return "setup";
  if (head === "propose" && rest.startsWith("decompose") || head === "gate" && (rest.startsWith("tools") || rest.startsWith("the problem"))) return "setup";
  if (head === "build" || head === "generation" && rest.startsWith("build ")) return "check";
  if (["test", "gate", "judge", "admitted", "verify", "probe"].includes(head)) return "check";
  if (["simulation", "physical", "analytical", "measure", "screen", "confirm", "estimate", "calibrate"].includes(head)) return "measure";
  if (["generation", "generate", "llm-gen", "template-fill", "template", "prototype", "patch", "repair", "rewrite",
    "design", "oracle", "invent", "compute", "evaluation"].includes(head)) return "design";
  if (["propose", "orchestrate", "decide", "frontier", "decision", "select"].includes(head)) return "choose";
  if (head === "critique") return "critic";
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

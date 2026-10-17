// Live (D1234): a journal written before the engine said what each task is (no `format` in its hello) read
// as Live always read one -- its facts guessed from the names, the words of `why`, sibling order and the marks.
// Only facts.js calls this, and only for such a journal; `journal_migrate` (flux_loop) brings old journals up
// to date with the same rules, once, and this module goes when none is left to read.

// D977: the kind of work a phase names: null when it names none of its own.
export function kindOf(name, why = "") {
  name = String(name).toLowerCase();
  const at = name.indexOf(":"), head = (at < 0 ? name : name.slice(0, at)).trim(), rest = at < 0 ? "" : name.slice(at + 1).trim();
  // D996: orchestrating decides what to search and design -- the Search box's, by a model or an agent
  if (head === "orchestrate" || head === "agent" && String(why || "").trim().toLowerCase().split(/\s+/)[0] === "orchestrate") return "search";
  // D1143: an agent digesting the library or the workbench is the Setup's
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
  // D1031: `evaluation` is the climb through the stages; D1094: the built-in gate stage is the checks'
  if (["simulation", "analytical"].includes(head) && rest.trim() === "gate") return "check";
  if (["simulation", "physical", "analytical", "measure", "screen", "confirm", "estimate", "calibrate", "evaluation"].includes(head)) return "measure";
  if (["generation", "generate", "llm-gen", "template-fill", "template", "prototype", "patch", "repair", "rewrite",
    "design", "oracle", "invent", "compute"].includes(head)) return "design";
  if (head === "propose" && rest.startsWith("brief")) return "design";        // D1029
  if (head === "propose" && rest.startsWith("plan")) return "plan";           // D1038
  if (["propose", "decide", "frontier", "decision", "select"].includes(head)) return "choose";
  // D1034: a critique is in the box of what it judges
  if (head === "critique") return rest.startsWith("decomposition") ? "setup" : rest.startsWith("decision") ? "choose" : rest.startsWith("measurement") ? "measure" : "check";
  if (head === "dse") return "search";
  if (head === "plan" || head === "search") return head;
  return null;
}

/** A task's box: its own name's, else its nearest ancestor's; under an agent with none, design. */
export function boxOf(node) {
  let agent = false;
  for (let p = node; p; p = p.parent) {
    const k = kindOf(p.name, p.why);
    if (k) return k;
    if (/^\s*agent\s*:/i.test(String(p.name))) agent = true;
  }
  return agent ? "design" : "setup";
}

/** The agent or model that worked in a task (D1050: a wrapper's taken-in turn): "agent:claude", "model:qwen", "". */
export function workerOf(n) {
  const turn = String(n.params?.turn || n.name || ""), m = /^\s*(agent|llm|model)\b\s*:?\s*(\S*)/i.exec(turn);
  if (!m) return "";
  return /^agent/i.test(m[1]) ? `agent:${m[2]}` : `model:${/^llm/i.test(m[1]) ? "" : m[2]}`;
}

/** The design a task names itself ("generate lut-chunk=4", a measurement's "stage esl x#4", a check's "ut demo#3"). */
export function designOwn(x) {
  const why = String(x.why || "");
  const m = /^(?:stage|estimate)\s+\S+\s+(\S+)/i.exec(why) || /^(?:generate|test)\s+(\S+)/i.exec(why);
  if (m) return m[1];
  const t = /^\s*tool\s*:/i.test(String(x.name)) && /^\S+\s+(\S+)$/.exec(why.trim());     // D1063
  return t ? t[1] : "";
}

/** D1037: the check or stage a command runs, from what it was told ("build bau#43", "stage esl bau#43"). */
export function stepOf(n) {
  const w = String(n.why || "").trim().split(/\s+/);
  if (w[0] === "stage" && w[1]) return w[1];
  if (w[0] === "estimate" && w[1]) return `estimate ${w[1]}`;
  return !w[0] || ["generate", "search", "baseline", "focus", "scope", "test"].includes(w[0]) ? "" : w[0];
}

/** The pass a task is in: `params.pass` up the chain, else the last pass mark before its root, else its
    name puts setup into the start's Setup (D978, D1012, D1041, D1143). */
export function passOf(node, marks) {
  let root = node;
  for (let p = node; p; p = p.parent) {
    const value = p.params?.pass;
    if (value != null && value !== "" && Number.isFinite(Number(value))) return Number(value);
    root = p;
  }
  let pass = null;
  const before = mark => mark.seq != null && root.seq != null ? mark.seq < root.seq : mark.t <= root.t0 + .001;
  for (const mark of marks) if (mark.name === "pass" && before(mark) && Number.isFinite(Number(mark.n))) pass = Number(mark.n);
  if (pass != null) return pass;
  const first = marks.find(m => m.name === "pass" && Number.isFinite(Number(m.n)));
  if (/^\s*(validate|digest|plan\b|lessons)/i.test(String(root.name))) return null;
  const digest = /^\s*agent\s*:/i.test(String(root.name)) && /^\s*digest\b/i.test(String(root.why || ""));
  if (!first) return digest || /^\s*(knowledge|records|feedback|extract|setup|gate:\s*(tools|the problem)|propose:\s*decompose)/i.test(String(root.name)) ? null : 1;
  if (Number(first.n) <= 0) return null;
  return Number(first.n) - 1;
}

/** D1065: the checks a failed check never reached -- the document's tests no command under it ran. */
export const notReached = (n, tests = []) => n.failed && tests.length && (n.kids || []).some(k => k.failed)
  ? tests.filter(t => !(n.kids || []).some(k => stepOf(k) === t)) : [];

// D1148: a quiet setup step written after the tasks it ran (a journal before D1143) takes them in
export const QUIET_SETUP = /^\s*(knowledge\s*:|propose\s*:\s*decompose|gate\s*:\s*(tools|the problem))/i;
export function takeIn(m, step, sibs) {
  const lastMark = m.marks.reduce((s, k) => k.name === "pass" ? Math.max(s, k.seq || 0) : s, 0);
  const inside = sibs.filter(k => k.t1 != null && k.t0 >= step.t0 - .002 && k.seq > lastMark);
  for (const k of inside) { sibs.splice(sibs.indexOf(k), 1); k.parent = step; step.kids.push(k); }
}

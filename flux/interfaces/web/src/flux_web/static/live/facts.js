// Live (D1234): what a task is, as the engine says it -- its box, pass, design, step, try and worker, and how
// its work came out (D1233: `tags` on its start and its end). The one place the views read them: a journal from
// before (no `format` in its hello) is read by legacy.js's rules instead.
import * as L from "./legacy.js";

const said = n => n && n.fmt >= 2;
const up = (n, k) => { for (let p = n; p; p = p.parent) { const v = p.tags?.[k]; if (v != null && v !== "") return v; } return null; };

/** Which box a task's work is: setup, plan, search, design, check, measure or choose. */
export const boxOf = n => said(n) ? up(n, "box") || "setup" : L.boxOf(n);
/** A note's box (a quiet step, D1047): null when it says none. */
export const noteBox = note => note.tags ? note.tags.box || null : L.kindOf(note.name, note.why);
/** The pass a task is in (null: the start's Setup). */
export const passOf = (n, marks) => { if (!said(n)) return L.passOf(n, marks); const p = up(n, "pass"); return p == null ? null : Number(p); };
/** The pass a note is in (D1052). */
export const notePassOf = (note, marks) => note.tags ? (note.tags.pass == null ? null : Number(note.tags.pass))
  : L.passOf({ name: note.name, t0: note.t, seq: note.seq, parent: null }, marks);
/** The design a task works on: its own, or the first a task under it names (breadth first). */
export function designOf(n) {
  const stack = [n];
  while (stack.length) {
    const x = stack.shift(), d = said(x) ? x.tags?.design || "" : L.designOwn(x);
    if (d) return d;
    stack.push(...(x.kids || []));
  }
  return "";
}
/** The check or stage a command runs ("" when it runs none -- a generator, the document's default check). */
export const stepOf = n => said(n) ? String(n.tags?.step || "") : L.stepOf(n);
/** The agent or model that answered in this task ("agent:claude", "model:qwen"), or "". */
export const workerOf = n => said(n) ? String(n.tags?.worker || "") : L.workerOf(n);
export const isAgent = n => workerOf(n).startsWith("agent:");
export const isModel = n => workerOf(n).startsWith("model:");
export const isAI = n => !!workerOf(n);
/** D978: an agent or the model works in this task or one it is under (the timeline's overlay). */
export const aiWorking = n => { for (let p = n; p; p = p.parent) if (isAI(p)) return true; return false; };
/** A part's try this task belongs to (D1233), or null. */
export const tryOf = n => said(n) ? up(n, "try") : null;
/** D1065: the checks a failed check never reached -- as the engine said it, or from the document's checks. */
export const notReachedOf = (n, tests = []) => said(n) ? (n.failed && Array.isArray(n.tags?.["not reached"]) ? n.tags["not reached"] : []) : L.notReached(n, tests);
/** A task that is a turn itself (an agent's, a model's), not a step that took one in. */
export const isTurn = n => /^\s*(agent|llm|model)\b/i.test(String(n.name));

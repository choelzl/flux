// Live's inspector (D982, D1095): what a task is and what it gave -- plain functions of a task and the
// task model, no page.

import { AGENT_COLOR, WORK_COLORS as COLORS, boxOf } from "../work_style.js";
import { designOf, stepOf, taskLabel } from "../views.js";
import { isAgent, isModel, leadNote, notReachedOf } from "../facts.js";
import { num, plural } from "../../format.js";

export { isAgent, isModel };
/** A command's task -- or a check's or a stage's that took its one command in (D1235: its `turn`). */
export const isTool = n => /^\s*tool\s*:/i.test(String(n.name)) || /^\s*tool\s*:/i.test(String(n.params?.turn || ""));
/** The program a command ran ("gen.py"), "" for a task that ran none. */
export const programOf = n => (/^\s*tool\s*:\s*(.+)$/i.exec(String(n.params?.turn || n.name)) || [])[1]?.trim().replace(/^fluxpreset\s+/, "") || "";
export const headOf = n => String(n.name).toLowerCase().split(":")[0].trim();
export const colorOf = n => isAgent(n) ? AGENT_COLOR : COLORS[boxOf(n)] || COLORS.setup;
export const running = n => n.t1 == null;
/** A task's pill: its tone and its word -- D1075: a try whose check failed is a failed try. */
export const stateOf = n => { const t = leadNote(n), o = t?.outcome;
  return running(n) ? ["run", "running"] : n.interrupted ? ["warn", "interrupted"] : n.failed ? ["bad", "failed"] : n.timedOut ? ["warn", "time limit"]
    : o === "check failed" || o === "no design" ? ["bad", `failed · ${t.why || (o === "no design" ? "no design" : "its check failed")}`]
    : o === "not built" ? ["warn", "not built"] : n.rejected ? ["warn", "rejected"] : ["ok", "done"]; };
/** What it gave so far: its live fields, and once it ended its output over them (D1083: the last word). */
export const fieldsOf = n => ({ ...(n.fields || {}), ...(running(n) ? {} : (n.output || {})) });
export const stdoutOf = n => { const f = fieldsOf(n); return String(f.stdout ?? f["stdout (live tail)"] ?? ""); };
export const linesOf = s => String(s).split("\n").filter(l => l.trim());
export const has = o => !!o && Object.keys(o).length > 0;

/** `name=value` pairs a command printed (the loop's own reading of a measurement). */
export function metricsOf(text) {
  const out = {};
  for (const m of String(text).matchAll(/(?:^|\s)([A-Za-z_][\w.]*)=([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)(?=\s|$)/g)) if (!(m[1] in out)) out[m[1]] = Number(m[2]);
  return out;
}
/** D1055: a metric that is a dictionary ("sim_us={"t1": 41, "t2": 79}") -- its numbers by name. */
export function dictsOf(text) {
  const out = {};
  for (const m of String(text).matchAll(/(?:^|\s)([A-Za-z_][\w.]*)=(\{[^{}]*\})/g)) {
    try { const d = JSON.parse(m[2]); if (d && typeof d === "object" && !(m[1] in out)) out[m[1]] = d; } catch (_) { /* not a dictionary */ }
  }
  return out;
}
/** A check's own result line, "N failing" or "N failing of M" (every preset prints it). */
export function failingOf(text) {
  const all = [...String(text).matchAll(/(\d+)\s+failing(?:\s+of\s+(\d+))?/g)], m = all.at(-1);
  return m ? { failing: Number(m[1]), of: m[2] != null ? Number(m[2]) : null, index: m.index } : null;
}
export const meanOf = d => { const xs = Object.values(d || {}).filter(x => typeof x === "number"); return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null; };
export const cell = v => v == null ? "—" : typeof v === "object" ? Object.entries(v).map(([k, x]) => `${k} ${typeof x === "number" ? num(x) : x}`).join(" · ") : num(v);

/** D1065: the document's checks a check stopped at a failure never reached. */
export const notReached = notReachedOf;
/** D1066: a label whose "what" is the loop's own name is its kind alone ("Design", not "Design · demo-x");
    every design said by its id (rules.js namer). */
export const plain = (t, N) => { const at = String(t).lastIndexOf(" · "); return at > 0 && N.loopNamed(String(t).slice(at + 3)) ? String(t).slice(0, at) : N.shortAll(String(t)); };

/** D1182: a choice's columns -- the metric the decision named first ("the smallest time_ms"), then the goals,
    then what was measured; only those some design has a number for, three at most. */
export function chooseColumns(rows, goals, by = "") {
  const seen = [...new Set(rows.flatMap(([, m]) => Object.keys(m)))], filled = k => rows.some(([, m]) => m[k] != null);
  const named = seen.filter(k => new RegExp(`(^|[^\\w.])${k.replace(/[.]/g, "\\.")}(?![\\w.])`).test(String(by)))
    .sort((a, b) => String(by).indexOf(a) - String(by).indexOf(b));
  return [...new Set([...named, ...goals.map(g => g.metric), ...seen])].filter(filled).slice(0, 3);
}

/** One line of what a task gave: a check's failing count, a measurement's numbers, else nothing. */
export function summaryOf(x) {
  const leaves = x.kids?.length ? x.kids.flatMap(k => k.kids?.length ? k.kids : [k]) : [x];
  // D1059: a step that failed says so first -- which test, and how many of its cases
  const bad = leaves.length > 1 && leaves.find(l => l.failed && failingOf(stdoutOf(l))?.failing);
  if (bad) { const f = failingOf(stdoutOf(bad)); return `${stepOf(bad) || "a test"} ${f.failing} failing${f.of != null ? ` of ${f.of}` : ""}`; }
  // D1068: several checks that passed -- how many, not the first one's own line
  if (leaves.length > 1 && leaves.every(l => !l.failed && failingOf(stdoutOf(l)))) return `${leaves.length} checks, none failing`;
  for (const l of leaves) {
    const out = stdoutOf(l), f = failingOf(out);
    if (f) return `${f.failing} failing${f.of != null ? ` of ${f.of}` : ""}`;
    const m = Object.entries(metricsOf(out)).slice(0, 2);
    if (m.length) return m.map(([k, v]) => `${k} ${num(v)}`).join(", ");
  }
  return running(x) ? "running" : "";
}
/** The usual time of a task like this one (the same name and purpose's first word), from its earlier runs. */
export function usualOf(n, nodes) {
  const word = String(n.why || "").split(/\s+/)[0], times = [];
  for (const x of nodes.values()) if (x !== n && x.t1 != null && x.name === n.name && String(x.why || "").split(/\s+/)[0] === word) times.push(x.seconds ?? x.t1 - x.t0);
  if (!times.length) return null;
  times.sort((a, b) => a - b);
  return times[Math.floor(times.length / 2)];
}
/** The best of each goal among the other designs measured before it on the same stage (D1063; D1064: a
    dictionary's by its mean) -- and the record's (D1094: the loop's, from every pass), by the goals' directions. */
export function bestOf(n, env) {
  const goals = Object.fromEntries((env.goals || []).map(g => [g.metric, /max/i.test(g.direction || "") ? 1 : -1]));
  const best = {}, stageOf = x => String(x.why || "").split(/\s+/).slice(0, 2).join(" "), mine = designOf(n);
  for (const x of env.nodes.values()) {
    if (x === n || x.t1 == null || x.failed || x.name !== n.name || stageOf(x) !== stageOf(n) || x.t0 > n.t0 || (mine && designOf(x) === mine)) continue;
    const who = designOf(x);
    for (const [k, v] of Object.entries(metricsOf(stdoutOf(x)))) if (goals[k] && (best[k] == null || goals[k] * v > goals[k] * best[k].v)) best[k] = { v, who };
    for (const [k, d] of Object.entries(dictsOf(stdoutOf(x)))) {
      const m = meanOf(d);
      if (goals[k] && m != null && (best[k] == null || goals[k] * m > goals[k] * best[k].m)) best[k] = { v: d, m, who };
    }
  }
  const stage = (/^(?:stage|estimate)\s+(\S+)/i.exec(String(n.why || "")) || /^\s*(?:simulation|analytical)\s*:\s*(\S+)/i.exec(String(n.name || "")) || [])[1];
  for (const d of (stage && env.record) || []) {
    const nums = d.stages?.[stage], who = String(d.name || "");
    if (!nums || (mine && (who === mine || who.endsWith(mine)))) continue;
    for (const [k, v] of Object.entries(nums)) if (goals[k] && typeof v === "number" && (best[k] == null || best[k].m == null && goals[k] * v > goals[k] * best[k].v)) best[k] = { v, who };
  }
  return { best, goals };
}
/** The candidates measured so far, with their numbers: the frontier a choice is made from; D1089: a stage
    that measured without a tool of its own says it on its row ("x#1.2: failures=0, cycles=41"). */
export function measured(nodes) {
  const by = new Map();
  for (const x of nodes.values()) {
    if (x.t1 == null || x.failed || !isTool(x)) continue;
    const c = designOf(x), m = { ...metricsOf(stdoutOf(x)), ...dictsOf(stdoutOf(x)) };
    if (c && Object.keys(m).length) by.set(c, { ...(by.get(c) || {}), ...m });
  }
  for (const x of nodes.values()) {
    if (x.t1 == null || x.failed || !/^\s*(simulation|analytical)\s*:/i.test(String(x.name))) continue;
    for (const line of String(x.output?.measured ?? "").split("\n")) {
      const at = line.indexOf(": "); if (at < 0) continue;
      const c = line.slice(0, at).trim(), m = {};
      for (const [, k, v] of line.slice(at + 2).matchAll(/([A-Za-z_][\w.]*)=(-?[\d.]+(?:e-?\d+)?)(?=,|\s|$)/gi)) m[k] = Number(v);
      if (c && Object.keys(m).length && !by.has(c)) by.set(c, m);
    }
  }
  return by;
}
/** A command as a person reads it (D976): Flux's own interpreter path gone, `python -m flux_presets` as the
    `fluxpreset` the document said, paths under its folder -- and under the loop's (D1094) -- relative. */
export function tidyCommand(cmd, folder) {
  let text = String(cmd).replace(/\/nix\/store\/[a-z0-9]{32}-[^/\s]+\/bin\//g, "")
    .replace(/(^|\s)\S*python3?(?:\.\d+)?(?: -W ignore)? -m flux_presets(?:\.(\w+))?(?=\s|$)/g, (_, pre, sub) => `${pre}fluxpreset${sub ? " " + sub : ""}`)
    .replace(/(^|\s)\S*python3?(?:\.\d+)?(?: -W ignore)? -m flux_cli(?=\s|$)/g, "$1flux");
  if (folder && folder.length > 1) text = text.split(folder.replace(/\/$/, "") + "/").join("");
  return text.replace(/(^|[\s=])\/\S*?\/users\/[^/\s]+\/apps\/[^/\s]+\//g, "$1");
}
/** A model's tokens as people read them: "7,405 in · 181 out", how it finished only when it did not end by itself
    (an older journal's "7405 in, 181 out, finish_reason=stop" too). */
const FINISHED = { stop: "", length: "cut at its length limit", tool_calls: "asked for tools", content_filter: "filtered" };
export const tokensSaid = v => {
  const m = /^\s*(\d[\d,]*|None|—) in, (\d[\d,]*|None|—) out(?:, finish_reason=(\S+))?\s*$/.exec(String(v));
  if (!m) return v;
  const n = x => /^\d/.test(x) ? Number(x.replace(/,/g, "")).toLocaleString("en-US") : "—", fin = m[3] && m[3] !== "None" ? FINISHED[m[3]] ?? m[3] : "";
  return [`${n(m[1])} in`, `${n(m[2])} out`, fin].filter(Boolean).join(" · ");
};
/** D1094: an agent's model as it reported it, said once ("codex 1.0", not "codex codex 1.0"). */
export const modelSaid = v => String(v ?? "").replace(/^(\S+) (?=\1\b)/, "");
/** A check's result: its "N failing" line, and whether it passed -- "0 failing" from a check that exited non-zero
    (it did not build, it broke) or whose task failed is a failure: its exit says so. */
export function checkResult(n, out) {
  const fails = failingOf(out), f = fieldsOf(n), exited = f.exit != null && f.exit !== "" && Number(f.exit) !== 0;
  return fails ? { ...fails, exited, ok: fails.failing === 0 && !exited && !n.failed } : null;
}
/** The pool the engine chose from ("6 in the pool", the choice's why) is not the table: that lists the designs
    measured in the passes loaded -- said when the two differ. */
export function poolSaid(why, shown) {
  const pool = Number((/^\s*(\d+) in the pool\b/.exec(String(why || "")) || [])[1]) || 0;
  return !pool || !shown || pool === shown ? "" : shown < pool ? `${shown} of ${pool} shown: the rest measured in passes not loaded`
    : `${shown} shown: every design measured, ${pool} of them in the pool`;
}
/** An older journal's words, as they are said now: "1 candidate(s)" is "1 candidate" (D1094), "the smallest
    time_ms" is "smallest time_ms", "least area_um2 at …" "smallest area_um2 at …" (the engine's one wording). */
export const tidyWords = t => String(t).replace(/\b(\d+) (\w+)\(s\)/g, (_, k, x) => plural(Number(k), x)).replace(/^\s*the\s+(?=(smallest|largest|least|most)\b)/i, "")
  .replace(/^\s*least\b/i, "smallest").replace(/^\s*most\b/i, "largest");
export { designOf, taskLabel };

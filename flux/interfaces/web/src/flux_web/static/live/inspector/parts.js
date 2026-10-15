// Live's inspector (D982, D1095): what a task is and what it gave -- plain functions of a task and the
// task model, no page.

import { AGENT_COLOR, WORK_COLORS as COLORS, phaseKind } from "../../work_style.js";
import { candidateOf, ranFor, taskLabel } from "../../live_views.js";

export const turnOf = n => String(n.params?.turn || n.name);          // D1050: a wrapper's taken-in turn
export const isAgent = n => /^\s*agent\s*:/i.test(turnOf(n));
export const isTool = n => /^\s*tool\s*:/i.test(String(n.name));
export const isModel = n => /^\s*(llm\b|model\b)/i.test(turnOf(n));
export const headOf = n => String(n.name).toLowerCase().split(":")[0].trim();
export const colorOf = n => isAgent(n) ? AGENT_COLOR : COLORS[phaseKind(n)] || COLORS.setup;
export const running = n => n.t1 == null;
/** A task's pill: its class and its word -- D1075: a try whose check failed is a failed try. */
export const stateOf = n => running(n) ? ["live", "running"] : n.interrupted ? ["warn", "interrupted"] : n.failed ? ["bad", "failed"] : n.timedOut ? ["warn", "time limit"]
  : n.tryFailed ? ["bad", `failed · ${n.tryFailed}`] : n.rejected ? ["warn", "rejected"] : ["ok", "done"];
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
export const fmt = v => Math.abs(v) >= 100 ? String(Math.round(v * 10) / 10) : Math.abs(v) >= 1 ? v.toFixed(2).replace(/\.?0+$/, "") : Number(v.toPrecision(2)).toString();
export const cell = v => v == null ? "—" : typeof v === "object" ? Object.entries(v).map(([k, x]) => `${k} ${typeof x === "number" ? fmt(x) : x}`).join(" · ") : fmt(v);

/** D1065: the document's checks a check stopped at a failure never reached. */
export const notReached = (n, tests = []) => n.failed && tests.length && (n.kids || []).some(k => k.failed)
  ? tests.filter(t => !(n.kids || []).some(k => ranFor(k) === t)) : [];
/** D1066: a label whose "what" is the loop's own name is its kind alone ("Design", not "Design · demo-x");
    every design said by its id (rules.js namer). */
export const plain = (t, N) => { const at = String(t).lastIndexOf(" · "); return at > 0 && N.loopNamed(String(t).slice(at + 3)) ? String(t).slice(0, at) : N.shortAll(String(t)); };

/** One line of what a task gave: a check's failing count, a measurement's numbers, else nothing. */
export function summaryOf(x) {
  const leaves = x.kids?.length ? x.kids.flatMap(k => k.kids?.length ? k.kids : [k]) : [x];
  // D1059: a step that failed says so first -- which test, and how many of its cases
  const bad = leaves.length > 1 && leaves.find(l => l.failed && failingOf(stdoutOf(l))?.failing);
  if (bad) { const f = failingOf(stdoutOf(bad)); return `${ranFor(bad) || "a test"} ${f.failing} failing${f.of != null ? ` of ${f.of}` : ""}`; }
  // D1068: several checks that passed -- how many, not the first one's own line
  if (leaves.length > 1 && leaves.every(l => !l.failed && failingOf(stdoutOf(l)))) return `${leaves.length} checks, none failing`;
  for (const l of leaves) {
    const out = stdoutOf(l), f = failingOf(out);
    if (f) return `${f.failing} failing${f.of != null ? ` of ${f.of}` : ""}`;
    const m = Object.entries(metricsOf(out)).slice(0, 2);
    if (m.length) return m.map(([k, v]) => `${k} ${fmt(v)}`).join(", ");
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
  const best = {}, stageOf = x => String(x.why || "").split(/\s+/).slice(0, 2).join(" "), mine = candidateOf(n);
  for (const x of env.nodes.values()) {
    if (x === n || x.t1 == null || x.failed || x.name !== n.name || stageOf(x) !== stageOf(n) || x.t0 > n.t0 || (mine && candidateOf(x) === mine)) continue;
    const who = candidateOf(x);
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
    const c = candidateOf(x), m = { ...metricsOf(stdoutOf(x)), ...dictsOf(stdoutOf(x)) };
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
export { candidateOf, taskLabel };

// LiveAlt (D1095): the tree's lines as data -- what each line says and does, drawn by Tree.js for the tree
// and for a graph box's list alike. One line per box of a pass's work, its tries or steps under it, the
// rules of what folds, merges and repeats as the tree has always drawn them (D980, D1012, D1016, D1038,
// D1044, D1045, D1050, D1054, D1059, D1061...). No page: the lines are plain objects.
//
// A line: { type: "row", key, depth, cls, k (its colour), fold: {key, collapsed, label} | null,
//   act: {task} (opens a task) | {toggle} (opens or folds) | {pass} | {box: {p, key}}, picked, title,
//   mark: "spin" | {cls, glyph, title}, name: [segment], pills: [{cls, text, title}], time }
// a segment: a string, {b: text}, {cls, text, title}, or {dots: [cls]}.
// Other lines: {type: "more", key, depth, text} (more tries), {type: "skip", key, depth, text} (not run),
// {type: "wait", key, depth, k, title} (a box still to come).

import { dur } from "../format.js";
import { WORK_COLORS as COLORS, AGENT_COLOR, kindOf, workLabel, phaseKind } from "../work_style.js";
import { boxKind, candidateOf, flowBoxes, isDraft, ownView, passName, taskLabel, taskNote } from "../live_alt_views.js";
import { notReached } from "../live_alt_inspector.js";
import { taskState as state } from "../live_alt_model.js";
import { AI, STAGE, checkWord, ended, firstFailing, isCritique, madeOf, numbersOf, rejectedBy, rest, soleOf } from "./rules.js";

const TRIES = 5;
const STRETCH = ["search", "design", "check", "measure"];
const doneMark = (failed, partial) => ({ cls: failed ? "failed" : partial ? "partial" : "done", glyph: failed ? "✗" : partial ? "!" : "✓",
  title: partial && !failed ? "a step under it failed" : null });
const markOf = (n, J) => n.t1 == null ? "spin"
  : n.interrupted ? { cls: state(n), glyph: "—" }
  : n.timedOut ? { cls: "partial", glyph: "!", title: "stopped at its time limit" }
  : n.rejected ? { cls: "partial", glyph: "!", title: "rejected: the critic's word is guidance, not a failure" }
  : doneMark(n.failed, J.marked(n.id));

/** The lines of `rows` (tasks with their parent's id when it is among them). `v`: what the view knows --
    selected, model, membership, J (rules.js judge), N (namer), goals, isFolded, opened, allTries,
    pass, passView, passNotes, noteShort, scopeRows (every task of the scope, for the boxes still to come). */
export function lines(rows, v) {
  const { selected, J, N, goals } = v, out = [];
  const ids = new Set(rows.map(r => r.node.id)), kids = new Map();
  for (const r of rows) if (r.parent != null && ids.has(r.parent)) (kids.get(r.parent) || kids.set(r.parent, []).get(r.parent)).push(r);
  // D1012: a task whose one child is of its own kind and has children of its own is one line;
  // D1034: a stage or check whose one child is its command is one line, the program its note
  const merged = new Map();
  for (const r of rows) {
    const only = kids.get(r.node.id);
    if (only && only.length === 1 && /^\s*tool\s*:/i.test(String(only[0].node.name)) && !(kids.get(only[0].node.id) || []).length) {
      kids.delete(r.node.id); merged.set(r.node.id, [only[0].node]); continue;
    }
    for (let k = kids.get(r.node.id); k && k.length === 1; k = kids.get(r.node.id)) {
      const c = k[0], ck = kids.get(c.node.id);
      if (!ck || !ck.length || phaseKind(c.node) !== phaseKind(r.node) || /^\s*agent\s*:/i.test(String(c.node.name))) break;
      kids.set(r.node.id, ck); merged.set(r.node.id, [...(merged.get(r.node.id) || []), c.node]);
    }
  }
  // D1044: a run that is only an agent's or a model's turn is its parent's line -- the ai pill on it
  const aiOf = new Map();
  for (const r of rows) {
    const only = kids.get(r.node.id);
    if (only && only.length === 1 && AI.test(String(only[0].node.name)) && !(kids.get(only[0].node.id) || []).length) { aiOf.set(r.node.id, only[0].node); kids.delete(r.node.id); }
  }
  // D1037: a merged command says its program
  const note = n => [...(merged.get(n.id) || []).map(c => taskNote(c) || taskLabel(c).replace(/^[^·]*·\s*/, "")), ...(merged.has(n.id) ? [] : [taskNote(n)])]
    .filter(x => x && !taskLabel(n).endsWith(` · ${x}`)).join(" › ");
  const sel = v.model.nodes.get(selected), holds = id => { for (let p = sel; p; p = p.parent) if (p.id === id) return true; return false; };
  const now = Date.now() / 1000, secs = n => n.seconds ?? (n.t1 ?? now) - n.t0;
  const span = nodes => dur(Math.max(...nodes.map(n => n.t1 ?? now)) - Math.min(...nodes.map(n => n.t0)));
  const fold = (key, collapsed, label) => ({ key, collapsed, label });
  const colour = (n, kind = phaseKind(n)) => COLORS[kind] || (AI.test(String(n.params?.turn || n.name)) ? AGENT_COLOR : COLORS.setup);
  const aiPill = (n, title) => ({ cls: "alt-ai-pill", text: "ai", title });
  const partial = nodes => nodes.some(n => J.hardFail(n) || n.rejected || J.marked(n.id));
  // D1044: a box's line says the box, then what it worked on; a step under it drops the box's name
  const said = (n, box) => {
    const full = taskLabel(n), at = full.indexOf(" · "), r = at < 0 ? "" : full.slice(at + 3);
    // D1064: a critique says whose and what it said ("critique · #3 · rejected")
    const verdict = String(n.output?.verdict ?? n.fields?.verdict ?? ""), of = String(n.why || "").replace(/@\S*$/, "").trim();
    const crit = ["critique", of && !/^round\b/i.test(of) ? N.short(of) : "", /^(rejected|sent back)/i.test(verdict) ? "rejected" : verdict ? "no objection" : ""].filter(Boolean).join(" · ");
    if (isCritique(n)) return box ? [{ b: workLabel(phaseKind(n) || "check") }, ` ${crit}`] : [crit];
    if (box) return [{ b: workLabel(phaseKind(n) || "setup") }, r ? { cls: "alt-what", text: ` ${N.shortAll(r)}`, title: r } : ""];
    const parentKind = n.parent ? phaseKind(n.parent) : null, kind = phaseKind(n);
    if (!r) return [kind ? kind : full];                                    // D1045: "Check" alone under Design is "check"
    return [N.shortAll(!parentKind || !kind || parentKind === kind || /^gate\b/.test(r) ? r : `${kind} · ${r}`)];
  };
  // D1066: the checks a failed check never reached, rows of their own under it
  const skipRows = (n, depth) => { for (const t of notReached(n)) out.push({ type: "skip", key: `skip:${n.id}:${t}`, depth, text: `${t} · not run` }); };
  const line = (r, depth, named = null, box = false, extra = [], flat = false) => {
    const n = box ? ownView(r.node) : r.node, under = flat ? [] : kids.get(n.id) || [], finished = n.t1 != null;
    const collapsed = !!under.length && v.isFolded(n.id, finished, holds(n.id) && n.id !== selected);
    const kind = phaseKind(n), opens = aiOf.get(n.id) || n, label = named ? [].concat(named) : said(n, box), rejected = rejectedBy(n);
    const picked = n.id === selected || opens.id === selected || (merged.get(n.id) || []).some(c => c.id === selected);   // D1064: its merged command too
    const nums = STAGE.test(String(n.name)) ? numbersOf(n, goals) : "";
    out.push({ type: "row", key: `t:${n.id}`, depth, cls: `${box ? "box" : "step"}${secs(n) < .05 && !under.length ? " quiet" : ""}`, k: colour(n, kind),
      fold: under.length ? fold(n.id, collapsed, label.map(s => typeof s === "string" ? s : s.b || s.text || "").join("").trim() || n.name) : null,
      act: { task: opens }, btnCls: state(n), picked, title: n.name + (n.why ? " · " + n.why : ""),
      mark: rejected ? doneMark(false, true) : markOf(n, J),
      name: [...label, ...[].concat(extra), note(n) ? { cls: "muted small", text: ` · ${note(n)}` } : "",
        nums ? { cls: "alt-what", text: ` · ${nums}` } : "",
        n.failed && kind === "check" && (n.kids || []).length && firstFailing(n) ? { cls: "alt-what", text: ` · ${firstFailing(n)}` } : "",   // D1063
        collapsed && notReached(n).length ? { cls: "muted small", text: ` · ${notReached(n).join(", ")} not run` } : ""],   // D1065
      pills: [isCritique(n) ? { cls: "alt-critic-tag", text: "critic" } : null,                                              // D1068: a critic, then the ai
        AI.test(String(n.params?.turn || n.name)) || aiOf.has(n.id) ? aiPill(n, (aiOf.get(n.id) || n).name) : null].filter(Boolean),
      time: dur(secs(n)) });
    if (!collapsed) runs(under, depth + 1);      // D1038: its children grouped as the pass's are
    if (!collapsed && under.length) skipRows(n, depth + 1);
  };
  // D1044: the tries of a list, newest first, five at a time; D1059: a box's steps in the order they ran
  const newestFirst = (key, items, draw, depth, keep = () => false, inOrder = false) => {
    const all = v.allTries.has(key), order = [...items].reverse(), latest = all ? order : order.filter((x, i) => i < TRIES || keep(x));
    const hidden = order.length - latest.length;
    const more = order.length > TRIES ? { type: "more", key, depth,
      text: all || !hidden ? (inOrder ? "▴ latest five only" : "▾ newest five only") : `${inOrder ? "▴" : "▸"} ${hidden} earlier ${inOrder ? "step" : "tr"}${hidden === 1 ? (inOrder ? "" : "y") : (inOrder ? "s" : "ies")}` } : null;
    if (inOrder) { if (more) out.push(more); [...latest].reverse().forEach(draw); return; }
    latest.forEach(draw);
    if (more) out.push(more);
  };
  const deep = n => [n, ...(n.kids || []).flatMap(deep)];
  const holdsTry = ([t]) => [t.lead, ...t.steps].flatMap(deep).some(n => n.id === selected || n.t1 == null);
  const toggleRow = (key, depth, cls, k, nodes, name, pills, time, fld) => out.push({ type: "row", key: `g:${key}`, depth, cls, k, fold: fld,
    act: { toggle: key, collapsed: fld ? fld.collapsed : false }, btnCls: nodes.every(n => n.t1 != null) ? "" : "running",
    mark: nodes.every(n => n.t1 != null) ? doneMark(ended(nodes), partial(nodes)) : "spin", name, pills, time });
  // D1016: the same task again in a row -- one line, ×N, its tries under it
  const again = (run, depth, box = false) => {
    if (run.length < 2) return line(run[0], depth, null, box);
    const nodes = run.map(r => r.node), first = nodes[0], key = `again:${first.id}`, finished = nodes.every(n => n.t1 != null);
    const collapsed = v.isFolded(key, finished, nodes.some(n => holds(n.id)));
    const opensTries = phaseKind(first) === "design";             // D1045: a step repeated elsewhere is one line, ×N
    const ai = nodes.some(n => aiOf.has(n.id) || AI.test(String(n.params?.turn || n.name)));
    toggleRow(key, depth, box ? "box" : "step", COLORS[phaseKind(first)] || COLORS.setup, nodes, said(first, box),
      [{ cls: "alt-again", text: `×${run.length}`, title: `${run.length} tries in this pass` }, ai ? { cls: "alt-ai-pill", text: "ai" } : null].filter(Boolean),
      span(nodes), opensTries ? fold(key, collapsed, taskLabel(first)) : null);
    if (!collapsed && opensTries) newestFirst(key, run.map((r, i) => [r, i]), ([r, i]) => {
      const what = candidateOf(r.node) || note(r.node);
      line(r, depth + 1, [`try ${i + 1}`], false, what ? [{ cls: "alt-what", text: ` ${what}` }] : []);
    }, depth + 1);
  };
  // D1038: tries of two steps (a draft, then its check) again and again -- one line, ×N
  const pairs = (list, k) => {
    const a = taskLabel(list[k].node), b = list[k + 1] && taskLabel(list[k + 1].node);
    if (!b || a === b) return 0;
    let e = k;
    while (e + 1 < list.length && taskLabel(list[e].node) === a && taskLabel(list[e + 1].node) === b) e += 2;
    return (e - k) / 2;
  };
  const againPairs = (list, k, m, depth) => {
    const run = list.slice(k, k + 2 * m), nodes = run.map(r => r.node), first = nodes[0], key = `again:${first.id}`;
    const collapsed = v.isFolded(key, nodes.every(n => n.t1 != null), nodes.some(n => holds(n.id)));
    const last = [run[2 * m - 2].node, run[2 * m - 1].node];
    out.push({ type: "row", key: `g:${key}`, depth, cls: "", k: COLORS[phaseKind(first)] || COLORS.setup, fold: fold(key, collapsed, taskLabel(first)),
      act: { toggle: key, collapsed }, btnCls: nodes.every(n => n.t1 != null) ? "" : "running",
      mark: nodes.every(n => n.t1 != null) ? doneMark(ended(last), partial(nodes)) : "spin",
      name: [taskLabel(first), { cls: "muted small", text: ` · ${taskLabel(nodes[1]).replace(/^[^·]*·\s*/, "")}` }],
      pills: [{ cls: "alt-again", text: `×${m}`, title: `${m} tries in a row` }], time: span(nodes) });
    if (!collapsed) run.forEach((r, i) => line(r, depth + 1, i % 2 ? null : [`try ${i / 2 + 1}`]));
  };
  // D1041: one stretch of a box's work in a row is one line of that box, its tasks under it
  const stretch = (run, depth, box = false) => {
    const nodes = run.map(r => r.node), kind = phaseKind(nodes[0]), key = `stretch:${nodes[0].id}`;
    const collapsed = v.isFolded(key, nodes.every(n => n.t1 != null), nodes.some(n => holds(n.id)));
    toggleRow(key, depth, box ? "box" : "step", COLORS[kind] || COLORS.setup, nodes, [box ? { b: workLabel(kind) } : workLabel(kind)], [], span(nodes),
      fold(key, collapsed, workLabel(kind)));
    if (!collapsed) runs(run, depth + 1, true);
  };
  const runs = (list, depth, inStretch = false, box = false) => {
    for (let k = 0; k < list.length;) {
      const kind = phaseKind(list[k].node);
      let s = k + 1;
      while (box && !inStretch && STRETCH.includes(kind) && s < list.length && phaseKind(list[s].node) === kind) s++;
      if (s - k >= 2 && new Set(list.slice(k, s).map(r => taskLabel(r.node))).size > 1) { stretch(list.slice(k, s), depth, box); k = s; continue; }
      let e = k + 1;
      while (e < list.length && taskLabel(list[e].node) === taskLabel(list[k].node) && !(kids.get(list[k].node.id) || []).length === !(kids.get(list[e].node.id) || []).length) e++;
      const m = e === k + 1 ? pairs(list, k) : 0;
      if (m >= 2) { againPairs(list, k, m, depth); k += 2 * m; continue; }
      again(list.slice(k, e), depth, box); k = e;
    }
  };
  // D1045: a design's tries -- a draft and the checks after it (what ran before the first draft is the first try's)
  const triesIn = n => {
    const got = [], before = []; let cur = null;
    for (const r of kids.get(n.id) || []) {
      if (isDraft(r.node)) got.push(cur = { lead: r.node, steps: [] });
      else if (cur) cur.steps.push(r.node);
      else before.push(r.node);
    }
    if (got.length) got[0].steps.unshift(...before);
    return got.length >= 2 ? got : null;
  };
  const draftsIn = n => { const t = triesIn(n); if (t) return t; const k = (kids.get(n.id) || []).map(r => r.node), i = k.findIndex(isDraft);
    return i >= 0 ? [{ lead: k[i], steps: k.filter((_, j) => j !== i) }] : null; };   // one draft: every other step is its
  const made = t => madeOf(t, aiOf.get(t.lead.id) || t.lead);
  const tryLine = (t, i, depth, onlyLine = false) => {
    const nodes = [t.lead, ...t.steps], all = nodes.flatMap(deep).map(ownView), key = `try:${t.lead.id}`;   // D1094
    const running = all.some(n => n.t1 == null), failedStep = t.steps.find(n => n.failed || n.rejected || J.marked(n.id));
    const under = t.run ? (kids.get(t.lead.id) || []) : t.steps.map(n => ({ node: n, parent: null }));
    const collapsed = onlyLine || !under.length || v.isFolded(key, !running, all.some(n => holds(n.id)) && all[0]?.id !== selected);
    const opens = aiOf.get(t.lead.id) || t.lead, picked = all.some(n => n.id === selected) && (onlyLine || opens.id === selected || t.lead.id === selected);   // D1075
    const what = made(t);
    const fail = failedStep ? (firstFailing(failedStep) ? (onlyLine ? firstFailing(failedStep) : `${checkWord(failedStep)}: ${firstFailing(failedStep)}`) : `${checkWord(failedStep)} failed`) : "";   // D1063; D1064
    opens.tryNo = t.lead.tryNo = t.part ? null : i;
    opens.tryFailed = !running && failedStep && !opens.failed ? fail || "its check failed" : null;   // D1075: the panel says how the try ended
    const shortMade = N.short(what), asPart = t.part && shortMade.startsWith(t.part.split(" ")[0]);   // D1066: "lo#1", not "lo #1"
    out.push({ type: "row", key: `try:${t.lead.id}`, depth, cls: "step try", k: COLORS[phaseKind(t.lead) || "design"] || COLORS.design,
      fold: under.length && !onlyLine ? fold(key, collapsed, t.part || `try ${i}`) : null,
      act: { task: opens }, btnCls: state(opens), picked, title: t.lead.name,
      mark: running ? "spin" : !failedStep && (opens.timedOut || t.lead.timedOut) ? markOf(opens.timedOut ? opens : t.lead, J)
        : doneMark(!!failedStep || !!t.lead.failed, all.some(n => J.marked(n.id))),
      name: [asPart ? shortMade : t.part || `try ${i}`, what && !asPart ? { cls: "alt-what", text: ` ${shortMade}`, title: what } : "", fail ? { cls: "alt-what", text: ` · ${fail}` } : ""],
      pills: aiOf.has(t.lead.id) || AI.test(String(t.lead.params?.turn || t.lead.name)) ? [{ cls: "alt-ai-pill", text: "ai" }] : [],
      time: span(all) });
    if (!collapsed) runs(under, depth + 1);
  };
  const boxLine = (nodes, depth) => {
    const first = nodes[0], last = nodes.at(-1), kind = phaseKind(first) || "setup", key = `box:${first.id}`;
    // D1045: only a design has tries -- its drafts, each with the checks after it, numbered across its runs
    let tries = kind === "design" ? nodes.flatMap(n => draftsIn(n) || [{ lead: n, steps: [], run: true }]) : null;
    if (tries && tries.length < 2) tries = null;
    // D1061: its own work only; D1094: a design handed on to its check is done there
    const flatK = n => [n, ...(kids.get(n.id) || []).flatMap(r => flatK(r.node))];
    const all = nodes.flatMap(flatK).filter(n => kind !== "design" || !["check", "measure"].includes(boxKind(n))).map(ownView), running = all.some(n => n.t1 == null);
    // D1050: a design of one draft is that draft -- the box opens its conversation, its checks under it
    const lone = !tries && kind === "design" && nodes.length === 1 ? draftsIn(first) : null;
    const sole = nodes.length === 1 && !lone ? soleOf(first) : null;
    const steps = tries ? [] : sole ? (kids.get(first.id) || []).filter(r => r.node !== sole) : lone ? lone[0].steps.map(n => ({ node: n, parent: null }))
      : nodes.length > 1 ? nodes.map(n => ({ node: n, parent: null })) : (kids.get(first.id) || []);
    const collapsed = !(tries || steps.length) || v.isFolded(key, !running, all.some(n => holds(n.id)) && !nodes.some(n => n.id === selected));
    const opens = lone ? (aiOf.get(lone[0].lead.id) || lone[0].lead) : sole || (nodes.length === 1 ? (aiOf.get(first.id) || first) : last);
    const picked = nodes.length === 1 && (first.id === selected || opens.id === selected || (merged.get(first.id) || []).some(c => c.id === selected));   // D1065
    // D1055; D1063: the newest design's latest stage -- not the evaluation's, which measures the composition again
    const stages = kind === "measure" ? all.filter(n => STAGE.test(String(n.name)) && numbersOf(n, goals)) : [];
    const newest = candidateOf(stages.filter(n => !/^\s*evaluation\b/i.test(String(n.parent?.name || ""))).at(-1) || stages.at(-1) || {});
    const staged = (newest && stages.filter(n => candidateOf(n) === newest).at(-1)) || stages.at(-1) || null;
    const planned = kind === "plan" ? String(last.output?.picked ?? last.output?.next ?? "") : "";
    const lastTry = kind === "design" ? (tries || lone || draftsIn(last) || []).at(-1) : null, made1 = lastTry ? made(lastTry) : "";   // D1061
    const failing = kind === "check" ? [firstFailing(last), notReached(last).length ? `${notReached(last).join(", ")} not run` : ""].filter(Boolean).join(" · ") : "";   // D1061; D1065
    const decided = kind === "choose" ? all.map(n => n.output?.decision).filter(Boolean).at(-1) : "";   // D1063
    const picked1 = decided ? (ended(nodes) ? `${N.short(decided)} rejected` : `★ ${N.short(decided)}`) : "";
    const what = staged ? `${String(staged.name).split(":")[1].trim()}${candidateOf(staged) ? ` ${N.short(candidateOf(staged))}` : ""} · ${numbersOf(staged, goals)}`
      : planned ? `picked ${planned}` : kind === "plan" ? "" : picked1 || [made1 || rest(last), failing].filter(Boolean).join(" · ");
    const ai = all.some(n => AI.test(String(n.params?.turn || n.name)) || aiOf.has(n.id)), crit = all.some(isCritique), rejected = all.some(rejectedBy);
    out.push({ type: "row", key: `box:${first.id}`, depth, cls: "box", k: COLORS[kind] || COLORS.setup,
      fold: tries || steps.length ? fold(key, collapsed, workLabel(kind)) : null,
      act: { task: opens }, btnCls: state(lone ? opens : nodes.length === 1 ? first : last), picked, title: lone ? opens.name : nodes.map(n => n.name).join(" · "),
      mark: running ? "spin" : lone && (opens.interrupted || opens.timedOut) ? markOf(opens, J)
        // D1065: a rejected result is "!" -- its critique the ✗; a box of only its critique's failure is ✗
        : doneMark(ended(nodes.filter(n => !isCritique(n))) || (rejected && nodes.every(isCritique)), all.some(n => J.hardFail(n) || n.rejected || aiOf.get(n.id)?.failed)),
      name: [{ b: workLabel(kind) }, what ? { cls: "alt-what", text: ` ${N.shortAll(what)}`, title: what } : "", nodes.length === 1 && note(first) ? { cls: "alt-what", text: ` · ${note(first)}` } : ""],
      pills: [tries && tries.length > 1 ? { cls: "alt-again", text: `×${tries.length}` } : null,      // D1067: how many, a critic, the ai
        crit ? { cls: "alt-critic-tag", text: "critic" } : null, ai ? { cls: "alt-ai-pill", text: "ai" } : null].filter(Boolean),
      time: span(all) });
    if (collapsed) return;
    if (tries) newestFirst(key, tries.map((t, i) => [t, i]), ([t, i]) => tryLine(t, i + 1, depth + 1), depth + 1, holdsTry);
    else { runs(steps, depth + 1); if (nodes.length === 1) skipRows(first, depth + 1); }
  };
  // D1054: a check that passed inside a design is the Check box's; one that failed stays the try's
  const passedIn = n => (kids.get(n.id) || []).filter(r => phaseKind(r.node) === "check" && boxKind(r.node) === "check").map(r => r.node);
  const boxes = (list, depth) => {
    // D1054: a search that holds the work it proposed (a sweep's point) is the Search line alone, that work the boxes after it
    list = list.flatMap(r => {
      if (phaseKind(r.node) !== "search") return [r];
      const own = kids.get(r.node.id) || [], work = own.filter(c => ["design", "check", "measure"].includes(phaseKind(c.node)));
      if (!work.length) return [r];
      kids.set(r.node.id, own.filter(c => !work.includes(c)));
      return [r, ...work];
    });
    for (let k = 0; k < list.length;) {
      const kind = phaseKind(list[k].node) || "setup";
      let e = k + 1;
      while (e < list.length && (phaseKind(list[e].node) || "setup") === kind) e++;
      const nodes = list.slice(k, e).map(r => r.node);
      k = e;
      // D1059; D1063: the evaluation's own children, not what the tree drew under it
      if (kind === "measure") { boxLine(nodes.flatMap(n => /^\s*evaluation\s*$/i.test(String(n.name)) && (n.kids || []).some(c => ids.has(c.id)) ? n.kids.filter(c => ids.has(c.id)) : [n]), depth); continue; }
      if (kind !== "design") { boxLine(nodes, depth); continue; }
      const moved = nodes.flatMap(passedIn);
      for (const c of moved) kids.set(c.parent.id, kids.get(c.parent.id).filter(r => r.node !== c));
      boxLine(nodes, depth);
      if (!moved.length) continue;
      let f = k;                                           // the Check box after it holds them first
      while (f < list.length && phaseKind(list[f].node) === "check") f++;
      boxLine([...moved, ...list.slice(k, f).map(r => r.node)], depth);
      k = f;
    }
  };
  return {
    out,
    /** The tree: per pass (with All passes) its heading, its Setup line, its boxes, the boxes still to come. */
    tree() {
      const top = rows.filter(r => r.parent == null), groups = [];
      for (const r of top) { const p = v.membership.get(r.node.id) ?? null; let g = groups.at(-1); if (!g || g.p !== p) groups.push(g = { p, rows: [] }); g.rows.push(r); }
      const inPass = p => v.scopeRows.filter(r => (v.membership.get(r.node.id) ?? null) === p);
      for (const g of groups) {
        let depth = 0;
        if (v.pass === "all") {
          const key = `pass:${g.p ?? "setup"}`, nodes = g.rows.map(r => r.node), finished = nodes.every(n => n.t1 != null);
          const collapsed = v.isFolded(key, finished, nodes.some(n => holds(n.id))), st = J.passState(g.p, nodes, inPass(g.p), v.passNotes(g.p));
          out.push({ type: "row", key, depth: 0, cls: "alt-pass-row", fold: fold(key, collapsed, passName(g.p)), act: { pass: g.p }, btnCls: "alt-pass-heading",
            picked: v.passView === g.p, mark: st === "running" ? "spin" : doneMark(st === "failed", st === "part"), name: [passName(g.p)], pills: [], time: span(nodes) });
          if (collapsed) continue;
          depth = 1;
        }
        // D1012: every setup task of the pass in one Setup line; D1094: one step is a Setup line too
        const lead = g.p != null ? g.rows.filter(r => phaseKind(r.node) === "setup") : [], others = g.rows.filter(r => !lead.includes(r));
        if (lead.length > 1) {
          const key = `setup:${g.p}`, nodes = lead.map(r => r.node);
          const collapsed = v.isFolded(key, nodes.every(n => n.t1 != null), nodes.some(n => holds(n.id))) || (!v.opened.has(key) && !nodes.some(n => holds(n.id)));
          out.push({ type: "row", key: `g:${key}`, depth, cls: "quiet", k: COLORS.setup, fold: fold(key, collapsed, "Setup"), act: { toggle: key, collapsed }, btnCls: "alt-setup-steps",
            mark: nodes.every(n => n.t1 != null) ? doneMark(nodes.some(n => n.failed), nodes.some(n => n.rejected || J.marked(n.id))) : "spin",
            name: [`Setup · ${lead.length} steps`], pills: [], time: dur(nodes.reduce((t, n) => t + secs(n), 0)) });
          if (!collapsed) runs(lead, depth + 1);
        } else lead.forEach(r => { const l = taskLabel(r.node), at = l.indexOf(" · "); line(r, depth, [`Setup · ${at < 0 ? l : l.slice(at + 3)}`]); });
        // D1054: a search done as a note (D1052) is the pass's Search line
        const searched = g.p != null && !others.some(r => phaseKind(r.node) === "search") ? v.passNotes(g.p).filter(n => kindOf(n.name, n.why) === "search") : [];
        if (searched.length) out.push({ type: "row", key: `note:${g.p}`, depth, cls: "box", k: COLORS.search, fold: null, act: { box: { p: g.p, key: "search" } },
          btnCls: "", mark: doneMark(false, false), name: [{ b: "Search" }, { cls: "alt-what", text: ` ${searched.map(n => v.noteShort(n, g.p)).join(", ")}` }], pills: [], noTime: true });
        boxes(others, depth);
        // D1044: in a pass at work, the boxes still to come, dimmed; D1059: the loop's last pass between two tasks is at work too
        if (g.rows.some(r => r.node.t1 == null) || (g === groups.at(-1) && g.p != null && [...v.model.nodes.values()].some(n => n.t1 == null)))
          for (const b of flowBoxes(inPass(g.p), g.p, true, v.passNotes(g.p)).filter(x => x.state === "wait" && !x.runs.length))
            out.push({ type: "wait", key: `wait:${g.p}:${b.key}`, depth, k: COLORS[b.kind] || COLORS.setup, title: b.title });
      }
      return out;
    },
    /** D1044: a graph box's list -- its tries, one flat line each, newest first, five at a time, when it ran
        one task again and again; else each of its runs; a single run, its steps. */
    boxList(runsOf, key, kind) {
      const own = n => {   // D1065; D1066: "critique · #3" -- its mark says the verdict, the narrow line keeps the design
        if (isCritique(n)) { const of = String(n.why || "").replace(/@\S*$/, "").trim(); return of && !/^round\b/i.test(of) ? `critique · ${N.short(of)}` : "critique"; }
        const full = taskLabel(n), at = full.indexOf(" · "), r = at >= 0 && phaseKind(n) === kind ? full.slice(at + 3) : full;
        const c = candidateOf(n), named = /^\s*test\s*:\s*(?:fast )?check\b/i.test(String(n.name)) ? `check · ${r}` : r;      // D1054
        return N.shortAll(kind === "check" && c && !named.includes(c) && !named.includes(N.short(c)) ? `${named} · ${c}` : named);   // D1059
      };
      if (kind === "design") {                    // D1045: the same tries as the tree; D1059; D1061: runs of other names are parts
        const parts = new Set(runsOf.map(rest)).size > 1;
        const tries = runsOf.flatMap(n => { const t = draftsIn(n) || (isDraft(n) ? [{ lead: n, steps: [], run: true }] : []);
          return t.map((x, k) => ({ ...x, part: parts ? N.shortAll(rest(n)) + (t.length > 1 ? ` · try ${k + 1}` : "") : "" })); });
        if (tries.length > 1) newestFirst(key, tries.map((t, i) => [t, i]), ([t, i]) => tryLine(t, i + 1, 0, true), 0, holdsTry, parts);
        return out;                               // one draft: the box opens its conversation
      }
      const one = runsOf.length === 1 ? (kids.get(runsOf[0].id) || []).map(r => r.node) : [];
      const items = one.length ? one : runsOf;
      const dots = n => (n.kids || []).length > 1 ? [{ dots: n.kids.map(c => c.failed ? "bad" : c.t1 == null ? "run" : "") }] : [];
      newestFirst(key, items.map((n, i) => [n, i]), ([n]) => {
        if (kind === "plan") line({ node: n, parent: null }, 0, [n.output?.picked != null ? `picked ${n.output.picked}` : own(n)], false, [], true);   // D1059
        else line({ node: n, parent: null }, 0, [own(n)], false, kind === "check" ? dots(n) : [], true);
      }, 0, () => false, true);
      if (kind === "check" && one.length) skipRows(runsOf[0], 0);   // D1089
      return out;
    },
  };
}

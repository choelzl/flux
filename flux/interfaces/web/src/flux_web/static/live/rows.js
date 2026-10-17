// Live (D1095): the tree's lines as data -- what each line says and does, drawn by Tree.js for the tree
// and for a graph box's list alike. One line per box of a pass's work, its tries or steps under it, the
// rules of what folds, merges and repeats as the tree has always drawn them (D980, D1012, D1016, D1038,
// D1044, D1045, D1050, D1054, D1059, D1061...). No page: the lines are plain objects.
//
// A line: { type: "row", key, depth, cls, k (its colour), fold: {key, collapsed, label} | null,
//   act: {task} (opens a task) | {toggle} (opens or folds) | {pass} | {box: {p, key}}, picked, title,
//   mark: "spin" | {tone, title}, name: [segment], pills: [{text, title, k (a kind's colour) or count}] -- tags, time }
// a segment: a string, {b: text}, {cls, text, title}, or {dots: [cls]}.
// Other lines: {type: "more", key, depth, text} (more tries), {type: "skip", key, depth, text} (not run),
// {type: "wait", key, depth, k, title, said} (a box still to come: "waiting", or "skipped" when the pass went past it).

import { dur } from "../format.js";
import { WORK_COLORS as COLORS, AGENT_COLOR, workLabel } from "./work_style.js";
import { boxOf, designOf, isAI, isTurn, noteBox, notReachedOf, stepOf, triesOf } from "./facts.js";
import { boxKind, flowBoxes, ownView, passName, taskLabel, taskNote, waitWord } from "./views.js";
import { byPass, taskState as state } from "./model.js";
import { STAGE, checkWord, ended, firstFailing, isCritique, numbersOf, rejectedBy, rest, soleOf } from "./rules.js";

const TRIES = 5;
const STRETCH = ["search", "design", "check", "measure"];
const doneMark = (failed, partial) => ({ tone: failed ? "bad" : partial ? "warn" : "ok", title: partial && !failed ? "a step under it failed" : null });
const markOf = (n, J) => n.t1 == null ? "spin"
  : n.interrupted ? { tone: "warn", title: "interrupted" }
  : n.timedOut ? { tone: "warn", title: "time limit" }
  : n.rejected ? { tone: "warn", title: "rejected by the critic (guidance, not a failure)" }
  : doneMark(n.failed, J.marked(n.id));

/** The lines of `rows` (tasks with their parent's id when it is among them). `v`: what the view knows --
    selected, passShown (a pass in the inspector: its row the picked one), model, membership, J (rules.js judge), N (namer), goals, isFolded, opened, allTries,
    pass, passView, passNotes, noteShort, scopeRows (every task of the scope, for the boxes still to come). */
export function lines(rows, v) {
  const { selected, J, N, goals } = v, out = [], notReached = n => notReachedOf(n, v.tests);
  const ids = new Set(rows.map(r => r.node.id)), kids = new Map();
  for (const r of rows) if (r.parent != null && ids.has(r.parent)) (kids.get(r.parent) || kids.set(r.parent, []).get(r.parent)).push(r);
  // D1044: a run that is only an agent's or a model's turn is its parent's line -- the ai pill on it
  const aiOf = new Map();
  for (const r of rows) {
    const only = kids.get(r.node.id);
    if (only && only.length === 1 && isTurn(only[0].node) && !(kids.get(only[0].node.id) || []).length) { aiOf.set(r.node.id, only[0].node); kids.delete(r.node.id); }
  }
  // D1037: a command says its program beside the check or stage it runs
  const note = n => { const x = taskNote(n); return x && !taskLabel(n).endsWith(` · ${x}`) ? x : ""; };
  const sel = v.model.nodes.get(selected), holds = id => { for (let p = sel; p; p = p.parent) if (p.id === id) return true; return false; };
  const now = Date.now() / 1000, secs = n => n.seconds ?? (n.t1 ?? now) - n.t0;
  const span = nodes => dur(Math.max(...nodes.map(n => n.t1 ?? now)) - Math.min(...nodes.map(n => n.t0)));
  const fold = (key, collapsed, label) => ({ key, collapsed, label });
  const colour = (n, kind = boxOf(n)) => COLORS[kind] || (isAI(n) ? AGENT_COLOR : COLORS.setup);
  const aiPill = (n, title) => ({ k: AGENT_COLOR, text: "AI", title });
  const partial = nodes => nodes.some(n => J.hardFail(n) || n.rejected || J.marked(n.id));
  // D1044: a box's line says the box, then what it worked on; a step under it drops the box's name
  const said = (n, box) => {
    const full = taskLabel(n), at = full.indexOf(" · "), r = at < 0 ? "" : full.slice(at + 3);
    // D1064: a critique says whose and what it said ("critique · #3 · rejected")
    const verdict = String(n.output?.verdict ?? n.fields?.verdict ?? ""), of = String(n.why || "").replace(/@\S*$/, "").trim();
    const crit = ["critique", of && !/^round\b/i.test(of) ? N.short(of) : "", n.rejected ? "rejected" : verdict ? "no objection" : ""].filter(Boolean).join(" · ");
    if (isCritique(n)) return box ? [{ b: workLabel(boxOf(n) || "check") }, ` ${crit}`] : [crit];
    if (box) return [{ b: workLabel(boxOf(n) || "setup") }, r ? { cls: "alt-what", text: ` ${N.shortAll(r)}`, title: r } : ""];
    const parentKind = n.parent ? boxOf(n.parent) : null, kind = boxOf(n);
    if (!r) return [kind ? kind : full];                                    // D1045: "Check" alone under Design is "check"
    return [N.shortAll(!parentKind || !kind || parentKind === kind || /^gate\b/.test(r) ? r : `${kind} · ${r}`)];
  };
  // D1066: the checks a failed check never reached, rows of their own under it
  const skipRows = (n, depth) => { for (const t of notReached(n)) out.push({ type: "skip", key: `skip:${n.id}:${t}`, depth, text: `${t} · not run` }); };
  const line = (r, depth, named = null, box = false, extra = [], flat = false) => {
    const n = box ? ownView(r.node) : r.node, under = flat ? [] : kids.get(n.id) || [], finished = n.t1 != null;
    const collapsed = !!under.length && v.isFolded(n.id, finished, holds(n.id) && n.id !== selected);
    const kind = boxOf(n), opens = aiOf.get(n.id) || n, label = named ? [].concat(named) : said(n, box), rejected = rejectedBy(n);
    const picked = !v.passShown && (n.id === selected || opens.id === selected);
    const nums = STAGE.test(String(n.name)) ? numbersOf(n, goals) : "";
    out.push({ type: "row", key: `t:${n.id}`, depth, cls: `${box ? "box" : "step"}${secs(n) < .05 && !under.length ? " quiet" : ""}`, k: colour(n, kind),
      fold: under.length ? fold(n.id, collapsed, label.map(s => typeof s === "string" ? s : s.b || s.text || "").join("").trim() || n.name) : null,
      act: { task: opens }, btnCls: state(n), picked, title: n.name + (n.why ? " · " + n.why : ""),
      mark: rejected ? doneMark(false, true) : markOf(n, J),
      name: [...label, ...[].concat(extra), note(n) ? { cls: "muted small", text: ` · ${note(n)}` } : "",
        nums ? { cls: "alt-what", text: ` · ${nums}` } : "",
        n.failed && kind === "check" && firstFailing(n) ? { cls: "alt-what", text: ` · ${firstFailing(n)}` } : "",   // D1063
        collapsed && notReached(n).length ? { cls: "muted small", text: ` · ${notReached(n).join(", ")} not run` } : ""],   // D1065
      pills: [isCritique(n) ? { text: "critic" } : null,                                              // D1068: a critic, then the ai
        isAI(n) || aiOf.has(n.id) ? aiPill(n, (aiOf.get(n.id) || n).name) : null].filter(Boolean),
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
  const holdsTry = ([t]) => t.members.flatMap(deep).some(n => n.id === selected || n.t1 == null);
  const toggleRow = (key, depth, cls, k, nodes, name, pills, time, fld) => out.push({ type: "row", key: `g:${key}`, depth, cls, k, fold: fld,
    act: { toggle: key, collapsed: fld ? fld.collapsed : false }, btnCls: nodes.every(n => n.t1 != null) ? "" : "running",
    mark: nodes.every(n => n.t1 != null) ? doneMark(ended(nodes), partial(nodes)) : "spin", name, pills, time });
  // D1016: the same task again in a row -- one line, ×N, its runs under it ("turn N" for an agent's turns)
  const again = (run, depth, box = false) => {
    if (run.length < 2) return line(run[0], depth, null, box);
    const nodes = run.map(r => r.node), first = nodes[0], key = `again:${first.id}`, finished = nodes.every(n => n.t1 != null);
    const collapsed = v.isFolded(key, finished, nodes.some(n => holds(n.id)));
    const opensTries = boxOf(first) === "design";             // D1045: a step repeated elsewhere is one line, ×N
    const ai = nodes.some(n => aiOf.has(n.id) || isAI(n));
    // D1181: an agent's turns again and again inside a try are its turns, not tries of their own
    const word = nodes.every(isTurn) ? "turn" : "try";
    toggleRow(key, depth, box ? "box" : "step", COLORS[boxOf(first)] || COLORS.setup, nodes, said(first, box),
      [{ count: true, text: `×${run.length}`, title: `${run.length} ${word === "try" ? "tries" : "turns"} in this pass` }, ai ? { k: AGENT_COLOR, text: "AI" } : null].filter(Boolean),
      span(nodes), opensTries ? fold(key, collapsed, taskLabel(first)) : null);
    if (!collapsed && opensTries) newestFirst(key, run.map((r, i) => [r, i]), ([r, i]) => {
      const what = designOf(r.node) || note(r.node);
      line(r, depth + 1, [`${word} ${i + 1}`], false, what ? [{ cls: "alt-what", text: ` ${what}` }] : []);
    }, depth + 1);
  };
  // D1041: one stretch of a box's work in a row is one line of that box, its tasks under it
  const stretch = (run, depth, box = false) => {
    const nodes = run.map(r => r.node), kind = boxOf(nodes[0]), key = `stretch:${nodes[0].id}`;
    const collapsed = v.isFolded(key, nodes.every(n => n.t1 != null), nodes.some(n => holds(n.id)));
    toggleRow(key, depth, box ? "box" : "step", COLORS[kind] || COLORS.setup, nodes, [box ? { b: workLabel(kind) } : workLabel(kind)], [], span(nodes),
      fold(key, collapsed, workLabel(kind)));
    if (!collapsed) runs(run, depth + 1, true);
  };
  const runs = (list, depth, inStretch = false, box = false) => {
    for (let k = 0; k < list.length;) {
      const kind = boxOf(list[k].node);
      let s = k + 1;
      while (box && !inStretch && STRETCH.includes(kind) && s < list.length && boxOf(list[s].node) === kind) s++;
      if (s - k >= 2 && new Set(list.slice(k, s).map(r => taskLabel(r.node))).size > 1) { stretch(list.slice(k, s), depth, box); k = s; continue; }
      let e = k + 1;
      while (e < list.length && taskLabel(list[e].node) === taskLabel(list[k].node) && !(kids.get(list[k].node.id) || []).length === !(kids.get(list[e].node.id) || []).length) e++;
      again(list.slice(k, e), depth, box); k = e;
    }
  };
  // D1233: a design run's tries are its tasks by the try they say; a passed try's checks are the Check box's (D1054)
  const moved = new Set();
  // a run that says no try of its own (it drafted nothing: a composition, a transpiled prototype) is one, as it ran
  const asTry = n => [{ lead: n, steps: [], members: [n], run: true, note: n.t1 == null ? null : { outcome: n.failed ? "no design" : "passed", why: String(n.output?.error || "").split("\n")[0], design: "" } }];
  const triesOfRun = n => { const t = triesOf(n, v.model); return (t.length ? t : asTry(n)).map(x => ({ ...x, steps: x.steps.filter(s => !moved.has(s.id) && ids.has(s.id)) }))
    .filter(x => ids.has(x.lead.id)); };
  const passed = t => ["passed", "drafted"].includes(t.note?.outcome);
  const madeBy = t => t.note?.design || t.members.map(designOf).find(Boolean) || "";
  /** A try's line: how it came out (the engine's word), what it made, its steps under it. */
  const tryLine = (t, i, depth, onlyLine = false) => {
    const nodes = [t.lead, ...t.steps], all = nodes.flatMap(deep).map(ownView), key = `try:${t.lead.id}`;   // D1094
    const running = all.some(n => n.t1 == null) || !t.note, o = t.note?.outcome || "";
    const under = t.run ? (kids.get(t.lead.id) || []) : [...(kids.get(t.lead.id) || []), ...t.steps.map(n => ({ node: n, parent: null }))];
    const collapsed = onlyLine || !under.length || v.isFolded(key, !running, all.some(n => holds(n.id)) && all[0]?.id !== selected);
    const opens = aiOf.get(t.lead.id) || t.lead, picked = !v.passShown && all.some(n => n.id === selected) && (onlyLine || opens.id === selected || t.lead.id === selected);   // D1075
    const what = madeBy(t), bad = t.steps.find(n => boxOf(n) === "check" && n.failed);
    const fail = running ? "" : o === "check failed" ? (bad ? (onlyLine ? firstFailing(bad) || t.note.why : `${checkWord(bad)}: ${firstFailing(bad) || "failed"}`) : t.note.why || "its check failed")
      : o === "not built" ? "not built" : o === "no design" ? t.note.why || "no design" : "";
    const shortMade = N.short(what), asPart = t.part && shortMade.startsWith(t.part.split(" ")[0]);   // D1066: "lo#1", not "lo #1"
    out.push({ type: "row", key: `try:${t.lead.id}`, depth, cls: "step try", k: COLORS.design,
      fold: under.length && !onlyLine ? fold(key, collapsed, t.part || `try ${i}`) : null,
      act: { task: opens }, btnCls: state(opens), picked, title: [t.lead.name, t.note?.why].filter(Boolean).join(" · "),
      mark: running ? "spin" : o === "not built" ? { tone: "warn", title: `refused before it was built${t.note.why ? `: ${t.note.why}` : ""}` }
        : o === "out of time" ? { tone: "warn", title: "time limit" }
        : doneMark(["check failed", "no design"].includes(o), all.some(n => J.marked(n.id))),
      name: [asPart ? shortMade : t.part || `try ${i}`, what && !asPart ? { cls: "alt-what", text: ` ${shortMade}`, title: what } : "", fail ? { cls: "alt-what", text: ` · ${fail}` } : ""],
      pills: aiOf.has(t.lead.id) || t.members.some(isAI) ? [{ k: AGENT_COLOR, text: "AI" }] : [],
      time: span(all) });
    if (!collapsed) runs(under, depth + 1);
  };
  const boxLine = (nodes, depth) => {
    const first = nodes[0], last = nodes.at(-1), kind = boxOf(first) || "setup", key = `box:${first.id}`;
    let tries = kind === "design" ? nodes.flatMap(triesOfRun) : null;
    const lone = tries && tries.length === 1 && nodes.length === 1 ? tries[0] : null;   // D1050: a design of one try is that try
    if (tries && tries.length < 2) tries = null;
    // D1061: its own work only; D1094: a design handed on to its check is done there
    const flatK = n => [n, ...(kids.get(n.id) || []).flatMap(r => flatK(r.node))];
    const all = nodes.flatMap(flatK).filter(n => kind !== "design" || !["check", "measure"].includes(boxKind(n))).map(ownView), running = all.some(n => n.t1 == null);
    const sole = nodes.length === 1 && !lone ? soleOf(first) : null;
    const steps = tries ? [] : sole ? (kids.get(first.id) || []).filter(r => r.node !== sole)
      : lone ? (lone.run ? kids.get(first.id) || [] : [...(kids.get(lone.lead.id) || []), ...lone.steps.map(n => ({ node: n, parent: null }))])
      : nodes.length > 1 ? nodes.map(n => ({ node: n, parent: null })) : (kids.get(first.id) || []);
    const collapsed = !(tries || steps.length) || v.isFolded(key, !running, all.some(n => holds(n.id)) && !nodes.some(n => n.id === selected));
    const opens = lone ? (aiOf.get(lone.lead.id) || lone.lead) : sole || (nodes.length === 1 ? (aiOf.get(first.id) || first) : last);
    const picked = !v.passShown && nodes.length === 1 && (first.id === selected || opens.id === selected);   // D1065
    // D1055; D1063: the newest design's latest stage
    const stages = kind === "measure" ? all.filter(n => STAGE.test(String(n.name)) && numbersOf(n, goals)) : [];
    const newest = designOf(stages.at(-1) || {});
    const staged = (newest && stages.filter(n => designOf(n) === newest).at(-1)) || stages.at(-1) || null;
    const planned = kind === "plan" ? String(last.output?.picked ?? last.output?.next ?? "") : "";
    const lastTry = kind === "design" ? (tries || (lone ? [lone] : null) || triesOfRun(last)).at(-1) : null, made1 = lastTry ? madeBy(lastTry) : "";   // D1061
    const failing = kind === "check" ? [firstFailing(last), notReached(last).length ? `${notReached(last).join(", ")} not run` : ""].filter(Boolean).join(" · ") : "";   // D1061; D1065
    const decided = kind === "choose" ? all.map(n => n.output?.decision).filter(Boolean).at(-1) : "";   // D1063
    const picked1 = decided ? (ended(nodes) ? `${N.short(decided)} rejected` : `${N.short(decided)} ★`) : "";
    const what = staged ? `${stepOf(staged) || String(staged.name).split(":")[1].trim()}${designOf(staged) ? ` ${N.short(designOf(staged))}` : ""} · ${numbersOf(staged, goals)}`
      : planned ? `picked ${planned}` : kind === "plan" ? "" : picked1 || [made1 || rest(last), failing].filter(Boolean).join(" · ");
    const ai = all.some(n => isAI(n) || aiOf.has(n.id)), crit = all.some(isCritique), rejected = all.some(rejectedBy);
    const loneOut = lone && !lone.run ? lone.note?.outcome : "";
    out.push({ type: "row", key: `box:${first.id}`, depth, cls: "box", k: COLORS[kind] || COLORS.setup,
      fold: tries || steps.length ? fold(key, collapsed, workLabel(kind)) : null,
      act: { task: opens }, btnCls: state(lone ? opens : nodes.length === 1 ? first : last), picked, title: lone ? opens.name : nodes.map(n => n.name).join(" · "),
      mark: running ? "spin" : lone && (opens.interrupted || opens.timedOut) ? markOf(opens, J)
        // D1065: a rejected result is "!" -- its critique the ✗; a box of only its critique's failure is ✗
        : doneMark(ended(nodes.filter(n => !isCritique(n))) || (rejected && nodes.every(isCritique)) || ["check failed", "no design"].includes(loneOut),
          all.some(n => J.hardFail(n) || n.rejected || aiOf.get(n.id)?.failed) || loneOut === "not built"),
      name: [{ b: workLabel(kind) }, what ? { cls: "alt-what", text: ` ${N.shortAll(what)}`, title: what } : "",
        nodes.length === 1 && note(first) && N.shortAll(note(first)) !== N.shortAll(what) ? { cls: "alt-what", text: ` · ${note(first)}` } : ""],   // D1181: not the design twice
      pills: [tries && tries.length > 1 ? { count: true, text: `×${tries.length}` } : null,      // D1067: how many, a critic, the ai
        crit ? { text: "critic" } : null, ai ? { k: AGENT_COLOR, text: "AI" } : null].filter(Boolean),
      time: span(all) });
    if (collapsed) return;
    if (tries) newestFirst(key, tries.map((t, i) => [t, t.n ?? i + 1]), ([t, i]) => tryLine(t, i, depth + 1), depth + 1, holdsTry);
    else { runs(steps, depth + 1); if (nodes.length === 1) skipRows(first, depth + 1); }
  };
  // the lines of a pass's work, box by box; `segs` where each box's lines begin (the boxes still to come go between)
  const segs = [];
  const boxes = (list, depth) => {
    // D1054: a search that holds the work it proposed (a sweep's point) is the Search line alone, that work the boxes after it
    list = list.flatMap(r => {
      if (boxOf(r.node) !== "search") return [r];
      const own = kids.get(r.node.id) || [], work = own.filter(c => ["design", "check", "measure"].includes(boxOf(c.node)));
      if (!work.length) return [r];
      kids.set(r.node.id, own.filter(c => !work.includes(c)));
      return [r, ...work];
    });
    for (let k = 0; k < list.length;) {
      const kind = boxOf(list[k].node) || "setup";
      let e = k + 1;
      while (e < list.length && (boxOf(list[e].node) || "setup") === kind) e++;
      const nodes = list.slice(k, e).map(r => r.node);
      k = e;
      segs.push([out.length, kind]);
      if (kind !== "design") { boxLine(nodes, depth); continue; }
      // D1054: a check that passed in a design's last try is the Check box's -- the Check line after it holds it first
      const checked = nodes.flatMap(triesOfRun).filter(passed).flatMap(t => [...(t.run ? [] : t.lead.kids || []), ...t.steps]
        .filter(n => boxKind(n) === "check" && !n.failed && ids.has(n.id)));
      for (const c of checked) moved.add(c.id);
      boxLine(nodes, depth);
      if (!checked.length) continue;
      let f = k;
      while (f < list.length && boxOf(list[f].node) === "check") f++;
      segs.push([out.length, "check"]);
      boxLine([...checked, ...list.slice(k, f).map(r => r.node)], depth);
      k = f;
    }
  };
  return {
    out,
    /** The tree: per pass (with All passes) its heading, its Setup line, its boxes, the boxes still to come. */
    tree() {
      // D1181: one heading per pass, in pass order -- passes run side by side interleave their tasks in time
      const top = rows.filter(r => r.parent == null), byP = new Map();
      for (const r of top) { const p = v.membership.get(r.node.id) ?? null; (byP.get(p) || byP.set(p, { p, rows: [] }).get(p)).rows.push(r); }
      // D1235: the pass at work that has no task yet is a row of its own, "starting" (live-32)
      const starting = v.headerPass != null && !byP.has(v.headerPass) && !v.scope?.current?.some(p => p !== v.headerPass)
        && (v.pass === "all" || v.pass === "current" || Number(v.pass) === v.headerPass);
      if (starting) byP.set(v.headerPass, { p: v.headerPass, rows: [], starting: true });
      const groups = [...byP.values()].sort((x, y) => byPass(x.p, y.p));
      const inPass = p => v.scopeRows.filter(r => (v.membership.get(r.node.id) ?? null) === p);
      for (const g of groups) {
        let depth = 0;
        if (v.pass === "all" || g.starting) {
          const key = `pass:${g.p ?? "setup"}`, nodes = g.rows.map(r => r.node), finished = nodes.every(n => n.t1 != null) && !g.starting;
          const collapsed = v.isFolded(key, finished, nodes.some(n => holds(n.id))), st = g.starting ? "running" : J.passState(g.p, nodes, inPass(g.p), v.passNotes(g.p));
          // D1182: a pass picked is drawn as any picked line -- its own tint, not the accent's
          out.push({ type: "row", key, depth: 0, cls: "alt-pass-row", k: COLORS.setup, fold: fold(key, collapsed, passName(g.p)), act: { pass: g.p }, btnCls: "alt-pass-heading",
            picked: v.passShown && v.passView === g.p, mark: st === "running" ? "spin" : doneMark(st === "failed", st === "part"),
            name: [g.starting ? `${passName(g.p)} · starting` : passName(g.p)], pills: [], time: g.starting ? "" : span(inPass(g.p).map(r => r.node)) });   // D1182: the pass's time as its panel says it
          if (collapsed) continue;
          depth = 1;
        }
        // D1012: every setup task of the pass in one Setup line; D1094: one step is a Setup line too
        const lead = g.p != null ? g.rows.filter(r => boxOf(r.node) === "setup") : [], others = g.rows.filter(r => !lead.includes(r));
        if (lead.length > 1) {
          const key = `setup:${g.p}`, nodes = lead.map(r => r.node);
          const collapsed = v.isFolded(key, nodes.every(n => n.t1 != null), nodes.some(n => holds(n.id))) || (!v.opened.has(key) && !nodes.some(n => holds(n.id)));
          out.push({ type: "row", key: `g:${key}`, depth, cls: "quiet", k: COLORS.setup, fold: fold(key, collapsed, "Setup"), act: { toggle: key, collapsed }, btnCls: "alt-setup-steps",
            mark: nodes.every(n => n.t1 != null) ? doneMark(nodes.some(n => n.failed), nodes.some(n => n.rejected || J.marked(n.id))) : "spin",
            name: [`Setup · ${lead.length} steps`], pills: [], time: dur(nodes.reduce((t, n) => t + secs(n), 0)) });
          if (!collapsed) runs(lead, depth + 1);
        } else lead.forEach(r => { const l = taskLabel(r.node), at = l.indexOf(" · "); line(r, depth, [`Setup · ${at < 0 ? l : l.slice(at + 3)}`]); });
        segs.length = 0;
        // D1054: a search done as a note (D1052) is the pass's Search line
        const searched = g.p != null && !others.some(r => boxOf(r.node) === "search") ? v.passNotes(g.p).filter(n => noteBox(n) === "search") : [];
        if (searched.length) { segs.push([out.length, "search"]);
          out.push({ type: "row", key: `note:${g.p}`, depth, cls: "box", k: COLORS.search, fold: null, act: { box: { p: g.p, key: "search" } },
            btnCls: "", mark: doneMark(false, false), name: [{ b: "Search" }, { cls: "alt-what", text: ` ${searched.map(n => v.noteShort(n, g.p)).join(", ")}` }], pills: [], noTime: true }); }
        boxes(others, depth);
        // D1044: in a pass at work, the boxes still to come, dimmed -- D1235: in the flow's order, between the
        // boxes that ran (live-10); one the pass went past is skipped, not waiting (live-28)
        if (g.starting || g.rows.some(r => r.node.t1 == null) || (g === groups.at(-1) && g.p != null && [...v.model.nodes.values()].some(n => n.t1 == null))) {
          const flow = flowBoxes(inPass(g.p), g.p, true, v.passNotes(g.p)), order = flow.map(b => b.key);
          const waiting = flow.filter(x => x.state === "wait" && !x.runs.length), row = b => ({ type: "wait", key: `wait:${g.p}:${b.key}`, depth,
            k: COLORS[b.kind] || COLORS.setup, title: b.title, said: waitWord(flow, b) });
          const at = b => segs.find(([, k]) => order.indexOf(k) > order.indexOf(b.key))?.[0];
          const tail = waiting.filter(b => at(b) == null);
          for (const b of waiting.filter(b => at(b) != null).reverse()) out.splice(at(b), 0, row(b));
          out.push(...tail.map(row));
        }
      }
      return out;
    },
    /** D1044: a graph box's list -- its tries, one flat line each, newest first, five at a time, when it ran
        one task again and again; else each of its runs; a single run, its steps. */
    boxList(runsOf, key, kind) {
      const own = n => {   // D1065; D1066: "critique · #3" -- its mark says the verdict, the narrow line keeps the design
        if (isCritique(n)) { const of = String(n.why || "").replace(/@\S*$/, "").trim(); return of && !/^round\b/i.test(of) ? `critique · ${N.short(of)}` : "critique"; }
        const full = taskLabel(n), at = full.indexOf(" · "), r = at >= 0 && boxOf(n) === kind ? full.slice(at + 3) : full;
        const c = designOf(n), named = /^\s*test\s*:\s*(?:fast )?check\b/i.test(String(n.name)) ? `check · ${r}` : r;      // D1054
        return N.shortAll(kind === "check" && c && !named.includes(c) && !named.includes(N.short(c)) ? `${named} · ${c}` : named);   // D1059
      };
      if (kind === "design") {                    // D1045: the same tries as the tree; D1059; D1061: runs of other names are parts
        const parts = new Set(runsOf.map(rest)).size > 1;
        const tries = runsOf.flatMap(n => { const t = triesOfRun(n);
          return t.map((x, k) => ({ ...x, part: parts ? N.shortAll(rest(n)) + (t.length > 1 ? ` · try ${k + 1}` : "") : "" })); });
        if (tries.length > 1) newestFirst(key, tries.map((t, i) => [t, t.n ?? i + 1]), ([t, i]) => tryLine(t, i, 0, true), 0, holdsTry, parts);
        return out;                               // one try: the box opens it
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

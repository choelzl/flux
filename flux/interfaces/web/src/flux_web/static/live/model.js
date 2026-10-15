// Pure task selection shared by the three Live representations.
/** D1052: a note with no task of its own (a scripted search's scope list) is the pass it was written in, as a task there would be. */
export const notePass = (note, marks) => taskPass({ name: note.name, t0: note.t, seq: note.seq, parent: null }, marks);
export function taskPass(node, marks) {
  let root = node;
  for (let p = node; p; p = p.parent) {
    const value = p.params?.pass;
    if (value != null && value !== "" && Number.isFinite(Number(value))) return Number(value);
    root = p;
  }
  let pass = null;
  // D1041: a mark is before a task by its place in the journal -- a pass's last steps can start in the
  // millisecond its successor's mark is written; by time alone (journals without the order) within 1ms
  const before = mark => mark.seq != null && root.seq != null ? mark.seq < root.seq : mark.t <= root.t0 + .001;
  for (const mark of marks) if (mark.name === "pass" && before(mark) && Number.isFinite(Number(mark.n))) pass = Number(mark.n);
  if (pass != null) return pass;
  // D978: before the first mark -- the engine marks passes from the second (or the baseline) on, so
  // what ran before is the pass before it, as Live counts it; the run's own setup (the document
  // checked, the papers read, the round planned) and anything before a baseline stay Setup
  const first = marks.find(m => m.name === "pass" && Number.isFinite(Number(m.n)));
  if (/^\s*(validate|digest|plan\b|lessons)/i.test(String(root.name))) return null;
  // D1012: no mark yet -- the first pass is not marked: its work is Pass 1, as Live says; its setup
  // (the papers, the library, the tools' gate) the start's Setup, as Live keeps it
  if (!first) return /^\s*(knowledge|records|feedback|extract|setup|gate:\s*(tools|the problem)|propose:\s*decompose)/i.test(String(root.name)) ? null : 1;
  if (Number(first.n) <= 0) return null;
  return Number(first.n) - 1;
}

export function taskScope(model, pass = "current") {
  const all = [...model.nodes.values()].sort((a, b) => a.t0 - b.t0 || Number(a.id) - Number(b.id));
  const membership = new Map(all.map(n => [n.id, taskPass(n, model.marks)]));
  // D1041: a run of one pass is never marked -- a setup step after its work began (the mentor read
  // again between designs) is that pass's, as it is in a marked pass, not a Setup between its steps
  if (!model.marks.some(m => m.name === "pass")) {
    const rootOf = n => { while (n.parent) n = n.parent; return n; };
    const began = Math.min(...all.filter(n => !n.parent && membership.get(n.id) === 1).map(n => n.t0));
    for (const n of all) { const r = rootOf(n); if (membership.get(n.id) == null && r.t0 > began) membership.set(n.id, 1); }
  }
  const passes = [...new Set([...model.marks.filter(m => m.name === "pass" && m.n != null).map(m => Number(m.n)),
    ...membership.values()].filter(n => n != null && Number.isFinite(n)))].sort((a, b) => a - b);
  const active = new Set(all.filter(n => n.t1 == null).map(n => membership.get(n.id)).filter(n => n != null));
  const last = model.marks.filter(m => m.name === "pass" && m.n != null).at(-1)?.n ?? passes.at(-1);
  const current = active.size ? active : new Set(last == null ? [] : [Number(last)]);
  const nodes = all.filter(n => pass === "all" || (pass === "current" ? !current.size || current.has(membership.get(n.id)) : membership.get(n.id) === Number(pass)));
  const included = new Set(nodes.map(n => n.id));
  const ordered = [], visit = node => { ordered.push(node); node.kids.filter(k => included.has(k.id)).forEach(visit); };
  nodes.filter(n => !n.parent || !included.has(n.parent.id)).forEach(visit);
  const rows = ordered.map(node => {
    let depth = 0;
    for (let p = node.parent; p; p = p.parent) if (included.has(p.id)) depth++;
    return { node, depth, parent: node.parent && included.has(node.parent.id) ? node.parent.id : null };
  });
  return { rows, passes, current: [...current], membership };
}

export function currentTask(rows) {
  const nodes = rows.map(r => r.node), active = nodes.filter(n => n.t1 == null);
  const latest = (items, ended = false) => items.reduce((best, n) => !best || (ended ? n.t1 ?? n.t0 : n.t0) >= (ended ? best.t1 ?? best.t0 : best.t0) ? n : best, null);
  return latest(active.filter(n => /^agent:/i.test(n.name)))
    || latest(active.filter(n => !n.kids.some(k => k.t1 == null)))
    // D1067: done, the latest work -- a critique of it is not what Following opens
    || latest(active) || latest(nodes.filter(n => !n.kids.length && !/^\s*critique\s*:/i.test(String(n.name))), true) || latest(nodes.filter(n => !n.kids.length), true) || latest(nodes, true);
}

export const taskState = n => n.t1 == null ? "running" : n.interrupted ? "interrupted" : n.failed ? "failed" : n.timedOut ? "timeout" : n.rejected ? "rejected" : "done";

// Recent tasks first in long journals, without dropping the selected task or its ancestors.
export function taskWindow(rows, limit, selected) {
  const recent = rows.slice().sort((a, b) => a.node.t0 - b.node.t0 || Number(a.node.id) - Number(b.node.id)).slice(-limit);
  const included = new Set(recent.map(r => r.node.id));
  if (rows.some(r => r.node.id === selected)) included.add(selected);
  for (let i = rows.length - 1; i >= 0; i--) if (included.has(rows[i].node.id) && rows[i].parent != null) included.add(rows[i].parent);
  return rows.filter(r => included.has(r.node.id));
}

export function campaignForStart(history, start) {
  const next = history.starts.filter(s => s.id > start.id).sort((a, b) => a.id - b.id)[0];
  const end = start.ended || next?.started || Infinity;
  const candidates = history.campaigns.filter(c => c.run_id === start.record_id && Date.parse(c.created_at) / 1000 <= end)
    .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
  return candidates.find(c => Date.parse(c.created_at) / 1000 <= start.started + 5) || candidates.at(-1) || null;
}

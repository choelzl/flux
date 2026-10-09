// Pure task selection shared by the three LiveAlt representations.
export function taskPass(node, marks) {
  let root = node;
  for (let p = node; p; p = p.parent) {
    const value = p.params?.pass;
    if (value != null && value !== "" && Number.isFinite(Number(value))) return Number(value);
    root = p;
  }
  let pass = null;
  for (const mark of marks) if (mark.name === "pass" && mark.t <= root.t0 + .001 && Number.isFinite(Number(mark.n))) pass = Number(mark.n);
  return pass;
}

export function taskScope(model, pass = "current") {
  const all = [...model.nodes.values()].sort((a, b) => a.t0 - b.t0 || Number(a.id) - Number(b.id));
  const membership = new Map(all.map(n => [n.id, taskPass(n, model.marks)]));
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
    || latest(active) || latest(nodes.filter(n => !n.kids.length), true) || latest(nodes, true);
}

export const taskState = n => n.t1 == null ? "running" : n.interrupted ? "interrupted" : n.failed ? "failed" : "done";

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

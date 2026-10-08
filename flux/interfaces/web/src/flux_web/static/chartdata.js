// Flux web: what the charts compute, apart from their drawing -- pure, no DOM, so node tests it
// (flux/tests/unit/test_web_charts.py). D914: every finite measured point is drawn; the best so
// far and the Pareto front count only eligible designs of one scope -- the whole, or one part.

/** D849: one point per design for the charts over time -- its number at the stage asked for, else
    at the deepest stage it reached, when it was first measured. D914: with what decides whether it
    counts -- its identity, group, eligibility and reasons. */
function designPoints(designs, obj) {
  const want = obj.stage && obj.stage !== "deepest" ? obj.stage : null;
  return (designs || []).map(d => {
    const stage = want || d.shown, v = Number(((d.stages || {})[stage] || {})[obj.metric] ?? NaN);
    const t = Date.parse(d.first || d.last || "") / 1000;
    return !isFinite(v) || !isFinite(t) ? null : { when: t, stage, name: d.name, key: d.key || "", part: d.part || "", group: d.group || "",
      ...verdictOf(d), baseline: !!d.baseline, decision: !!d.decision, design: d, metrics: { [obj.metric]: v } };
  }).filter(Boolean);
}

/** A design's standing as the results say it (D899): eligible only when it meets every requirement. */
function verdictOf(d) {
  const eligible = d.eligible != null ? !!d.eligible : d.verdict === "accepted";
  const pending = !eligible && !!(d.pending || d.verdict === "pending");
  return { eligible, pending, verdict: eligible ? "accepted" : pending ? "pending" : "failed", reasons: (d.reasons && d.reasons.length ? d.reasons : d.why) || [] };
}

/** The groups of a loop's designs (D896), the whole first, then its parts by name, the unnamed last;
    none when the designs are not of parts. */
function groupList(designs) {
  const gs = [...new Set((designs || []).map(d => d.group || ""))];
  if (gs.length < 2 && !gs.includes("whole")) return [];
  const rank = (g) => g === "whole" ? 0 : g === "" ? 2 : 1;
  return gs.sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
}

/** D915: each group's colour and shape, from the results' whole list of groups (groupList), so a
    part keeps its colour whatever is plotted -- another stage, metric, filter or a refresh; past the
    eight colours a group takes another shape. A design not of parts: no colour of its own (""). */
const SHAPES = ["circle", "square", "triangle", "diamond"];
function groupStyles(groups) {
  const at = new Map(groups.map((g, i) => [g, i]));
  const of = (g) => { const i = at.get(g || "");
    return i == null ? { i: -1, color: "", shape: "circle" } : { i, color: `var(--g${i % 8})`, shape: SHAPES[Math.floor(i / 8) % SHAPES.length] }; };
  return { groups, of };
}

/** D914: the scopes a design of parts is compared in -- the whole first, then each part; a design not
    of parts has one scope, every design (""). `groups`: the results' groups, whole first. */
function scopesOf(groups) {
  if (!groups.length) return [["", "All designs"]];
  return [["whole", "Whole"], ...groups.filter(g => g !== "whole").map(g => [g, g || "other"])];
}
const inScope = (groups, scope) => (p) => !groups.length || (p.group || "") === (scope || "whole");

/** The best so far, design by design, of the points that count (D914: eligible, in scope); null
    before the first. `pts` in measurement order, each with `v`. */
function bestSeries(pts, maxi, counts) {
  let best = null;
  const steps = pts.map(p => { if (counts(p) && (best === null || (maxi ? p.v > best : p.v < best))) best = p.v; return best; });
  return { steps, best };
}

/** The latest baseline for each group/stage, drawn as references rather than search points. */
function baselinePoints(rows, counts = () => true) {
  const latest = new Map();
  const when = (p) => Date.parse(p.design?.last || "") / 1000 || p.when;
  for (const p of rows) if (p.baseline && counts(p)) {
    const key = JSON.stringify([p.group || "", p.stage]);
    if (!latest.has(key) || when(p) >= when(latest.get(key))) latest.set(key, p);
  }
  return [...latest.values()];
}

/** The non-dominated points of `pts` ({x, y}) for the two directions, in O(n log n) (D914: a loop's
    20,000 designs): sorted by x, best first, a point is on the front when its y beats every y before
    it; equal points are all on it, as neither beats the other. */
function frontier(pts, dx, dy) {
  const sx = dx === "minimize" ? 1 : -1, sy = dy === "minimize" ? 1 : -1;
  const s = pts.map(p => ({ p, x: sx * p.x, y: sy * p.y })).sort((a, b) => a.x - b.x || a.y - b.y);
  const out = [];
  let bestY = Infinity;
  for (let i = 0; i < s.length;) {
    let j = i;
    while (j < s.length && s[j].x === s[i].x) j++;
    if (s[i].y < bestY) {                                  // the least y at this x, if it beats the rest
      for (let k = i; k < j && s[k].y === s[i].y; k++) out.push(s[k].p);
      bestY = s[i].y;
    }
    i = j;
  }
  return out;
}

/** Keep missing measurements and recorded sampling outages empty in time charts. */
function timeSegments(samples, get) {
  const segments = [];
  let segment = null;
  for (const sample of samples) {
    const value = get(sample);
    if (sample.gap_before || value == null || !Number.isFinite(value)) segment = null;
    if (value == null || !Number.isFinite(value)) continue;
    if (!segment) { segment = []; segments.push(segment); }
    segment.push(sample);
  }
  return segments;
}

export { baselinePoints, bestSeries, designPoints, frontier, groupList, groupStyles, inScope, scopesOf, timeSegments, verdictOf };

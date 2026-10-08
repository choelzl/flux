// References use the complete results set, independently of table filtering/paging.
import { directionOf, verdictOf } from "./chartdata.js";

export function measurementComparison(designs, objectives = []) {
  const buckets = new Map(), key = (d, stage, metric) => JSON.stringify([d.group || "", stage, metric]);
  for (const d of designs || []) {
    const accepted = verdictOf(d).eligible;
    for (const [stage, metrics] of Object.entries(d.stages || {})) {
      for (const [metric, value] of Object.entries(metrics)) {
        if (!Number.isFinite(value)) continue;
        const k = key(d, stage, metric);
        if (!buckets.has(k)) buckets.set(k, { values: [], baseline: null, direction: directionOf(metric, objectives) });
        const b = buckets.get(k);
        if (accepted) b.values.push(value);
        const when = Date.parse(d.last || d.first || "") || 0;
        if (d.baseline && (!b.baseline || when >= b.baseline.when)) b.baseline = { kind: "baseline", name: d.name, value, when };
      }
    }
  }
  for (const b of buckets.values()) {
    b.values.sort((a, c) => a - c);
    const n = b.values.length, q = b.direction === "minimize" ? .1 : .9;
    const pos = (n - 1) * q, lo = Math.floor(pos), hi = Math.ceil(pos), weight = pos - lo;
    // Interpolate between adjacent ranks; P10 for a minimized metric is P90 performance.
    b.reference = b.baseline || (n ? { kind: q === .1 ? "P10" : "P90", count: n,
      value: b.values[lo] * (1 - weight) + b.values[hi] * weight } : null);
  }
  return (d, metric, stage = d.shown) => {
    const value = (d.stages?.[stage] || {})[metric], reference = buckets.get(key(d, stage, metric))?.reference;
    const change = Number.isFinite(value) && reference && reference.value !== 0 ? (value - reference.value) / Math.abs(reference.value) * 100 : null;
    return { value, reference, percent: Number.isFinite(change) ? change : null };
  };
}

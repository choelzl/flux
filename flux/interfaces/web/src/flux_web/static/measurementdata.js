// References use the complete measured population, independently of table filtering/paging.
export function measurementComparison(designs) {
  const buckets = new Map(), key = (d, stage, metric) => JSON.stringify([d.group || "", stage, metric]);
  for (const d of designs || []) for (const [stage, metrics] of Object.entries(d.stages || {})) {
    for (const [metric, value] of Object.entries(metrics)) {
      if (!Number.isFinite(value)) continue;
      const k = key(d, stage, metric);
      if (!buckets.has(k)) buckets.set(k, { values: [], baseline: null });
      const b = buckets.get(k); b.values.push(value);
      const when = Date.parse(d.last || d.first || "") || 0;
      if (d.baseline && (!b.baseline || when >= b.baseline.when)) b.baseline = { kind: "baseline", name: d.name, value, when };
    }
  }
  for (const b of buckets.values()) {
    b.values.sort((a, c) => a - c);
    const n = b.values.length, mid = Math.floor(n / 2);
    b.reference = b.baseline || { kind: "median", value: n % 2 ? b.values[mid] : b.values[mid - 1] / 2 + b.values[mid] / 2 };
  }
  return (d, metric, stage = d.shown) => {
    const value = (d.stages?.[stage] || {})[metric], reference = buckets.get(key(d, stage, metric))?.reference;
    const change = Number.isFinite(value) && reference && reference.value !== 0 ? (value - reference.value) / Math.abs(reference.value) * 100 : null;
    return { value, reference, percent: Number.isFinite(change) ? change : null };
  };
}

// Fixed example content for layout previews; it never reads or writes a loop.
function overviewMockData() {
  const now = Date.now() / 1000;
  const design = (index, latency, area, baseline = false) => ({
    name: `sample#${index}`, base: `sample#${index}`, key: String(index), part: "", group: "whole",
    baseline, decision: index === 3, closest: false, rank: index ? 4 - index : 4,
    eligible: !baseline, verdict: "accepted", pending: false, why: [], reasons: [], shown: "timing",
    numbers: { latency, area }, stages: { timing: { latency, area } }, meets: { latency: true, area: true },
    first: new Date((now - (4 - index) * 120) * 1000).toISOString(), last: new Date((now - 90) * 1000).toISOString(),
  });
  return {
    state: { running: true, since: now - 3600, last_active: now, passes: 12, at_rest: false },
    results: {
      designs: [design(0, 10, 100, true), design(1, 9.1, 92), design(2, 8.4, 87), design(3, 7.6, 81)],
      metrics: ["latency", "area"], stages: ["timing"], metric_info: { latency: { unit: "ms" }, area: { unit: "mm²" } },
      counts: { accepted: 3, pending: 0, failed: 0 }, objectives: "Minimize latency; area ≤ 100 mm²",
      objective_list: [{ metric: "latency", direction: "minimize" }, { metric: "area", direction: "minimize", goal: 100 }],
      limits: [{ metric: "area", direction: "minimize", goal: 100 }], decided_by: "lowest latency among designs meeting the area limit",
      passes: Array.from({ length: 12 }, (_, i) => ({ when: now - (11 - i) * 300 - 60,
        conclusion: { decision: "sample#3", decided_by: "Lower latency with all checks passing" } })),
    },
    notes: [{ id: "mock-1", by: "You", t: now - 900, text: "Try a smaller working set." },
      { id: "mock-2", by: "You", t: now - 240, text: "Keep the memory limit while improving latency." }],
    workbench: [{ path: "workbench/ideas.json", mtime: now - 180, first: "Compare two cache layouts" },
      { path: "workbench/benchmark.py", mtime: now - 420, first: "Repeat each timing test five times" }],
    usage: { total: { turns: 24, seconds: 1080, counted: 24, tokens_in: 48200, tokens_out: 12600, cost_usd: 1.24, partial: 0 } },
  };
}

export { overviewMockData };

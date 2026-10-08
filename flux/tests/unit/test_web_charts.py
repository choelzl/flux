"""D914: what the results charts compute (chartdata.js), under node -- the points carry each design's
eligibility, the best so far and the Pareto front count only eligible designs of one scope, and the
front is exact and O(n log n) on a loop's 20,000 designs."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).parent
CHARTDATA = HERE.parents[1] / "interfaces/web/src/flux_web/static/chartdata.js"
MEASUREMENTDATA = CHARTDATA.with_name("measurementdata.js")
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")


def run(tmp_path: Path, body: str) -> dict:
    """`body` runs with chartdata.js's exports as `C`, and prints its answer as JSON."""
    shutil.copy(CHARTDATA, tmp_path / "chartdata.mjs")
    (tmp_path / "measurementdata.mjs").write_text(MEASUREMENTDATA.read_text().replace('"./chartdata.js"', '"./chartdata.mjs"'))
    (tmp_path / "t.mjs").write_text("import * as C from './chartdata.mjs';\nimport * as M from './measurementdata.mjs';\n" + body)
    r = subprocess.run(["node", str(tmp_path / "t.mjs")], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


DESIGNS = """
const design = (name, group, area, freq, eligible, extra = {}) => ({ name, key: 'k' + name, part: group === 'whole' ? '' : group, group,
  eligible, pending: false, verdict: eligible ? 'accepted' : 'failed', reasons: eligible ? [] : ['fmax_mhz 800 is below 1000'],
  why: [], decision: false, shown: 'confirm', first: '2026-10-0' + (1 + (name.length % 5)) + 'T10:00:00Z',
  stages: { confirm: { area_um2: area, fmax_mhz: freq } }, ...extra });
"""


def test_time_series_break_at_outages_and_missing_measurements(tmp_path):
    got = run(tmp_path, """
const samples = [{t: 0, v: 1}, {t: 60, v: 2}, {t: 3600, v: 8, gap_before: true},
  {t: 3660, v: 9}, {t: 3720, v: null}, {t: 3780, v: 3}];
console.log(JSON.stringify({segments: C.timeSegments(samples, s => s.v).map(ps => ps.map(s => s.t)),
  empty: C.timeSegments(samples, s => null)}));
""")
    assert got == {"segments": [[0, 60], [3600, 3660], [3780]], "empty": []}


def test_points_carry_identity_and_eligibility(tmp_path):
    got = run(tmp_path, DESIGNS + """
const ds = [design('a', 'whole', 31, 1200, true), design('bb', 'whole', 12.5, 800, false), design('ccc', 'whole', NaN, 900, true)];
console.log(JSON.stringify(C.designPoints(ds, { metric: 'area_um2', stage: 'confirm' }).map(p => { const { design, ...rest } = p; return rest; })));
""")
    assert [p["name"] for p in got] == ["a", "bb"]                 # the non-finite number is not a point
    a, b = got
    assert a["eligible"] and a["verdict"] == "accepted" and a["key"] == "ka" and a["group"] == "whole"
    assert not b["eligible"] and b["verdict"] == "failed" and b["reasons"] == ["fmax_mhz 800 is below 1000"]


def test_baseline_is_retained_on_points_at_any_stage(tmp_path):
    got = run(tmp_path, DESIGNS + """
const ds = [design('initial', '', 31, 1200, true, { baseline: true }), design('new', '', 30, 1300, true)];
console.log(JSON.stringify(['confirm', 'deepest'].map(stage => C.designPoints(ds, { metric: 'area_um2', stage }).map(p => p.baseline))));
""")
    assert got == [[True, False], [True, False]]


def test_relative_measurements_match_baseline_by_stage_and_group(tmp_path):
    got = run(tmp_path, DESIGNS + """
const base = design('base', 'whole', 100, 1000, true, {baseline: true});
const better = design('better', 'whole', 70, 1300, true);
const part = design('part', 'decoder', 5, 500, true);
const other = design('other', 'decoder', 15, 700, true);
base.stages.screen = {area_um2: 50}; better.stages.screen = {area_um2: 25};
const compare = M.measurementComparison([base, better, part, other]);
console.log(JSON.stringify({cost: compare(better, 'area_um2'), freq: compare(better, 'fmax_mhz'),
  screen: compare(better, 'area_um2', 'screen'), part: compare(part, 'area_um2'), missing: compare(better, 'power_w')}));
""")
    assert got["cost"]["percent"] == -30 and got["freq"]["percent"] == 30
    assert got["cost"]["reference"]["kind"] == "baseline"
    assert got["screen"]["percent"] == -50
    assert got["part"]["percent"] == pytest.approx(-100 / 6) and got["part"]["reference"]["value"] == 6
    assert got["part"]["reference"]["kind"] == "P10"
    assert got["missing"]["percent"] is None


def test_relative_reference_uses_latest_baseline_and_handles_zero(tmp_path):
    got = run(tmp_path, DESIGNS + """
const old = design('old', '', 100, 1000, true, {baseline: true, last: '2026-10-01T12:00:00Z'});
const newer = design('newer', '', 50, 0, true, {baseline: true, last: '2026-10-02T12:00:00Z'});
const d = design('d', '', 25, 800, true);
const compare = M.measurementComparison([newer, old, d]);
console.log(JSON.stringify({cost: compare(d, 'area_um2'), zero: compare(d, 'fmax_mhz')}));
""")
    assert got["cost"]["percent"] == -50 and got["cost"]["reference"]["name"] == "newer"
    assert got["zero"]["percent"] is None and got["zero"]["reference"]["value"] == 0


def test_percentile_fallback_resists_bad_outliers_and_ignores_missing_values(tmp_path):
    got = run(tmp_path, DESIGNS + """
const ds = [10, 20, 1000, null, NaN].map((v, i) => design(String(i), '', v, 1, true));
const compare = M.measurementComparison(ds);
console.log(JSON.stringify(compare(ds[0], 'area_um2')));
""")
    assert got["reference"] == {"kind": "P10", "count": 3, "value": 12}
    assert got["percent"] == pytest.approx(-100 / 6)


def test_p90_speedup_uses_accepted_designs_only_and_interpolates(tmp_path):
    got = run(tmp_path, DESIGNS + """
const measured = (name, speedup, eligible, extra = {}) => {
  const d = design(name, 'whole', 1, 1, eligible, extra);
  d.stages.confirm = {mean_speedup: speedup}; return d;
};
const ds = Array.from({length: 10}, (_, i) => measured(String(i), i + 1, true));
const failed = measured('failed', 1e9, false, {verdict: 'accepted'});
const pending = measured('pending', -1e9, false, {pending: true, verdict: 'pending'});
const part = measured('part', 1e9, true); part.group = 'decoder';
const compare = M.measurementComparison([...ds, failed, pending, part]);
console.log(JSON.stringify(compare(ds[4], 'mean_speedup')));
""")
    assert got["reference"] == {"kind": "P90", "count": 10, "value": 9.1}
    assert got["percent"] == pytest.approx((5 - 9.1) / 9.1 * 100)


def test_percentile_follows_declared_objective_direction(tmp_path):
    got = run(tmp_path, DESIGNS + """
const ds = [10, 20, 100].map((v, i) => {
  const d = design(String(i), '', 1, 1, true); d.stages.confirm = {score: v}; return d;
});
const compare = M.measurementComparison(ds, [{metric: 'score', direction: 'minimize'}]);
console.log(JSON.stringify(compare(ds[0], 'score')));
""")
    assert got["reference"] == {"kind": "P10", "count": 3, "value": 12}
    assert got["percent"] == pytest.approx(-100 / 6)


def test_no_accepted_measurements_leaves_reference_unavailable(tmp_path):
    got = run(tmp_path, DESIGNS + """
const d = design('failed', 'decoder', 5, 1, false);
const other = design('accepted', 'whole', 10, 1, true);
const compare = M.measurementComparison([d, other]);
console.log(JSON.stringify(compare(d, 'area_um2')));
""")
    assert got == {"value": 5, "reference": None, "percent": None}


@pytest.mark.parametrize("value", [0, -10])
def test_percentile_with_one_accepted_design_and_zero_or_negative_reference(tmp_path, value):
    got = run(tmp_path, DESIGNS + f"""
const base = design('accepted', '', {value}, 1, true), d = design('failed', '', 20, 1, false);
const compare = M.measurementComparison([base, d]);
console.log(JSON.stringify(compare(d, 'area_um2')));
""")
    assert got["reference"] == {"kind": "P10", "count": 1, "value": value}
    assert got["percent"] == (None if value == 0 else 300)


def test_baseline_references_are_latest_and_scoped(tmp_path):
    got = run(tmp_path, """
const rows = [{name: 'old', baseline: true, when: 2, design: {last: '2026-10-01T12:00:00Z'}, group: 'whole', stage: 'fine'},
  {name: 'current', baseline: true, when: 1, design: {last: '2026-10-02T12:00:00Z'}, group: 'whole', stage: 'fine'},
  {name: 'part', baseline: true, when: 3, group: 'decoder', stage: 'fine'},
  {name: 'screen', baseline: true, when: 1, group: 'whole', stage: 'screen'},
  {name: 'design', baseline: false, when: 4, group: 'whole', stage: 'fine'}];
console.log(JSON.stringify(C.baselinePoints(rows, C.inScope(['whole', 'decoder'], 'whole')).map(p => p.name)));
""")
    assert got == ["current", "screen"]


def test_a_design_failing_another_requirement_is_never_the_best(tmp_path):
    """The review's case (W12): accepted area 31 against failed area 12.5 (frequency below its floor)."""
    got = run(tmp_path, DESIGNS + """
const pts = C.designPoints([design('a', '', 31, 1200, true), design('bb', '', 12.5, 800, false)], { metric: 'area_um2', stage: 'confirm' })
  .map(p => ({ ...p, v: p.metrics.area_um2 })).sort((x, y) => x.when - y.when);
const counts = C.inScope([], '');
const none = C.bestSeries(pts.filter(p => !p.eligible), false, p => p.eligible && counts(p));
console.log(JSON.stringify({ best: C.bestSeries(pts, false, p => p.eligible && counts(p)).best, none: none.best, steps: none.steps }));
""")
    assert got["best"] == 31
    assert got["none"] is None and got["steps"] == [None]           # before a feasible design: no best line


def test_scopes_never_pool_parts(tmp_path):
    got = run(tmp_path, DESIGNS + """
const ds = [design('dec', 'decoder', 1, 1, true), design('enc', 'encoder', 10, 10, true)];
const groups = C.groupList(ds), pts = ds.map(d => ({ ...C.verdictOf(d), group: d.group, x: d.stages.confirm.area_um2, y: d.stages.confirm.fmax_mhz }));
const whole = pts.filter(C.inScope(groups, 'whole')), enc = pts.filter(C.inScope(groups, 'encoder'));
console.log(JSON.stringify({ groups, scopes: C.scopesOf(groups), whole: whole.length,
  encFront: C.frontier(enc, 'minimize', 'minimize').map(p => p.x), flat: C.scopesOf(C.groupList([design('x', '', 1, 1, true)])) }));
""")
    assert got["groups"] == ["decoder", "encoder"]
    assert got["scopes"][0] == ["whole", "Whole"] and got["whole"] == 0   # no whole: nothing to compare, not the parts pooled
    assert got["encFront"] == [10]                                       # the decoder does not knock the encoder off its front
    assert got["flat"] == [["", "All designs"]]


def test_group_list_is_stable_whole_first(tmp_path):
    got = run(tmp_path, DESIGNS + """
console.log(JSON.stringify([C.groupList([design('e', 'encoder', 1, 1, true), design('w', 'whole', 1, 1, true), design('o', '', 1, 1, true), design('d', 'decoder', 1, 1, true)]),
  C.groupList([design('x', '', 1, 1, true)]), C.groupList([design('w', 'whole', 1, 1, true)])]));
""")
    assert got == [["whole", "decoder", "encoder", ""], [], ["whole"]]


def test_a_group_keeps_its_colour(tmp_path):
    """D915: the colour map is of the results' whole list of groups -- what one chart plots does not
    shift it; past eight groups a colour comes back with another shape, never two groups the same."""
    got = run(tmp_path, """
const S = C.groupStyles(['whole', 'decoder', 'encoder']);
const many = C.groupStyles(Array.from({ length: 20 }, (_, i) => 'p' + String(i).padStart(2, '0')));
const looks = many.groups.map(g => { const s = many.of(g); return s.color + '/' + s.shape; });
console.log(JSON.stringify({ dec: S.of('decoder'), enc: S.of('encoder'), whole: S.of('whole'), none: C.groupStyles([]).of(''),
  distinct: new Set(looks).size, n: looks.length }));
""")
    assert got["whole"]["color"] == "var(--g0)" and got["dec"]["color"] == "var(--g1)" and got["enc"]["color"] == "var(--g2)"
    assert got["none"]["color"] == ""
    assert got["distinct"] == got["n"] == 20


def test_the_front_is_exact_and_fast(tmp_path):
    """Against the pairwise definition on random points with ties, every direction; then 20,000 points."""
    got = run(tmp_path, """
let seed = 7; const rnd = () => (seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648;
const better = (a, b, d) => d === 'minimize' ? a < b : a > b, notWorse = (a, b, d) => d === 'minimize' ? a <= b : a >= b;
const brute = (pts, dx, dy) => pts.filter(p => !pts.some(o => o !== p && notWorse(o.x, p.x, dx) && notWorse(o.y, p.y, dy) && (better(o.x, p.x, dx) || better(o.y, p.y, dy))));
let bad = 0;
for (let t = 0; t < 300; t++) {
  const pts = Array.from({ length: 1 + Math.floor(rnd() * 40) }, (_, i) => ({ i, x: Math.floor(rnd() * 8), y: Math.floor(rnd() * 8) }));
  for (const dx of ['minimize', 'maximize']) for (const dy of ['minimize', 'maximize']) {
    const a = C.frontier(pts, dx, dy).map(p => p.i).sort((u, v) => u - v).join(), b = brute(pts, dx, dy).map(p => p.i).sort((u, v) => u - v).join();
    if (a !== b) bad++;
  }
}
const big = Array.from({ length: 20000 }, () => ({ x: rnd() * 1000, y: rnd() * 1000 }));
const t0 = performance.now(); const f = C.frontier(big, 'minimize', 'maximize'); const ms = performance.now() - t0;
console.log(JSON.stringify({ bad, ms, n: f.length }));
""")
    assert got["bad"] == 0
    assert got["n"] > 0 and got["ms"] < 500, got

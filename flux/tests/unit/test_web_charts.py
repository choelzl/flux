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
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")


def run(tmp_path: Path, body: str) -> dict:
    """`body` runs with chartdata.js's exports as `C`, and prints its answer as JSON."""
    shutil.copy(CHARTDATA, tmp_path / "chartdata.mjs")
    (tmp_path / "t.mjs").write_text("import * as C from './chartdata.mjs';\n" + body)
    r = subprocess.run(["node", str(tmp_path / "t.mjs")], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


DESIGNS = """
const design = (name, group, area, freq, eligible, extra = {}) => ({ name, key: 'k' + name, part: group === 'whole' ? '' : group, group,
  eligible, pending: false, verdict: eligible ? 'accepted' : 'failed', reasons: eligible ? [] : ['fmax_mhz 800 is below 1000'],
  why: [], decision: false, shown: 'confirm', first: '2026-10-0' + (1 + (name.length % 5)) + 'T10:00:00Z',
  stages: { confirm: { area_um2: area, fmax_mhz: freq } }, ...extra });
"""


def test_points_carry_identity_and_eligibility(tmp_path):
    got = run(tmp_path, DESIGNS + """
const ds = [design('a', 'whole', 31, 1200, true), design('bb', 'whole', 12.5, 800, false), design('ccc', 'whole', NaN, 900, true)];
console.log(JSON.stringify(C.designPoints(ds, { metric: 'area_um2', stage: 'confirm' }).map(p => { const { design, ...rest } = p; return rest; })));
""")
    assert [p["name"] for p in got] == ["a", "bb"]                 # the non-finite number is not a point
    a, b = got
    assert a["eligible"] and a["verdict"] == "accepted" and a["key"] == "ka" and a["group"] == "whole"
    assert not b["eligible"] and b["verdict"] == "failed" and b["reasons"] == ["fmax_mhz 800 is below 1000"]


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

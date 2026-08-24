"""The Pareto-UCT tree policy (docs/decisions.md D368, after MicroEvo, arXiv:2608.06183).

The geometry is pinned on hand-computed volumes; the policy on small trees where the right
choice is checkable by eye; and the whole loop on a synthetic two-objective landscape whose
frontier is known, where the policy must find both ends and the knee without being told which
axis matters.
"""

from __future__ import annotations

from pathlib import Path

FLUX_ROOT = Path(__file__).resolve().parents[2]

from flux_frontier.pareto_uct import Node, ParetoUCT, dominates, hypervolume  # noqa: E402


# ---- geometry ------------------------------------------------------------------------------

def test_hypervolume_is_the_dominated_area_above_the_reference():
    assert hypervolume([(1.0, 1.0)], (0.0, 0.0)) == 1.0
    assert hypervolume([(1.0, 0.5), (0.5, 1.0)], (0.0, 0.0)) == 0.75
    assert hypervolume([(1.0, 0.5), (0.5, 1.0), (0.4, 0.4)], (0.0, 0.0)) == 0.75, (
        "a dominated point adds nothing")
    assert hypervolume([(-1.0, 0.5)], (0.0, 0.0)) == 0.0, "below the reference: no volume"


def test_dominance_is_maximise_form():
    assert dominates((2.0, 2.0), (1.0, 2.0))
    assert not dominates((2.0, 1.0), (1.0, 2.0))
    assert not dominates((1.0, 1.0), (1.0, 1.0))


def _tree(**kw) -> ParetoUCT:
    defaults = dict(reference=(0.0, 0.0), scale=(1.0, 1.0), budget=30)
    defaults.update(kw)
    return ParetoUCT(**defaults)


# ---- the front and the credit --------------------------------------------------------------

def test_the_front_keeps_only_non_dominated_points():
    t = _tree()
    t.grow([("a", (0.9, 0.1)), ("b", (0.1, 0.9)), ("c", (0.5, 0.5)), ("d", (0.4, 0.4))])
    labels = sorted(n.candidate for n in t.front())
    assert labels == ["a", "b", "c"], "d is dominated by c"
    t.record(t.root, "e", (0.95, 0.55))
    labels = sorted(n.candidate for n in t.front())
    assert "e" in labels and "a" not in labels and "c" not in labels, (
        "a new point evicts what it dominates")


def test_credit_flows_up_the_branch_that_earns_the_frontier():
    t = _tree()
    t.grow([("a", (0.5, 0.5)), ("b", (0.5, 0.49))])
    a, b = t.root.children
    t.record(a, "a1", (0.9, 0.6))       # expands the front
    t.record(b, "b1", (0.3, 0.3))       # dominated: no improvement
    assert a.q_hvi > b.q_hvi
    assert t.select() is not None


def test_selection_prefers_the_productive_branch_once_exploration_decays():
    t = _tree(budget=8, explore0=0.1)
    t.grow([("a", (0.5, 0.5)), ("b", (0.5, 0.49))])
    a, b = t.root.children
    t.record(a, "a1", (0.8, 0.7))
    t.record(b, "b1", (0.2, 0.2))
    t.record(b, "b2", (0.1, 0.1))
    picked = t.select()
    walk = picked
    while walk.parent is not None and walk.parent is not t.root:
        walk = walk.parent
    assert walk is a or picked is a, "the branch that bought hypervolume gets the next wave"


def test_an_unvisited_child_is_always_worth_one_look():
    t = _tree()
    t.grow([("a", (0.5, 0.5))])
    a = t.root.children[0]
    fresh = Node(candidate="f", objectives=(0.1, 0.1), parent=a)
    a.children.append(fresh)
    assert t.select() is fresh, "infinite exploration bonus before the first visit"


def test_seen_uses_the_caller_identity():
    t = _tree(identity=lambda c: c.lower())
    t.grow([("A", (0.5, 0.5))])
    assert t.seen("a") and not t.seen("b")


# ---- the whole loop on a known landscape ---------------------------------------------------

def test_the_policy_traces_a_known_frontier_within_budget():
    """Candidates are integers 0..63. Quality rises with x, cost rises faster past the knee:
    the true front is every x (quality strictly rises), but hypervolume concentrates around
    the knee. The policy must find the top-quality point AND keep small-x points on its
    front, expanding from more than one branch along the way."""
    def objectives(x: int) -> tuple[float, float]:
        quality = x / 63.0
        cost = (x / 63.0) ** 3
        return (quality, 1.0 - cost)

    def moves(x: int) -> list[int]:
        return [y for y in (x - 4, x - 1, x + 1, x + 4) if 0 <= y <= 63]

    t = ParetoUCT(reference=(0.0, 0.0), scale=(1.0, 1.0), budget=64, explore0=1.0)
    t.grow([(8, objectives(8)), (32, objectives(32))])
    expanded = []
    while t.spent < 64:
        node = t.select()
        base = node.candidate if node.candidate is not None else 8
        fresh = [m for m in moves(base) if not t.seen(m)][:4]
        if not fresh:
            node.expansions += 3
            if all(not [m for m in moves(n.candidate) if not t.seen(m)] for n in t.front()):
                break
            continue
        expanded.append(base)
        for m in fresh:
            t.record(node, m, objectives(m))
    xs = sorted(n.candidate for n in t.front())
    assert xs[-1] >= 55, f"the top-quality end was not reached: {xs}"
    assert xs[0] <= 12, f"the cheap end fell off the front: {xs}"
    assert len(set(expanded)) >= 3, "one branch monopolised the budget"


# ---- the prefetcher wiring -----------------------------------------------------------------

def test_prefetcher_stage1_pareto_uct_spends_the_same_budget_on_more_of_the_frontier():
    from flux_prefetcher.config import DEFAULT, storage_bytes
    from flux_prefetcher.flow import Measurer, _stage1, _stage1_pareto
    from flux_prefetcher.objective import BENCHMARKS, Baseline

    base = Baseline(ipc={b: 1.0 for b in BENCHMARKS})
    traces = {b: Path(f"/nonexistent/{b}") for b in BENCHMARKS}

    def backend(jobs, parallelism):
        out = []
        for job in jobs:
            cfg = job["config"]
            # A planted landscape: speedup rises with the PHT but saturates; storage rises
            # linearly. The frontier therefore has a cheap end and a fast end.
            ipc = 1.0 + 0.06 * (min(cfg.pht_size, 16384) / 16384.0) ** 0.5 \
                + 0.005 * (cfg.ft_size / 256.0)
            out.append({"ipc": ipc, "cycles": 1.0, "instructions": 1.0, "wall_clock_s": 0.0})
        return out

    def run(stage1):
        measurer = Measurer(None, backend, warmup=1, simulation=1, parallelism=8)
        refused: list[tuple[str, str]] = []
        scored = stage1([(DEFAULT, "incumbent")], traces, base, measurer, 24, set(), refused,
                        lambda _m: None)
        return scored, measurer.runs // len(BENCHMARKS)

    from flux_frontier import frontier

    uct_scored, uct_spent = run(_stage1_pareto)
    climb_scored, climb_spent = run(_stage1)
    assert uct_spent <= 24 + 6, "the tree respects the budget (within one wave)"
    uct_front = frontier(uct_scored, better=lambda s: s.geomean_speedup,
                         cost=lambda s: s.storage_bytes)
    climb_front = frontier(climb_scored, better=lambda s: s.geomean_speedup,
                           cost=lambda s: s.storage_bytes)
    assert len(uct_front) >= len(climb_front), (
        f"the tree should hold at least the climb's frontier: {len(uct_front)} vs "
        f"{len(climb_front)}")
    seed = 1.03125                      # DEFAULT on this landscape, computed by hand
    assert max(s.geomean_speedup for s in uct_scored) > seed + 0.001, (
        "the tree improved on its seed")
    assert min(s.storage_bytes for s in uct_front) <= storage_bytes(DEFAULT), (
        "the cheap end stayed on the front")


# ---- the rollout (D368: MCTS's simulation phase, as a wave-ordering estimate) ---------------

def test_the_rollout_votes_with_the_nearest_measured_configurations():
    from flux_prefetcher.config import DEFAULT
    from flux_prefetcher.flow import _rollout_speedup
    from flux_prefetcher.objective import BENCHMARKS, Baseline, score

    base = Baseline(ipc={b: 1.0 for b in BENCHMARKS})

    def scored_at(pht: int, g: float):
        from flux_prefetcher.study import ScoredConfig

        return ScoredConfig(config=DEFAULT.replace(pht_size=pht),
                            score=score({b: g for b in BENCHMARKS}, base, 1), provenance="t")

    measured = [scored_at(1024, 1.01), scored_at(2048, 1.02), scored_at(4096, 1.03),
                scored_at(8192, 1.04), scored_at(16384, 1.05)]
    assert _rollout_speedup(DEFAULT.replace(pht_size=4096), measured[:4]) is None, (
        "fewer than five measurements: no estimate")
    exact = _rollout_speedup(DEFAULT.replace(pht_size=4096), measured)
    assert exact == 1.03, "a measured configuration is its own estimate"
    between = _rollout_speedup(DEFAULT.replace(pht_size=8192, ft_size=128), measured)
    assert 1.03 < between < 1.05, f"an unmeasured neighbour interpolates: {between}"


def test_the_rollout_orders_the_wave_but_never_replaces_measurement():
    """On the planted landscape the rollout must not make the tree worse, and every scored
    point must still come from the backend, never from an estimate."""
    from flux_prefetcher.config import DEFAULT
    from flux_prefetcher.flow import Measurer, _stage1_pareto
    from flux_prefetcher.objective import BENCHMARKS, Baseline

    base = Baseline(ipc={b: 1.0 for b in BENCHMARKS})
    traces = {b: Path(f"/nonexistent/{b}") for b in BENCHMARKS}
    truth = {}

    def backend(jobs, parallelism):
        out = []
        for job in jobs:
            cfg = job["config"]
            ipc = 1.0 + 0.06 * (min(cfg.pht_size, 16384) / 16384.0) ** 0.5 \
                + 0.005 * (cfg.ft_size / 256.0)
            truth[cfg] = ipc
            out.append({"ipc": ipc, "cycles": 1.0, "instructions": 1.0, "wall_clock_s": 0.0})
        return out

    measurer = Measurer(None, backend, warmup=1, simulation=1, parallelism=8)
    scored = _stage1_pareto([(DEFAULT, "incumbent")], traces, base, measurer, 24, set(), [],
                            lambda _m: None)
    assert all(s.geomean_speedup == truth[s.config] for s in scored), (
        "an estimate leaked into a recorded number")
    assert max(s.geomean_speedup for s in scored) > 1.03125 + 0.001


# ---- the progress picture (D373) -----------------------------------------------------------

def test_the_progress_svg_draws_both_panels_from_the_record(tmp_path):
    from flux_report.progress import Point, render_progress

    pts = [Point(1.02, 40_000, "a"), Point(1.05, 120_000, "b"), Point(1.04, 60_000, "c"),
           Point(1.055, 125_000, "b", confirmed=True), Point(1.041, 61_000, "c", confirmed=True)]
    out = render_progress(pts, out=tmp_path / "p.svg", title="T", quality_label="speedup",
                          cost_label="bytes", refused=3, decision_label="c",
                          baseline_quality=1.0)
    svg = out.read_text()
    assert svg.count("<circle") == len(pts) * 2 + 2, "every point in both panels + 2 legend"
    assert "best so far" in svg and "3 refused" in svg and "decision" in svg
    assert svg.count("&#9733;") >= 2, "the decision is starred"
    assert "<title>" in svg, "native tooltips"
    assert 'stroke="#eb6834"' in svg, "the frontier is stepped in the confirmed hue"
    assert "NaN" not in svg


def test_points_from_campaign_reads_trials_in_order(tmp_path):
    from flux_prefetcher.config import DEFAULT
    from flux_prefetcher.measure import Recorder
    from flux_report.progress import points_from_campaign

    rec = Recorder(str(tmp_path / "x.db"), {"study": "t"}, log=lambda m: None)
    rec.trial(DEFAULT, ("bingo",), rung="screen", strategy="s",
              metrics={"geomean_speedup": 1.03, "storage_bytes": 35096.0}, error=None, wall_s=0)
    rec.trial(DEFAULT.replace(pht_size=8192), ("bingo",), rung="screen", strategy="s",
              metrics={"geomean_speedup": 1.05, "storage_bytes": 60000.0}, error=None, wall_s=0)
    rec.trial(DEFAULT, ("bingo",), rung="screen", strategy="s", metrics=None,
              error="refused", wall_s=0)
    rec.trial(DEFAULT.replace(pht_size=8192), ("bingo",), rung="decide", strategy="confirmed",
              metrics={"geomean_speedup": 1.045, "storage_bytes": 60000.0}, error=None, wall_s=0)
    rec.close("completed")
    points, refused = points_from_campaign(str(tmp_path / "x.db"))
    assert [round(p.quality, 3) for p in points] == [1.03, 1.05, 1.045]
    assert refused == 1
    assert [p.confirmed for p in points] == [False, False, True]


def test_the_trend_panel_can_track_the_cost_axis_with_a_running_minimum(tmp_path):
    """An area-optimal study's trend is "cheapest yet", not "fastest yet" (D373)."""
    from flux_report.progress import Point, render_progress

    pts = [Point(10.0, 900, "a"), Point(12.0, 700, "b"), Point(11.0, 800, "c"),
           Point(13.0, 650, "d")]
    svg = render_progress(pts, out=tmp_path / "p.svg", title="T", quality_label="served",
                          cost_label="mux bits", trend_axis="cost", log_cost=False).read_text()
    assert "best (lowest) 650" in svg
    assert "mux bits" in svg and "NaN" not in svg

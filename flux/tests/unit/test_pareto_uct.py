"""The Pareto-UCT tree policy (D368, after MicroEvo, arXiv:2608.06183).

The geometry is pinned on hand-computed volumes; the policy on small trees where the right
choice is checkable by eye; and the whole loop on a synthetic two-objective landscape whose
frontier is known, where the policy must find both ends and the knee without being told which
axis matters.
"""

from __future__ import annotations

from flux_frontier.pareto_uct import Node, ParetoUCT, dominates, hypervolume


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


# ---- the tree as a document's phase (D583), on the toy document ------------------------------

def _front_of(flow: list) -> tuple[list, list]:
    from flux_frontier import frontier
    from toy_document import run_toy, toy

    _prob, out = run_toy(toy(flow={"orchestrate": flow}))
    scored = [s for s in out.scored if "speedup" in s.metrics]
    return scored, frontier(scored, better=lambda s: s.metrics["speedup"], cost=lambda s: s.metrics["bytes"])


def test_the_pareto_phase_spends_the_same_budget_on_at_least_as_much_frontier():
    from toy_document import SEED, landscape

    uct, uct_front = _front_of([{"policy": "pareto", "budget": 12, "wave": 4, "reach": "any"}])
    climb, climb_front = _front_of([{"name": "climb", "budget": 12, "wave": 4, "reach": "any"}])
    assert len(uct) == len(climb) == 1 + 12, "the seed, then a budget of twelve"
    assert len(uct_front) >= len(climb_front)
    seed = landscape({**SEED})
    assert max(s.metrics["speedup"] for s in uct) > seed["speedup"], "the tree improved on its seed"
    assert min(s.metrics["bytes"] for s in uct_front) < seed["bytes"], "and reached below it on the cheap end"


def test_the_estimate_orders_the_wave_but_never_replaces_measurement():
    """Every scored point is what the stage printed for it, never the tree's estimate."""
    from flux_loop.dse import point_of
    from toy_document import landscape

    scored, _front = _front_of([{"policy": "pareto", "budget": 12, "wave": 4, "reach": "any"}])
    assert all({k: s.metrics[k] for k in ("speedup", "bytes")} == landscape(point_of(s.candidate)) for s in scored)

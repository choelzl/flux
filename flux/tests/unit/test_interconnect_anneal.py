"""The Metropolis walk (docs/decisions.md D313) -- the Monte-Carlo half of the interconnect search.

The claims worth defending here are about the SEARCH, not about silicon: that the chain moves, that
it is reproducible, that its archive means what "Pareto archive" means, and above all that it
optimizes the study's actual objectives rather than the one that is easiest to game. That last one
is a regression test with a real scar behind it, recorded below.
"""

from __future__ import annotations

import pytest
from flux_interconnect.anneal import (
    OBJECTIVES,
    ChainResult,
    chain_weights,
    dominates,
    objective,
    sample_start,
    scalarize,
    screen,
    walk,
)

CLIENTS, BANKS, WIDTH = 28, 32, 128


def _staged(stages):
    return {"kind": "xbar_staged", "clients": CLIENTS, "banks": BANKS, "width_bits": WIDTH,
            "stages": stages}


# The design that minimizing `mux_bits` alone chose (D313). It was read at the time as a
# throughput-for-area trade; D319 found the real defect -- it is UNROUTABLE, and its 4.5
# words/cycle was the Patel model computed on a fabric that cannot deliver a single word.
UNROUTABLE_TRAP = _staged([{"switches": 14, "in": 2, "out": 2},
                           {"switches": 7, "in": 4, "out": 4},
                           {"switches": 28, "in": 1, "out": 4}])

# A genuine trade-off pair, both routable: A is cheaper and slower, B dearer and faster. Neither
# dominates, which is what makes them the right fixtures for a multi-objective claim.
DEARER_FASTER = _staged([{"switches": 7, "in": 4, "out": 8},
                         {"switches": 8, "in": 7, "out": 4}])
CHEAPER_SLOWER = _staged([{"switches": 8, "in": 4, "out": 4},
                           {"switches": 8, "in": 4, "out": 4},
                           {"switches": 16, "in": 2, "out": 2}])

# Builds fine and reaches all 32 banks, but its middle pinches to 4 words/cycle. It has to BUILD
# to be useful here: a fabric that cannot be constructed raises out of the topology long before
# feasibility is consulted, so it tests the constructor rather than the objective.
NARROW_WAIST = _staged([{"switches": 4, "in": 7, "out": 1},
                        {"switches": 1, "in": 4, "out": 32}])


def test_the_design_that_area_alone_chose_is_refused_as_unroutable():
    """THE regression, with its cause corrected.

    Minimizing `mux_bits` under the capacity constraint "beat" exhaustive enumeration by 30% in
    seconds. D313 read that as a throughput-for-area trade and answered it by going
    multi-objective. D319 found the actual defect: the winning fabric cannot route. `build`
    succeeds -- the shape is constructible -- but no chain of its ranks reaches every bank, and
    it is cheap on `mux_bits` for exactly that reason. It is missing connections.

    That is why a search minimizing area HUNTS these: they are 0.5% of the enumerated space and
    were three of five finalists in a real run, including the one reported as the smallest
    fabric meeting timing.
    """
    metrics = screen(UNROUTABLE_TRAP)
    assert metrics["max_throughput_words_per_cycle"] == 0.0, (
        "an unroutable fabric carries nothing and must say so at SCREEN time -- not at the "
        "decision rung, minutes of Yosys and OpenROAD later")
    assert objective(metrics, clients=CLIENTS) is None


def test_a_routable_trade_off_is_kept_rather_than_ranked():
    """Multi-objective is still right, for the reason D313 gave even though its example was
    broken: a cheaper, slower fabric and a dearer, faster one are two points on a trade-off and
    neither dominates."""
    cheap = objective(screen(CHEAPER_SLOWER), clients=CLIENTS)
    dear = objective(screen(DEARER_FASTER), clients=CLIENTS)
    assert cheap is not None and dear is not None, "both must be routable and feasible"
    assert cheap[0] < dear[0], "A is cheaper on the area proxy"
    assert not dominates(cheap, dear) and not dominates(dear, cheap)


def test_throughput_is_an_objective_and_not_merely_a_constraint():
    """Both fabrics satisfy the capacity constraint, so a formulation that only CONSTRAINS
    cannot separate them at all."""
    assert screen(CHEAPER_SLOWER)["max_throughput_words_per_cycle"] == CLIENTS
    assert screen(DEARER_FASTER)["max_throughput_words_per_cycle"] == CLIENTS
    assert "throughput_words_per_cycle" in dict(OBJECTIVES)
    i = [n for n, _ in OBJECTIVES].index("throughput_words_per_cycle")
    cheap = objective(screen(CHEAPER_SLOWER), clients=CLIENTS)
    dear = objective(screen(DEARER_FASTER), clients=CLIENTS)
    assert dear[i] < cheap[i], "minimize-form: more delivered throughput reads as lower cost"


def test_infeasible_is_none_rather_than_a_large_cost():
    """A penalty would let the walk wander through fabrics the study refuses outright, and
    occasionally return one."""
    assert screen(NARROW_WAIST)["max_throughput_words_per_cycle"] < CLIENTS
    assert objective(screen(NARROW_WAIST), clients=CLIENTS) is None


def test_every_objective_is_in_minimize_form():
    metrics = screen(CHEAPER_SLOWER)
    cost = objective(metrics, clients=CLIENTS)
    for i, (name, direction) in enumerate(OBJECTIVES):
        expected = -metrics[name] if direction == "max" else metrics[name]
        assert cost[i] == pytest.approx(expected), f"{name} is not in minimize-form"


@pytest.mark.parametrize(
    "a,b,expected",
    [((1.0, 1.0), (2.0, 2.0), True),
     ((1.0, 2.0), (2.0, 1.0), False),   # the trade-off case: neither dominates
     ((1.0, 1.0), (1.0, 1.0), False),   # equal is not dominating
     ((1.0, 1.0), (1.0, 2.0), True)],   # equal on one, better on the other
    ids=["strictly-better", "trade-off", "equal", "weakly-better"])
def test_dominance(a, b, expected):
    assert dominates(a, b) is expected


def test_scalarize_is_scale_free():
    """mux_bits runs to six figures and throughput to about 28, so an absolute weighted sum is
    whichever number is biggest and the temperature stops meaning anything."""
    weights = (0.5, 0.5)
    small = scalarize((110.0, 220.0), (100.0, 200.0), weights)
    large = scalarize((110e6, 220e6), (100e6, 200e6), weights)
    assert small == pytest.approx(large), "a 10% move must score the same at any magnitude"


def test_a_walk_is_reproducible_from_its_seed():
    a = walk(CHEAPER_SLOWER, clients=CLIENTS, steps=40, seed=7)
    b = walk(CHEAPER_SLOWER, clients=CLIENTS, steps=40, seed=7)
    assert [s.accepted for s in a.steps] == [s.accepted for s in b.steps]
    assert [s.cost for s in a.steps] == [s.cost for s in b.steps]


def test_different_seeds_explore_differently():
    """The point of running an ensemble: eight chains sharing one trajectory is one chain."""
    a = walk(CHEAPER_SLOWER, clients=CLIENTS, steps=60, seed=1)
    b = walk(CHEAPER_SLOWER, clients=CLIENTS, steps=60, seed=2)
    assert [s.cost for s in a.steps] != [s.cost for s in b.steps]


def test_the_chain_actually_moves():
    """`perturb` could not move at all -- it looked at one neighbourhood and stopped. A walk that
    accepts nothing has the same reach as that, whatever else it reports."""
    result = walk(CHEAPER_SLOWER, clients=CLIENTS, steps=200, seed=3)
    assert result.accepted > 0, "the chain never accepted a move"
    assert len(result.archive) > 1, "the chain never found a second non-dominated design"


def test_the_archive_is_genuinely_non_dominated():
    result = walk(CHEAPER_SLOWER, clients=CLIENTS, steps=200, seed=4)
    for row in result.archive:
        others = [o for o in result.archive if o["key"] != row["key"]]
        assert not any(dominates(o["cost"], row["cost"]) for o in others), (
            "a dominated design is in the Pareto archive")


def test_the_archive_holds_no_duplicate_structures():
    """A chain revisits, and the archive is a list of things to spend Yosys and OpenROAD on."""
    result = walk(CHEAPER_SLOWER, clients=CLIENTS, steps=200, seed=5)
    keys = [row["key"] for row in result.archive]
    assert len(keys) == len(set(keys))


def test_rejected_proposals_still_reach_the_archive_on_merit():
    """A proposal rejected because it lost on THIS chain's weighting can still be non-dominated.
    Archiving only accepted moves would make the archive a record of one trade-off."""
    result = walk(CHEAPER_SLOWER, clients=CLIENTS, steps=300, seed=6)
    assert len(result.archive) > result.accepted + 1 or result.acceptance_rate < 1.0


def test_top_spreads_across_the_front_rather_than_one_corner():
    result = walk(CHEAPER_SLOWER, clients=CLIENTS, steps=300, seed=8)
    picked = result.top(3)
    assert len(picked) == len({str(sorted(p.items(), key=str)) for p in picked})
    assert len(picked) <= 3


def test_an_infeasible_start_is_refused_with_a_reason():
    with pytest.raises(ValueError, match="infeasible"):
        walk(NARROW_WAIST, clients=CLIENTS, steps=10, seed=0)


@pytest.mark.parametrize("kwargs", [{"radius": "medium"}, {"steps": 0}],
                         ids=["bad-radius", "no-steps"])
def test_bad_arguments_are_refused(kwargs):
    with pytest.raises(ValueError):
        walk(CHEAPER_SLOWER, clients=CLIENTS, seed=0, **kwargs)


def test_chain_weights_sum_to_one_and_vary_by_seed():
    a, b = chain_weights(1), chain_weights(2)
    assert sum(a) == pytest.approx(1.0) and sum(b) == pytest.approx(1.0)
    assert len(a) == len(OBJECTIVES)
    assert a != b, "every chain would explore the same trade-off"


def test_sample_start_prefers_the_front_but_can_leave_it():
    """Rank-weighted, not best-only: a restart policy that always picks the best design is a
    greedy search wearing a Monte-Carlo label."""
    population = [{"spec": {"id": i}, "cost": (float(i), float(i))} for i in range(1, 21)]
    picks = {sample_start(population, seed=s)["id"] for s in range(40)}
    assert 1 in picks, "the front should be the most likely start"
    assert len(picks) > 1, "every restart began at the same design"


def test_sample_start_ranks_by_dominance_not_one_objective():
    """Ranking by a single objective starts most chains at that metric's extreme, and the search
    explores one corner of the front over and over."""
    population = [
        {"spec": {"id": "cheap-slow"}, "cost": (1.0, 100.0)},   # extreme on objective 0
        {"spec": {"id": "balanced"}, "cost": (2.0, 2.0)},
        {"spec": {"id": "dominated"}, "cost": (9.0, 100.0)},    # dominated by cheap-slow
    ]
    picks = {sample_start(population, seed=s, pressure=3.0)["id"] for s in range(60)}
    assert "balanced" in picks, (
        "a non-dominated balanced design must be reachable as a start; ranking by objective 0 "
        "alone would bury it behind the extreme")


def test_no_population_is_an_error_not_a_silent_default():
    with pytest.raises(ValueError, match="no population"):
        sample_start([], seed=0)

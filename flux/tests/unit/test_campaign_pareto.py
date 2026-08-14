"""CI-aware multi-objective dominance (docs/decisions.md D218).

Synthetic Results are legitimate here — the claims are about the campaign's own comparison
arithmetic, not about any evaluator. The one claim that touches other code — single-objective
contender equivalence — calls the REAL `flux_search_architecture.dse.contenders()` on identical
inputs rather than restating its rule.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from flux_evaluator_abi import (
    Bottleneck,
    Domain,
    Escalation,
    Estimate,
    Limiter,
    Method,
    Provenance,
    Result,
    Validity,
)
from flux_search_campaign import parse_objective
from flux_search_campaign.pareto import (
    compare_metric,
    frontier_contenders,
    interval_dominates,
    pareto_frontier,
    point_dominates,
    weighted_scalar,
    BETTER,
    WORSE,
    UNRESOLVED,
)


def _result(**metrics_spec) -> Result:
    """metrics_spec: name=(value,) for a point estimate or name=(lo, value, hi)."""
    metrics = {}
    for name, spec in metrics_spec.items():
        if len(spec) == 1:
            lo = value = hi = spec[0]
        else:
            lo, value, hi = spec
        metrics[name] = Estimate(value=value, ci_low=lo, ci_high=hi, unit="x", method=Method.ANALYTIC)
    return Result(
        metrics=metrics,
        validity=Validity(ok=True, checker_version="test"),
        domain=Domain(in_domain=False),
        bottleneck=Bottleneck(limiter=Limiter.COMPUTE),
        provenance=Provenance(evaluator="test@0", inputs={}),
        escalation=Escalation(recommended=False),
    )


@dataclass
class _Trial:
    name: str
    result: Result


def _objective(mode="pareto", metrics=None, weights=None) -> "Objective":
    entries = []
    for i, (m, direction) in enumerate(metrics or [("latency_cycles", "minimize"), ("energy_pj", "minimize")]):
        e = {"metric": m, "direction": direction}
        if weights:
            e["weight"] = weights[i]
        entries.append(e)
    return parse_objective(
        {
            "schema_version": "0.1.0",
            "id": "t/v1",
            "objectives": entries,
            "mode": mode,
            "workload": {"ref": "w"},
            "base_arch": {"ref": "a"},
            "backends": {"screening": "zigzag"},
            "search": {"kind": "architecture_width", "widths": [4, 8]},
            "strategy": {"kind": "grid"},
            "budget": {"evaluations": 8},
        }
    )


OBJ = _objective()
LAT = OBJ.metrics[0]


def test_point_estimates_give_classic_dominance():
    a = _Trial("a", _result(latency_cycles=(10,), energy_pj=(10,)))
    b = _Trial("b", _result(latency_cycles=(20,), energy_pj=(20,)))
    tied = _Trial("t", _result(latency_cycles=(10,), energy_pj=(30,)))

    assert point_dominates(a.result, b.result, OBJ.metrics)
    assert not point_dominates(b.result, a.result, OBJ.metrics)
    # equal on one metric, worse on the other: dominated; equal on all: neither
    assert point_dominates(a.result, tied.result, OBJ.metrics)
    assert not point_dominates(a.result, a.result, OBJ.metrics)

    assert [t.name for t in pareto_frontier([a, b, tied], OBJ)] == ["a"]


def test_overlapping_intervals_never_eliminate():
    """The operational meaning of D105's 'screening data cannot rule out': an unresolved ranking
    keeps the candidate."""
    a = _Trial("a", _result(latency_cycles=(8, 10, 12), energy_pj=(10,)))
    b = _Trial("b", _result(latency_cycles=(11, 14, 17), energy_pj=(10,)))

    assert compare_metric(a.result, b.result, LAT) == UNRESOLVED
    assert not interval_dominates(a.result, b.result, OBJ.metrics)
    contenders = frontier_contenders([a, b], OBJ)
    assert {t.name for t in contenders} == {"a", "b"}
    # but the point frontier is crisp
    assert [t.name for t in pareto_frontier([a, b], OBJ)] == ["a"]


def test_disjoint_intervals_do_eliminate():
    a = _Trial("a", _result(latency_cycles=(8, 10, 12), energy_pj=(10,)))
    b = _Trial("b", _result(latency_cycles=(13, 15, 17), energy_pj=(10,)))
    assert compare_metric(a.result, b.result, LAT) == BETTER
    assert compare_metric(b.result, a.result, LAT) == WORSE
    # energy is an exact tie (UNRESOLVED), latency strictly better, never point-worse
    assert interval_dominates(a.result, b.result, OBJ.metrics)
    assert [t.name for t in frontier_contenders([a, b], OBJ)] == ["a"]  # only a survives


def test_maximize_orientation():
    obj = _objective(metrics=[("throughput", "maximize")])
    hi = _Trial("hi", _result(throughput=(90, 100, 110)))
    lo = _Trial("lo", _result(throughput=(40, 50, 60)))
    assert compare_metric(hi.result, lo.result, obj.metrics[0]) == BETTER
    assert point_dominates(hi.result, lo.result, obj.metrics)
    assert [t.name for t in pareto_frontier([hi, lo], obj)] == ["hi"]


def test_interval_better_on_one_metric_cannot_erase_a_point_win_on_another():
    """The conservative clause D218 adds over textbook interval dominance: g beats f cleanly on
    latency, but f's energy point value wins inside overlapping intervals — g must NOT eliminate
    f, because buying only g would discard the candidate the energy data actually favours."""
    f = _Trial("f", _result(latency_cycles=(20, 22, 24), energy_pj=(8, 10, 12)))
    g = _Trial("g", _result(latency_cycles=(10, 12, 14), energy_pj=(9, 11, 13)))

    assert compare_metric(g.result, f.result, LAT) == BETTER
    assert not interval_dominates(g.result, f.result, OBJ.metrics)
    assert {t.name for t in frontier_contenders([f, g], OBJ)} == {"f", "g"}
    # frontier (point values) keeps g only... both: f point-wins energy, g point-wins latency
    assert {t.name for t in pareto_frontier([f, g], OBJ)} == {"f", "g"}


def test_contenders_is_always_a_superset_of_the_frontier():
    trials = [
        _Trial(f"t{i}", _result(latency_cycles=(10 + 3 * i, 12 + 3 * i, 14 + 3 * i),
                                energy_pj=(30 - 4 * i, 32 - 4 * i, 34 - 4 * i)))
        for i in range(5)
    ]
    frontier = {t.name for t in pareto_frontier(trials, OBJ)}
    contenders = {t.name for t in frontier_contenders(trials, OBJ)}
    assert frontier <= contenders


def test_single_objective_contenders_matches_the_real_dse_function():
    """Membership equivalence against the REAL `contenders()` — imported and called, not
    restated — on uniformly-CI'd inputs. If the two rules drift where they are meant to agree,
    this fails naming the trial."""
    from flux_search_architecture.dse import SweepPoint, contenders as dse_contenders

    obj = _objective(metrics=[("latency_cycles", "minimize")])
    specs = {
        "leader": (90, 100, 110),
        "overlaps_leader": (105, 120, 135),
        "disjoint_worse": (140, 150, 160),
        "chain_overlap": (130, 145, 155),  # overlaps disjoint_worse, not the leader
    }
    points = [SweepPoint(candidate=name, result=_result(latency_cycles=spec), error=None)
              for name, spec in specs.items()]

    expected = {p.candidate for p in dse_contenders(points, "latency_cycles", minimize=True)}
    got = {p.candidate for p in frontier_contenders(points, obj)}
    assert got == expected, f"diverged from dse.contenders(): {sorted(got)} != {sorted(expected)}"
    # Guards the guard: the input must actually exercise both elimination and retention.
    assert "disjoint_worse" not in expected and "overlaps_leader" in expected


def test_contenders_is_at_most_dse_and_tighter_exactly_where_a_point_separates():
    """The one deliberate divergence (docs/decisions.md D218): `dse.contenders()` is
    leader-relative, so a point estimate that is not the point-minimum never eliminates anyone.
    With leader=[90,110], a point trial at exactly 100 strictly separates from [105,135] — that
    candidate's true value can never beat 100, and the campaign rule drops it where dse keeps it.
    Tightening is safe in exactly one direction: campaign contenders are always a SUBSET of
    dse's, never a superset — asserted on both the divergent and the agreeing inputs."""
    from flux_search_architecture.dse import SweepPoint, contenders as dse_contenders

    obj = _objective(metrics=[("latency_cycles", "minimize")])
    specs = {
        "leader": (90, 100, 110),
        "overlaps_leader": (105, 120, 135),
        "point_equal_leader": (100, 100, 100),
        "disjoint_worse": (140, 150, 160),
    }
    points = [SweepPoint(candidate=name, result=_result(latency_cycles=spec), error=None)
              for name, spec in specs.items()]

    dse_set = {p.candidate for p in dse_contenders(points, "latency_cycles", minimize=True)}
    ours = {p.candidate for p in frontier_contenders(points, obj)}
    assert ours <= dse_set
    # the divergence, pinned precisely: dse keeps the hopeless candidate, the campaign does not
    assert "overlaps_leader" in dse_set and "overlaps_leader" not in ours


def test_weighted_scalar_interval_arithmetic():
    obj = _objective(mode="weighted", weights=[2.0, 1.0])
    r = _result(latency_cycles=(10, 12, 14), energy_pj=(100, 110, 120))
    lo, value, hi = weighted_scalar(r, obj)
    assert (lo, value, hi) == (2 * 10 + 100, 2 * 12 + 110, 2 * 14 + 120)


def test_weighted_scalar_orients_maximize_before_weighting():
    obj = _objective(mode="weighted", metrics=[("throughput", "maximize")], weights=[3.0])
    r = _result(throughput=(90, 100, 110))
    lo, value, hi = weighted_scalar(r, obj)
    assert (lo, value, hi) == (-330.0, -300.0, -270.0)  # oriented: lower is better


def test_weighted_mode_picks_one_winner_where_pareto_keeps_both_and_weights_decide_it():
    """The differentiation that was silently missing (docs/decisions.md D221): a genuine
    trade-off keeps both trials on the pareto frontier, while weighted mode picks the one the
    weights favour — and flipping the weights flips the winner, which is what proves the weights
    are load-bearing rather than decorative."""
    fast_hot = _Trial("fast_hot", _result(latency_cycles=(10,), energy_pj=(100,)))
    slow_cool = _Trial("slow_cool", _result(latency_cycles=(20,), energy_pj=(10,)))

    pareto_obj = _objective()
    assert {t.name for t in pareto_frontier([fast_hot, slow_cool], pareto_obj)} == {
        "fast_hot", "slow_cool"
    }

    latency_heavy = _objective(mode="weighted", weights=[10.0, 0.1])
    assert [t.name for t in pareto_frontier([fast_hot, slow_cool], latency_heavy)] == ["fast_hot"]

    energy_heavy = _objective(mode="weighted", weights=[0.1, 10.0])
    assert [t.name for t in pareto_frontier([fast_hot, slow_cool], energy_heavy)] == ["slow_cool"]


def test_weighted_contenders_matches_dse_on_a_single_unit_weight_metric():
    """weight=1.0 on one metric makes the scalar the metric itself, so weighted contenders must
    reproduce the real `dse.contenders()` membership — called, not restated."""
    from flux_search_architecture.dse import SweepPoint, contenders as dse_contenders

    obj = _objective(mode="weighted", metrics=[("latency_cycles", "minimize")], weights=[1.0])
    specs = {
        "leader": (90, 100, 110),
        "overlaps_leader": (105, 120, 135),
        "disjoint_worse": (140, 150, 160),
        "chain_overlap": (130, 145, 155),
    }
    points = [SweepPoint(candidate=name, result=_result(latency_cycles=spec), error=None)
              for name, spec in specs.items()]

    expected = {p.candidate for p in dse_contenders(points, "latency_cycles", minimize=True)}
    got = {p.candidate for p in frontier_contenders(points, obj)}
    assert got == expected == {"leader", "overlaps_leader"}


def test_weighted_ties_all_stay_on_the_frontier():
    a = _Trial("a", _result(latency_cycles=(10,), energy_pj=(20,)))
    b = _Trial("b", _result(latency_cycles=(20,), energy_pj=(10,)))  # same scalar under equal weights
    obj = _objective(mode="weighted", weights=[1.0, 1.0])
    assert {t.name for t in pareto_frontier([a, b], obj)} == {"a", "b"}


def test_pareto_mode_with_weights_is_refused_not_ignored():
    """Symmetric with grid-plus-llm_model: a field that validates but changes nothing misleads
    the reader about what ran."""
    from flux_search_campaign import InvalidObjectiveError

    with pytest.raises(InvalidObjectiveError, match="mode=pareto ignores weights"):
        _objective(mode="pareto", weights=[1.0, 2.0])


def test_generative_strategy_pairs_only_with_open_architecture():
    """docs/decisions.md D233: a generative strategy has no grid to walk, and an open space has
    nothing for grid/agentic to enumerate — both mismatches refuse at parse."""
    from flux_search_campaign import InvalidObjectiveError

    base = {
        "schema_version": "0.1.0", "id": "t/g/v1",
        "objectives": [{"metric": "latency_cycles", "direction": "minimize"}],
        "mode": "pareto", "workload": {"ref": "w"}, "base_arch": {"ref": "a"},
        "backends": {"screening": "zigzag"},
        "search": {"kind": "open_architecture"},
        "strategy": {"kind": "generative", "seed": 0},
        "budget": {"evaluations": 4},
    }
    parse_objective(dict(base))  # the valid pairing

    import copy

    bad = copy.deepcopy(base)
    bad["strategy"] = {"kind": "grid", "seed": 0}
    with pytest.raises(InvalidObjectiveError, match="open_architecture"):
        parse_objective(bad)

    bad = copy.deepcopy(base)
    bad["search"] = {"kind": "architecture_width", "widths": [4, 8]}
    with pytest.raises(InvalidObjectiveError, match="open_architecture"):
        parse_objective(bad)


def test_generative_structural_guard_and_fallback():
    """The guard keeps every candidate inside the screening backend's expressible space; the
    seeded mutation fallback produces a fresh, valid, unseen architecture deterministically."""
    import flux_ir
    from flux_search_campaign.strategies import GenerativeStrategy

    base = {
        "schema_version": "0.1.0", "id": "t-base/v1",
        "hierarchy": [
            {"level": "dram", "class": "memory", "attrs": {"size_kb": 1024}},
            {"level": "gbuf", "class": "memory", "attrs": {"size_kb": 64}},
            {"level": "pe", "class": "compute", "attrs": {"dims": {"X": 8}}},
        ],
    }
    obj = _objective(metrics=[("latency_cycles", "minimize")])

    class _NeverCalled:
        def propose(self, prompt):  # pragma: no cover - the guard tests never reach the LLM
            raise AssertionError("unexpected LLM call")

    s = GenerativeStrategy(obj, base, set(), _NeverCalled())

    import copy

    ok = copy.deepcopy(base); ok["id"] = "t-new/v1"
    ok["hierarchy"][2]["attrs"]["dims"]["X"] = 16
    s._validate(ok)  # structure preserved, knob changed: accepted

    bad = copy.deepcopy(base)
    bad["hierarchy"].append({"level": "l2", "class": "memory", "attrs": {"size_kb": 32}})
    with pytest.raises(ValueError, match="skeleton"):
        s._validate(bad)

    bad = copy.deepcopy(base)
    bad["hierarchy"][2]["attrs"]["dims"] = {"X": 4, "Y": 4}
    with pytest.raises(ValueError, match="dims keys"):
        s._validate(bad)

    # fallback: deterministic per seed, never repeats a seen hash, always schema-valid
    first = s._mutated_fallback()
    flux_ir.validate("architecture", first)
    s._seen_hashes.add(flux_ir.content_hash(first))
    second = s._mutated_fallback()
    assert flux_ir.content_hash(second) != flux_ir.content_hash(first)
    s2 = GenerativeStrategy(obj, base, set(), _NeverCalled())
    assert flux_ir.content_hash(s2._mutated_fallback()) == flux_ir.content_hash(first)

"""The prefetcher study end to end on the loop (docs/decisions.md D446), with ChampSim replaced
by a planted landscape.

The stages are real -- the seed pool, the climb, the reference at shipped defaults, the shrink,
the confirmation at full length, the report's drift and frontier lines -- and only the simulator
is not: `measure_batch` is injected (D349), so the whole policy chain runs in a second on a
machine with no traces. This is the test the study did not have: every earlier one covered a
stage in isolation, and the wiring between them was only ever exercised by a six-hour live run.
"""

from __future__ import annotations

import pytest
from flux_prefetcher.config import DEFAULT, BingoConfig, storage_bytes
from flux_prefetcher.objective import BENCHMARKS


def _backend(jobs: list[dict], parallelism: int = 1) -> list[dict]:
    """A planted landscape: speedup rises with the PHT and saturates, and a partner prefetcher
    is worth a little more than any knob. Deterministic, so the whole run is."""
    out = []
    for job in jobs:
        cfg: BingoConfig = job["config"]
        ipc = 1.0
        if job["types"]:                                  # [] is the no-prefetcher baseline
            ipc += 0.06 * (min(cfg.pht_size, 16384) / 16384.0) ** 0.5
            ipc += 0.004 * (cfg.ft_size / 256.0)
            ipc += 0.01 * (len(job["types"]) - 1)
        out.append({"ipc": ipc, "cycles": 1.0, "instructions": 1.0, "wall_clock_s": 0.0})
    return out


@pytest.fixture
def study(tmp_path, monkeypatch):
    import flux_evaluator_champsim_bingo.binary as binmod
    from flux_prefetcher import world

    binary = tmp_path / "pythia"
    binary.write_text("")
    monkeypatch.setattr(binmod, "resolve_binary", lambda b=None: binary)
    monkeypatch.setattr(world, "_resolve_traces",
                        lambda r: {b: tmp_path / f"{b}.gz" for b in BENCHMARKS}, raising=False)
    monkeypatch.setattr(world, "stage_traces", lambda t, log=None: t, raising=False)
    monkeypatch.setattr(world, "_fingerprint", lambda b: {"champsim": "planted"}, raising=False)
    return tmp_path


class _Result:
    """The old `PrefetcherResult`'s face over the world's `result(out)` dict, so the assertions
    below read as they did."""

    def __init__(self, d):
        self.__dict__.update(d)


def _run(tmp_path, **kw):
    from prefetcher_fixtures import run_study

    fields = dict(db=str(tmp_path / "p.db"), measurements=6, llm_round=0, compose_rounds=1,
                  tune_partners=0, stage=2, finalists=2, include_invented=False, workers=4)
    fields.update(kw)
    if "budget" in fields:
        fields["measurements"] = fields.pop("budget")
    if "decide_on_finalists" in fields:
        fields["finalists"] = fields.pop("decide_on_finalists")
    backend = fields.pop("measure_batch", _backend)
    return _Result(run_study(measure_batch=backend, **fields))


def test_the_study_searches_confirms_and_decides(study):
    out = _run(study)
    assert out.decision is not None and out.decision_score is not None
    assert out.provenance["confirmed_at_full_length"] >= 2, "the finalists were re-measured"
    assert out.incumbent_score is not None, "the incumbent is confirmed on the report's stage"
    assert out.stage1_best is not None and out.stage2_best is not None
    assert len(out.measured) > 6, "seeds and a climb"
    assert out.frontier and out.frontier == sorted(out.frontier, key=lambda p: p.storage_bytes)
    assert out.provenance["simulations_run"] > 0 and out.provenance["screen_instructions"]
    # the landscape rewards a bigger PHT, so the search must not return the shipped default
    assert out.decision_score.geomean_speedup >= out.incumbent_score.geomean_speedup
    assert any("frontier" in l for l in out.lessons)
    assert any("not comparable" in n for n in out.not_established), (
        "the shipped reference baseline was measured at other instruction counts")


def test_screen_only_decides_on_the_screen_and_says_the_numbers_are_not_quotable(study):
    """A screen ranks candidates; it does not measure a speedup. A run that confirmed nothing
    must decide on the screen AND say that every number below it is an ordering hint (D351)."""
    out = _run(study, screen_only=True, stage=1, compose_rounds=0)
    assert out.decision is not None and out.provenance["confirmed_at_full_length"] == 0
    assert any("nothing was confirmed at full length" in n for n in out.not_established)
    assert all(l.startswith("[") or "confirm" not in l for l in out.lessons)


def test_the_record_holds_the_search_and_seeds_the_next_run(study):
    from flux_prefetcher.measure import known_configs
    from flux_records import Records
    from prefetcher_fixtures import identity

    first = _run(study)
    rec = Records(str(study / "p.db"), objective=identity(first.problem, str(study / "p.db")),
                  log=lambda _m: None)
    assert rec.resumed
    known = known_configs(rec, stage="screen")
    assert known and known[0][1] > 1.0, "the screen rows read back as (config, geomean)"
    assert set(rec.stages()) >= {"screen", "confirm"}, "one stage vocabulary (D446)"
    # a resumed run is told what it already measured, and re-measures none of it
    again = _run(study)
    assert again.provenance["cache_hits"] > 0
    assert again.decision is not None


def test_the_storage_budget_keeps_the_search_inside_what_could_be_built(study):
    """The budget is applied BEFORE anything is simulated (D362): the pool and the climb's moves
    are filtered, so the budget never spends a six-minute measurement to refuse a design."""
    budget = storage_bytes(DEFAULT)
    out = _run(study, max_storage_bytes=budget, stage=1, compose_rounds=0)
    assert out.decision is not None
    assert storage_bytes(out.decision) <= budget
    assert all(m.storage_bytes <= budget for m in out.measured), (
        "nothing over the budget was ever simulated")


def test_the_gate_refuses_an_over_budget_design_that_reaches_it(study):
    """The backstop, exercised directly: a proposer (or a resumed record) can hand the loop a
    design the policy never filtered, and the loop's gate must refuse it unmeasured -- the
    incumbent and a shipped-default reference excepted, since they are denominators."""
    from flux_loop import LoopRequest, LoopState
    from prefetcher_fixtures import prefetcher_problem

    budget = storage_bytes(DEFAULT)
    big = DEFAULT.replace(pht_size=DEFAULT.pht_size * 8)
    assert storage_bytes(big) > budget
    problem = prefetcher_problem(max_storage_bytes=budget).world
    state = LoopState(request=LoopRequest(), say=lambda _m: None, proposer=None, feedback=None)
    for who, ok in (("llm", False), ("incumbent", True)):
        cand = problem._cand((big, ("bingo",), {}), who)
        verdict = problem.judge(problem.build(cand, None, state), cand, None, state)
        assert verdict.ok is ok
        if not ok:
            assert "over the storage budget" in verdict.why
    illegal = problem._cand((DEFAULT.replace(pht_ways=DEFAULT.pht_size * 4), ("bingo",), {}),
                            "llm")
    assert not problem.judge(problem.build(illegal, None, state), illegal, None, state).ok


def test_a_simulator_that_fails_every_candidate_reports_no_result(study):
    """A crash is a measurement, not an error (D349) -- but a run where every candidate crashed
    has no answer, and must say so instead of deciding over nothing."""
    def only_the_baseline(jobs, parallelism=1):
        return [{"ipc": 1.0} if not job["types"] else {"error": "champsim exited 1"}
                for job in jobs]

    out = _run(study, db=str(study / "crash.db"), measurements=4, llm_round=0,
               compose_rounds=0, tune_partners=0, stage=1, measure_batch=only_the_baseline)
    assert out.decision is None and not out.measured
    assert any("measured nothing successfully" in n for n in out.not_established)
    assert out.refused and all("champsim exited 1" in why for _l, why in out.refused)


def test_a_baseline_that_cannot_be_measured_stops_the_run(study):
    """Every speedup divides by the baseline, so a run that could not measure one stops here
    rather than continuing against a denominator it had to invent."""
    from flux_prefetcher.objective import IncompleteMeasurement

    with pytest.raises(IncompleteMeasurement):
        _run(study, db=str(study / "nobase.db"),
             measure_batch=lambda jobs, parallelism=1: [{"error": "no trace"} for _ in jobs])

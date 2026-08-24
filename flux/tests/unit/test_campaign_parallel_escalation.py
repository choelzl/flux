"""Concurrent escalation (docs/decisions.md D290): the measured rung's tools are single-threaded
processes, so escalating one contender at a time leaves most of a machine idle. The runner may
measure a rung's contenders together, and every property of the serial path has to survive it —
the same results, the same durable trial log, and per-candidate error isolation.

Synthetic evaluators throughout: the claims here are about the loop's bookkeeping and its
concurrency, not about any real tool. Whether OpenROAD is genuinely faster in parallel is a
property of the host, measured elsewhere.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
import yaml
from flux_evaluator_abi import (
    Bottleneck,
    Budget,
    Candidate,
    Domain,
    Escalation,
    Estimate,
    Limiter,
    Method,
    Provenance,
    Result,
    Validity,
)
from flux_search_campaign import parse_objective, run_campaign_steps
from flux_store import CampaignStore

FLUX_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def base_arch():
    return yaml.safe_load(
        (FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml").read_text())


def _result(latency: float, area: float, energy: float) -> Result:
    def est(v, unit):
        return Estimate(value=v, ci_low=v, ci_high=v, unit=unit, method=Method.ANALYTIC)

    return Result(
        metrics={"latency_cycles": est(latency, "cycles"), "area_mm2": est(area, "mm2"),
                 "energy_pj": est(energy, "pJ")},
        validity=Validity(ok=True, checker_version="test"),
        domain=Domain(in_domain=True),
        bottleneck=Bottleneck(limiter=Limiter.COMPUTE),
        provenance=Provenance(evaluator="fake@1", inputs={}),
        escalation=Escalation(recommended=False),
    )


class _SlowRungEvaluator:
    """Sleeps per call, so wall-clock separates concurrent from serial. Records true overlap
    rather than inferring it from timing alone."""

    def __init__(self, delay: float = 0.25, poison_width: int | None = None) -> None:
        self.delay = delay
        self.poison_width = poison_width
        self.calls = 0
        self.live = 0
        self.max_live = 0
        self._lock = threading.Lock()

    def _width(self, candidate: Candidate) -> int:
        compute = next(n for n in candidate.arch["hierarchy"] if n["class"] == "compute")
        (width,) = compute["attrs"]["dims"].values()
        return width

    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset) -> Result:
        with self._lock:
            self.calls += 1
            self.live += 1
            self.max_live = max(self.max_live, self.live)
        try:
            time.sleep(self.delay)
            width = self._width(candidate)
            if width == self.poison_width:
                raise RuntimeError(f"poisoned width {width}")
            return _result(1024.0 / width, width / 1000.0, float(width))
        finally:
            with self._lock:
                self.live -= 1


class _ScreenEvaluator:
    """Instant screen that makes every width a contender. The two SCREEN-VISIBLE metrics have to
    disagree on order, or one candidate dominates and exactly one escalates — which is not a quirk
    of this fixture but the degeneracy D288 found in the interconnect demo itself: an objective
    measured only at escalation cannot separate anything at screen time. Latency falls with width
    and energy rises with it, so every width is on the screened front."""

    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset) -> Result:
        compute = next(n for n in candidate.arch["hierarchy"] if n["class"] == "compute")
        (width,) = compute["attrs"]["dims"].values()
        return _result(1024.0 / width, width / 1000.0, float(width))


def _doc(base_arch, widths, *, budget=64):
    return {
        "schema_version": "0.1.0",
        "id": "test/parallel-escalate/v1",
        "objectives": [
            {"metric": "latency_cycles", "direction": "minimize"},
            {"metric": "energy_pj", "direction": "minimize"},
            {"metric": "area_mm2", "direction": "minimize", "measured_at": "escalation"},
        ],
        "mode": "pareto",
        "workload": {"inline": {"schema_version": "0.1.0", "id": "w", "ops": [
            {"id": "op", "kind": "einsum", "expr": "B C, C K -> B K",
             "bounds": {"B": 4, "C": 32, "K": 32}}]}},
        "base_arch": {"inline": base_arch},
        "backends": {"screening": "fake", "escalation": ["fake_rung"]},
        "search": {"kind": "architecture_width", "widths": widths},
        "strategy": {"kind": "grid", "seed": 0},
        "budget": {"evaluations": budget},
    }


def _run(doc, tmp_path, name, screen, rung, **kwargs):
    objective = parse_objective(doc)
    store = CampaignStore(str(tmp_path / f"{name}.db"))
    cid, _ = store.start_campaign(doc, objective.objective_hash)
    run_campaign_steps(
        store, cid,
        make_evaluator=lambda n: rung if n == "fake_rung" else screen,
        **kwargs)
    return store, cid


def _escalated(store, cid):
    return {t.candidate_key: t.result.value_of("area_mm2")
            for t in store.ok_trials(cid, phase="escalate")}


def test_parallel_escalation_gives_the_same_results_as_serial(base_arch, tmp_path):
    widths = [1, 2, 4, 8]
    serial_rung = _SlowRungEvaluator(delay=0.05)
    par_rung = _SlowRungEvaluator(delay=0.05)
    s_store, s_cid = _run(_doc(base_arch, widths), tmp_path, "ser", _ScreenEvaluator(),
                          serial_rung)
    p_store, p_cid = _run(_doc(base_arch, widths), tmp_path, "par", _ScreenEvaluator(),
                          par_rung, escalation_parallelism=4)

    assert _escalated(s_store, s_cid) == _escalated(p_store, p_cid)
    assert len(_escalated(p_store, p_cid)) == len(widths)


def test_it_actually_runs_concurrently(base_arch, tmp_path):
    """The point of the change. Overlap is observed inside the evaluator rather than inferred
    from wall-clock, which would be flaky on a loaded machine."""
    rung = _SlowRungEvaluator(delay=0.2)
    _run(_doc(base_arch, [1, 2, 4, 8]), tmp_path, "conc", _ScreenEvaluator(), rung,
         escalation_parallelism=4)
    assert rung.calls == 4
    assert rung.max_live > 1, "escalations never overlapped"


def test_parallelism_one_keeps_the_serial_path_exactly(base_arch, tmp_path):
    rung = _SlowRungEvaluator(delay=0.01)
    _run(_doc(base_arch, [1, 2, 4, 8]), tmp_path, "one", _ScreenEvaluator(), rung,
         escalation_parallelism=1)
    assert rung.max_live == 1, "parallelism=1 must not overlap anything"


def test_one_failing_candidate_does_not_take_the_batch_with_it(base_arch, tmp_path):
    """Per-candidate isolation is the repo's posture everywhere else and batching must not
    quietly trade it away for speed."""
    rung = _SlowRungEvaluator(delay=0.02, poison_width=4)
    store, cid = _run(_doc(base_arch, [1, 2, 4, 8]), tmp_path, "iso", _ScreenEvaluator(), rung,
                      escalation_parallelism=4)
    ok = _escalated(store, cid)
    errors = [t for t in store.trials(cid, phase="escalate") if t.status == "error"]
    assert len(ok) == 3, "the three healthy candidates must still be measured"
    assert len(errors) == 1
    assert "poisoned width 4" in (errors[0].error or "")


def test_no_trial_is_left_running(base_arch, tmp_path):
    """Every intent row written before dispatch must be completed, including on the path where
    the budget stops the wave — otherwise a batch begun is abandoned and its rows never close."""
    rung = _SlowRungEvaluator(delay=0.01)
    store, cid = _run(_doc(base_arch, [1, 2, 4, 8], budget=6), tmp_path, "run",
                      _ScreenEvaluator(), rung, escalation_parallelism=4)
    left = [t for t in store.trials(cid, phase="escalate") if t.status == "running"]
    assert left == [], f"{len(left)} escalation trial(s) left running"

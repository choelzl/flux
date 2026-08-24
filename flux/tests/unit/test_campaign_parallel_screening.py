"""Batched grid screening (docs/decisions.md D238): the runner batches, the injected
evaluator's `evaluate_batch` decides concurrency, and every durability/budget/equivalence
property of the sequential path must survive batching. Synthetic evaluators — the claims are
about the loop's bookkeeping; the flows layer supplies real Ray dispatch."""

from __future__ import annotations

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


def _result(value: float) -> Result:
    return Result(
        metrics={"latency_cycles": Estimate(value=value, ci_low=value, ci_high=value,
                                            unit="cycles", method=Method.ANALYTIC)},
        validity=Validity(ok=True, checker_version="test"),
        domain=Domain(in_domain=True),
        bottleneck=Bottleneck(limiter=Limiter.COMPUTE),
        provenance=Provenance(evaluator="fake@1", inputs={}),
        escalation=Escalation(recommended=False),
    )


class _BatchRecordingEvaluator:
    """Deterministic per-width answers; records every batch size and every sequential call."""

    def __init__(self, fail_batch: bool = False, poison_width: int | None = None) -> None:
        self.batch_sizes: list[int] = []
        self.sequential_calls = 0
        self.fail_batch = fail_batch
        self.poison_width = poison_width

    def _width(self, candidate: Candidate) -> int:
        compute = next(n for n in candidate.arch["hierarchy"] if n["class"] == "compute")
        (width,) = compute["attrs"]["dims"].values()
        return width

    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset) -> Result:
        self.sequential_calls += 1
        width = self._width(candidate)
        if width == self.poison_width:
            raise RuntimeError(f"poisoned width {width}")
        return _result(1024.0 / width)

    def evaluate_batch(
        self, candidates: list[Candidate], budget: Budget, metrics: frozenset
    ) -> list[Result]:
        self.batch_sizes.append(len(candidates))
        if self.fail_batch:
            raise RuntimeError("batch transport failed")
        return [_result(1024.0 / self._width(c)) for c in candidates]


def _doc(base_arch, widths, *, strategy=None, budget=32, **overrides):
    doc = {
        "schema_version": "0.1.0",
        "id": "test/parallel-screen/v1",
        "objectives": [{"metric": "latency_cycles", "direction": "minimize"}],
        "mode": "pareto",
        "workload": {"inline": {"schema_version": "0.1.0", "id": "w", "ops": [
            {"id": "op", "kind": "einsum", "expr": "B C, C K -> B K",
             "bounds": {"B": 4, "C": 32, "K": 32}}]}},
        "base_arch": {"inline": base_arch},
        "backends": {"screening": "fake"},
        "search": {"kind": "architecture_width", "widths": widths},
        "strategy": strategy or {"kind": "grid", "seed": 0},
        "budget": {"evaluations": budget},
    }
    doc.update(overrides)
    return doc


def _run(doc, tmp_path, name, evaluator, **kwargs):
    objective = parse_objective(doc)
    store = CampaignStore(str(tmp_path / f"{name}.db"))
    cid, _ = store.start_campaign(doc, objective.objective_hash)
    report = run_campaign_steps(store, cid, make_evaluator=lambda _n: evaluator, **kwargs)
    return store, cid, report


def test_batched_screening_is_result_equivalent_to_sequential(base_arch, tmp_path):
    widths = [1, 2, 4, 8, 16, 32]
    seq_eval, par_eval = _BatchRecordingEvaluator(), _BatchRecordingEvaluator()
    seq_store, seq_cid, _ = _run(_doc(base_arch, widths), tmp_path, "seq", seq_eval)
    par_store, par_cid, _ = _run(_doc(base_arch, widths), tmp_path, "par", par_eval,
                                 screening_parallelism=4)

    def values(store, cid):
        return {t.candidate_key: t.result.value_of("latency_cycles")
                for t in store.ok_trials(cid, phase="screen")}

    assert values(seq_store, seq_cid) == values(par_store, par_cid)
    assert len(values(par_store, par_cid)) == 6
    # the batched run really batched: 6 candidates at parallelism 4 -> batches of 4 then 2,
    # and NO per-candidate sequential evaluation happened
    assert par_eval.batch_sizes == [4, 2]
    assert par_eval.sequential_calls == 0
    # the sequential run never touched the batch surface
    assert seq_eval.batch_sizes == []


def test_the_evaluations_budget_caps_the_batch(base_arch, tmp_path):
    """Running rows do not count as spent, so an uncapped batch would overshoot the grant —
    the cap is applied when the batch is sized."""
    evaluator = _BatchRecordingEvaluator()
    store, cid, report = _run(_doc(base_arch, [1, 2, 4, 8, 16, 32], budget=3),
                              tmp_path, "cap", evaluator, screening_parallelism=8)
    assert report.status == "budget_exhausted"
    assert evaluator.batch_sizes == [3]
    assert len(store.ok_trials(cid, phase="screen")) == 3


def test_agentic_campaigns_ignore_parallelism(base_arch, tmp_path):
    """An agentic proposal at step t+1 legally depends on step t's outcome — batching would
    change what the strategy is, so the parallelism is ignored, not misapplied."""

    class _PickFirst:
        def propose(self, prompt: str) -> str:
            import json

            line = prompt.split("Untried candidates")[1].splitlines()[1]
            return json.dumps(__import__("json").loads(line))

    evaluator = _BatchRecordingEvaluator()
    doc = _doc(base_arch, [1, 2, 4],
               strategy={"kind": "agentic", "seed": 0, "llm_model": "scripted"})
    store, cid, _ = _run(doc, tmp_path, "agentic", evaluator,
                         screening_parallelism=4, make_llm=lambda model: _PickFirst())
    assert len(store.ok_trials(cid, phase="screen")) == 3
    assert evaluator.batch_sizes == []  # sequential path throughout
    assert evaluator.sequential_calls == 3


def test_a_failed_batch_falls_back_to_isolated_sequential_evaluation(base_arch, tmp_path):
    """`evaluate_batch` has no per-candidate isolation, so its failure must not cost the whole
    batch: the runner falls back to per-candidate calls, keeps the good results, records the
    poisoned candidate as its own error trial, and says the fallback happened in the event
    log — never silently absorbed."""
    evaluator = _BatchRecordingEvaluator(fail_batch=True, poison_width=4)
    store, cid, report = _run(_doc(base_arch, [1, 2, 4, 8]), tmp_path, "fallback", evaluator,
                              screening_parallelism=4)
    assert report.status == "done"
    assert evaluator.batch_sizes == [4]  # the batch was attempted...
    assert evaluator.sequential_calls == 4  # ...then each candidate evaluated in isolation
    ok = store.ok_trials(cid, phase="screen")
    errors = store.trials(cid, phase="screen", status="error")
    assert len(ok) == 3 and len(errors) == 1
    assert "poisoned width 4" in errors[0].error
    assert any(e["kind"] == "batch_fallback" for e in store.events(cid))


def test_composed_batches_deduplicate_shared_engines_across_the_batch(base_arch, tmp_path):
    """docs/decisions.md D238 meets D236: one batch of composition candidates names each
    distinct (op, width) engine once in the single inner batch call — four assignments, eight
    component slots, four unique engines dispatched."""
    workload = {
        "schema_version": "0.1.0", "id": "test/two-layer", "ops": [
            {"id": "layer0", "kind": "einsum", "expr": "B C, C K -> B K",
             "bounds": {"B": 4, "C": 64, "K": 32}},
            {"id": "layer1", "kind": "einsum", "expr": "B C, C K -> B K",
             "bounds": {"B": 4, "C": 32, "K": 16}},
        ]}
    evaluator = _BatchRecordingEvaluator()
    doc = _doc(base_arch, [8, 16], workload={"inline": workload},
               search={"kind": "composition_width", "widths": [8, 16]})
    store, cid, _ = _run(doc, tmp_path, "composed-batch", evaluator,
                         screening_parallelism=4)
    assert len(store.ok_trials(cid, phase="screen")) == 4
    assert evaluator.batch_sizes == [4]  # 4 unique engines, one inner batch
    assert evaluator.sequential_calls == 0

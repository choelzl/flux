"""`flux replay` restores the stored mapping (D189).

Replaying without it re-evaluates a different design, and the difference reads as evaluator
non-determinism. A stub evaluator is used: the point is which candidate `cmd_replay` builds.
"""

from __future__ import annotations

import pytest
from flux_cli import commands as commands_module
from flux_cli.main import main
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
from flux_store import ResultStore

_WORKLOAD = {"schema_version": "0.1.0", "id": "test/wl", "ops": []}
_ARCH = {"schema_version": "0.1.0", "id": "test/arch"}
_MAPPING = {"schema_version": "0.1.0", "id": "test/mapping", "for_op": "op0"}


def _result(value: float) -> Result:
    return Result(
        metrics={"latency_cycles": Estimate(
            value=value, ci_low=value, ci_high=value, unit="cycles", method=Method.ANALYTIC)},
        validity=Validity(ok=True, checker_version="test"),
        domain=Domain(in_domain=False),
        bottleneck=Bottleneck(limiter=Limiter.COMPUTE),
        provenance=Provenance(evaluator="zigzag@3.8.5", inputs={}),
        escalation=Escalation(recommended=False),
    )


class _MappingSensitiveEvaluator:
    """Returns different numbers with and without a mapping, and records what it was handed."""

    def __init__(self):
        self.seen_mappings = []

    def evaluate(self, candidate, budget, metrics):
        self.seen_mappings.append(candidate.mapping)
        return _result(42.0 if candidate.mapping is not None else 999.0)

    def evaluate_batch(self, candidates, budget, metrics):
        return [self.evaluate(c, budget, metrics) for c in candidates]


@pytest.fixture
def stored(tmp_path):
    db = str(tmp_path / "flux.db")
    with ResultStore(db) as store:
        workload_hash = store.put_document("workload", _WORKLOAD)
        arch_hash = store.put_document("architecture", _ARCH)
        mapping_hash = store.put_document("mapping", _MAPPING)
        result_id = store.put_result(
            _result(42.0), workload_hash=workload_hash, arch_hash=arch_hash,
            mapping_hash=mapping_hash,
        )
    return db, result_id


def test_replay_rebuilds_the_candidate_with_its_stored_mapping(stored, monkeypatch, capsys):
    db, result_id = stored
    evaluator = _MappingSensitiveEvaluator()
    monkeypatch.setattr(commands_module, "make_evaluator", lambda name: evaluator)

    exit_code = main(["replay", str(result_id), "--store", db])

    assert evaluator.seen_mappings == [_MAPPING], (
        "replay must hand the evaluator the mapping it stored, not None"
    )
    assert exit_code == 0
    assert "all metrics match" in capsys.readouterr().out


def test_a_missing_mapping_document_is_refused_rather_than_replayed_without_it(
    tmp_path, monkeypatch, capsys
):
    """A dangling mapping_hash is an error, not a mapping-free replay."""
    db = str(tmp_path / "flux.db")
    with ResultStore(db) as store:
        workload_hash = store.put_document("workload", _WORKLOAD)
        arch_hash = store.put_document("architecture", _ARCH)
        result_id = store.put_result(
            _result(42.0), workload_hash=workload_hash, arch_hash=arch_hash,
            mapping_hash="0" * 64,  # never stored as a document
        )
    evaluator = _MappingSensitiveEvaluator()
    monkeypatch.setattr(commands_module, "make_evaluator", lambda name: evaluator)

    exit_code = main(["replay", str(result_id), "--store", db])

    assert exit_code == 1
    assert evaluator.seen_mappings == [], "nothing should have been evaluated"
    assert "would evaluate a different candidate" in capsys.readouterr().err


def test_a_result_stored_without_a_mapping_still_replays(stored, tmp_path, monkeypatch, capsys):
    """Control: the mapping-free case is untouched."""
    db = str(tmp_path / "nomap.db")
    with ResultStore(db) as store:
        workload_hash = store.put_document("workload", _WORKLOAD)
        arch_hash = store.put_document("architecture", _ARCH)
        result_id = store.put_result(
            _result(999.0), workload_hash=workload_hash, arch_hash=arch_hash, mapping_hash=None,
        )
    evaluator = _MappingSensitiveEvaluator()
    monkeypatch.setattr(commands_module, "make_evaluator", lambda name: evaluator)

    assert main(["replay", str(result_id), "--store", db]) == 0
    assert evaluator.seen_mappings == [None]

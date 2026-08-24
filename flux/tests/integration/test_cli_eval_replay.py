"""Integration tests for `flux eval` and `flux replay` (docs/roadmap.md Phase 1), exercised through
real ZigZag. Timeloop's Docker-backed path isn't re-tested here — the interesting new surface is
the CLI plumbing (store round-trip, replay comparison), not a third run of either adapter.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from flux_cli.main import main
from flux_store import ResultStore

logging.getLogger("zigzag").setLevel(logging.WARNING)

FLUX_ROOT = Path(__file__).resolve().parents[2]
GEMM_WORKLOAD = FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml"
SIMPLE_NPU_1D = FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml"


def test_eval_without_store_prints_result_json(capsys):
    exit_code = main(
        ["eval", "--workload", str(GEMM_WORKLOAD), "--backend", "zigzag"]
    )
    out = capsys.readouterr().out
    assert exit_code == 0

    result = json.loads(out)
    assert result["metrics"]["energy_pj"]["value"] == pytest.approx(113416.448, rel=1e-6)
    assert result["metrics"]["latency_cycles"]["value"] == pytest.approx(145.0)


def test_eval_with_arch_and_store_then_replay_round_trips(tmp_path, capsys):
    db_path = tmp_path / "flux.db"

    exit_code = main(
        [
            "eval",
            "--workload", str(GEMM_WORKLOAD),
            "--arch", str(SIMPLE_NPU_1D),
            "--backend", "zigzag",
            "--store", str(db_path),
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    result = json.loads(captured.out)
    assert result["metrics"]["latency_cycles"]["value"] == pytest.approx(1554.0)

    with ResultStore(db_path) as store:
        rows = store.find_results()
        assert len(rows) == 1
        result_id = rows[0]["id"]

    exit_code = main(["replay", str(result_id), "--store", str(db_path)])
    replay_out = capsys.readouterr().out
    assert exit_code == 0
    assert "energy_pj" in replay_out
    assert "[OK]" in replay_out
    assert "MISMATCH" not in replay_out
    assert "replay: all metrics match" in replay_out


def test_replay_unknown_result_id_fails_cleanly(tmp_path, capsys):
    db_path = tmp_path / "empty.db"
    with ResultStore(db_path):
        pass  # create an empty, valid store

    exit_code = main(["replay", "999", "--store", str(db_path)])
    err = capsys.readouterr().err
    assert exit_code == 1
    assert "no result with id=999" in err


def test_eval_unknown_backend_is_rejected_by_argparse():
    with pytest.raises(SystemExit) as exc_info:
        main(["eval", "--workload", str(GEMM_WORKLOAD), "--backend", "bogus"])
    assert exc_info.value.code == 2

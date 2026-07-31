"""Unit tests for `flux import` (docs/05.md Phase 1). Pure schema validation + hashing — no
evaluator backend involved, so these run without zigzag-dse/docker.
"""

from __future__ import annotations

from pathlib import Path

import flux_ir
import yaml
from flux_cli.main import main
from flux_store import ResultStore

FLUX_ROOT = Path(__file__).resolve().parents[2]
GEMM_WORKLOAD = FLUX_ROOT / "ir/workload/examples/mlp-gemm0.yaml"
SIMPLE_NPU_1D = FLUX_ROOT / "ir/architecture/examples/simple-npu-1d-v1.yaml"


def test_import_valid_workload_prints_kind_id_and_hash(capsys):
    exit_code = main(["import", str(GEMM_WORKLOAD)])

    out = capsys.readouterr().out
    doc = flux_ir.load_document(GEMM_WORKLOAD)
    assert exit_code == 0
    assert "kind: workload" in out
    assert f"id:   {doc['id']}" in out
    assert f"hash: {flux_ir.content_hash(doc)}" in out


def test_import_auto_detects_architecture_kind(capsys):
    exit_code = main(["import", str(SIMPLE_NPU_1D)])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "kind: architecture" in out


def test_import_rejects_wrong_explicit_kind(capsys):
    exit_code = main(["import", str(SIMPLE_NPU_1D), "--kind", "workload"])
    err = capsys.readouterr().err
    assert exit_code == 1
    assert "failed schema validation" in err


def test_import_rejects_undetectable_kind(tmp_path, capsys):
    ambiguous = tmp_path / "ambiguous.yaml"
    ambiguous.write_text(yaml.safe_dump({"id": "x", "schema_version": "0.1.0"}))

    exit_code = main(["import", str(ambiguous)])
    err = capsys.readouterr().err
    assert exit_code == 1
    assert "could not auto-detect" in err


def test_import_with_store_persists_the_document(tmp_path, capsys):
    db_path = tmp_path / "flux.db"
    exit_code = main(["import", str(GEMM_WORKLOAD), "--store", str(db_path)])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert f"stored in {db_path}" in out

    doc = flux_ir.load_document(GEMM_WORKLOAD)
    with ResultStore(db_path) as store:
        assert store.get_document(flux_ir.content_hash(doc)) == doc

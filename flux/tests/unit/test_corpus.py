"""Unit tests for flux_store.corpus: holdout-discipline enforcement (docs/04.md §8, docs/05.md
§3) using synthetic manifest files written to a temp dir, not the real corpus/ tree — see
tests/integration/test_holdout_generalization.py for a test against the real corpus/ and real
evaluators, formalizing docs/calibration-report.md's Finding 3.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from flux_store.corpus import (
    CorpusPartition,
    CorpusStore,
    DuplicateCorpusEntryError,
    HoldoutAccessError,
    load_corpus,
)


def _write_entry(corpus_root: Path, partition: str, entry_id: str) -> None:
    d = corpus_root / partition
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{entry_id}.yaml").write_text(
        f"id: {entry_id}\n"
        f"workload_path: ir/workload/examples/mlp-gemm0.yaml\n"
        f"arch_path: ir/architecture/examples/simple-npu-1d-v1.yaml\n"
        f"description: synthetic test entry\n"
    )


@pytest.fixture
def corpus_root(tmp_path):
    _write_entry(tmp_path, "public", "pub-a")
    _write_entry(tmp_path, "public", "pub-b")
    _write_entry(tmp_path, "holdout", "hold-a")
    return tmp_path


def test_load_corpus_tags_partition_by_directory(corpus_root):
    entries = load_corpus(corpus_root)
    by_id = {e.id: e for e in entries}
    assert by_id["pub-a"].partition is CorpusPartition.PUBLIC
    assert by_id["pub-b"].partition is CorpusPartition.PUBLIC
    assert by_id["hold-a"].partition is CorpusPartition.HOLDOUT


def test_load_corpus_rejects_duplicate_ids_across_partitions(tmp_path):
    _write_entry(tmp_path, "public", "same-id")
    _write_entry(tmp_path, "holdout", "same-id")
    with pytest.raises(DuplicateCorpusEntryError):
        load_corpus(tmp_path)


def test_public_entries_never_includes_holdout(corpus_root):
    store = CorpusStore(corpus_root)
    ids = {e.id for e in store.public_entries()}
    assert ids == {"pub-a", "pub-b"}
    assert "hold-a" not in ids


def test_all_entries_requires_explicit_acknowledgement(corpus_root):
    store = CorpusStore(corpus_root)
    with pytest.raises(HoldoutAccessError):
        store.all_entries(acknowledge_holdout_access=False)


def test_all_entries_omitted_argument_is_a_typeerror_not_a_silent_default(corpus_root):
    # No default value exists for acknowledge_holdout_access — Python itself refuses the call,
    # which is the actual enforcement mechanism, not just a runtime check inside the method.
    store = CorpusStore(corpus_root)
    with pytest.raises(TypeError):
        store.all_entries()  # type: ignore[call-arg]


def test_all_entries_with_explicit_acknowledgement_includes_holdout(corpus_root):
    store = CorpusStore(corpus_root)
    ids = {e.id for e in store.all_entries(acknowledge_holdout_access=True)}
    assert ids == {"pub-a", "pub-b", "hold-a"}

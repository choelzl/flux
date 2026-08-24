"""Unit tests for flux_store.corpus: holdout-discipline enforcement (docs/stores.md, docs/roadmap.md)
using synthetic manifest files written to a temp dir, not the real corpus/ tree — see
tests/unit/test_corpus_holdout_real.py for a test against the real corpus/ and real
evaluators, formalizing docs/calibration-report.md's Finding 3.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from flux_store.corpus import (
    CorpusPartition,
    CorpusRootError,
    CorpusStore,
    DuplicateCorpusEntryError,
    HoldoutAccessError,
    Objective,
    load_corpus,
)


def _write_entry(corpus_root: Path, partition: str, entry_id: str, *, objective: str = "") -> None:
    d = corpus_root / partition
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{entry_id}.yaml").write_text(
        f"id: {entry_id}\n"
        f"workload_path: ir/workload/examples/mlp-gemm0.yaml\n"
        f"arch_path: ir/architecture/examples/simple-npu-1d-v1.yaml\n"
        f"description: synthetic test entry\n"
        f"{objective}"
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


def test_corpus_entry_to_dict_is_json_safe_and_encodes_partition_as_a_string(corpus_root):
    import json

    entry = next(e for e in load_corpus(corpus_root) if e.id == "pub-a")
    d = entry.to_dict()
    json.dumps(d)  # raises if the CorpusPartition enum leaked through unconverted
    assert d["partition"] == "public"
    assert d["id"] == "pub-a"


def test_entry_without_an_objective_field_parses_with_objective_none(corpus_root):
    """docs/decisions.md D58: `objective` is optional, not required — an entry written before
    this field existed (every synthetic fixture in this file, and any real manifest not yet
    updated) must still load, just with `objective=None`, not raise a `KeyError`."""
    entry = next(e for e in load_corpus(corpus_root) if e.id == "pub-a")
    assert entry.objective is None


def test_entry_with_an_objective_field_parses_it(tmp_path):
    _write_entry(
        tmp_path, "public", "with-obj",
        objective="objective:\n  metric: latency_cycles\n  minimize: true\n",
    )
    entry = next(e for e in load_corpus(tmp_path) if e.id == "with-obj")
    assert entry.objective == Objective(metric="latency_cycles", minimize=True)


def test_objective_survives_to_dict_json_safely(tmp_path):
    import json

    _write_entry(
        tmp_path, "public", "with-obj",
        objective="objective:\n  metric: energy_pj\n  minimize: false\n",
    )
    entry = next(e for e in load_corpus(tmp_path) if e.id == "with-obj")
    d = entry.to_dict()
    json.dumps(d)
    assert d["objective"] == {"metric": "energy_pj", "minimize": False}


def test_entry_without_objective_to_dict_reports_none_not_a_missing_key(corpus_root):
    """A caller reading `to_dict()['objective']` should never need a `.get()`/`KeyError` guard —
    the key is always present, `None` when there's genuinely no objective declared."""
    entry = next(e for e in load_corpus(corpus_root) if e.id == "pub-a")
    d = entry.to_dict()
    assert "objective" in d
    assert d["objective"] is None


def test_a_nonexistent_corpus_root_is_an_error_not_an_empty_corpus(tmp_path):
    """`Path.glob` on a directory that doesn't exist yields nothing rather than raising, so a
    typo'd or mis-rooted path loaded silently as an empty corpus — `public_entries()` returned
    `[]`, indistinguishable from "this corpus genuinely has no public entries". `leaderboard.py`
    names exactly this anti-pattern in its own docstring while its data source practised it
    (docs/decisions.md D172).
    """
    with pytest.raises(CorpusRootError, match="does not exist"):
        load_corpus(tmp_path / "typo-not-a-real-path")


def test_a_directory_with_neither_partition_is_an_error(tmp_path):
    (tmp_path / "something-else").mkdir()

    with pytest.raises(CorpusRootError, match="neither a public/ nor a holdout/"):
        load_corpus(tmp_path)


def test_a_corpus_with_public_but_no_holdout_directory_is_fine(tmp_path):
    """An absent holdout partition is normal, not a misconfiguration — the guard must not
    over-reach into rejecting a legitimate corpus."""
    (tmp_path / "public").mkdir()

    assert load_corpus(tmp_path) == []


def test_a_duplicate_id_within_one_partition_names_that_partition(tmp_path):
    """The message said "declared in both public/ and public/" for a same-directory duplicate,
    which reads like a cross-partition conflict and sends the reader to the wrong file."""
    public = tmp_path / "public"
    public.mkdir()
    for name in ("a.yaml", "b.yaml"):
        (public / name).write_text(
            "id: same-id\nworkload_path: w.yaml\narch_path: a.yaml\ndescription: d\n"
        )

    with pytest.raises(DuplicateCorpusEntryError, match="twice in public/"):
        load_corpus(tmp_path)

"""Holdout discipline checked against the **real** `corpus/` directory, in the suite that runs on
every change (docs/decisions.md D123).

`tests/unit/test_corpus.py` already proves the mechanism on a synthetic corpus, and an integration
test enumerates the real public ids — but that enumeration went stale the moment a seventh entry
was added, and nothing noticed for the same reason D119 documents: the standard regression command
runs `tests/unit/` and `tests/conformance/` only.

So the property worth running everywhere is not the list, which changes legitimately whenever
someone adds a benchmark. It is the invariant: whatever is in `corpus/holdout/` must never be
reachable through the public surface. That is checked here, from the filesystem, so adding a
holdout entry cannot quietly become visible to a model.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_CORPUS_ROOT = Path(__file__).resolve().parents[2] / "mentor" / "benchmarks"


def _ids(partition: str) -> set[str]:
    directory = _CORPUS_ROOT / partition
    return {
        (yaml.safe_load(p.read_text()) or {}).get("id", p.stem)
        for p in sorted(directory.glob("*.yaml"))
    }


def test_the_real_corpus_has_both_partitions_populated():
    """Guards the guard: an empty or moved holdout directory would make every check below pass
    vacuously, which is the one way this file could lie."""
    assert (_CORPUS_ROOT / "public").is_dir() and (_CORPUS_ROOT / "holdout").is_dir()
    assert _ids("public"), "no public corpus entries found — has corpus/ moved?"
    assert _ids("holdout"), "no holdout entries found — this test would pass vacuously"


def test_no_holdout_entry_is_reachable_through_the_public_surface():
    from flux_store.corpus import CorpusStore

    public = {e.id for e in CorpusStore(str(_CORPUS_ROOT)).public_entries()}
    holdout = _ids("holdout")

    leaked = public & holdout
    assert not leaked, f"holdout entries visible on the public surface: {sorted(leaked)}"


def test_the_public_surface_matches_the_public_directory_exactly():
    """Both directions: a public entry silently dropped is a quieter bug than a leak, but it still
    means the surface and the repository disagree about what exists."""
    from flux_store.corpus import CorpusStore

    assert {e.id for e in CorpusStore(str(_CORPUS_ROOT)).public_entries()} == _ids("public")


def test_reaching_the_holdout_partition_still_takes_an_explicit_acknowledgement():
    from flux_store.corpus import CorpusStore

    store = CorpusStore(str(_CORPUS_ROOT))
    with pytest.raises(TypeError):
        store.all_entries()  # no default — Python itself refuses the call
    assert {e.id for e in store.all_entries(acknowledge_holdout_access=True)} == (
        _ids("public") | _ids("holdout")
    )

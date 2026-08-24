"""Holdout discipline checked against the real `corpus/` directory, in the unit suite (D123).

The invariant, not the list: whatever is in `corpus/holdout/` is never reachable through the
public surface. Checked from the filesystem, so a new holdout entry cannot quietly become visible.
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
    """Guards the guard: an empty or moved holdout directory would make every check vacuous."""
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
    """The public surface lists exactly the public entries on disk, none dropped."""
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

"""Retrieval against the real, ingested RISC-V corpus (knowledge/corpus/riscv-unpriv/, five hand-
picked chapters — see that directory's PROVENANCE.md for source/license). Not a synthetic test:
this builds the actual index this repo ships and checks it answers real questions sensibly.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from flux_knowledge import build_default_index, knowledge_lookup

FLUX_ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE_ROOT = FLUX_ROOT / "knowledge"


@pytest.fixture(scope="module")
def index():
    return build_default_index(KNOWLEDGE_ROOT, repo_root=FLUX_ROOT)


def test_index_is_built_from_all_five_hand_picked_chapters(index):
    stems = {Path(c.source_path).stem for c in index._chunks}  # noqa: SLF001 - test-only introspection
    assert stems == {"preface", "naming", "zicsr", "m-st-ext", "zifencei"}


def test_index_has_a_realistic_number_of_chunks(index):
    # Not pinned to an exact count (re-fetching upstream at a different pinned commit could
    # shift paragraph boundaries slightly) — just a sanity band around what ingesting these five
    # real files actually produces (~180-220 as of the commit in PROVENANCE.md).
    assert 100 < len(index) < 400


def test_lookup_fence_i_finds_the_zifencei_chapter(index):
    results = knowledge_lookup("what does the fence.i instruction synchronize", k=5, index=index)
    assert results, "expected at least one match"
    assert any(r.chunk.standard_id == "riscv-unpriv" for r in results)
    assert any("fence.i" in r.chunk.text for r in results)


def test_lookup_csr_finds_the_zicsr_chapter(index):
    results = knowledge_lookup("control and status register CSR instructions", k=5, index=index)
    assert results
    top_sources = {Path(r.chunk.source_path).stem for r in results}
    assert "zicsr" in top_sources


def test_lookup_multiplication_finds_the_m_extension_chapter(index):
    results = knowledge_lookup("integer multiplication and division extension", k=5, index=index)
    assert results
    top_sources = {Path(r.chunk.source_path).stem for r in results}
    assert "m-st-ext" in top_sources


def test_lookup_restricted_to_standard_id_only_returns_that_standard(index):
    results = knowledge_lookup("instruction", standard_id="riscv-unpriv", k=10, index=index)
    assert results
    assert all(r.chunk.standard_id == "riscv-unpriv" for r in results)


def test_lookup_restricted_to_a_nonexistent_standard_returns_nothing(index):
    results = knowledge_lookup("fence.i instruction", standard_id="amba-axi4", k=5, index=index)
    assert results == []


def test_every_chunk_has_real_provenance(index):
    for chunk in index._chunks:  # noqa: SLF001 - test-only introspection
        assert chunk.source_path.startswith("mentor/knowledge/corpus/riscv-unpriv/")
        assert chunk.text.strip()

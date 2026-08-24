"""Unit tests for flux_knowledge.connectors.adoc: parsing synthetic AsciiDoc snippets covering
the specific markup constructs knowledge/corpus/riscv-unpriv/'s real files use (see that
directory's PROVENANCE.md). Not testing against the real corpus here — see
tests/integration/test_knowledge_riscv_corpus.py for that.
"""

from __future__ import annotations

from pathlib import Path

from flux_knowledge.connectors.adoc import (
    ingest_adoc_directory,
    ingest_adoc_file,
    parse_adoc,
)


def test_heading_and_paragraph_are_captured():
    text = "=== A Heading\nSome paragraph text.\n"
    paras = parse_adoc(text)
    assert paras == [("A Heading", "Some paragraph text.")]


def test_blank_line_separates_paragraphs_under_the_same_heading():
    text = "=== H\nFirst paragraph.\n\nSecond paragraph.\n"
    paras = parse_adoc(text)
    assert paras == [("H", "First paragraph."), ("H", "Second paragraph.")]


def test_consecutive_non_blank_lines_join_into_one_paragraph():
    text = "=== H\nLine one\nline two continues it.\n"
    paras = parse_adoc(text)
    assert paras == [("H", "Line one line two continues it.")]


def test_inline_macro_with_content_is_unwrapped():
    text = "=== H\nUse insn:fence.i[] to synchronize the ext:zifencei[] extension.\n"
    paras = parse_adoc(text)
    assert paras == [("H", "Use fence.i to synchronize the zifencei extension.")]


def test_inline_macro_bracket_only_is_unwrapped():
    text = "=== H\nAs discussed in cite:[majc].\n"
    paras = parse_adoc(text)
    assert paras == [("H", "As discussed in majc.")]


def test_index_entry_macro_is_stripped():
    text = "=== H\n(((store instruction word, not included)))\nReal content here.\n"
    paras = parse_adoc(text)
    assert paras == [("H", "Real content here.")]


def test_anchor_and_attribute_and_include_and_comment_lines_are_dropped():
    text = (
        "=== H\n"
        "[[some-anchor]]\n"
        "[NOTE]\n"
        "include::images/foo.edn[]\n"
        "//a comment\n"
        ":sectnums!:\n"
        "The actual paragraph text.\n"
    )
    paras = parse_adoc(text)
    assert paras == [("H", "The actual paragraph text.")]


def test_block_delimiters_do_not_leak_into_paragraph_text():
    text = "=== H\n====\nInside a note block.\n====\nAfter the block.\n"
    paras = parse_adoc(text)
    assert paras == [("H", "Inside a note block."), ("H", "After the block.")]


def test_heading_before_any_section_is_none():
    text = "Preamble text with no heading yet.\n\n=== First Heading\nBody.\n"
    paras = parse_adoc(text)
    assert paras[0] == (None, "Preamble text with no heading yet.")
    assert paras[1] == ("First Heading", "Body.")


def test_ingest_adoc_file_produces_stable_content_derived_ids(tmp_path):
    repo_root = tmp_path
    corpus_dir = tmp_path / "knowledge" / "corpus" / "test-standard"
    corpus_dir.mkdir(parents=True)
    (corpus_dir / "chapter.adoc").write_text("=== H\nFirst.\n\nSecond.\n")

    chunks = ingest_adoc_file(
        corpus_dir / "chapter.adoc", standard_id="test-standard", repo_root=repo_root
    )
    assert [c.id for c in chunks] == ["test-standard/chapter#0", "test-standard/chapter#1"]
    assert chunks[0].source_path == str(Path("knowledge/corpus/test-standard/chapter.adoc"))
    assert chunks[0].standard_id == "test-standard"
    assert chunks[0].heading == "H"
    assert chunks[0].text == "First."


def test_an_anchored_span_straddling_a_line_break_is_stripped():
    """AsciiDoc anchored spans open `[#id]#` and close `#`, and in this repo's own corpus they
    routinely straddle a line break — m-st-ext.adoc opens one on line 23 and closes it on line 25.
    The substitution ran per line, so it could never match those, and the raw markup survived into
    the retrievable text (docs/decisions.md D180).
    """
    source = (
        "[#norm:mul_op]#insn:mul[] performs an XLEN-bit multiplication of\n"
        "`rs1` by `rs2` and places the lower XLEN bits in the destination\n"
        "register.#\n"
    )

    paragraphs = parse_adoc(source)

    assert len(paragraphs) == 1
    text = paragraphs[0][1]
    assert text.startswith("mul performs an XLEN-bit")
    assert "[#" not in text and "#" not in text


def test_a_single_line_anchored_span_still_works():
    """Control: the per-line case the original substitution did handle must keep working."""
    paragraphs = parse_adoc("[#norm:x]#a short span.#\n")

    assert paragraphs[0][1] == "a short span."


def test_the_real_corpus_ingests_with_no_residual_markup():
    """Checked against the actual documents rather than a fixture — this defect was invisible to
    every hand-written fixture because they all fit on one line, which is exactly how it survived
    (docs/decisions.md D178's lesson about fixture provenance).
    """
    import re

    corpus = Path(__file__).resolve().parents[2] / "mentor/knowledge/corpus/riscv-unpriv"
    chunks = ingest_adoc_directory(
        corpus, standard_id="riscv-unpriv", repo_root=corpus.parents[2]
    )

    assert len(chunks) > 100, "guards the guard: an empty ingest would pass vacuously"
    residual = [c.id for c in chunks if re.search(r"\[#|\({2,3}", c.text)]
    assert residual == [], f"chunks still carrying raw AsciiDoc markup: {residual}"

"""Unit tests for flux_knowledge.connectors.adoc: parsing synthetic AsciiDoc snippets covering
the specific markup constructs knowledge/corpus/riscv-unpriv/'s real files use (see that
directory's PROVENANCE.md). Not testing against the real corpus here — see
tests/integration/test_knowledge_riscv_corpus.py for that.
"""

from __future__ import annotations

from pathlib import Path

from flux_knowledge.connectors.adoc import ingest_adoc_file, parse_adoc


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

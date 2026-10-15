"""D1099: a library document's text whatever it was written in -- Word, Excel, PowerPoint, OpenDocument, HTML and
EPUB read with the standard library (headings as `#`, a table's rows as `a | b`, a slide or a sheet its own `##`);
the library indexes them, so their digests are made; a model handed one reads its text."""

from __future__ import annotations

import zipfile
from pathlib import Path

from flux_knowledge.connectors.text import LIBRARY_SUFFIXES, ingest_library, library_files
from flux_knowledge.convert import document_text, html_text

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
O = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
     'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"')


def _zip(path: Path, files: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return path


def _docx(path: Path) -> Path:
    body = (f'<w:document {W}><w:body>'
            '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Carry-save adders</w:t></w:r></w:p>'
            '<w:p><w:r><w:t xml:space="preserve">A tree of </w:t></w:r><w:r><w:t>3:2 counters.</w:t></w:r></w:p>'
            '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/></w:numPr></w:pPr><w:r><w:t>Wallace</w:t></w:r></w:p>'
            '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>width</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>delay</w:t></w:r></w:p></w:tc></w:tr>'
            '<w:tr><w:tc><w:p><w:r><w:t>8</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>4.2 ns</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
            '</w:body></w:document>')
    return _zip(path, {"word/document.xml": body})


def test_a_word_document_reads_with_its_heading_list_and_table(tmp_path):
    text = document_text(_docx(tmp_path / "adders.docx"))
    assert "# Carry-save adders" in text and "A tree of 3:2 counters." in text and "- Wallace" in text
    assert "width | delay" in text and "8 | 4.2 ns" in text


def test_a_presentation_reads_slide_by_slide(tmp_path):
    slide = lambda t: f'<p:sld {A}><p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>{t}</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>'  # noqa: E731
    path = _zip(tmp_path / "talk.pptx", {"ppt/slides/slide2.xml": slide("Results"), "ppt/slides/slide1.xml": slide("Motivation"),
                                         "ppt/slides/slide10.xml": slide("Thanks")})
    text = document_text(path)
    assert text.index("## Slide 1\n\nMotivation") < text.index("## Slide 2\n\nResults") < text.index("## Slide 3\n\nThanks"), text


def test_a_spreadsheet_reads_sheet_by_sheet_its_cells_in_their_columns(tmp_path):
    book = f'<workbook {S}><sheets><sheet name="Timing" sheetId="1" r:id="rId1"/></sheets></workbook>'
    rels = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="x"/></Relationships>')
    shared = f'<sst {S}><si><t>design</t></si><si><t>fmax</t></si><si><t>wallace</t></si></sst>'
    sheet = (f'<worksheet {S}><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="C1" t="s"><v>1</v></c></row>'
             '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="C2"><v>1120.5</v></c></row></sheetData></worksheet>')
    path = _zip(tmp_path / "timing.xlsx", {"xl/workbook.xml": book, "xl/_rels/workbook.xml.rels": rels,
                                           "xl/sharedStrings.xml": shared, "xl/worksheets/sheet1.xml": sheet})
    text = document_text(path)
    assert "## Sheet: Timing" in text and "design |  | fmax" in text and "wallace |  | 1120.5" in text, text


def test_an_opendocument_text_and_spreadsheet_read_too(tmp_path):
    odt = _zip(tmp_path / "notes.odt", {"content.xml": f'<office:document-content {O}><office:body><office:text>'
               '<text:h text:outline-level="2">Booth recoding</text:h><text:p>Radix<text:s text:c="2"/>4 halves the rows.</text:p>'
               '<text:list><text:list-item><text:p>fewer partial products</text:p></text:list-item></text:list>'
               '</office:text></office:body></office:document-content>'})
    text = document_text(odt)
    assert "## Booth recoding" in text and "Radix  4 halves the rows." in text and "- fewer partial products" in text, text
    ods = _zip(tmp_path / "runs.ods", {"content.xml": f'<office:document-content {O}><office:body><office:spreadsheet>'
               '<table:table table:name="Runs"><table:table-row><table:table-cell><text:p>pass</text:p></table:table-cell>'
               '<table:table-cell table:number-columns-repeated="2"><text:p>ok</text:p></table:table-cell></table:table-row></table:table>'
               '</office:spreadsheet></office:body></office:document-content>'})
    assert "## Runs" in document_text(ods) and "pass | ok | ok" in document_text(ods)


def test_html_and_an_epub_read_as_text_without_their_scripts(tmp_path):
    page = "<html><head><style>p{}</style><script>evil()</script></head><body><h2>Results</h2><p>It <b>fits</b>.</p><ul><li>one</li></ul>" \
           "<table><tr><td>a</td><td>b</td></tr></table></body></html>"
    text = html_text(page)
    assert "## Results" in text and "It fits." in text and "- one" in text and "a | b" in text and "evil" not in text, text
    container = ('<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                 '<rootfile full-path="OEBPS/book.opf"/></rootfiles></container>')
    opf = ('<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="b" href="two.xhtml"/><item id="a" href="one.xhtml"/></manifest>'
           '<spine><itemref idref="a"/><itemref idref="b"/></spine></package>')
    epub = _zip(tmp_path / "book.epub", {"META-INF/container.xml": container, "OEBPS/book.opf": opf,
                                         "OEBPS/one.xhtml": "<html><body><h1>First</h1></body></html>",
                                         "OEBPS/two.xhtml": "<html><body><h1>Second</h1></body></html>"})
    text = document_text(epub)
    assert text.index("# First") < text.index("# Second"), "the reading order its package says"


def test_what_cannot_be_read_says_why(tmp_path):
    said: list[str] = []
    broken = tmp_path / "broken.docx"
    broken.write_bytes(b"not a zip")
    assert document_text(broken, why=said.append) is None and "broken.docx" in said[0]
    said.clear()
    assert document_text(tmp_path / "image.png", why=said.append) is None and "not a document" in said[0]


def test_the_library_indexes_them_so_they_are_digested(tmp_path):
    lib = tmp_path / "library"
    lib.mkdir()
    _docx(lib / "adders.docx")
    (lib / "page.html").write_text("<h1>Notes</h1><p>Kogge-Stone prefix adders.</p>")
    assert {".docx", ".xlsx", ".pptx", ".odt", ".html", ".epub", ".doc", ".rtf"} <= set(LIBRARY_SUFFIXES)
    assert [p.name for p in library_files(lib)] == ["adders.docx", "page.html"]
    chunks = ingest_library(lib, repo_root=tmp_path)
    text = " ".join(c.text for c in chunks)
    assert "3:2 counters" in text and "Kogge-Stone" in text and {c.source_path.rsplit("/", 1)[-1] for c in chunks} == {"adders.docx", "page.html"}


def test_a_model_handed_a_word_file_reads_its_text(tmp_path):
    from flux_loop.document import read_input

    assert "3:2 counters" in read_input(_docx(tmp_path / "spec.docx"))
    said = read_input(tmp_path / "missing.docx")
    assert said.startswith("(missing.docx")

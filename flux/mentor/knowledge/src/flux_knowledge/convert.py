"""A document's text, whatever it was written in (D1099): what the library indexes and digests, and what a model or
an agent is handed to read.

Office files of today are zips of XML and are read here with the standard library -- Word (.docx), Excel (.xlsx),
PowerPoint (.pptx) and their OpenDocument kin (.odt, .ods, .odp) -- headings as Markdown `#`, a table's rows as
`a | b | c`, a slide or a sheet under its own `##`. HTML and EPUB through the HTML parser. A PDF through `pdftotext`
(poppler); the older binary formats (.doc, .xls, .ppt) and RTF through LibreOffice when it is on PATH. Text formats
are read as they are. `document_text(path)` gives None for what it cannot read, and says why through `why`.
"""

from __future__ import annotations

import html.parser
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

#: read as they are
TEXT_SUFFIXES = (".md", ".txt", ".rst", ".tex", ".bib", ".csv", ".tsv", ".json", ".yaml", ".yml", ".xml", ".org")
#: converted to text here
OFFICE_SUFFIXES = (".docx", ".docm", ".xlsx", ".xlsm", ".pptx", ".pptm", ".odt", ".ods", ".odp")
WEB_SUFFIXES = (".html", ".htm", ".xhtml", ".epub")
#: through a tool: pdftotext, LibreOffice
TOOL_SUFFIXES = (".pdf", ".doc", ".xls", ".ppt", ".rtf")
CONVERTED_SUFFIXES = OFFICE_SUFFIXES + WEB_SUFFIXES + TOOL_SUFFIXES
DOCUMENT_SUFFIXES = TEXT_SUFFIXES + CONVERTED_SUFFIXES
#: a library's papers -- what is read and digested first, what an agent is pointed to -- as against its source files
PAPER_SUFFIXES = (".pdf", ".md", ".txt", ".adoc", ".tex", ".rst", ".html", ".htm", ".xhtml", ".epub", ".docx", ".docm", ".odt",
                  ".doc", ".rtf", ".pptx", ".pptm", ".odp", ".ppt", ".xlsx", ".xlsm", ".ods", ".xls")

MAX_ROWS = 5000                                   # a spreadsheet's rows read per sheet: its head, not its million


def document_text(path: str | os.PathLike, why=lambda _m: None) -> str | None:
    """The text of the document at `path`, or None (`why(reason)` said) when it cannot be read here."""
    path = Path(path)
    ext = path.suffix.lower()
    try:
        if ext in TEXT_SUFFIXES:
            return path.read_text(errors="replace")
        if ext in (".docx", ".docm"):
            return _docx(path)
        if ext in (".xlsx", ".xlsm"):
            return _xlsx(path)
        if ext in (".pptx", ".pptm"):
            return _pptx(path)
        if ext in (".odt", ".ods", ".odp"):
            return _odf(path)
        if ext in (".html", ".htm", ".xhtml"):
            return html_text(path.read_text(errors="replace"))
        if ext == ".epub":
            return _epub(path)
        if ext == ".pdf":
            return _pdf(path, why)
        if ext in (".doc", ".xls", ".ppt", ".rtf"):
            return _office(path, why)
    except (zipfile.BadZipFile, ET.ParseError, KeyError, OSError, UnicodeError) as exc:
        why(f"{path.name}: not read ({type(exc).__name__}: {str(exc)[:120]})")
        return None
    why(f"{path.name}: a {ext or 'suffix-less'} file is not a document read here")
    return None


# ---- the zips of XML
def _ln(tag: str) -> str:
    """An element's local name, its namespace dropped."""
    return tag.rsplit("}", 1)[-1]


def _attr(el: ET.Element, name: str, default: str | None = None) -> str | None:
    """An attribute by its local name, its namespace whatever it is."""
    return next((v for k, v in el.attrib.items() if _ln(k) == name), default)


def _texts(el: ET.Element, *, tab: str = "\t") -> str:
    """The text of a run of Office XML: its `t` pieces, a tab and a break kept."""
    out = []
    for e in el.iter():
        n = _ln(e.tag)
        if n == "t" and e.text:
            out.append(e.text)
        elif n == "tab":
            out.append(tab)
        elif n in ("br", "cr"):
            out.append("\n")
    return "".join(out)


def _row(cells: list[str]) -> str:
    return " | ".join(c.replace("\n", " ").strip() for c in cells).rstrip(" |")


def _docx(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        body = ET.fromstring(z.read("word/document.xml"))
    lines: list[str] = []
    for block in next((e for e in body if _ln(e.tag) == "body"), body):
        n = _ln(block.tag)
        if n == "p":
            text = _texts(block).strip()
            if not text:
                continue
            style = next((_attr(e, "val", "") for e in block.iter() if _ln(e.tag) == "pStyle"), "")
            m = re.match(r"(?i)heading\s*(\d)", style)
            if m or style.lower() == "title":
                lines.append("#" * (int(m.group(1)) if m else 1) + " " + text)
            elif any(_ln(e.tag) == "numPr" for e in block.iter()):
                lines.append("- " + text)
            else:
                lines.append(text)
        elif n == "tbl":
            for tr in (e for e in block.iter() if _ln(e.tag) == "tr"):
                lines.append(_row([_texts(tc) for tc in tr if _ln(tc.tag) == "tc"]))
            lines.append("")
    return "\n\n".join(l for l in lines if l is not None).strip() + "\n"


def _pptx(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        slides = sorted((n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                        key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)))
        out = []
        for i, name in enumerate(slides, 1):
            root = ET.fromstring(z.read(name))
            paras = [_texts(p).strip() for p in root.iter() if _ln(p.tag) == "p"]
            out.append(f"## Slide {i}\n\n" + "\n".join(p for p in paras if p))
    return "\n\n".join(out) + "\n"


def _xlsx(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        shared = []
        if "xl/sharedStrings.xml" in names:
            shared = [_texts(si) for si in ET.fromstring(z.read("xl/sharedStrings.xml")) if _ln(si.tag) == "si"]
        book = ET.fromstring(z.read("xl/workbook.xml"))
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels")) if "xl/_rels/workbook.xml.rels" in names else None
        target = {r.get("Id"): r.get("Target") for r in (rels if rels is not None else [])}
        out = []
        for sheet in (e for e in book.iter() if _ln(e.tag) == "sheet"):
            rid = _attr(sheet, "id")
            where = target.get(rid, "")
            where = where.lstrip("/") if where.startswith("/") else "xl/" + where
            if where not in names:
                continue
            rows = []
            for r in (e for e in ET.fromstring(z.read(where)).iter() if _ln(e.tag) == "row"):
                cells: dict[int, str] = {}
                for c in (e for e in r if _ln(e.tag) == "c"):
                    col = _column(c.get("r", ""), len(cells))
                    kind, v = c.get("t"), next((e.text for e in c if _ln(e.tag) == "v"), None)
                    cells[col] = (shared[int(v)] if kind == "s" and v is not None and v.isdigit() and int(v) < len(shared)
                                  else _texts(c) if kind == "inlineStr" else (v or ""))
                if cells:
                    rows.append(_row([cells.get(i, "") for i in range(max(cells) + 1)]))
                if len(rows) >= MAX_ROWS:
                    rows.append(f"… (the first {MAX_ROWS} rows)")
                    break
            out.append(f"## Sheet: {sheet.get('name', '')}\n\n" + "\n".join(rows))
    return "\n\n".join(out) + "\n"


def _column(ref: str, fallback: int) -> int:
    """A cell reference's column, from 0 (B7 -> 1); without one, the next."""
    m = re.match(r"([A-Za-z]+)", ref)
    if not m:
        return fallback
    n = 0
    for ch in m.group(1).upper():
        n = n * 26 + ord(ch) - 64
    return n - 1


def _odf(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("content.xml"))
    lines: list[str] = []

    def text(el: ET.Element) -> str:
        out = [el.text or ""]
        for e in el:
            n = _ln(e.tag)
            if n == "tab":
                out.append("\t")
            elif n == "line-break":
                out.append("\n")
            elif n == "s":                             # spaces, `c` of them
                c = _attr(e, "c", "1")
                out.append(" " * (int(c) if c.isdigit() else 1))
            else:
                out.append(text(e))
            out.append(e.tail or "")
        return "".join(out)

    def walk(el: ET.Element) -> None:
        for e in el:
            n = _ln(e.tag)
            if n == "h":
                level = int(_attr(e, "outline-level", "1")) if (_attr(e, "outline-level", "1") or "").isdigit() else 1
                lines.append("#" * level + " " + text(e).strip())
            elif n == "p":
                t = text(e).strip()
                if t:
                    lines.append(t)
            elif n == "list-item":
                for p in (x for x in e.iter() if _ln(x.tag) in ("p", "h")):
                    t = text(p).strip()
                    if t:
                        lines.append("- " + t)
            elif n == "table":
                name = _attr(e, "name", "")
                if name:
                    lines.append(f"## {name}")
                rows = 0
                for tr in (x for x in e.iter() if _ln(x.tag) == "table-row"):
                    cells = []
                    for tc in (x for x in tr if _ln(x.tag) == "table-cell"):
                        r = _attr(tc, "number-columns-repeated", "1")
                        repeat = int(r) if r.isdigit() else 1
                        value = " ".join(text(p).strip() for p in tc if _ln(p.tag) == "p")
                        cells.extend([value] * min(repeat, 64))
                    row = _row(cells)
                    if row:
                        lines.append(row)
                        rows += 1
                    if rows >= MAX_ROWS:
                        lines.append(f"… (the first {MAX_ROWS} rows)")
                        break
            elif n == "page":                        # a presentation's slide
                lines.append("## " + (_attr(e, "name", "") or "Slide"))
                walk(e)
            else:
                walk(e)

    walk(root)
    return "\n\n".join(lines).strip() + "\n"


# ---- the web's
class _HTML(html.parser.HTMLParser):
    BLOCK = {"p", "div", "section", "article", "li", "tr", "br", "pre", "blockquote", "table", "ul", "ol", "dl", "dt", "dd", "figure"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0
        self.cell = False

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ("script", "style", "noscript", "svg"):
            self.skip += 1
        elif re.fullmatch(r"h[1-6]", tag):
            self.out.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag in ("td", "th"):
            self.out.append(" | " if self.cell else "")
            self.cell = True
        elif tag == "li":
            self.out.append("\n- ")
        elif tag in self.BLOCK:
            self.out.append("\n")
        if tag == "tr":
            self.cell = False

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript", "svg"):
            self.skip = max(0, self.skip - 1)
        elif re.fullmatch(r"h[1-6]", tag) or tag in ("p", "table", "pre", "blockquote"):
            self.out.append("\n\n")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.out.append(re.sub(r"\s+", " ", data))


def html_text(markup: str) -> str:
    """An HTML page's text: headings as `#`, list items as `- `, a table's cells joined by `|`; no script or style."""
    p = _HTML()
    p.feed(markup)
    text = "".join(p.out)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def _epub(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        order: list[str] = []
        try:                                       # the reading order its package says
            container = ET.fromstring(z.read("META-INF/container.xml"))
            opf = next(e.get("full-path") for e in container.iter() if _ln(e.tag) == "rootfile")
            pkg, base = ET.fromstring(z.read(opf)), opf.rsplit("/", 1)[0] + "/" if "/" in opf else ""
            items = {e.get("id"): base + e.get("href", "") for e in pkg.iter() if _ln(e.tag) == "item"}
            order = [items[e.get("idref")] for e in pkg.iter() if _ln(e.tag) == "itemref" and e.get("idref") in items]
        except (KeyError, StopIteration, ET.ParseError):
            pass
        order = [n for n in order if n in names] or sorted(n for n in names if n.lower().endswith((".xhtml", ".html", ".htm")))
        return "\n\n".join(html_text(z.read(n).decode("utf-8", "replace")) for n in order)


# ---- through a tool
def _pdf(path: Path, why) -> str | None:
    if shutil.which("pdftotext") is None:
        why(f"{path.name}: a PDF, and pdftotext (poppler) is not on PATH to read it")
        return None
    r = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, errors="replace", timeout=120)
    if r.returncode != 0:
        why(f"{path.name}: pdftotext failed: {r.stderr.strip()[:200]}")
        return None
    return r.stdout


def _office(path: Path, why) -> str | None:
    """An older Office file or RTF through LibreOffice (headless, a profile of its own): a text document as text, a
    spreadsheet as CSV (its first sheet), a presentation through PDF."""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice is None:
        why(f"{path.name}: a {path.suffix} file, and LibreOffice (soffice) is not on PATH to read it")
        return None
    ext = path.suffix.lower()
    to = {".xls": "csv", ".ppt": "pdf"}.get(ext, "txt:Text")
    with tempfile.TemporaryDirectory(prefix="flux-convert-") as tmp:
        r = subprocess.run([soffice, f"-env:UserInstallation=file://{tmp}/profile", "--headless", "--convert-to", to,
                            "--outdir", tmp, str(path)], capture_output=True, text=True, timeout=180)
        made = next((p for p in Path(tmp).iterdir() if p.is_file() and p.stem == path.stem), None)
        if r.returncode != 0 or made is None:
            why(f"{path.name}: LibreOffice could not convert it: {(r.stderr or r.stdout).strip()[:200]}")
            return None
        return _pdf(made, why) if made.suffix.lower() == ".pdf" else made.read_text(errors="replace").lstrip("\ufeff")

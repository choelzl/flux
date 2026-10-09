"""The library as one thing (D648): the shared folder (`mentor/knowledge/library/`, or `FLUX_LIBRARY`) plus any folder
a document adds, one BM25 index per set of folders (built once per process), and what every
consumer needs from it -- whether it holds anything, one line per paper, the files nearest a
question as absolute paths, and the LIBRARY section a coding agent's brief carries.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any, Iterable, Sequence

__all__ = ["agent_section", "cited_files", "index_for", "library_files", "paper_lines",
           "relevant_files", "status"]

KNOWLEDGE_ROOT = Path(__file__).resolve().parents[2]     # mentor/knowledge
FLUX_ROOT = KNOWLEDGE_ROOT.parents[1]                    # a chunk's source path is relative to it
#: A paper, as opposed to an implementation: what the one-line index lists.
PAPER_SUFFIXES = (".pdf", ".md", ".txt", ".adoc")

_INDEXES: dict[tuple[str, ...], Any] = {}


def _folders(extra: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({str(Path(f).resolve()) for f in extra if f}))


def library_files(folders: Iterable[str] = ()) -> list[Path]:
    """Every file the index reads, in the shared folder and `folders`."""
    from .connectors.text import library_files as walk

    from .retrieval import shared_library

    return [p for d in (shared_library(), *map(Path, _folders(folders))) for p in walk(d)]


def status(folders: Iterable[str] = ()) -> dict[str, Any]:
    """What the library holds, without indexing it: documents, PDFs, whether PDFs can be read."""
    from .retrieval import shared_library

    files = library_files(folders)
    return {"documents": len(files), "pdfs": sum(p.suffix.lower() == ".pdf" for p in files),
            "pdftotext": shutil.which("pdftotext") is not None, "path": str(shared_library())}


def index_for(folders: Iterable[str] = ()) -> Any:
    """The index over the corpus, the shared library and `folders`: the default index when
    there are none, else one built once per process per set of folders."""
    from .retrieval import BM25Index, _cached_default_index

    base = _cached_default_index()
    key = _folders(folders)
    if not key:
        return base
    key = (str(id(base)), *key)          # the shared library the extra folders join
    if key not in _INDEXES:
        from .connectors.text import ingest_library

        extra = [c for d in key[1:] for c in ingest_library(d, repo_root=FLUX_ROOT)]
        _INDEXES[key] = BM25Index(list(base._chunks) + extra)
    return _INDEXES[key]


def absolute(source_path: str) -> str:
    """A chunk's source path as an absolute path (the index keeps it relative to flux/)."""
    p = Path(source_path)
    return str(p if p.is_absolute() else FLUX_ROOT / p)


def _chunks(index: Any) -> list[Any]:
    return [c for c in getattr(index, "_chunks", []) if c.standard_id == "library"]


def paper_lines(index: Any, db: str = "", *, width: int = 200) -> list[str]:
    """One line per paper (implementations left out): its name and the digest's first line
    when the run's store holds one (D576), else the paper's first sentence-like paragraph."""
    from .digest import digests_in

    digests = digests_in(db) if db else {}
    first: dict[str, str] = {}
    for c in _chunks(index):
        if not c.source_path.lower().endswith(PAPER_SUFFIXES) or c.source_path in first:
            continue
        if len(c.text.split()) >= 5:
            first[c.source_path] = c.text
    papers = sorted({c.source_path for c in _chunks(index) if c.source_path.lower().endswith(PAPER_SUFFIXES)})
    out = []
    for path in papers:
        d = (digests.get(path) or {}).get("digest") or ""
        head = d.strip().splitlines()[0].strip() if d.strip() else " ".join(first.get(path, "").split())
        out.append(f"  [{path.rsplit('/', 1)[-1]}] {head}"[:width].rstrip())
    return out


def _relevant_paths(queries: Sequence[str] | str, index: Any, n: int, *, papers: bool = False) -> list[str]:
    from .retrieval import knowledge_lookup

    score: dict[str, float] = {}
    k = max(20, len(_chunks(index))) if papers else 20
    for q in [queries] if isinstance(queries, str) else queries:
        for hit in knowledge_lookup(q, standard_id="library", k=k, index=index):
            if papers and not hit.chunk.source_path.lower().endswith(PAPER_SUFFIXES):
                continue
            score[hit.chunk.source_path] = score.get(hit.chunk.source_path, 0.0) + hit.score
    return [p for p, _s in sorted(score.items(), key=lambda t: -t[1])[:max(0, n)]]


def relevant_files(queries: Sequence[str] | str, index: Any, n: int = 5) -> list[str]:
    """The `n` library files whose chunks score highest for `queries`, as absolute paths."""
    return [absolute(p) for p in _relevant_paths(queries, index, n)]


def agent_section(question: str | Sequence[str], folders: Iterable[str] = (), db: str = "", n: int = 5) -> str:
    """A coding agent's LIBRARY: full stored digests of the nearest `n` papers, plus paths
    and a paper index. Missing digests are identified, without substituting lexical snippets."""
    from .digest import digests_in

    queries = [_query(question)] if isinstance(question, str) else list(question)
    try:
        index = index_for(folders)
        lines = [f"  [{Path(p).name}]" for p in sorted({c.source_path for c in _chunks(index)
                                                     if c.source_path.lower().endswith(PAPER_SUFFIXES)})]
        queries = [q for q in queries if q.strip()]
        near = relevant_files(queries, index, n)
        papers = _relevant_paths(queries, index, n, papers=True)
        digests = {absolute(p): d for p, d in digests_in(db).items()}
    except Exception:  # noqa: BLE001 -- the library is help, never a reason to fail a turn
        return ""
    if not lines and not near:
        return ""
    out = ["LIBRARY (the operator's papers and reference implementations on this machine; open "
           "what helps -- PDFs read with `pdftotext <file> -`):"]
    out += lines
    if papers:
        out.append("\nNearest papers -- use these methods, numbers and pitfalls to inform your design:")
        for path in papers:
            full = absolute(path)
            digest = str(digests.get(full, {}).get("digest") or "").strip()
            out.append(f"\n[{Path(path).name}] {full}\n" + (digest or "Digest unavailable: open the paper for its method and details."))
    if near:
        out.append("Nearest this question:")
        out += [f"  {p}" for p in near]
    return "\n".join(out)


def _query(text: str, words: int = 60) -> str:
    """A long question as one lookup: its first `words` words (BM25 scores each term)."""
    return " ".join(text.split()[:words])


def cited_files(prompt: str) -> list[str]:
    """The library files a prompt's excerpt block cites, in order, once each."""
    from .context import LIBRARY_HEADER

    at = prompt.find(LIBRARY_HEADER)
    if at < 0:
        return []
    block = prompt[at + len(LIBRARY_HEADER):].lstrip("\n").split("\n\n", 1)[0]
    return list(dict.fromkeys(re.findall(r"^  \* \[([^\]\n]+)\] ", block, re.MULTILINE)))

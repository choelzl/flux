"""Source code in the library (docs/decisions.md D477): fpnew, HardFloat, FloPoCo, and any
other implementation the operator drops next to the papers.

Papers explain a method; a proven implementation shows the shape -- the classification of
an FP16 input, the leading-zero normalisation of a subnormal, round-to-nearest-even with the
carry into the exponent -- which is exactly the part sampled designs got wrong most (D473).
A source file is chunked per CONSTRUCT rather than per paragraph: a `module`/`entity`/
`function`/`class`/`def` and the comment block right above it is one chunk, headed by its
signature, so a lookup for "leading zero count normalize subnormal" lands on the function
that does it, cited by file and name. Long constructs are split at blank lines into pieces
that fit a prompt; the file's leading comment (a licence, a description) is the first chunk.

Reading only: the library is other people's work and is never pushed (D407); the licence
each repo carries is the operator's to honour, and the connector records the path so every
excerpt the model sees says where it came from.
"""

from __future__ import annotations

import re
from pathlib import Path

from flux_knowledge.document import Chunk, chunks_from

__all__ = ["SOURCE_SUFFIXES", "ingest_source_file", "parse_source"]

SOURCE_SUFFIXES = (".sv", ".svh", ".v", ".vh", ".vhd", ".vhdl", ".py", ".cpp", ".hpp", ".cc",
                   ".h", ".scala", ".c")

# A construct starts a chunk: HDL modules and functions, VHDL entities/architectures/functions,
# Python defs/classes, C++ functions of the FloPoCo generators (the `Foo::Foo(` shape).
_CONSTRUCT = re.compile(
    r"^\s*(?:"
    r"(?:module|macromodule|package|interface|function\s+automatic|function|task|class)\s+[^\s(;#]+"
    r"|(?:entity|architecture|package)\s+\w+"
    r"|(?:def|class)\s+\w+"
    r"|\w[\w:<>*&\s]*\b\w+::\w+\s*\("
    r")", re.IGNORECASE)
_COMMENT = re.compile(r"^\s*(//|#|--|/\*|\*)")
MAX_CHUNK_CHARS = 1800
MIN_CHUNK_CHARS = 60


def _cut(block: str, limit: int) -> list[str]:
    """A block longer than `limit`, cut at line ends."""
    if len(block) <= limit:
        return [block]
    pieces: list[str] = []
    cur: list[str] = []
    size = 0
    for ln in block.split("\n"):
        if size and size + len(ln) + 1 > limit:
            pieces.append("\n".join(cur))
            cur, size = [], 0
        cur.append(ln)
        size += len(ln) + 1
    if cur:
        pieces.append("\n".join(cur))
    return pieces


def _split_long(text: str, limit: int = MAX_CHUNK_CHARS) -> list[str]:
    """Pieces at most `limit` chars, cut at blank lines first, then at line ends."""
    if len(text) <= limit:
        return [text]
    out: list[str] = []
    cur: list[str] = []
    size = 0
    for para in re.split(r"\n\s*\n", text):
        block = para.strip("\n")
        if not block.strip():
            continue
        for piece in _cut(block, limit):
            if size and size + len(piece) + 2 > limit:
                out.append("\n\n".join(cur))
                cur, size = [], 0
            cur.append(piece)
            size += len(piece) + 2
    if cur:
        out.append("\n\n".join(cur))
    return out


def parse_source(text: str) -> list[tuple[str | None, str]]:
    """`(heading, chunk)` pairs: the leading comment, then one entry per construct with
    the comment block above it, headed by the construct's first line."""
    lines = text.splitlines()
    starts: list[int] = [i for i, ln in enumerate(lines) if _CONSTRUCT.match(ln)]
    pairs: list[tuple[str | None, str]] = []
    if not starts:
        body = "\n".join(lines).strip()
        return [(None, piece) for piece in _split_long(body)] if len(body) >= MIN_CHUNK_CHARS else []
    # pull each construct's start back over the comment block directly above it -- the
    # CONTIGUOUS one: a blank line ends it, so a file's licence header stays its own chunk
    bounds: list[int] = []
    for s in starts:
        b = s
        while b > 0 and _COMMENT.match(lines[b - 1]):
            b -= 1
        bounds.append(b)
    head = "\n".join(lines[:bounds[0]]).strip()
    if len(head) >= MIN_CHUNK_CHARS:
        pairs += [(None, piece) for piece in _split_long(head)]
    for i, (b, s) in enumerate(zip(bounds, starts)):
        end = bounds[i + 1] if i + 1 < len(bounds) else len(lines)
        body = "\n".join(lines[b:end]).strip()
        if len(body) < MIN_CHUNK_CHARS:
            continue
        heading = lines[s].strip()[:120]
        pairs += [(heading, piece) for piece in _split_long(body)]
    return pairs


def ingest_source_file(path: str | Path, *, standard_id: str, repo_root: Path,
                       id_stem: str | None = None) -> list[Chunk]:
    path = Path(path)
    try:
        source_path = str(path.resolve().relative_to(repo_root.resolve()))
    except ValueError:
        source_path = str(path)
    stem = id_stem or path.stem
    return chunks_from(parse_source(path.read_text(errors="replace")), standard_id=standard_id,
                       stem=stem, source_path=source_path)

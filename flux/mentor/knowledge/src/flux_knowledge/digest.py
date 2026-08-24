"""The library, DIGESTED (D576, Cedric: "build into the main flux db some summary / key points
of the library to reduce tokens and let the orchestrator or generator use that knowledge").

A library document (a paper, a spec, a source tree's file) is digested ONCE by the model into
a designer's key points -- the method, the exact numbers it states, the constructs, the
pitfalls -- and the digest lives in the flux store's `documents` table under the kind
`digest`, keyed by the document's content: every campaign on the machine reads it, a changed
document is digested again, an unchanged one never is. Two consumers: the generator's static
prefix carries the digests as the `digest` knowledge source (window-bound, ranked against the
part in hand, D548/D550); the orchestrator's planning prompt carries the library's INDEX, one
line per document, so it can name a method from the library when it picks what to try.
"""

from __future__ import annotations

import hashlib
from typing import Any, Iterable

__all__ = ["Digest", "RECIPE", "digest_library", "digests_in", "index_lines", "library_documents"]

RECIPE = "designer-key-points-v1"
MAX_DOC_CHARS = 80_000                    # a paper whole; a long spec's head, said so in the digest

BRIEF = """Read the document below and write its KEY POINTS for a hardware designer who will use it to
design or improve an RTL block: the method (what it computes and how), every exact number it
states that a designer would reuse (bit widths, ULP or error bounds, table sizes, latency,
area, clock, technology), the constructs (algorithms, encodings, tricks), and the pitfalls it
names. Numbers only as written; nothing you did not read. At most 900 characters, as short
lines, no preamble. Begin with one line naming what the document is.

DOCUMENT `{name}`{cut}:
{text}
"""


def _key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def library_documents(index: Any = None, *, standard_id: str = "library") -> list[tuple[str, str]]:
    """(source path, whole text) per library document, from the index's chunks, in path order."""
    if index is None:
        from .retrieval import _cached_default_index

        index = _cached_default_index()
    by_path: dict[str, list[str]] = {}
    for c in getattr(index, "_chunks", []):
        if c.standard_id == standard_id:
            by_path.setdefault(c.source_path, []).append(c.text)
    return [(path, "\n\n".join(texts)) for path, texts in sorted(by_path.items())]


def _store(db: str):
    from flux_store import CampaignStore

    return CampaignStore(db)


def digests_in(db: str) -> dict[str, dict[str, Any]]:
    """The digests the store holds under this recipe: source path -> the digest document."""
    if not db:
        return {}
    try:
        rows = _store(db).results.documents("digest")
    except Exception:  # noqa: BLE001 -- no store, no digests
        return {}
    return {d["source"]: d for d in rows if d.get("recipe") == RECIPE and d.get("source")}


def digest_library(db: str, proposer: Any, *, index: Any = None, say=lambda _m: None,
                   documents: Iterable[tuple[str, str]] | None = None) -> list[dict[str, Any]]:
    """Digest every library document the store does not hold yet (by content), one model
    call each, and store the digests. Returns the digests made this call."""
    have = digests_in(db)
    made: list[dict[str, Any]] = []
    docs = list(documents) if documents is not None else library_documents(index)
    todo = [(p, t) for p, t in docs if not t.strip() or have.get(p, {}).get("hash") != _key(t)]
    todo = [(p, t) for p, t in todo if t.strip()]
    if not todo:
        say(f"  digest: every library document is digested ({len(have)})")
        return made
    say(f"  digest: {len(todo)} library document(s) to digest, one model call each")
    store = _store(db)
    for path, text in todo:
        name = path.rsplit("/", 1)[-1]
        cut = f" (the first {MAX_DOC_CHARS:,} characters of {len(text):,})" if len(text) > MAX_DOC_CHARS else ""
        prompt = BRIEF.format(name=name, cut=cut, text=text[:MAX_DOC_CHARS])
        try:
            got = (proposer.propose(prompt).text or "").strip()
        except Exception as exc:  # noqa: BLE001 -- one document's failure is not the library's
            say(f"  digest: {name} not digested ({exc!s:.100})")
            continue
        if not got:
            say(f"  digest: {name}: the model wrote nothing")
            continue
        doc = {"source": path, "hash": _key(text), "recipe": RECIPE, "chars": len(text),
               "model": str(getattr(proposer, "model", "") or ""), "digest": got[:2000]}
        store.results.put_document("digest", doc)
        made.append(doc)
        say(f"  digest: {name}: {len(got)} chars")
    return made


def index_lines(db: str, *, width: int = 160) -> list[str]:
    """One line per digested document -- its name and its digest's first line -- for a
    prompt that needs to know what the library holds without reading it."""
    out = []
    for path, d in sorted(digests_in(db).items()):
        first = (d.get("digest") or "").strip().splitlines()
        head = first[0].strip() if first else ""
        out.append(f"  [{path.rsplit('/', 1)[-1]}] {head}"[:width])
    return out


class Digest:
    """The knowledge source: every digest in the run's store, as one block; the missing ones
    are made first when the run has a model (once per document, stored), else said."""

    key = "digest"
    title = "KEY POINTS FROM THE LIBRARY (each document digested once by a model; the excerpts below are the source)"
    static = True

    def __init__(self, db: str = "", make: bool = True) -> None:
        self.db = db
        self.make = make

    def render(self, state: Any) -> str:
        db = self.db or str(getattr(getattr(state, "request", None), "db", "") or "")
        if not db:
            return ""
        proposer = getattr(state, "proposer", None)
        say = getattr(state, "say", None) or (lambda _m: None)
        if self.make and proposer is not None:
            try:
                digest_library(db, proposer, say=say)
            except Exception as exc:  # noqa: BLE001 -- the library stays what it is
                say(f"  digest: could not digest the library ({exc!s:.100})")
        have = digests_in(db)
        if not have:
            return ""
        return "\n\n".join(f"[{path.rsplit('/', 1)[-1]}]\n{d['digest']}" for path, d in sorted(have.items()))

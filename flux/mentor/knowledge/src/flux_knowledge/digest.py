"""The library, digested (D576).

A library document (a paper, a spec, a source file) is digested once by the model into a
designer's key points -- the method, the exact numbers, the constructs, the pitfalls -- and
stored in the flux store's `documents` table under the kind `digest`, keyed by content: every
campaign on the machine reads it, and only a changed document is digested again. Two consumers:
the generator's static prefix carries the digests as the `digest` knowledge source
(window-bound, ranked against the part in hand, D548/D550); the orchestrator's planning prompt
carries the library's index, one line per document.

D771: a run digests in its Setup (the `knowledge: digest` phase), so its prompts only read what
is stored; `ask` hands each document to someone else than the run's model -- a coding agent the
document names (`knowledge: {digest: {agent: opencode}}`), which reads the file itself.
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
                   documents: Iterable[tuple[str, str]] | None = None, ask: Any = None) -> list[dict[str, Any]]:
    """Digest every library document the store does not hold yet (by content), one model
    call each -- or one `ask(path, prompt) -> (text, by)` each (D771) -- and store the
    digests. Returns the digests made this call."""
    have = digests_in(db)
    made: list[dict[str, Any]] = []
    docs = list(documents) if documents is not None else library_documents(index)
    todo = [(p, t) for p, t in docs if not t.strip() or have.get(p, {}).get("hash") != _key(t)]
    todo = [(p, t) for p, t in todo if t.strip()]
    if not todo:
        say(f"  digest: every library document is digested ({len(have)})")
        return made
    say(f"  digest: {len(todo)} library document(s) to digest, one {'agent' if ask else 'model'} call each")
    store = _store(db)
    for path, text in todo:
        name = path.rsplit("/", 1)[-1]
        cut = f" (the first {MAX_DOC_CHARS:,} characters of {len(text):,})" if len(text) > MAX_DOC_CHARS else ""
        prompt = BRIEF.format(name=name, cut=cut, text=text[:MAX_DOC_CHARS])
        by = str(getattr(proposer, "model", "") or "")
        try:
            if ask is not None:
                got, by = ask(path, prompt)
                got = (got or "").strip()
            else:
                got = (proposer.propose(prompt).text or "").strip()
        except Exception as exc:  # noqa: BLE001 -- one document's failure is not the library's
            say(f"  digest: {name} not digested ({exc!s:.100})")
            continue
        if not got:
            say(f"  digest: {name}: the model wrote nothing")
            continue
        doc = {"source": path, "hash": _key(text), "recipe": RECIPE, "chars": len(text),
               "model": by, "digest": got[:2000]}
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

    def __init__(self, db: str = "", make: bool = True, folders: Iterable[str] = (), ask: Any = None,
                 whole: bool = False) -> None:
        self.db = db
        self.make = make
        self.whole = whole                                 # D774: the shared library's papers too, beside `folders`
        self.ask = ask                                     # D771: who digests, when not the run's model
        # D753: a loop's own papers (`library/`, `inputs/`): only those are digested and shown
        self.folders = tuple(str(f) for f in folders)

    def _documents(self) -> list[tuple[str, str]] | None:
        if not self.folders:
            return None                                    # the shared library's, as before
        from pathlib import Path

        from .library import absolute, index_for

        mine = [str(Path(f).resolve()) for f in self.folders]
        return [(p, t) for p, t in library_documents(index_for(self.folders))
                if self.whole or any(absolute(p).startswith(f + "/") for f in mine)]

    def make_now(self, state: Any) -> dict[str, Any]:
        """The documents not digested yet, digested now (D771: in the run's Setup) -- what was
        done, for the task pane."""
        db = self.db or str(getattr(getattr(state, "request", None), "db", "") or "")
        proposer = getattr(state, "proposer", None)
        if not db or not self.make or (proposer is None and self.ask is None):
            return {}
        say = getattr(state, "say", None) or (lambda _m: None)
        documents = self._documents()
        try:
            made = digest_library(db, proposer, say=say, documents=documents, ask=self.ask)
        except Exception as exc:  # noqa: BLE001 -- the library stays what it is
            say(f"  digest: could not digest the library ({exc!s:.100})")
            return {"error": f"{exc!s:.300}"}
        have = digests_in(db)
        if documents is not None:
            have = {p: d for p, d in have.items() if p in {q for q, _t in documents}}
        return {"digested": len(made), "in all": len(have),
                "new": ", ".join(d["source"].rsplit("/", 1)[-1] for d in made)[:600],
                "by": ", ".join(sorted({d["model"] for d in made if d.get("model")}))}

    def render(self, state: Any) -> str:
        db = self.db or str(getattr(getattr(state, "request", None), "db", "") or "")
        if not db:
            return ""
        documents = self._documents()
        self.make_now(state)                               # nothing left to do after the Setup's
        have = digests_in(db)
        if documents is not None:                          # D753: the loop's own papers' digests
            own = {p for p, _t in documents}
            have = {p: d for p, d in have.items() if p in own}
        if not have:
            return ""
        return "\n\n".join(f"[{path.rsplit('/', 1)[-1]}]\n{d['digest']}" for path, d in sorted(have.items()))

"""The library, digested (D576).

A library document (a paper, a spec, a source file) is digested once by the model into what
someone solving the loop's problem can use from it -- its methods, its exact figures, its
constraints, its trade-offs, its pitfalls, in any field (D1101: no longer a hardware designer's
alone; the loop's statement in the brief) -- and stored in the flux store's `documents` table
under the kind `digest`, keyed by its content and the problem: only a changed document, or
another problem, is digested again. Two consumers:
the generator's static prefix carries the digests as the `digest` knowledge source
(window-bound, ranked against the part in hand, D548/D550); the orchestrator's planning prompt
carries the library's index, one line per document.

D794: a digest made once is kept in the run's home too (`~/.cache/flux/digests`, or
`FLUX_DIGESTS`), keyed by the document's content and the recipe: another loop, or the next run,
takes it from there instead of asking again. Inside the sandbox that home is the user's Flux
home, so one user's papers never reach another's runs.

D771: a run digests in its Setup (the `knowledge: digest` phase), so its prompts only read what
is stored; `ask` hands each document to someone else than the run's model -- a coding agent the
document names (`knowledge: {digest: {agent: opencode}}`), which reads the file itself.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable

__all__ = ["Digest", "RECIPE", "digest_library", "digests_in", "index_lines", "library_documents"]

RECIPE = "problem-key-points-v2"           # D1101: any field, read for the problem in hand
MAX_DOC_CHARS = 80_000                    # a paper whole; a long spec's head, said so in the digest

BRIEF = """Read the document below and write what {reader} can use from it.

Begin with one line naming what the document is (its kind and subject) and how it bears on the problem -- or that
it does not. Then, as short lines, what is specific and usable:
- the methods and approaches it describes: what each does and how, enough to apply it;
- every figure that matters for the problem, exactly as written with its unit (sizes, limits, parameters,
  measurements, results, costs);
- the requirements and constraints it sets;
- the trade-offs and comparisons it reports, with their numbers;
- the pitfalls, failure cases and caveats it names.
Prefer the specific over the general and the actionable over a summary; names and numbers exactly as written,
nothing you did not read. If nothing in it bears on the problem, say so on the first line and give its main points
in a few lines. At most 1500 characters, no preamble.
{problem}
DOCUMENT `{name}`{cut}:
{text}
"""


def _key(text: str, problem: str = "") -> str:
    """A digest's key: its document's content -- and the problem it was read for (D1101), so one made for another
    problem is not taken for this one's."""
    return hashlib.sha256((text + ("\0" + problem if problem else "")).encode("utf-8", "replace")).hexdigest()[:16]


def _brief(name: str, cut: str, text: str, problem: str) -> str:
    """D1101: the brief -- for whoever solves the loop's problem, its statement in it; without one, for anyone
    solving a design or optimisation problem."""
    return BRIEF.format(name=name, cut=cut, text=text,
                        reader="someone solving the problem below" if problem else "someone solving a design or optimisation problem",
                        problem=f"\nTHE PROBLEM it is read for:\n{problem[:4000]}\n" if problem else "")


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


def shared_dir() -> Path:
    """Where digests are kept across loops and runs (D794): `FLUX_DIGESTS`, else the run's home cache."""
    return Path(os.environ.get("FLUX_DIGESTS") or Path.home() / ".cache" / "flux" / "digests")


def _shared_get(text_key: str) -> dict[str, Any] | None:
    try:
        got = json.loads((shared_dir() / f"{RECIPE}-{text_key}.json").read_text())
        return got if isinstance(got, dict) and len(str(got.get("digest") or "")) >= MIN_DIGEST_CHARS else None
    except (OSError, ValueError):
        return None


def _shared_put(doc: dict[str, Any]) -> None:
    try:
        d = shared_dir()
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f".{RECIPE}-{doc['hash']}.json.{os.getpid()}"
        tmp.write_text(json.dumps({k: doc[k] for k in ("hash", "recipe", "chars", "model", "digest")}))
        tmp.replace(d / f"{RECIPE}-{doc['hash']}.json")
    except OSError:
        pass                                          # a cache that cannot be written is no cache


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


#: D782: what one Setup digests at most -- the rest in the passes after, so a library of
#: hundreds of files never holds a pass up for hours.
PER_PASS = 8
#: papers before sources: what a reader reads first
from .convert import PAPER_SUFFIXES as PAPERS  # noqa: E402 -- D1099: documents before source files
_GIVE_UP = 3                                   # failures in a row: the digester is not working today
MIN_DIGEST_CHARS = 80                          # D785: shorter is no digest (a word, a fragment)


def unwrapped(text: str) -> str:
    """D785: a model that answers with a tool call written as text (`{"arguments": {"message":
    "..."}}`, as Qwen coder through OpenCode does) -- its message, else the text as it is."""
    import json

    t = (text or "").strip()
    if not t.startswith("{"):
        return t
    try:
        doc = json.loads(t)
    except ValueError:
        return t
    for _ in range(3):                             # {"arguments": {"message": ...}}, {"message": ...}, ...
        if not isinstance(doc, dict):
            break
        for key in ("message", "text", "content", "answer", "summary"):
            if isinstance(doc.get(key), str) and doc[key].strip():
                return doc[key].strip()
        doc = doc.get("arguments") or doc.get("input") or doc.get("parameters")
    return t


def digest_library(db: str, proposer: Any, *, index: Any = None, say=lambda _m: None,
                   documents: Iterable[tuple[str, str]] | None = None, ask: Any = None,
                   limit: int | None = None, first: Iterable[str] = (),
                   stopped: Any = None, problem: str = "") -> list[dict[str, Any]]:
    """Digest the library documents the store does not hold yet (by content), one model call
    each -- or one `ask(path, prompt) -> (text, by)` each (D771) -- and store the digests.
    D782: at most `limit` this call (the rest in the next), those under `first` (the loop's own
    folders) first and papers before sources; three failures in a row end the call, saying
    why. D1101: `problem` the loop's statement -- each document read for it, the digest kept for it. Returns the
    digests made this call."""
    import os

    have = digests_in(db)
    made: list[dict[str, Any]] = []
    docs = list(documents) if documents is not None else library_documents(index)
    todo = [(p, t) for p, t in docs if t.strip() and have.get(p, {}).get("hash") != _key(t, problem)]
    store = _store(db) if todo else None
    kept = 0
    for path, text in list(todo):                     # D794: digested before, by another loop or run
        got = _shared_get(_key(text, problem))
        if got is not None:
            doc = {"source": path, "hash": _key(text, problem), "recipe": RECIPE, "chars": len(text),
                   "model": str(got.get("model") or ""), "digest": str(got["digest"])[:2000], "reused": True}
            store.results.put_document("digest", doc)
            made.append(doc)
            todo.remove((path, text))
            kept += 1
    if kept:
        say(f"  digest: {kept} {'document' if kept == 1 else 'documents'} taken from the digests kept before (no call)")
    if not todo:
        say(f"  digest: every library document is digested ({len(have) + kept})")
        return made
    from .library import absolute

    mine = tuple(str(f).rstrip("/") + "/" for f in first)
    todo.sort(key=lambda pt: (not absolute(pt[0]).startswith(mine) if mine else False,
                              os.path.splitext(pt[0])[1].lower() not in PAPERS, pt[0]))
    limit = len(todo) if limit is None else max(0, int(limit))
    now, later = todo[:limit], len(todo) - min(limit, len(todo))
    say(f"  digest: {len(todo)} library {'document' if len(todo) == 1 else 'documents'} to digest, one {'agent' if ask else 'model'} call each"
        + (f"; {len(now)} now, {later} in the passes after" if later else ""))
    failed: list[str] = []
    for path, text in now:
        why = stopped() if stopped is not None else None
        if why:                                           # D793: a stop does not wait for the library
            say(f"  digest: stopped ({why}) -- the rest wait for the next start")
            break
        name = path.rsplit("/", 1)[-1]
        cut = f" (the first {MAX_DOC_CHARS:,} characters of {len(text):,})" if len(text) > MAX_DOC_CHARS else ""
        prompt = _brief(name, cut, text[:MAX_DOC_CHARS], problem)
        by = str(getattr(proposer, "model", "") or "")
        try:
            if ask is not None:
                got, by = ask(path, prompt, text=text[:MAX_DOC_CHARS])
                got = (got or "").strip()
            else:
                got = (proposer.propose(prompt).text or "").strip()
        except Exception as exc:  # noqa: BLE001 -- one document's failure is not the library's
            say(f"  digest: {name} not digested ({exc!s:.300})")
            failed.append(f"{exc!s:.300}")
            if len(failed) >= _GIVE_UP and not made:
                say(f"  digest: {_GIVE_UP} failures in a row -- the rest wait for the next pass ({failed[-1]})")
                break
            continue
        got = unwrapped(got)
        if len(got) < MIN_DIGEST_CHARS:                   # D785: a word or a fragment is not a digest
            say(f"  digest: {name} not digested (the answer is {len(got)} characters: {got[:60]!r})")
            failed.append(f"an answer of {len(got)} characters")
            if len(failed) >= _GIVE_UP and not made:
                say(f"  digest: {_GIVE_UP} failures in a row -- the rest wait for the next pass ({failed[-1]})")
                break
            continue
        failed.clear()
        doc = {"source": path, "hash": _key(text, problem), "recipe": RECIPE, "chars": len(text),
               "model": by, "digest": got[:2000]}
        store.results.put_document("digest", doc)
        _shared_put(doc)
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
                 whole: bool = False, own: Iterable[str] = (), problem: str = "") -> None:
        self.db = db
        self.problem = problem                             # D1101: the problem each document is read for
        self.make = make
        self.whole = whole                                 # D774: the shared library's papers too, beside `folders`
        self.own = tuple(str(f) for f in own)              # D782: the loop's own folders, digested first
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
        import os

        try:
            limit = int(os.environ.get("FLUX_DIGEST_PER_PASS") or PER_PASS)
        except ValueError:
            limit = PER_PASS
        try:
            try:
                from flux_loop.ops import stop_requested as stopped
            except ImportError:
                stopped = None
            made = digest_library(db, proposer, say=say, documents=documents, ask=self.ask, limit=limit,
                                  first=self.own, stopped=stopped, problem=self.problem)
        except Exception as exc:  # noqa: BLE001 -- the library stays what it is
            say(f"  digest: could not digest the library ({exc!s:.100})")
            return {"error": f"{exc!s:.300}"}
        have = digests_in(db)
        if documents is not None:
            have = {p: d for p, d in have.items() if p in {q for q, _t in documents}}
        total = len(documents) if documents is not None else None
        return {"digested": len(made), **({"reused": sum(1 for d in made if d.get("reused"))} if any(d.get("reused") for d in made) else {}),
                "in all": len(have), **({"to do": total - len(have)} if total is not None else {}),
                "new": ", ".join(d["source"].rsplit("/", 1)[-1] for d in made)[:600],
                "by": ", ".join(sorted({d["model"] for d in made if d.get("model")}))}

    def render(self, state: Any) -> str:
        db = self.db or str(getattr(getattr(state, "request", None), "db", "") or "")
        if not db:
            return ""
        documents = self._documents()                      # D782: the Setup digests; a prompt only reads
        have = digests_in(db)
        if documents is not None:                          # D753: the loop's own papers' digests
            own = {p for p, _t in documents}
            have = {p: d for p, d in have.items() if p in own}
        if not have:
            return ""
        return "\n\n".join(f"[{path.rsplit('/', 1)[-1]}]\n{d['digest']}" for path, d in sorted(have.items()))

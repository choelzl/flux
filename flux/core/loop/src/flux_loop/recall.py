"""`flux loop ...`: what a loop already knows, read back on demand -- by an agent inside its turn,
by a step script, or by a person at a shell.

    flux loop ideas    [--part P] [--status S]      the ideas notebook and each idea's evidence
    flux loop tried    [--part P] [--last N]         the designs tried: how far each got, its numbers or why it was refused
    flux loop results  [--part P] [--stage S]        the measured designs ranked by the objectives
    flux loop design   NAME                          a design's text, as the record keeps it
    flux loop diff     A B                           two designs (or a design and a file), line by line
    flux loop library  [QUERY] [--n N]               the papers, one line each; with QUERY the nearest, with their digests
    flux loop digest   PAPER                         one paper's digest
    flux loop gate     FILE                          the loop's own gate on a file
    flux loop measure  FILE [--stage S]... [--gate]  the loop's own stages on a file

Which loop: inside an agent's turn, the turn's context (`FLUX_PROBE`: the document, the part, the
record, the campaign); else `--loop` or the current folder. Reads add nothing to the record; a
record not made yet is said, not created. `--json` gives each answer as data.
"""

from __future__ import annotations

import difflib
import functools
import json
import math
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["Where", "where", "ideas", "tried", "results", "design", "diff", "library", "digest", "gate", "measure"]

#: Stages that check a design rather than measure it: a row there is evidence, not a number to rank.
_CHECKS = ("gate", "admit", "prototype")


class NotHere(Exception):
    """The loop, its record or a named thing is not there: said as one line, exit 2."""


@dataclass
class Where:
    """The loop being asked about: its document, folder, record, campaign and part."""
    home: str
    doc: dict[str, Any]
    db: str
    campaign: str
    part: str | None = None
    ctx: str = ""                                  # the turn's probe context, when inside a turn
    task: Any = field(default=None, repr=False)

    def spec(self) -> Any:
        if self.task is None:
            from .document import TaskSpec

            self.task = TaskSpec.from_dict(self.doc, base=self.home)
        return self.task


def where(loop: str | None = None, db: str | None = None, campaign: str | None = None,
          part: str | None = None) -> Where:
    """The loop named, else the agent turn's, else the current folder's."""
    ctx_path = os.environ.get("FLUX_PROBE", "")
    if not loop and ctx_path and Path(ctx_path).is_file():
        ctx = json.loads(Path(ctx_path).read_text())
        return Where(ctx.get("home") or ".", ctx.get("doc") or {}, db or ctx.get("record") or "",
                     campaign or ctx.get("campaign") or "", part if part is not None else (ctx.get("part") or None), ctx_path)
    from .document import load_task

    path = Path(loop or ".")
    if path.is_dir() and not any((path / f).is_file() for f in ("problem.yaml", "problem.yml", "problem.json")):
        raise NotHere(f"{path.resolve()} is no loop folder (no problem.yaml); name one with --loop")
    try:
        task = load_task(path)
    except Exception as exc:  # noqa: BLE001 -- a document that does not load is said as it is
        raise NotHere(f"{path}: {exc}") from exc
    root = (task.record or task.id).split("/")[0]
    return Where(str(task.home or path.resolve()), task.to_dict(), db or str(task.out_dir() / f"{root}.db"),
                 campaign or task.record or task.id, part, "", task)


# ---- the record -------------------------------------------------------------------------
def _store(w: Where):
    if not w.db or not Path(w.db).is_file():
        raise NotHere(f"no record yet ({w.db or 'none named'}): the loop has not run")
    from flux_store import CampaignStore

    return CampaignStore(w.db)


def _campaign(store: Any, w: Where) -> str:
    ids = [c["campaign_id"] for c in store.list_campaigns()]
    if w.campaign in ids:
        return w.campaign
    near = [c for c in ids if c.startswith(w.campaign)]
    if not near:
        raise NotHere(f"{w.db}: no campaign {w.campaign!r}; it holds " + (", ".join(c[:24] for c in ids) or "none"))
    return near[-1]


def _numbers(trial: Any) -> dict[str, float]:
    out: dict[str, float] = {}
    for k, e in (trial.result.metrics.items() if trial.result else ()):
        try:
            v = float(e.value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(v):
            out[k] = v
    return out


def _part_of(c: dict[str, Any]) -> str:
    return str(c.get("subgoal") or c.get("op") or "")


def _mine(c: dict[str, Any], part: str | None) -> bool:
    return part is None or _part_of(c) == part


def _num(v: float) -> str:
    return f"{v:.4g}"


def _line(m: dict[str, float], keys: list[str] | None = None) -> str:
    keys = [k for k in (keys or []) if k in m] or list(m)
    return ", ".join(f"{k}={_num(m[k])}" for k in keys[:6])


def ideas(w: Where, status: str | None = None) -> dict[str, Any]:
    from .ideas import notebook

    store = _store(w)
    try:
        cid = _campaign(store, w)
        rows = [i for i in notebook(store, cid)["ideas"] if w.part is None or i.get("part") == w.part]
    finally:
        store.close()
    if status:
        rows = [i for i in rows if i.get("status") == status]
    return {"campaign": cid, "part": w.part, "ideas": rows}


def ideas_text(got: dict[str, Any]) -> str:
    rows = got["ideas"]
    if not rows:
        return "no ideas on record" + (f" for {got['part']}" if got["part"] else "") + " yet"
    out = [f"{len(rows)} idea(s), campaign {got['campaign'][:24]}" + (f", part {got['part']}" if got["part"] else "")]
    for i in rows:
        out.append(f"\n{i['id']}  [{i.get('status', 'proposed')}]" + (f"  part {i['part']}" if i.get("part") and not got["part"] else "")
                   + f"\n  {i['title']}\n  {i['hypothesis'][:600]}" + (f"\n  test: {i['test']}" if i.get("test") else ""))
        for e in i.get("evaluations", [])[-4:]:
            out.append(f"  - {e['design']} @ {e['stage']}: {e['status']} {_line(e['metrics'])} {e['error'][:120]}".rstrip())
    return "\n".join(out)


def tried(w: Where, last: int = 12, failed: bool = False) -> dict[str, Any]:
    """Each design tried, newest last: the stages it reached, its deepest numbers, why it stopped."""
    store = _store(w)
    try:
        cid = _campaign(store, w)
        trials = store.trials(cid, current=True, artifacts=False)
    finally:
        store.close()
    designs: dict[str, dict[str, Any]] = {}
    for t in trials:
        c = t.candidate or {}
        if not _mine(c, w.part) or not t.stage:
            continue
        name = str(c.get("name") or "?")
        d = designs.setdefault(name, {"name": name, "part": _part_of(c), "stages": [], "metrics": {}, "stage": None,
                                      "refused": None, "ideas": [], "first_seq": t.seq})
        d["stages"].append({"stage": t.stage, "status": t.status})
        d["last_seq"] = t.seq
        d["ideas"] = list(dict.fromkeys(d["ideas"] + list(((c.get("meta") or {}).get("idea_ids")) or [])))
        if t.status == "ok" and t.stage not in _CHECKS and _numbers(t):
            d["metrics"], d["stage"] = _numbers(t), t.stage
        elif t.status != "ok" and t.status not in ("running",):
            d["refused"] = {"stage": t.stage, "why": (t.error or "").strip()}
    rows = sorted(designs.values(), key=lambda d: d["last_seq"])
    if failed:
        rows = [d for d in rows if d["refused"] and not d["metrics"]]
    return {"campaign": cid, "part": w.part, "count": len(rows), "designs": rows[-last:] if last else rows}


def tried_text(got: dict[str, Any]) -> str:
    rows = got["designs"]
    if not rows:
        return "nothing tried" + (f" for {got['part']}" if got["part"] else "") + " yet"
    out = [f"{got['count']} design(s) tried, the last {len(rows)} (oldest first); `flux loop design NAME` shows one"]
    for d in rows:
        path = " > ".join(f"{s['stage']}{'' if s['status'] == 'ok' else ' ' + s['status']}" for s in d["stages"][-5:])
        head = f"\n{d['name']}" + (f"  [{d['part']}]" if d["part"] and not got["part"] else "") + f"  {path}"
        out.append(head)
        if d["metrics"]:
            out.append(f"  {d['stage']}: {_line(d['metrics'])}")
        if d["refused"]:
            why = " ".join(d["refused"]["why"].split())
            out.append(f"  stopped at {d['refused']['stage']}: {why[:240]}")
        if d["ideas"]:
            out.append("  ideas: " + ", ".join(d["ideas"]))
    return "\n".join(out)


def results(w: Where, stage: str | None = None, top: int = 10) -> dict[str, Any]:
    """The measured designs at one stage (the goal's, else the deepest measured), best first by the
    loop's own objectives; whether each meets every limit."""
    from .report import load

    if not Path(w.db).is_file():
        raise NotHere(f"no record yet ({w.db}): the loop has not run")
    store = _store(w)
    try:
        cid = _campaign(store, w)
    finally:
        store.close()
    rep = load(w.db, cid)
    if any("no objective vector" in n for n in rep.notes) and w.doc.get("objectives"):
        from .objective import Objectives

        rep = load(w.db, cid, Objectives.from_doc(w.doc["objectives"]))   # the record's own, else the document's
    objs = rep.objectives
    rows = [r for r in rep.rows if (r.part == w.part if w.part else r.whole)]
    stages = list(dict.fromkeys(r.stage for r in rows))
    at = stage or (rep.stage if rep.stage in stages else (stages[-1] if stages else None))
    latest: dict[str, Any] = {}
    for r in rows:
        if r.stage == at:
            latest[r.name] = r                       # the latest measurement of each design there
    ranked = sorted(latest.values(), key=functools.cmp_to_key(lambda a, b: _order(objs, a.metrics, b.metrics, at, stages)))
    keys = [o.metric for o in objs]
    out = [{"name": r.name, "part": r.part or "", "metrics": r.metrics, "knobs": r.knobs,
            "meets": not objs.missed(r.metrics, at, stages) if objs.limits else None} for r in ranked]
    return {"campaign": cid, "part": w.part, "stage": at, "stages": stages, "objectives": objs.describe(),
            "metrics": keys, "measured": len(out), "designs": out[:top] if top else out}


def _order(objs: Any, a: dict[str, float], b: dict[str, float], stage: str | None, stages: list[str]) -> int:
    ab, ba = objs.better(a, b, stage, stages), objs.better(b, a, stage, stages)
    return -1 if ab and not ba else 1 if ba and not ab else 0


def results_text(got: dict[str, Any]) -> str:
    rows = got["designs"]
    if not rows:
        return "nothing measured" + (f" for {got['part']}" if got["part"] else "") + " yet"
    out = [f"{got['measured']} design(s) measured at {got['stage']} (stages: {', '.join(got['stages'])}); "
           f"objectives: {got['objectives'] or '(none on record)'}"]
    for k, d in enumerate(rows, 1):
        mark = "" if d["meets"] is None else ("  meets the goals" if d["meets"] else "  misses a goal")
        knobs = ("  " + " ".join(f"{a}={b}" for a, b in list(d["knobs"].items())[:5])) if d["knobs"] else ""
        out.append(f"{k:>3}. {d['name']}  {_line(d['metrics'], got['metrics'])}{mark}{knobs}")
    return "\n".join(out)


def design(w: Where, name: str) -> dict[str, Any]:
    """A design's text: the record's latest trial of that name (or the one name it starts)."""
    store = _store(w)
    try:
        cid = _campaign(store, w)
        found: dict[str, Any] = {}
        for t in store.trials(cid):
            c = t.candidate or {}
            n = str(c.get("name") or "")
            if c.get("artifact") and (n == name or n.startswith(name)):
                found[n] = {"name": n, "part": _part_of(c), "stage": t.stage, "status": t.status, "seq": t.seq,
                            "text": c["artifact"]}
    finally:
        store.close()
    if name in found:
        return found[name]
    if len(found) == 1:
        return next(iter(found.values()))
    if found:
        raise NotHere(f"{name!r} names {len(found)} designs: " + ", ".join(sorted(found)[:12]))
    raise NotHere(f"no design {name!r} on record; `flux loop tried` lists them")


def diff(w: Where, a: str, b: str, context: int = 3) -> dict[str, Any]:
    """Two designs line by line; either may be a file instead."""
    def text(x: str) -> tuple[str, str]:
        p = Path(x)
        if p.is_file():
            return str(p), p.read_text()
        d = design(w, x)
        return d["name"], d["text"]

    (na, ta), (nb, tb) = text(a), text(b)
    lines = list(difflib.unified_diff(ta.splitlines(), tb.splitlines(), na, nb, n=context, lineterm=""))
    return {"a": na, "b": nb, "same": not lines, "diff": "\n".join(lines)}


# ---- the library ------------------------------------------------------------------------
def _index(w: Where) -> Any:
    from flux_knowledge.library import index_for

    from .document import library_folders

    return index_for(library_folders(w.spec()))


def _digests(w: Where, index: Any, sources: list[str] | None = None) -> dict[str, dict[str, Any]]:
    """Source path -> digest: the record's, else the shared cache's for this loop's statement (the
    same key the loop's setup looks for, D794, D1101) -- a paper another run of it digested."""
    from flux_knowledge.digest import _key, _shared_get, digests_in, library_documents

    got = digests_in(w.db if w.db and Path(w.db).is_file() else "")
    want = None if sources is None else set(sources) - set(got)
    if want is None or want:
        statement = str(w.doc.get("statement") or "")
        for src, text in library_documents(index):
            if src in got or (want is not None and src not in want) or not text.strip():
                continue
            shared = _shared_get(_key(text, statement))
            if shared is not None:
                got[src] = {**shared, "source": src}
    return got


def library(w: Where, query: str = "", n: int = 5) -> dict[str, Any]:
    """Every paper with its digest's first line; with a query, the nearest papers whole."""
    from flux_knowledge.library import _relevant_paths, absolute, paper_lines

    index = _index(w)
    db = w.db if w.db and Path(w.db).is_file() else ""
    if not query.strip():
        return {"query": "", "papers": [ln.strip() for ln in paper_lines(index, db, width=240)]}
    paths = _relevant_paths([query], index, n, papers=True)
    digests = _digests(w, index, paths)
    near = []
    for p in paths:
        d = str((digests.get(p) or {}).get("digest") or "").strip()
        near.append({"paper": Path(p).name, "path": absolute(p), "digest": d})
    return {"query": query, "papers": near}


def library_text(got: dict[str, Any]) -> str:
    rows = got["papers"]
    if not got["query"]:
        return (f"{len(rows)} paper(s) in the library; `flux loop library QUERY` gives the nearest with their digests\n"
                + "\n".join(rows)) if rows else "the library holds no papers (add them to the loop's library/)"
    if not rows:
        return f"no paper matches {got['query']!r}"
    return "\n\n".join(f"[{r['paper']}] {r['path']}\n" + (r["digest"] or "no digest yet: open the paper (pdftotext FILE -)")
                       for r in rows)


def digest(w: Where, paper: str) -> dict[str, Any]:
    got = _digests(w, _index(w))
    hits = {src: d for src, d in got.items() if Path(src).name == paper or src.endswith(paper) or Path(src).stem == paper}
    if not hits:
        names = sorted(Path(s).name for s in got)
        raise NotHere(f"no digest of {paper!r} on record" + (f"; digested: {', '.join(names[:20])}" if names
                                                            else "; nothing digested yet (each pass's setup digests the library)"))
    src, d = sorted(hits.items())[0]
    return {"paper": Path(src).name, "source": src, "model": d.get("model") or "", "digest": str(d.get("digest") or "")}


# ---- the gate and the stages --------------------------------------------------------------
def _ctx(w: Where) -> str:
    """The turn's probe context, or one made for this call from the loop folder: no probe budget
    outside a turn, nothing on the record."""
    if w.ctx:
        return w.ctx
    from .probe import probe_context

    work = Path(tempfile.mkdtemp(prefix="flux-loop-"))
    return probe_context(w.spec(), work, w.part or "", {"gate": 10 ** 6, "stages": 10 ** 6})


def _probe(w: Where, kind: str, file: str, stages: list[str] | None, gate_first: bool = False) -> tuple[int, str]:
    """The probe, in the turn (on its budget) or in a folder of its own made and removed here (no
    budget to count, so its lines are left out)."""
    import re
    import shutil

    from .probe import probe

    ctx = _ctx(w)
    try:
        code, text = probe(kind, file, stages, ctx_path=ctx, gate_first=gate_first)
    finally:
        if not w.ctx:
            shutil.rmtree(Path(ctx).parents[1], ignore_errors=True)
    return (code, text) if w.ctx else (code, re.sub(r"\n?\[\w+ probe \d+ of \d+\]", "", text))


def gate(w: Where, file: str) -> tuple[int, str]:
    return _probe(w, "gate", file, None)


def measure(w: Where, file: str, stages: list[str] | None = None, gate_first: bool = False) -> tuple[int, str]:
    return _probe(w, "measure", file, stages, gate_first)


#: The brief's line on these reads (a coding agent's turn).
LOOKUP_LINE = ("WHAT THE LOOP KNOWS is not pasted here: read it before you design. `flux loop tried` (each design tried, how far it got, "
               "its numbers or why it was refused), `flux loop results` (the measured designs ranked by the objectives), "
               "`flux loop ideas`, `flux loop design NAME` (a design's text), `flux loop diff A B` (two designs, or a design "
               "and your file), `flux loop library [QUERY]` and `flux loop digest PAPER` (the papers). They only read.")

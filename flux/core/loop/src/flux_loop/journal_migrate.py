"""A task journal of an earlier form brought to today's (D811 for journals). A journal before D1233 says nothing of
what its tasks are: Live read it by rules -- a task's box from its name, its pass from the marks around it, its
design from the words of its `why`, its try from the order of its siblings. Each change the journal went through
is a step -- the decision that made it, what it does -- applied in order to every start of the journal, so a
journal of any age comes out current (`format` in its hello); a start already current is left as it is.

    from flux_loop.journal_migrate import migrate_events, migrate_journal
    events, said = migrate_events(events, tests=["build", "ut"])   # a start's events, as dicts
    got = migrate_journal(path)                                     # a journal file: rewritten, the original kept

The rules are Live's as they were (live/legacy.js), frozen here: they run once on an old journal and never grow.
`migrate_journal` keeps the original beside it (`events.jsonl.orig.gz`) and writes `marks.jsonl` anew: the
lines are longer, their offsets move."""

from __future__ import annotations

import gzip
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Callable

__all__ = ["FORMAT", "STEPS", "migrate_events", "migrate_journal"]

FORMAT = 2
Event = dict[str, Any]


# ---------------------------------------------------------------- the rules Live read an old journal by
def _kind_of(name: str, why: str = "") -> str | None:
    """The box a phase's own name says (live/legacy.js `kindOf`); None when it says none."""
    name = str(name).lower()
    at = name.find(":")
    head = (name if at < 0 else name[:at]).strip()
    rest = "" if at < 0 else name[at + 1:].strip()
    word = (str(why or "").strip().lower().split() or [""])[0]
    if head == "orchestrate" or head == "agent" and word == "orchestrate":
        return "search"
    if head == "agent" and word == "digest":
        return "setup"
    if head in ("agent", "llm") or name.startswith("model"):
        return None
    if head == "tool":
        if word == "generate":
            return "design"
        if word in ("stage", "estimate"):
            return "check" if re.match(r"^\s*stage\s+gate\b", str(why), re.I) else "measure"
        if word in ("test", "lint", "golden", "build", "compile"):
            return "check"
        return None
    if head in ("knowledge", "records", "feedback", "extract", "validate", "setup"):
        return "setup"
    if head == "propose" and rest.startswith("decompose") or head == "gate" and (rest.startswith("tools") or rest.startswith("the problem")):
        return "setup"
    if head == "build" or head == "generation" and rest.startswith("build "):
        return "check"
    if head in ("test", "gate", "judge", "admitted", "verify", "probe"):
        return "check"
    if head in ("simulation", "analytical") and rest == "gate":
        return "check"
    if head in ("simulation", "physical", "analytical", "measure", "screen", "confirm", "estimate", "calibrate", "evaluation"):
        return "measure"
    if head in ("generation", "generate", "llm-gen", "template-fill", "template", "prototype", "patch", "repair", "rewrite",
                "design", "oracle", "invent", "compute"):
        return "design"
    if head == "propose" and rest.startswith("brief"):
        return "design"
    if head == "propose" and rest.startswith("plan"):
        return "plan"
    if head in ("propose", "decide", "frontier", "decision", "select"):
        return "choose"
    if head == "critique":
        return ("setup" if rest.startswith("decomposition") else "choose" if rest.startswith("decision")
                else "measure" if rest.startswith("measurement") else "check")
    if head == "dse":
        return "search"
    if head in ("plan", "search"):
        return head
    return None


def _design_own(name: str, why: str) -> str:
    why = str(why or "")
    m = re.match(r"^(?:stage|estimate)\s+\S+\s+(\S+)", why, re.I) or re.match(r"^(?:generate|test)\s+(\S+)", why, re.I)
    if m:
        return m.group(1)
    t = re.match(r"^\s*tool\s*:", str(name), re.I) and re.match(r"^\S+\s+(\S+)$", why.strip())
    return t.group(1) if t else ""


def _step_of(why: str) -> str:
    w = str(why or "").strip().split()
    if w and w[0] == "stage" and len(w) > 1:
        return w[1]
    if w and w[0] == "estimate" and len(w) > 1:
        return f"estimate {w[1]}"
    return "" if not w or w[0] in ("generate", "search", "baseline", "focus", "scope", "test") else w[0]


def _worker_of(name: str, params: dict) -> str:
    turn = str(params.get("turn") or name or "")
    m = re.match(r"^\s*(agent|llm|model)\b\s*:?\s*(\S*)", turn, re.I)
    if not m:
        return ""
    return f"agent:{m.group(2)}" if m.group(1).lower() == "agent" else f"model:{'' if m.group(1).lower() == 'llm' else m.group(2)}"


_AI = re.compile(r"^\s*(agent|llm|model)\b", re.I)
_TOOL = re.compile(r"^\s*tool\s*:", re.I)
_CRIT = re.compile(r"^\s*critique\s*:", re.I)
_QUIET_SETUP = re.compile(r"^\s*(knowledge\s*:|propose\s*:\s*decompose|gate\s*:\s*(tools|the problem))", re.I)
#: the steps the engine writes as notes when they went well and fast (D1047, D1235): bookkeeping, waits, a model's rounds
_QUIET = re.compile(r"^\s*(llm:\s*round|wait:|frontier\b|calibrate\b|propose:\s*(finalists|brief|decompose)|template-fill:\s*compose|"
                    r"orchestrate:\s*scope list|gate:\s*(tools|the problem)|knowledge:\s*(workbench|prepare|mentor|baseline reused))", re.I)
_SETUP_ROOT = re.compile(r"^\s*(knowledge|records|feedback|extract|setup|gate:\s*(tools|the problem)|propose:\s*decompose)", re.I)


class _Start:
    """One start's events and its tasks: id -> {"s": start event, "e": end event or None, "kids": [ids]}."""

    def __init__(self, events: list[Event]) -> None:
        self.events = events
        self.index()

    def index(self) -> None:
        self.nodes: dict[Any, dict[str, Any]] = {}
        for i, e in enumerate(self.events):
            e["_seq"] = i
            if e.get("ev") == "start":
                self.nodes[e.get("id")] = {"s": e, "e": None, "kids": [], "notes": [], "p": []}
            elif e.get("ev") == "end" and e.get("id") in self.nodes:
                self.nodes[e["id"]]["e"] = e
            elif e.get("ev") == "params" and e.get("id") in self.nodes:      # D1050: the turn a wrapper took in
                self.nodes[e["id"]]["p"].append(e)
        for nid, n in self.nodes.items():
            p = self.nodes.get(n["s"].get("parent"))
            if p is not None:
                p["kids"].append(nid)
        for e in self.events:
            if e.get("ev") == "note" and e.get("parent") in self.nodes:
                self.nodes[e["parent"]]["notes"].append(e)

    def params(self, nid: Any) -> dict:
        """A task's inputs: its start's, and those of the turn it took in (D1050)."""
        n = self.nodes[nid]
        out = dict(n["s"].get("params") or {})
        for e in n["p"]:
            out.update(e.get("params") or {})
        return out

    def parent(self, nid: Any) -> Any:
        p = self.nodes[nid]["s"].get("parent")
        return p if p in self.nodes else None

    def chain(self, nid: Any):
        while nid is not None:
            yield nid
            nid = self.parent(nid)

    def name(self, nid: Any) -> str:
        return str(self.nodes[nid]["s"].get("name") or "")

    def why(self, nid: Any) -> str:
        return str(self.nodes[nid]["s"].get("why") or "")

    def out(self, nid: Any) -> dict:
        e = self.nodes[nid]["e"]
        return (e or {}).get("output") or {}

    def failed(self, nid: Any) -> bool:
        e = self.nodes[nid]["e"]
        if e is None:
            return False
        o = e.get("output") or {}
        if _CRIT.match(self.name(nid)) and (o.get("rejected") or re.match(r"^(REJECTED|SENT BACK)", str(o.get("verdict") or ""))):
            return False
        timed = o.get("timed out") or re.match(r"^\s*agent\s*:", self.name(nid), re.I) and str(o.get("exit")) == "124"
        if timed:
            return False
        return bool(e.get("failed") or _TOOL.match(self.name(nid)) and o.get("exit") not in (None, 0, "0")
                    or re.match(r"^\s*test\s*:", self.name(nid), re.I) and o.get("error"))

    def rejected(self, nid: Any) -> bool:
        o = self.out(nid)
        return bool(_CRIT.match(self.name(nid)) and (o.get("rejected") or re.match(r"^(REJECTED|SENT BACK)", str(o.get("verdict") or ""))))

    def drop(self, nid: Any) -> None:
        """A task gone: its start and end out of the events, its tasks and notes under its parent."""
        n = self.nodes[nid]
        up = n["s"].get("parent")
        for k in n["kids"]:
            self.nodes[k]["s"]["parent"] = up
        for note in n["notes"]:
            note["parent"] = up
        gone = {id(n["s"]), id(n["e"]), *(id(p) for p in n["p"])}
        self.events[:] = [e for e in self.events if id(e) not in gone]
        self.index()


# ---------------------------------------------------------------- the steps
def _verdicts(st: _Start, said: Callable[[str], None]) -> None:
    """What the engine writes as fields since, read from an older journal's words: a critic's rejection (D1075:
    `rejected`, its verdict "REJECTED…"/"SENT BACK…"), an agent stopped at its time limit (D1053: `timed out`, exit
    124), a check that did not build (D1167: `failed`, its `error` said)."""
    n = 0
    for nid, node in st.nodes.items():
        e = node["e"]
        if e is None:
            continue
        if not isinstance(e.get("output"), dict):
            e["output"] = {}
        o = e["output"]
        name = st.name(nid)
        if _CRIT.match(name) and not o.get("rejected") and re.match(r"^(REJECTED|SENT BACK)", str(o.get("verdict") or "")):
            o["rejected"], n = True, n + 1
        if re.match(r"^\s*agent\s*:", name, re.I) and str(o.get("exit")) == "124" and not o.get("timed out"):
            o["timed out"], n = True, n + 1
        if re.match(r"^\s*test\s*:", name, re.I) and o.get("error") and not e.get("failed"):
            e["failed"], n = True, n + 1
    if n:
        said(f"{n} verdicts said as fields")


def _d1148(st: _Start, said: Callable[[str], None]) -> None:
    """A quiet setup step written after the tasks it ran (before D1143) takes them in, as Live read it."""
    last_mark, seen, moved, first = -1, [], 0, {}
    ended = {e.get("id"): e for e in st.events if e.get("ev") == "end"}
    for i, e in enumerate(st.events):
        if e.get("ev") == "mark" and e.get("name") == "pass":
            last_mark = i
        if e.get("ev") != "start":
            continue
        if _QUIET_SETUP.match(str(e.get("name") or "")):
            for s in seen:
                if (s.get("parent") == e.get("parent") and s["id"] in ended and ended[s["id"]]["_seq"] < i
                        and float(s.get("t") or 0) >= float(e.get("t") or 0) - 0.002 and s["_seq"] > last_mark):
                    s["parent"] = e["id"]
                    first.setdefault(id(e), (e, s))
                    moved += 1
        seen.append(e)
    for e, s in first.values():                      # the step's start before the first task it took in, as the engine writes it now
        del st.events[_at(st.events, e)]
        st.events.insert(_at(st.events, s), e)
    if moved:
        said(f"{moved} tasks taken in by the quiet step written after them")
    st.index()


def _d1233(st: _Start, said: Callable[[str], None]) -> None:
    """Every task and note says its box, pass, design, step and worker (the engine says them since D1233)."""
    marks = [(i, e) for i, e in enumerate(st.events) if e.get("ev") == "mark" and e.get("name") == "pass"]

    def n_of(m: Event) -> float | None:
        try:
            v = json.loads(m.get("why") or "{}").get("n")
            return float(v) if v is not None else None
        except (ValueError, AttributeError, TypeError):
            return None

    passes = [(i, n_of(m)) for i, m in marks if n_of(m) is not None]

    def pass_of(name: str, why: str, seq: int, params_pass: Any = None) -> Any:
        if params_pass not in (None, ""):
            try:
                return _num(float(params_pass))
            except (TypeError, ValueError):
                pass
        got = None
        for i, n in passes:
            if i < seq:
                got = n
        if got is not None:
            return _num(got)
        if re.match(r"^\s*(validate|digest|plan\b|lessons)", name, re.I):
            return None
        digest = re.match(r"^\s*agent\s*:", name, re.I) and re.match(r"^\s*digest\b", why, re.I)
        if not passes:
            return None if digest or _SETUP_ROOT.match(name) else 1
        first = passes[0][1]
        return None if first <= 0 else _num(first - 1)

    def box_of(nid: Any) -> str:
        agent = False
        for p in st.chain(nid):
            k = _kind_of(st.name(p), st.why(p))
            if k:
                return k
            if re.match(r"^\s*agent\s*:", st.name(p), re.I):
                agent = True
        return "design" if agent else "setup"

    roots_pass: dict[Any, Any] = {}
    orig_pass = {nid: (n["s"].get("params") or {}).get("pass") for nid, n in st.nodes.items()}   # read before any is moved to the tags
    for nid, n in st.nodes.items():
        s = n["s"]
        params = dict(s.get("params") or {})
        inturn = re.match(r"^\s*test\s*:", st.name(nid)) and str(st.out(nid).get("measured") or "").startswith("inside the turn")
        chain = list(st.chain(nid))
        given = next((orig_pass[p] for p in chain if orig_pass.get(p) not in (None, "")), None)
        root = chain[-1]
        if root not in roots_pass:
            roots_pass[root] = pass_of(st.name(root), st.why(root), st.nodes[root]["s"]["_seq"])
        p = pass_of("", "", 0, given) if given not in (None, "") else roots_pass[root]
        tags = {"box": "design" if inturn else box_of(nid), "pass": p, "part": params.pop("part", None),
                "design": _design_own(st.name(nid), st.why(nid)), "step": _step_of(st.why(nid)) if _TOOL.match(st.name(nid)) else "",
                "worker": _worker_of(st.name(nid), st.params(nid))}
        if not tags["design"] and re.match(r"^\s*(agent|generation|generate|repair|prototype)\b", st.name(nid), re.I):
            asked = re.search(r"\S+#\S+", str(st.params(nid).get("turn why") or st.why(nid)))      # D1059: the design a draft was asked for
            tags["design"] = asked.group(0) if asked else ""
        params.pop("pass", None)
        s["params"] = params
        s["tags"] = {k: v for k, v in tags.items() if v not in (None, "")}
    for e in st.events:
        if e.get("ev") == "note":
            k = _kind_of(str(e.get("name") or ""), str(e.get("why") or ""))
            p = pass_of(str(e.get("name") or ""), str(e.get("why") or ""), e["_seq"])
            e["tags"] = {k2: v for k2, v in {"box": k, "pass": p}.items() if v is not None}
    # D1041: a start of one pass is never marked -- a setup step after its work began is that pass's
    if not passes:
        began = min((float(n["s"].get("t") or 0) for nid, n in st.nodes.items() if st.parent(nid) is None and n["s"]["tags"].get("pass") == 1),
                    default=None)
        if began is not None:
            for nid, n in st.nodes.items():
                root = list(st.chain(nid))[-1]
                if "pass" not in n["s"]["tags"] and float(st.nodes[root]["s"].get("t") or 0) > began:
                    n["s"]["tags"]["pass"] = 1
    said(f"{len(st.nodes)} tasks tagged")


def _at(events: list[Event], e: Event) -> int:
    return next(i for i, x in enumerate(events) if x is e)


def _num(v: float) -> int | float:
    return int(v) if float(v).is_integer() else v


def _right_tasks(st: _Start, said: Callable[[str], None], tests: list[str] | None = None) -> None:
    """The tasks that do the work (D1235): no `evaluation` around the stages, one `decide` (the one inside it its
    fields), a check's or a stage's one command the step's own, short waits and rounds and numbers read back notes;
    a design run's tries numbered and each said how it came out (a `try` note); a check stopped at a failure says
    the check and those it never reached."""
    tests = list(tests or [])
    # the climb is its stages' work: what ran in it is the Measure box's (a composition, the frontier), a gate stage the checks'
    for nid in [n for n in st.nodes if st.name(n).strip().lower() == "evaluation"]:
        def own(x: Any) -> None:
            for k in st.nodes[x]["kids"]:
                if st.nodes[k]["s"]["tags"].get("box") in ("design", "choose"):
                    st.nodes[k]["s"]["tags"]["box"] = "measure"
                own(k)
        own(nid)
        for note in st.nodes[nid]["notes"]:
            if note.get("tags", {}).get("box") in ("design", "choose"):
                note["tags"]["box"] = "measure"
        st.drop(nid)
    # one decide: the one inside it is its fields
    for nid in [n for n in st.nodes if st.name(n).strip().lower() == "decide" and st.parent(n) is not None
                and st.name(st.parent(n)).strip().lower().startswith("decide")]:
        if nid not in st.nodes:
            continue
        up = st.parent(nid)
        pool = re.match(r"^\s*(\d+) in the pool", st.why(nid))
        if st.nodes[up]["e"] is not None:
            st.nodes[up]["e"]["output"] = {**(st.nodes[up]["e"].get("output") or {}), **st.out(nid),
                                          **({"pool": int(pool.group(1))} if pool else {})}
        st.drop(nid)
    # a check's or a stage's one command is the step's own task
    folds = 0
    for nid in list(st.nodes):
        n = st.nodes.get(nid)
        if n is None or len(n["kids"]) != 1:
            continue
        t = n["kids"][0]
        head = st.name(nid).split(":")[0].strip().lower()
        if not _TOOL.match(st.name(t)) or st.nodes[t]["kids"] or n["s"]["tags"].get("box") == "search" \
                or st.params(nid).get("turn") \
                or st.nodes[t]["s"]["tags"].get("box") != n["s"]["tags"].get("box"):     # a draft's check is no command of the draft
            continue
        if head not in ("test", "simulation", "analytical") and not re.match(r"^\s*generation:.*\bdraft\s+\d+\s*$", st.name(nid), re.I):
            continue
        ts, te = st.nodes[t]["s"], st.nodes[t]["e"]
        p = {k: v for k, v in (ts.get("params") or {}).items() if k in ("command", "folder", "stdin")}
        n["s"]["params"] = {**(n["s"].get("params") or {}), **p, "turn": st.name(t), **({"turn why": st.why(t)} if st.why(t) else {})}
        prog = re.sub(r"^\s*tool\s*:\s*", "", st.name(t)).replace("fluxpreset ", "").strip()
        step = ts.get("tags", {}).get("step") or prog                     # Live said a command by its program when no check named it
        if ts.get("tags", {}).get("design"):
            n["s"]["tags"]["design"] = ts["tags"]["design"]
        n["s"]["tags"]["step"] = step
        if n["e"] is not None and te is not None:
            n["e"]["output"] = {**(te.get("output") or {}), **(n["e"].get("output") or {})}
            if st.failed(t):
                n["e"]["failed"] = True
                n["e"]["tags"] = {**(n["e"].get("tags") or {}), "failed at": step}
        st.drop(t)
        folds += 1
    # a check that stopped at a failure: where, and what it never reached (the document's checks, in order)
    for nid, n in st.nodes.items():
        if n["e"] is None or not st.failed(nid) or not n["kids"] or n["s"]["tags"].get("box") != "check":
            continue
        bad = next((k for k in n["kids"] if st.failed(k)), None)
        if bad is None:
            continue
        ran = [st.nodes[k]["s"]["tags"].get("step") for k in n["kids"]]
        n["e"]["tags"] = {**(n["e"].get("tags") or {}), "failed at": st.nodes[bad]["s"]["tags"].get("step") or "",
                          **({"not reached": [t for t in tests if t not in ran]} if tests else {})}
    _tries(st)
    # short waits, a model's rounds, numbers read back: notes on their task
    noted = 0
    for nid in sorted(st.nodes, key=lambda x: -len(list(st.chain(x)))):     # the deepest first: a step whose tasks were notes is one
        n = st.nodes.get(nid)
        if n is None or n["kids"] or n["e"] is None or st.failed(nid):
            continue
        name, o = st.name(nid), st.out(nid)
        quiet = (_QUIET.match(name) or o.get("reused") or re.match(r"^\s*measure:", name) and "provided" in st.why(nid))
        if not quiet or float(n["e"].get("seconds") or 0) >= 0.5 and not re.match(r"^\s*llm:\s*round", name, re.I):   # a round: a note however long
            continue
        note = {"t": n["e"].get("t"), "ev": "note", "parent": n["s"].get("parent"), "name": name, "why": st.why(nid),
                "output": o, "tags": n["s"].get("tags") or {}}
        at = _at(st.events, n["e"])
        st.events.insert(at, note)
        st.drop(nid)
        noted += 1
    said(f"{folds} commands folded into their step, {noted} tasks made notes")


def _tries(st: _Start) -> None:
    """A design run's tries as Live numbered them (D1045, D1182): a draft and the checks after it, numbered across
    the pass's runs of the part; each said by a `try` note on its run."""
    def tags(nid: Any) -> dict:
        return st.nodes[nid]["s"]["tags"]

    def is_draft(nid: Any) -> bool:
        name = st.name(nid)
        return not _TOOL.match(name) and ((tags(nid).get("box") == "design" and not re.match(r"^\s*(propose|template-fill|test)\b", name, re.I))
                                          or bool(tags(nid).get("worker")))

    def in_measure(nid: Any) -> bool:
        return any(tags(p).get("box") == "measure" for p in list(st.chain(nid))[1:])

    runs = [nid for nid in st.nodes if tags(nid).get("box") == "design" and not in_measure(nid)
            and not any(tags(p).get("box") == "design" for p in list(st.chain(nid))[1:])
            and not re.match(r"^\s*(propose|template-fill)\b", st.name(nid), re.I) and not st.nodes[nid]["s"].get("_note")]
    runs.sort(key=lambda r: float(st.nodes[r]["s"].get("t") or 0))
    count: dict[tuple, int] = {}

    def under(nid: Any) -> list:
        out = [nid]
        for k in st.nodes[nid]["kids"]:
            out += under(k)
        return out

    notes: list[tuple[Event, Event]] = []
    for r in runs:
        kids = sorted(st.nodes[r]["kids"], key=lambda k: float(st.nodes[k]["s"].get("t") or 0))
        drafts = [k for k in kids if is_draft(k)]
        groups: list[dict] = []
        if drafts:
            before: list = []
            for k in kids:
                if k in drafts:
                    groups.append({"lead": k, "steps": []})
                elif groups:
                    groups[-1]["steps"].append(k)
                else:
                    before.append(k)
            groups[0]["steps"][:0] = before
        else:
            groups = [{"lead": r, "steps": [], "run": True}]
        key = (tags(r).get("pass"), tags(r).get("part"))
        for gi, g in enumerate(groups):
            count[key] = count.get(key, 0) + 1
            n = count[key]
            members = [g["lead"], *g["steps"]] if not g.get("run") else [r]
            for m in members:
                for x in under(m):
                    tags(x)["try"] = n
                    if re.match(r"^\s*attempt\s+\d+\s*$", st.why(x)):        # its run's count, said as the try it is
                        st.nodes[x]["s"]["why"] = f"try {n}"
            lead = g["lead"]
            ended = [st.nodes[x]["e"] for m in members for x in under(m)]
            if any(e is None for e in ended):
                continue                                           # still at work: how it ends is not known yet
            design = next((tags(x).get("design") for m in members for x in under(m) if tags(x).get("design")), "")
            bad = next((s for s in g["steps"] if st.failed(s) or st.rejected(s) or any(st.failed(x) for x in under(s))), None)
            last = gi == len(groups) - 1
            o = st.out(lead)
            if bad is not None:
                k = next((x for x in st.nodes[bad]["kids"] if st.failed(x)), None)
                f = st.out(k).get("failing") if k is not None else None
                what = (tags(k).get("step") if k is not None else "") or "a test"
                outcome, why = "check failed", f"{what} {f} failing" if f else f"{what} failed"
            elif not g.get("run") and _unchecked(st, r, lead, drafts, is_draft) and not (
                    last and st.failed(r) and not any(tags(k).get("box") == "check" for k in kids) and not re.search(r"\bagain\b|already measured", str(st.out(r).get("error") or ""))):
                outcome, why = "not built", ""      # refused before it was built; the last of a failed run that drafted nothing: no design
            elif o.get("timed out") or str(o.get("exit")) == "124" and re.match(r"^\s*agent", st.name(lead), re.I):
                outcome, why = "out of time", ""
            elif st.failed(lead) or last and st.nodes[r]["e"] is not None and st.failed(r):
                outcome, why = "no design", str(st.out(r).get("error") or st.out(lead).get("error") or "").split("\n")[0][:300]
            else:
                outcome, why = ("drafted" if g.get("run") and _TOOL.match(st.name(r)) else "passed"), ""
            last_end = max((e for e in ended if e is not None), key=lambda e: e["_seq"])
            note = {"t": last_end.get("t"), "ev": "note", "parent": r if not g.get("run") else st.nodes[r]["s"].get("parent"),
                    "name": "try", "why": f"try {n}", "output": {"why": why} if why else {},
                    "tags": {"box": "design", **({"pass": tags(r)["pass"]} if tags(r).get("pass") is not None else {}),
                             **({"part": tags(r)["part"]} if tags(r).get("part") else {}),
                             "try": n, **({"design": design} if design else {}), "outcome": outcome}}
            notes.append((last_end, note))
    for after, note in notes:
        st.events.insert(_at(st.events, after) + 1, note)
    st.index()


def _unchecked(st: _Start, run: Any, lead: Any, drafts: list, is_draft: Callable[[Any], bool]) -> bool:
    """D1064: a draft its run never checked -- refused before it was built (a patch rejected, a design measured
    already) -- in a run that checks its drafts, or one that failed with every draft unchecked (Live read the last
    try of a run as passed: the engine says it was not built, D1235)."""
    sib = sorted(st.nodes[run]["kids"], key=lambda k: float(st.nodes[k]["s"].get("t") or 0))
    i = sib.index(lead)
    j = next((k for k in range(i + 1, len(sib)) if is_draft(sib[k])), None)

    def is_check(k: Any) -> bool:
        return st.nodes[k]["s"]["tags"].get("box") == "check"
    if any(is_check(k) for k in sib[i + 1:j]):
        return False
    return any(is_check(k) for k in sib) or st.nodes[run]["e"] is not None and st.failed(run)


#: Each change the journal went through, in order: (decision, what it does, step(start, said, tests)).
STEPS: list[tuple[str, str, Callable[..., None]]] = [
    ("D1075", "a critic's rejection, an agent out of time, a check that did not build: fields", lambda st, said, tests: _verdicts(st, said)),
    ("D1148", "a quiet setup step written after its tasks takes them in", lambda st, said, tests: _d1148(st, said)),
    ("D1233", "every task says its box, pass, design, step and worker", lambda st, said, tests: _d1233(st, said)),
    ("D1235", "the right tasks: no evaluation, one decide, a check's one command its own, tries and how they came out",
     lambda st, said, tests: _right_tasks(st, said, tests)),
]


def migrate_events(events: list[Event], tests: list[str] | None = None, say: Callable[[str], None] | None = None
                   ) -> tuple[list[Event], list[str]]:
    """A journal's events (several starts) brought to the current format: (the events, what was done). A start
    whose hello says the current format is left as it is."""
    out: list[Event] = []
    said: list[str] = []
    starts: list[list[Event]] = []
    for e in events:
        if e.get("ev") == "hello" or not starts:
            starts.append([])
        starts[-1].append(dict(e))
    for evs in starts:
        hello = evs[0] if evs and evs[0].get("ev") == "hello" else None
        if hello is not None and int(hello.get("format") or 1) >= FORMAT:
            out += evs
            continue
        st = _Start(evs)
        for decision, what, step in STEPS:
            step(st, lambda m, d=decision: (said.append(f"{d}: {m}"), say and say(f"{d}: {m}")), tests)
        if hello is not None:
            hello["format"] = FORMAT
        for e in st.events:
            e.pop("_seq", None)
            e.pop("_note", None)
        out += st.events
    return out, said


def migrate_journal(path: str | Path, tests: list[str] | None = None, say: Callable[[str], None] | None = None) -> bool:
    """A journal file brought to the current format in place: written beside it first, the original kept as
    `events.jsonl.orig.gz`, `marks.jsonl` written anew. False when it is current already (or unreadable)."""
    path = Path(path)
    try:
        lines = path.read_bytes().splitlines()
    except OSError:
        return False
    events = []
    for line in lines:
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    if not events or all(int(e.get("format") or 1) >= FORMAT for e in events if e.get("ev") == "hello") and \
            any(e.get("ev") == "hello" for e in events):
        return False
    new, _said = migrate_events(events, tests, say)
    tmp, mtmp = path.with_name(path.name + ".converting"), path.with_name("marks.jsonl.converting")
    marks = []
    with open(tmp, "w") as fh:
        for e in new:
            at = fh.tell()
            fh.write(json.dumps(e, default=str) + "\n")
            if e.get("ev") == "hello" or e.get("ev") == "mark" and e.get("name") == "pass":
                try:
                    n = int(json.loads(e.get("why") or "{}").get("n") or 0) if e.get("ev") == "mark" else 0
                except (ValueError, AttributeError, TypeError):
                    n = 0
                marks.append({"at": at, "ev": "hello" if e.get("ev") == "hello" else "pass", "n": n})
    mtmp.write_text("".join(json.dumps(m) + "\n" for m in marks))
    orig = path.with_name(path.name + ".orig.gz")
    if not orig.exists():
        with open(path, "rb") as src, gzip.open(orig, "wb") as gz:
            shutil.copyfileobj(src, gz)
    marks_path = path.with_name("marks.jsonl")
    for t, p in ((tmp, path), (mtmp, marks_path)):
        if p.exists():
            os.chmod(t, p.stat().st_mode & 0o777)
        os.replace(t, p)
    return True

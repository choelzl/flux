"""A problem document of an earlier form brought to today's (D811). Each change the document format
went through is a step -- the decision that made it, what it does -- applied in order, so a
document of any age comes out current; a document already current comes back as it is. What a
step cannot do for you (a `world:` the loop no longer has, a library folder to move) is said,
not guessed.

    from flux_loop.migrate import migrate, migrate_loop
    new, said, manual = migrate(doc)                # a document as a dict
    got = migrate_loop(folder, write=True)          # a loop's folder: its documents, its record

`migrate_loop` writes a document only when the result loads; the original is kept beside it
(`<file>.orig`; YAML comments are not carried over). A document of the old naming
(`<id>.problem.yaml` with an `id:`, D786) becomes `problem.yaml`, and when its id was not the
folder's name, the record follows: `out/` files and the record's campaigns are renamed.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Callable

__all__ = ["STEPS", "migrate", "migrate_loop"]

Say = Callable[[str], None]
_CHECK = ("count_re", "fail_re", "timeout_s")


def _d775(doc: dict[str, Any], say: Say) -> None:
    """Each box's settings under `flow`: top-level gate, stages, space, seeds and knowledge, and
    `budget.finalists` / `budget.calibrate`."""
    flow = doc.setdefault("flow", {}) if isinstance(doc.get("flow", {}), dict) else None
    if flow is None:
        return
    if flow.get("test") == "gate":
        flow.pop("test")
    if doc.get("gate") not in (None, "", [], {}):
        flow["test"] = doc.pop("gate")
        say("gate -> flow.test")
    doc.pop("gate", None)
    stages = doc.pop("stages", None)
    if isinstance(stages, list) and stages:
        measure: dict[str, Any] = {}
        for st in stages:
            st = dict(st)
            name = str(st.pop("name"))
            if st.get("timeout_s") == 600:
                st.pop("timeout_s")
            measure[name] = st["command"] if set(st) == {"command"} else (st or None)
        flow["measure"] = measure
        say("stages -> flow.measure, a map by name")
    space, seeds = doc.pop("space", None), doc.pop("seeds", None)
    if space or seeds:
        dse = flow.get("dse")
        new: dict[str, Any] = dict(dse) if isinstance(dse, dict) else ({"policy": dse} if dse is not None else {})
        if space:
            new["space"] = space
        if seeds:
            new["seeds"] = seeds
        flow["dse"] = new
        say("space and seeds -> the search's (flow.dse)")
    if "knowledge" in doc:
        k, fk = doc.pop("knowledge"), flow.get("knowledge")
        know: dict[str, Any] = dict(fk) if isinstance(fk, dict) else {}
        if isinstance(k, str) and k:
            know["text"] = k
        elif isinstance(k, dict):
            know.update({x: v for x, v in k.items() if x in ("files", "sheet", "text", "library") and v not in (None, "", [])})
        if know:
            flow["knowledge"] = know
        say("knowledge -> flow.knowledge")
    fk = flow.get("knowledge")
    if fk is not None and not isinstance(fk, dict) and fk not in ("off", False):
        srcs = [fk] if isinstance(fk, str) else list(fk)
        flow["knowledge"] = "off" if ("none" in srcs or "off" in srcs) else {}
        if not flow["knowledge"]:
            flow.pop("knowledge")
        say("flow.knowledge: the list of sources -> on (or off)")
    budget = doc.get("budget") if isinstance(doc.get("budget"), dict) else None
    if budget is not None and "finalists" in budget:
        sel = flow.get("select")
        flow["select"] = {**(sel if isinstance(sel, dict) else {}), "finalists": budget.pop("finalists")}
        say("budget.finalists -> flow.select.finalists")
    if budget is not None and "calibrate" in budget:
        if budget.pop("calibrate") is False:
            flow["calibrate"] = "off"
        say("budget.calibrate -> flow.calibrate")
    if budget is not None and not budget:
        doc.pop("budget")
    if not flow:
        doc.pop("flow", None)


def _d786(doc: dict[str, Any], say: Say) -> None:
    """No `id:`: the folder's name is the loop's id."""
    if "id" in doc:
        say(f"id: {doc.pop('id')!r} dropped (the folder's name is the id)")


def _d789(doc: dict[str, Any], say: Say) -> None:
    """`flow.test` a map by name, like `flow.measure`."""
    flow = doc.get("flow") if isinstance(doc.get("flow"), dict) else {}
    t = flow.get("test")
    if isinstance(t, list) and t and all(isinstance(c, dict) and "run" in c for c in t):
        flow["test"] = {str(c.get("name") or f"check{i + 1}"): ({"run": c["run"], **{k: c[k] for k in _CHECK if k in c}}
                                                                if any(k in c for k in _CHECK) else c["run"])
                        for i, c in enumerate(t)}
        say("flow.test: the list of checks -> a map by name")
    elif isinstance(t, dict) and set(t) & set(_CHECK):
        settings = {k: t.pop(k) for k in _CHECK if k in t}
        new: dict[str, Any] = {}
        for name in ("build", "test"):
            if name in t:
                own = dict(settings) if name == "test" else {k: v for k, v in settings.items() if k == "timeout_s"}
                new[name] = {"run": t.pop(name), **own} if own else t.pop(name)
        new.update(t)
        flow["test"] = new
        say(f"flow.test: {', '.join(settings)} under the check's name")


def _d790_d792(doc: dict[str, Any], say: Say) -> None:
    """No `cache:`, `workbench:` (always on, always `workbench/`), `joiner:`, `max_parts:`;
    `parts` a list of names or a map from each name to what it is."""
    for key in ("cache", "workbench", "joiner", "max_parts"):
        if key in doc:
            say(f"{key}: dropped ({doc.pop(key)!r})")
    parts = doc.get("parts")
    if isinstance(parts, list) and parts and any(isinstance(p, dict) for p in parts):
        items = [(p, "") if isinstance(p, str) else (p.get("name"), p.get("statement") or "") for p in parts]
        doc["parts"] = {n: s for n, s in items} if any(s for _n, s in items) else [n for n, _s in items]
        say("parts: a map from each name to what it is")


def _d791(doc: dict[str, Any], say: Say, manual: Say) -> None:
    """The library is the loop's `library/` folder, digested whenever it is on."""
    flow = doc.get("flow") if isinstance(doc.get("flow"), dict) else {}
    k = flow.get("knowledge")
    if isinstance(k, dict):
        if "digest" in k:
            k.pop("digest")
            say("flow.knowledge.digest: dropped (always digested)")
        if "library" in k:
            lib = k.pop("library")
            say("flow.knowledge.library: dropped")
            manual(f"move the files of {lib!r} into the loop's library/ folder")
        if not k:
            flow.pop("knowledge")


_WORDS = {"validate": {"llm": "model"}, "orchestrate": {"llm": "model", "agent": "tools"},
          "plan": {"llm": "model", "none": "off"}, "critique": {"llm": "model", "none": "off"},
          "extract": {"none": "off"}, "feedback": {"none": "off"}}


def _by(v: Any) -> Any:
    if isinstance(v, dict) and "agent" in v:
        v = dict(v)
        a = v.pop("agent")
        if isinstance(a, dict) and a.get("preset"):
            return {"by": a["preset"], **{k: x for k, x in a.items() if k != "preset"}, **v}
        return {"by": a, **v}
    return v


def _d795_d797(doc: dict[str, Any], say: Say) -> None:
    """Who works a box is said the same way everywhere (`model`, `off`, `tools`, `{by: ...}`);
    `brief` gone, `extract` is `knowledge.lessons`; the search is the orchestrator's."""
    flow = doc.get("flow") if isinstance(doc.get("flow"), dict) else None
    if flow is not None:
        for box in list(flow):
            v = flow[box]
            if isinstance(v, str) and v in _WORDS.get(box, {}):
                flow[box] = _WORDS[box][v]
                say(f"flow.{box}: {v} -> {flow[box]}")
            elif isinstance(v, dict) and "agent" in v and box != "generate":
                flow[box] = _by(v)
                say(f"flow.{box}: agent -> by")
        g = flow.get("generate")
        if isinstance(g, dict) and set(g) == {"agent"}:
            flow["generate"] = _by(g)
            say("flow.generate: agent -> by")
        if "dse" in flow:
            d = flow.pop("dse")
            if d == "llm":
                d = {"by": "model"}
            elif isinstance(d, dict) and d.get("policy") == "llm":
                d = {"by": "model", **{k: x for k, x in d.items() if k != "policy"}}
            elif isinstance(d, dict) and "llm" in d:
                d = {"by": "model", **(d.pop("llm") or {}), **d}
            if isinstance(d, dict) and d.get("policy") == "agent":
                d = {"by": "tools", **{k: x for k, x in d.items() if k != "policy"}}
            o = flow.get("orchestrate")
            if o is not None and o not in ("rules", "model", "llm"):
                flow["dse"] = d                                    # both said: left to the loader to refuse, with why
            else:
                flow["orchestrate"] = _by(d)
                say("flow.dse -> flow.orchestrate")
        if "extract" in flow:
            les = flow.pop("extract")
            if les not in ("off", "none"):
                k = flow.get("knowledge")
                k = {"off": True} if k in ("off", False) else ({} if k is None else dict(k))
                k["lessons"] = les
                flow["knowledge"] = k
            say("flow.extract -> flow.knowledge.lessons")
        k = flow.get("knowledge")
        if isinstance(k, dict) and "agent" in k:
            flow["knowledge"] = {**{x: v for x, v in k.items() if x != "agent"}, "by": k["agent"]}
            say("flow.knowledge.agent -> by")
    if "brief" in doc:
        doc.pop("brief")
        say("brief: dropped (the plan briefs the parts)")


def _d803(doc: dict[str, Any], say: Say, manual: Say) -> None:
    """No `world:` and no `hooks:`: what a document cannot say is a command beside it."""
    for key in ("world", "hooks"):
        if key in doc:
            manual(f"`{key}:` is gone (D803): say its steps as commands -- flow.test, flow.measure, flow.generate: "
                   "{command}, orchestrate: {command} -- with the scripts beside the document")


#: Each change the format went through, in order: (decision, what it does, the step).
STEPS: list[tuple[str, str, Callable[..., None]]] = [
    ("D775", "each box's settings under flow", _d775),
    ("D786", "no id: the folder is the loop's id", _d786),
    ("D789", "flow.test a map by name", _d789),
    ("D790-D792", "no cache, workbench, joiner, max_parts; parts by name", _d790_d792),
    ("D791", "the library is the library/ folder", _d791),
    ("D795-D797", "who works a box, said the same way; the search under orchestrate", _d795_d797),
    ("D803", "no world, no hooks", _d803),
]


def migrate(doc: Any) -> tuple[Any, list[str], list[str]]:
    """(the document brought to today's form, what was changed, what needs a person). Inline
    sub-tasks are brought along; a document already current comes back equal."""
    if not isinstance(doc, dict):
        return doc, [], []
    import copy

    out = copy.deepcopy(doc)
    said: list[str] = []
    manual: list[str] = []
    for code, _what, step in STEPS:
        say = lambda text, code=code: said.append(f"{code}: {text}")            # noqa: E731
        hand = lambda text, code=code: manual.append(f"{code}: {text}")         # noqa: E731
        if step in (_d791, _d803):
            step(out, say, hand)
        else:
            step(out, say)
    subs = out.get("subtasks")
    if isinstance(subs, list):
        new = []
        for i, c in enumerate(subs):
            if isinstance(c, dict):
                c2, s2, m2 = migrate(c)
                said += [f"subtasks[{i}]: {x}" for x in s2]
                manual += [f"subtasks[{i}]: {x}" for x in m2]
                new.append(c2)
            else:
                new.append(c)
        out["subtasks"] = new
    return out, said, manual


# ---- a loop's folder
def documents_of(folder: Path) -> list[Path]:
    """The loop's documents: `problem.yaml` and `NAME.problem.yaml`, and the old namings
    (`*.problem.yml`, `*.task.yaml`, `*.task.json`)."""
    out: list[Path] = []
    for pattern in ("problem.yaml", "*.problem.yaml", "*.problem.yml", "*.task.yaml", "*.task.json"):
        out += [p for p in sorted(folder.glob(pattern)) if p.is_file() and p not in out]
    return out


def _read(path: Path) -> Any:
    import yaml

    text = path.read_text()
    return json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)


def _text(doc: Any) -> str:
    import yaml

    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=110, default_flow_style=None)


def _target(path: Path, doc: Any, docs: list[Path]) -> Path:
    """Where a document goes (D786/D787): `problem.yaml` for the loop's one document of the old
    naming (an `<id>.problem.yaml` that said its id, a `.task.*`, a `.yml`); else where it is."""
    folder = path.parent
    if path.name == "problem.yaml":
        return path
    old_naming = (isinstance(doc, dict) and "id" in doc and path.name.endswith(".problem.yaml")) \
        or path.suffix in (".json", ".yml") or path.name.endswith(".task.yaml")
    if old_naming and not (folder / "problem.yaml").exists() and len(docs) == 1:
        return folder / "problem.yaml"
    if path.suffix in (".json", ".yml") or path.name.endswith(".task.yaml"):
        stem = path.name.split(".")[0]
        return folder / f"{stem}.problem.yaml"
    return path


def _rename_record(folder: Path, old: str, new: str, write: bool, say: Say) -> dict[str, str]:
    """D786: the record of a loop whose id was not its folder's name, renamed after the folder --
    `out/` files with the old id as a dotted part, and the campaigns (and their sub-campaigns)."""
    moves: dict[str, str] = {}
    out = folder / "out"
    if not out.is_dir():
        return moves
    for db in [p for p in out.glob(f"{old}.db") if p.is_file()]:
        con = sqlite3.connect(db)
        try:
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "campaigns" in tables:
                ids = [r[0] for r in con.execute("SELECT campaign_id FROM campaigns WHERE campaign_id = ? OR campaign_id LIKE ?",
                                                 (old, old + "/%"))]
                for cid in ids:
                    to = new + cid[len(old):]
                    say(f"the record's campaign {cid} -> {to}")
                    if write:
                        con.execute("PRAGMA foreign_keys = OFF")
                        for table in ("campaigns", "trials", "campaign_events"):
                            if table in tables:
                                con.execute(f"UPDATE {table} SET campaign_id = ? WHERE campaign_id = ?", (to, cid))
                con.commit()
        finally:
            con.close()
    for p in sorted(out.iterdir()):
        parts = p.name.split(".")
        if old not in parts:
            continue
        to = p.with_name(".".join(new if s == old else s for s in parts))
        if to.exists():
            continue
        say(f"out/{p.name} -> out/{to.name}")
        moves[str(p)] = str(to)
        if write:
            p.rename(to)
    pointer = out / f"{new}.db.runs.json"
    if write and pointer.is_file():
        try:
            data = json.loads(pointer.read_text())
            if isinstance(data, dict) and old in data and new not in data:
                data[new] = data.pop(old)
                pointer.write_text(json.dumps(data, indent=1))
        except ValueError:
            pass
    return moves


def migrate_loop(folder: str | Path, *, write: bool = False) -> dict[str, Any]:
    """Each document of a loop's folder brought to today's form: {"documents": [{"file", "to",
    "status" (current | would migrate | migrated | needs a hand | failed), "said", "manual",
    "why", "text"}], "moves": {old path: new path}}. A document is written only when the result
    loads -- checked in a copy of the folder -- and none needs a hand; the original is kept as
    `<file>.orig`."""
    from .document import load_task

    folder = Path(folder)
    docs = documents_of(folder)
    out: list[dict[str, Any]] = []
    moves: dict[str, str] = {}
    for path in docs:
        entry: dict[str, Any] = {"file": path.name, "to": path.name, "said": [], "manual": [], "why": "", "text": ""}
        out.append(entry)
        try:
            moves.update(_one(folder, path, docs, entry, write, load_task))
        except Exception as exc:  # noqa: BLE001 -- one document's trouble, said; the others go on
            entry.update(status="failed", why=f"{type(exc).__name__}: {exc}"[:500])
    return {"documents": out, "moves": moves}


def _one(folder: Path, path: Path, docs: list[Path], entry: dict[str, Any], write: bool, load_task: Any) -> dict[str, str]:
    moves: dict[str, str] = {}
    try:
        doc = _read(path)
    except Exception as exc:  # noqa: BLE001 -- said
        entry.update(status="failed", why=f"not YAML or JSON: {exc}")
        return moves
    new, said, manual = migrate(doc)
    target = _target(path, doc, docs)
    old_id = doc.get("id") if isinstance(doc, dict) else None
    if target != path:
        said.append(f"D786: {path.name} -> {target.name}")
    entry.update(to=target.name, said=said, manual=manual, text=_text(new) if said else "")
    if not said and not manual:
        entry["status"] = "current"
        return moves
    if manual:
        entry["status"] = "needs a hand"
        return moves
    import tempfile

    with tempfile.TemporaryDirectory(prefix="flux-migrate-") as tmp:
        trial = Path(tmp) / folder.name
        shutil.copytree(folder, trial, ignore=shutil.ignore_patterns("out", "runs", "workbench", "__pycache__", "*.db*"),
                        symlinks=True)
        if target != path:
            (trial / path.name).unlink(missing_ok=True)
        (trial / target.name).write_text(entry["text"])
        try:
            load_task(str(trial / target.name))
        except Exception as exc:  # noqa: BLE001 -- said; the original left alone
            entry.update(status="failed", why=f"the result does not load: {exc}")
            return moves
    if not write:
        entry["status"] = "would migrate"
        return moves
    path.with_name(path.name + ".orig").write_text(path.read_text())
    target.write_text(entry["text"])
    if target != path:
        path.unlink()
        moves[str(path)] = str(target)
    if isinstance(old_id, str) and old_id and old_id != folder.name:
        moves.update(_rename_record(folder, old_id, folder.name, True, said.append))
    entry["status"] = "migrated"
    return moves

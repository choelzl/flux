"""Move a stopped loop with its records, trace pointers and server settings.

Prepare changed records before moving anything. Keep replaced files until the server's
transaction commits, so an error restores the original folder and its settings together.
The route holds the launch, agent and maintenance locks throughout.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any

from .admin import _key, cache_root
from .confine import within
from .store import Store, User
from .workspace import Exists, Workspace, WorkspaceError, check_name, _rename_noreplace


def _campaign(value: str, old: str, new: str) -> str:
    return new + value[len(old):] if value == old or value.startswith((old + "/", old + ".")) else value


def _paths(value: Any, pairs: list[tuple[str, str]]) -> Any:
    if isinstance(value, str):
        for old, new in pairs:
            if value == old or value.startswith(old + "/"):
                return new + value[len(old):]
        return value
    if isinstance(value, list):
        return [_paths(x, pairs) for x in value]
    if isinstance(value, dict):
        return {k: _paths(v, pairs) for k, v in value.items()}
    return value


def _regular(path: Path, root: Path) -> None:
    within(path, root)
    if path.is_symlink() or not path.is_file():
        raise WorkspaceError(f"cannot move linked or non-file metadata: {path.name}")


def move_loop(store: Store, source: User, name: str, target: User, to: str, *, keep_permissions: bool = False) -> dict[str, str]:
    old = Workspace(store.data, source.name).app(name)
    new = Workspace(store.data, target.name).root / check_name(to)
    if old.is_symlink():
        raise WorkspaceError("cannot move a linked loop folder")
    within(old, old.parent)
    if old == new:
        raise WorkspaceError("choose a different name or owner")
    if os.path.lexists(new):
        raise Exists(f"application {to!r} exists for {target.name}")
    transfer = source.id != target.id
    cache, new_cache = cache_root() / _key(source.name, name), cache_root() / _key(target.name, to)
    # Cache keys are truncated: never move a folder another loop also owns.
    if cache == new_cache or any(
        _key(u.name, a["name"]) in (cache.name, new_cache.name) and (u.id, a["name"]) != (source.id, name)
        for u in store.users() for a in Workspace(store.data, u.name).apps()
    ):
        raise WorkspaceError("this loop shares a cache key with another loop; cannot move it safely")
    if os.path.lexists(new_cache):
        raise Exists("the destination has an existing cache; clean that unused cache first")
    if cache.is_symlink():
        raise WorkspaceError("cannot move a linked cache folder")
    pairs = [(str(old), str(new)), (str(cache), str(new_cache)), (f"/sandbox/{name}", f"/sandbox/{to}")]
    meta = old / ".flux-app.json"
    _regular(meta, old)
    out = old / "out"
    within(out, old)
    if out.is_symlink():
        raise WorkspaceError("cannot move a linked results folder")
    renames = {p.name: to + p.name[len(name):] for p in out.iterdir()
               if to != name and p.name.startswith(name + ".")} if out.is_dir() else {}
    for before, after in renames.items():
        if os.path.lexists(out / after):
            raise Exists(f"out/{after} already exists")
    pairs = [(str(out / a), str(new / "out" / b)) for a, b in renames.items()] + [
        (f"/sandbox/{name}/out/{a}", f"/sandbox/{to}/out/{b}") for a, b in renames.items()] + pairs

    with tempfile.TemporaryDirectory(prefix=".loop-move-", dir=store.data) as scratch:
        stage = Path(scratch)
        replacements: list[tuple[Path, Path | None, Path]] = []

        def prepare(rel: Path, value: Any) -> None:
            original = old / rel
            _regular(original, old)
            prepared = stage / f"prepared-{len(replacements)}"
            prepared.write_text(json.dumps(value))
            shutil.copymode(original, prepared)
            replacements.append((rel, prepared, stage / f"backup-{len(replacements)}"))

        metadata = json.loads(meta.read_text())
        metadata.update(id=to)
        metadata.pop("last_check", None)         # checks may depend on the original working directory
        metadata.pop("last_start_digest", None)
        prepare(Path(meta.name), metadata)
        for p in sorted(out.iterdir()) if out.is_dir() else []:
            if p.suffix == ".db" and p.name in renames:
                _regular(p, old)
                for suffix in ("-wal", "-shm", "-journal"):
                    if os.path.lexists(str(p) + suffix):
                        _regular(Path(str(p) + suffix), old)
                prepared = stage / f"prepared-{len(replacements)}"
                with closing(sqlite3.connect(f"{p.as_uri()}?mode=ro", uri=True)) as src, closing(sqlite3.connect(prepared)) as dst, dst:
                    src.backup(dst)
                    tables = {row[0] for row in dst.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if "campaigns" in tables:
                        for (cid,) in dst.execute("SELECT campaign_id FROM campaigns").fetchall():
                            changed = _campaign(cid, name, to)
                            if changed != cid:
                                for table in ("campaigns", "trials", "campaign_events"):
                                    if table in tables:
                                        dst.execute(f"UPDATE {table} SET campaign_id = ? WHERE campaign_id = ?", (changed, cid))
                shutil.copymode(p, prepared)
                replacements.append((Path("out") / renames[p.name], prepared, stage / f"backup-{len(replacements)}"))
                for suffix in ("-wal", "-shm", "-journal"):
                    if os.path.lexists(str(p) + suffix):
                        replacements.append((Path("out") / (renames[p.name] + suffix), None, stage / f"backup-{len(replacements)}"))
            elif p.name.endswith(".runs.json"):
                _regular(p, old)
                pointer = json.loads(p.read_text())
                pointer = {_campaign(k, name, to): _paths(v, pairs) for k, v in pointer.items()}
                # The file will already have its new name when installed.
                rel = Path("out") / renames.get(p.name, p.name)
                prepared = stage / f"prepared-{len(replacements)}"
                prepared.write_text(json.dumps(pointer))
                shutil.copymode(p, prepared)
                replacements.append((rel, prepared, stage / f"backup-{len(replacements)}"))

        # SQLite can create WAL/SHM sidecars while reading the source. Include them too.
        for p in out.iterdir() if out.is_dir() else []:
            if to != name and p.name.startswith(name + ".") and p.name not in renames:
                after = to + p.name[len(name):]
                if os.path.lexists(out / after):
                    raise Exists(f"out/{after} already exists")
                renames[p.name] = after
        con = store._db()
        moved = cached = False
        renamed: list[tuple[Path, Path]] = []
        replaced: list[tuple[Path, Path]] = []
        try:
            con.execute("BEGIN IMMEDIATE")
            _rename_noreplace(old, new)
            moved = True
            if cache.exists():
                _rename_noreplace(cache, new_cache)
                cached = True
            for before, after in renames.items():
                a, b = new / "out" / before, new / "out" / after
                a.rename(b)
                renamed.append((a, b))
            for rel, prepared, backup in replacements:
                destination = new / rel
                destination.rename(backup)
                replaced.append((destination, backup))
                if prepared is not None:
                    prepared.rename(destination)
            for row in con.execute("SELECT id, db, log, argv, options FROM runs WHERE user_id = ? AND app = ?", (source.id, name)).fetchall():
                db_path = _paths(row["db"], pairs)
                if Path(db_path).parent == new / "out":
                    db_path = str(new / "out" / renames.get(Path(db_path).name, Path(db_path).name))
                con.execute("UPDATE runs SET user_id = ?, app = ?, db = ?, log = ?, argv = ?, options = ? WHERE id = ?",
                            (target.id, to, db_path, _paths(row["log"], pairs), json.dumps(_paths(json.loads(row["argv"]), pairs)),
                             json.dumps(_paths(json.loads(row["options"]), pairs)), row["id"]))
            for prefix in ("env:loop", "adv", "share", "results"):
                key, destination = f"{prefix}:{source.name}:{name}", f"{prefix}:{target.name}:{to}"
                con.execute("DELETE FROM server WHERE key = ?", (destination,))
                if transfer and prefix == "adv" and keep_permissions:
                    from .runs import PERMISSION_KEYS

                    row = con.execute("SELECT value FROM server WHERE key = ?", (key,)).fetchone()
                    kept = {k: v for k, v in json.loads(row["value"]).items() if k in PERMISSION_KEYS} if row else {}
                    con.execute("DELETE FROM server WHERE key = ?", (key,))
                    if kept:
                        con.execute("INSERT INTO server VALUES (?, ?)", (destination, json.dumps(kept)))
                elif transfer and prefix in ("adv", "share"):
                    con.execute("DELETE FROM server WHERE key = ?", (key,))
                else:
                    con.execute("UPDATE server SET key = ? WHERE key = ?", (destination, key))
            con.commit()
        except BaseException:
            con.rollback()
            for destination, backup in reversed(replaced):
                os.replace(backup, destination)
            for a, b in reversed(renamed):
                b.rename(a)
            if cached:
                new_cache.rename(cache)
            if moved:
                new.rename(old)
            raise
        finally:
            con.close()
    return {"name": to, "owner": target.name}

"""What sits BESIDE a campaign store (docs/decisions.md D344).

A store records what a study tried and what it measured. Three other things accumulate around it,
and every one of them was invented inside one application's demo before anyone noticed they were
not about that application at all:

  * SIDECARS. Calibration residuals, mined lessons, a toolchain baseline, a placement cache — each
    a file next to the store, each with its own `Path(db).with_suffix(...)` spelled out again.
  * A TOOLCHAIN BASELINE. A store outlives the environment that filled it. Which binaries produced
    its numbers is a property of that environment, not of any single result, so it belongs in one
    place rather than on several hundred rows (D316).
  * A MEASUREMENT CACHE. Anything a real tool computes is worth keeping, and is only valid for the
    tools that computed it. Keyed by (toolchain, identity), a tool change makes stale entries
    unreachable instead of served with a caveat (D340).

None of this knows what a fabric, a candidate or a metric is. An application supplies an identity
string and gets memoisation that survives the process and invalidates itself when the tools move.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

__all__ = ["CachedBatch", "MeasurementCache", "SingleFlightMemo", "ToolchainBaseline", "sidecar_path"]


def sidecar_path(db: str | Path, suffix: str) -> Path:
    """The path of a file that belongs to `db`, e.g. `run.db` + `calibration.db`.

    One spelling. Four of these were written out separately and they agreed only by luck; a
    sidecar that lands somewhere else is a study silently keeping two sets of books.
    """
    return Path(db).with_suffix(f".{suffix.lstrip('.')}")


class ToolchainBaseline:
    """The tools a store's measurements were taken with, recorded once.

    NOT on the results. A build of OpenROAD is a property of the run, not of each number it
    produced, and stamping it onto every row stores one fact several hundred times (D316).
    """

    def __init__(self, db: str | Path, fingerprint: dict[str, str]) -> None:
        self.path = sidecar_path(db, "toolchain.json")
        self.fingerprint = dict(fingerprint)

    def recorded(self) -> dict[str, str]:
        """What was recorded, or {} when nothing has been — which is not the same as agreement."""
        if not self.path.exists():
            return {}
        try:
            got = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        return got if isinstance(got, dict) else {}

    def drift(self) -> list[str]:
        """Tools whose current build differs from the recorded one, or [] if none was recorded.

        Records the current fingerprint on first call, so a fresh store acquires a baseline rather
        than reporting drift against nothing.
        """
        previous = self.recorded()
        if not previous:
            try:
                self.path.write_text(json.dumps(self.fingerprint, indent=2, sort_keys=True))
            except OSError:
                pass
            return []
        return sorted(name for name, was in previous.items()
                      if name in self.fingerprint and self.fingerprint[name] != was)

    def accept(self) -> None:
        """Adopt the current tools as this store's baseline."""
        try:
            self.path.write_text(json.dumps(self.fingerprint, indent=2, sort_keys=True))
        except OSError:
            pass


class MeasurementCache:
    """Results of real tool runs, keyed by (toolchain, identity), persisted beside the store.

    A whole-fabric placement is minutes of Yosys and OpenROAD; one run placed the same design
    twice and every later run re-placed all of them (D340). The toolchain is part of the key
    rather than a caveat on the value: bump a tool and the old entries are simply unreachable.

    A cache that cannot be read is a MISS, never an error. This is an optimisation, and a corrupt
    or unwritable sidecar must cost time rather than a study.
    """

    def __init__(self, db: str | Path, fingerprint: dict[str, str], *,
                 suffix: str = "placements.json") -> None:
        self.path = sidecar_path(db, suffix)
        self._fingerprint = dict(fingerprint)

    def _key(self, identity: str) -> str:
        return json.dumps([self._fingerprint, identity], sort_keys=True)

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            got = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        return got if isinstance(got, dict) else {}

    def keys(self) -> set[str]:
        """Every key held, for a caller asking what has already been measured."""
        return set(self._load())

    def holds(self, identity: str) -> bool:
        return self._key(identity) in self._load()

    def get(self, identity: str, default: Any = None) -> Any:
        """The cached result for `identity`, or `default` -- a plain read (D436), so no caller
        has to probe with `get_or_measure(identity, lambda: None)`."""
        return self._load().get(self._key(identity), default)

    def put(self, identity: str, value: Any) -> None:
        """Store `value` for `identity`; an unwritable cache costs time, never correctness."""
        held = self._load()
        held[self._key(identity)] = value
        try:
            self.path.write_text(json.dumps(held, indent=2, sort_keys=True))
        except OSError:
            pass

    def get_or_measure(self, identity: str, measure: Callable[[], Any]) -> Any:
        """The cached result for `identity`, or `measure()` — stored before it is returned."""
        key = self._key(identity)
        held = self._load()
        if key in held:
            return held[key]
        value = measure()
        held[key] = value
        try:
            self.path.write_text(json.dumps(held, indent=2, sort_keys=True))
        except OSError:
            pass          # an unwritable cache costs time, never correctness
        return value


_MISSING = object()


class SingleFlightMemo:
    """An in-process memo with one lock per key -- the second arrival for a key waits for
    the first's answer instead of paying for it again -- optionally persisted through a
    `MeasurementCache`, so the memo is toolchain-keyed and survives the process (D436).
    Two adapters and one flow had each written the dict-plus-locks by hand, none of them
    invalidated by a tool bump; this is the one copy.
    """

    def __init__(self, cache: MeasurementCache | None = None, *, namespace: str = "") -> None:
        import threading

        self.cache = cache
        self.namespace = namespace
        self._values: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._inflight: dict[str, Any] = {}
        self._threading = threading

    def _ident(self, key: Any) -> str:
        return self.namespace + json.dumps(key, sort_keys=True, default=str)

    def _flight_lock(self, ident: str) -> Any:
        with self._lock:
            return self._inflight.setdefault(ident, self._threading.Lock())

    def get_or_measure(self, key: Any, measure: Callable[[], Any]) -> Any:
        ident = self._ident(key)
        with self._lock:
            if ident in self._values:
                return self._values[ident]
        if self.cache is not None:
            held = self.cache.get(ident, _MISSING)
            if held is not _MISSING:
                with self._lock:
                    self._values[ident] = held
                return held
        with self._flight_lock(ident):
            with self._lock:                 # another thread may have finished it meanwhile
                if ident in self._values:
                    return self._values[ident]
            value = measure()
            with self._lock:
                self._values[ident] = value
            if self.cache is not None:
                self.cache.put(ident, value)
            return value


class CachedBatch:
    """Cache-aware batch measurement (D436): partition the items into what the cache holds
    and what must run, run the misses together (however the caller runs them), store the
    non-error results back, and say what happened. Two applications' `Measurer` classes
    were this loop written twice; `hits` and `runs` accumulate over the instance's life.
    """

    def __init__(self, cache: MeasurementCache | None, *, on_progress: Callable[[str], None] | None = None,
                 noun: str = "item", prefix: str = "", parallel: int = 1) -> None:
        self.cache = cache
        self.on_progress = on_progress or (lambda _m: None)
        self.noun, self.prefix, self.parallel = noun, prefix, parallel
        self.hits = self.runs = 0

    def run(self, items: list[Any], *, identity: Callable[[Any], str],
            measure: Callable[[list[Any]], list[Any]],
            is_error: Callable[[Any], bool] = lambda r: isinstance(r, dict) and "error" in r,
            ) -> list[Any]:
        """One result per item, in order: the cached one, or a fresh one from `measure`
        (called once with every miss). Errors are returned, never stored."""
        import time

        results: list[Any] = [None] * len(items)
        todo: list[tuple[int, Any, str]] = []
        for i, item in enumerate(items):
            ident = identity(item)
            if self.cache is not None and self.cache.holds(ident):
                results[i] = self.cache.get(ident)
                self.hits += 1
            else:
                todo.append((i, item, ident))
        if todo:
            self.on_progress(f"{self.prefix}measuring {len(todo)} {self.noun}(s), "
                             f"{self.parallel} at a time ({self.hits} served from cache)")
            started = time.monotonic()
            got = measure([item for _i, item, _ident in todo])
            self.runs += len(todo)
            self.on_progress(f"  {len(todo)} run(s) in {time.monotonic() - started:.0f}s")
            for (i, _item, ident), r in zip(todo, got):
                results[i] = r
                if self.cache is not None and not is_error(r):
                    self.cache.put(ident, r)
        return results

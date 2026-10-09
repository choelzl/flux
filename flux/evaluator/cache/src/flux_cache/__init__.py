"""Files that sit beside a campaign store (D344), application-agnostic.

  * Sidecars: calibration residuals, lessons, toolchain baseline, caches, all at `sidecar_path`.
  * A toolchain baseline: which binaries produced the store's numbers, recorded once (D316).
  * A measurement cache keyed by (toolchain, identity), so a tool change makes stale entries
    unreachable (D340).
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Callable

__all__ = ["MeasurementCache", "ToolchainBaseline", "sidecar_path"]

#: One writer at a time for every cache file in this process (D747: passes running at once).
_WRITING = threading.RLock()


def sidecar_path(db: str | Path, suffix: str) -> Path:
    """The path of a file that belongs to `db`, e.g. `run.db` + `calibration.db`.

    The single place sidecar paths are spelled, so they cannot diverge.
    """
    return Path(db).with_suffix(f".{suffix.lstrip('.')}")


class ToolchainBaseline:
    """The tools a store's measurements were taken with, recorded once.

    Stored per store, not per result: the build is a property of the run (D316).
    """

    def __init__(self, db: str | Path, fingerprint: dict[str, str]) -> None:
        self.path = sidecar_path(db, "toolchain.json")
        self.fingerprint = dict(fingerprint)

    def recorded(self) -> dict[str, str]:
        """What was recorded, or {} when nothing has been (which is not agreement)."""
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

    Tool runs take minutes (D340). The toolchain is part of the key, so after a tool change old
    entries are unreachable. An unreadable or unwritable cache is a miss, never an error.
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
        """The cached result for `identity`, or `default` (D436)."""
        return self._load().get(self._key(identity), default)

    def put(self, identity: str, value: Any) -> None:
        """Store `value` for `identity`; an unwritable cache costs time, never correctness."""
        with _WRITING:
            held = self._load()
            held[self._key(identity)] = value
            self._write(held)

    def _write(self, held: dict[str, Any]) -> None:
        """The whole file, written beside and renamed over (D747): passes running at once never
        read half of it, and each writes what it read with its own entry added, under one lock."""
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.{threading.get_ident()}")
        try:
            tmp.write_text(json.dumps(held, indent=2, sort_keys=True))
            os.replace(tmp, self.path)
        except OSError:
            tmp.unlink(missing_ok=True)

    def get_or_measure(self, identity: str, measure: Callable[[], Any]) -> Any:
        """The cached result for `identity`, or `measure()` — stored before it is returned."""
        key = self._key(identity)
        held = self._load()
        if key in held:
            return held[key]
        value = measure()
        with _WRITING:
            held = self._load()
            held[key] = value
            self._write(held)          # an unwritable cache costs time, never correctness
        return value

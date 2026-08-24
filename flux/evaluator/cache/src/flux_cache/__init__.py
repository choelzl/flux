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

__all__ = ["MeasurementCache", "ToolchainBaseline", "sidecar_path"]


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

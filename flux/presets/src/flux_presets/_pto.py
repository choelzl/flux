"""Reading a PTO-style spec kept in the application's own folder (D1019): its ASL units and the AVS
tests that name them, by reading the files. (Its NDF clauses are the NDF tool's to read.)

A unit is an ASL file under `asl/` whose `// PTO-INSTRUCTION: {json}` line gives its id, mnemonic,
summary and classification. A test is an ASL file under `tests/` whose `// PTO-TEST: {json}` line
names the unit's source and the requirements it verifies.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Test:
    id: str
    path: Path
    requirements: list[str]
    kind: str
    summary: str


@dataclass
class Unit:
    id: str
    mnemonic: str
    path: Path
    source: str                                 # its path inside the spec, as tests name it
    summary: str
    classification: list[str]
    tests: list[Test] = field(default_factory=list)


def _header(text: str, tag: str) -> dict | None:
    for line in text.splitlines()[:20]:
        m = re.match(rf"\s*//\s*{tag}:\s*(\{{.*\}})\s*$", line)
        if m:
            try:
                return json.loads(m.group(1))
            except ValueError:
                return None
    return None


def load(spec: Path) -> dict[str, Unit]:
    """Every unit of the spec at `spec` by id, each with its tests; {} when there is none."""
    units: dict[str, Unit] = {}
    for path in sorted((spec / "asl").rglob("*.asl")) if (spec / "asl").is_dir() else []:
        head = _header(path.read_text(errors="replace"), "PTO-INSTRUCTION")
        if head and head.get("id"):
            units[head["id"]] = Unit(head["id"], str(head.get("mnemonic") or head["id"]), path, str(path.relative_to(spec)),
                                     str(head.get("summary") or ""), list(head.get("classification") or []))
    by_source = {u.source: u for u in units.values()}
    for path in sorted((spec / "tests").rglob("*.asl")) if (spec / "tests").is_dir() else []:
        head = _header(path.read_text(errors="replace"), "PTO-TEST")
        unit = by_source.get(str((head or {}).get("source") or ""))
        if head and head.get("id") and unit is not None:
            unit.tests.append(Test(head["id"], path, [str(r) for r in head.get("requirements") or []],
                                   str(head.get("kind") or ""), str(head.get("summary") or "")))
    return units


def find(units: dict[str, Unit], name: str) -> Unit | None:
    """A unit by its id or its mnemonic, in any case (`PTO-TILE-TADD`, `tadd`)."""
    low = name.strip().lower()
    return next((u for u in units.values() if u.id.lower() == low or u.mnemonic.lower() == low), None)

"""Invented prefetchers as PARTNERS: what the invention loop kept becomes what the study composes.

The invention loop (`flux_invent_prefetcher`) writes every design that compiled to
`applications/prefetcher/invented/`, with its measured numbers. Until now those headers were a
record: a human could read them, nothing could run them again. The study's compose phase adds
partners from a fixed list of the simulator's own sixteen prefetchers; a design that beat the
stack by +0.0014 confirmed was not on that list.

This module builds ONE binary with every kept design installed -- the harness does the wiring,
the Makefile does the rest, about a minute -- and registers each design's knobs so the partner
phases treat an invention exactly like `sms`: composable, tunable, refusable. The binary is
cached by the hash of the headers it contains, so a study that changes nothing rebuilds nothing,
and a measurement's identity carries the digest of every invention its stack enables, so a
result is served across rebuilds when the code that produced it is the same, and never when
it is not.

Only designs the loop measured as NOT harmful are offered (geomean alone >= 0.99): a partner
that costs IPC by itself has already lost the argument.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

#: applications/prefetcher/invented, from lib/src/flux_prefetcher/invented.py
INVENTED_DIR = Path(__file__).resolve().parents[3] / "invented"


@dataclass(frozen=True)
class Invention:
    name: str
    header: str
    knobs: dict[str, int]
    idea: str
    geomean_alone: float | None
    geomean_with_stack: float | None
    #: The stack it was measured beside, so the invention loop can ask the next design to beat
    #: THIS stack with this design in it, rather than the stock stack it has already moved past.
    reference_stack: tuple[str, ...] = ()
    reference_geomean: float | None = None

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.header.encode()).hexdigest()[:12]


def library(root: Path | None = None, *, min_alone: float = 0.99) -> list[Invention]:
    """Every kept design worth offering, best-with-stack first."""
    root = root or INVENTED_DIR
    out: list[Invention] = []
    for meta_path in sorted(root.glob("*.json")) if root.is_dir() else []:
        try:
            meta = json.loads(meta_path.read_text())
            header = (root / f"{meta['name']}.h").read_text()
        except (OSError, ValueError, KeyError):
            continue
        alone = meta.get("geomean_alone")
        if alone is not None and alone < min_alone:
            continue
        # A design that added nothing beside its reference stack -- inert, or worse than the
        # stack alone -- has already lost the argument for a menu slot. Older records carry no
        # reference; they are admitted on the `min_alone` rule only.
        with_stack, reference = meta.get("geomean_with_stack"), meta.get("reference_geomean")
        if with_stack is not None and reference is not None and with_stack <= reference:
            continue
        out.append(Invention(
            name=meta["name"], header=header, knobs=dict(meta.get("knobs", {})),
            idea=str(meta.get("idea", ""))[:200], geomean_alone=alone,
            geomean_with_stack=meta.get("geomean_with_stack", meta.get("geomean_with_bingo")),
            reference_stack=tuple(meta.get("reference_stack") or ()),
            reference_geomean=reference))
    out.sort(key=lambda i: -(i.geomean_with_stack or 0.0))
    return out


def knob_spaces(inventions: list[Invention]) -> dict[str, dict[str, tuple[int, tuple[int, ...]]]]:
    """A tuning space per invention, in `partners.PARTNER_KNOBS` shape, from the design's default.

    Derived, not authored: halves and doubles of what the model chose, clipped to sane integers.
    Coarse on purpose -- a partner is worth a handful of measurements, not a second study.
    """
    spaces: dict[str, dict[str, tuple[int, tuple[int, ...]]]] = {}
    for inv in inventions:
        knobs = {}
        for knob, default in inv.knobs.items():
            d = int(default)
            cands = sorted({max(1, d // 4), max(1, d // 2), d, d * 2, d * 4})
            knobs[knob] = (d, tuple(cands))
        spaces[inv.name] = knobs
    return spaces


def build_binary(inventions: list[Invention], *, source_tree: Path, cache_dir: Path,
                 log: Callable[[str], None] = lambda _m: None) -> Path | None:
    """A simulator with every listed invention installed, cached by what it contains."""
    if not inventions:
        return None
    from flux_codegen_champsim_prefetcher import build, install, stage_tree

    key = hashlib.sha256("|".join(f"{i.name}:{i.digest}" for i in inventions).encode()).hexdigest()[:16]
    out = cache_dir / f"pythia-invented-{key}"
    binary = out / "bin" / "perceptron-no-multi-no-ship-1core"
    if binary.is_file():
        log(f"  invented: reusing the build with {len(inventions)} design(s) ({key})")
        return binary
    log(f"  invented: building a simulator with {[i.name for i in inventions]} installed")
    tree = stage_tree(source_tree, out, warm=False)
    for inv in inventions:
        install(inv.name, inv.header, inv.knobs, tree)
    result = build(tree)
    if not result.ok:
        log(f"  invented: build failed -- {result.first_error[:120]}")
        return None
    log(f"  invented: built in {result.elapsed_s:.0f}s")
    return result.binary


def register(inventions: list[Invention]) -> list[str]:
    """Make the inventions known to the partner phases. Returns their names, best first."""
    from . import partners, space

    partners.PARTNER_KNOBS.update(knob_spaces(inventions))
    names = [i.name for i in inventions]
    # FIRST, not last. Compose offers six partners a round, and appended to twelve stock ones
    # the inventions were never reached: the first run built the binary, listed them on the
    # menu, and composed `bingo+sms+stride` exactly as before. They earn the front: each was
    # measured beside the actual stack, which is better evidence than the family survey that
    # ordered the stock list.
    space.PARTNERS = tuple(names) + tuple(p for p in space.PARTNERS if p not in names)
    return names

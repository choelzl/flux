"""Measuring a PE on ASAP7: the synthesis screen and the placement stage, cached and parallel.

Two stages, one method (D365). The SCREEN synthesizes with Yosys and times the mapped netlist
with OpenSTA, no placement, no wires: a few seconds, optimistic by what the wires would cost,
identical in method across candidates, so it orders the space. The CONFIRM stage places the
netlist with OpenROAD and estimates parasitics from placement: tens of seconds, and the number
a report may quote. Both are keyed in one cache by the tool fingerprints and the exact source,
so a resumed run re-measures nothing and a changed tool makes old entries unreachable (D340).

Every measurement is a separate single-threaded process, so candidates run in a thread pool
sized like the interconnect study's escalation workers (D290).
"""

from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .config import PeConfig
from .objective import Score, Scored
from .rtl import Design

SCREEN, CONFIRM = "synthesis", "placement"


def _identity(design: Design, stage: str, clock_period_ps: float) -> str:
    """Source, stage, clock and MAPPING: two designs from one RTL differ in the netlist."""
    digest = hashlib.sha256(design.all_sources.encode()).hexdigest()[:16]
    return (f"{design.module_name}@{digest}|{stage}|{clock_period_ps:.0f}ps"
            f"|map={design.config.mapping}")


def measure_one(design: Design, *, stage: str, clock_period_ps: float,
                timeout_s: float = 600.0) -> dict[str, Any]:
    """One PE through one stage. Returns a plain dict (cacheable) or `{"error": ...}`."""
    from flux_evaluator_openroad import run_ppa_flow, run_synthesis_flow

    kw = dict(clock_port="clk" if design.config.clocked else None,
              reset_port="rst_n" if design.config.clocked else None,
              clock_period_ps=clock_period_ps, timeout_s=timeout_s,
              map_for=design.config.mapping)
    try:
        if stage == SCREEN:
            report = run_synthesis_flow(design.all_sources, design.module_name, **kw)
        elif stage == CONFIRM:
            report = run_ppa_flow(design.all_sources, design.module_name, flow_depth="placement",
                                  repair_design=True, **kw)
        else:
            raise ValueError(stage)
    except Exception as exc:                                              # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {str(exc)[:400]}"}
    return {**report.metrics(), "flow_depth": report.flow_depth,
            "critical_path": _critical_path(report.openroad_log_tail)}


def _critical_path(log_tail: str) -> str:
    """The startpoint/endpoint of the worst path, from the report the flow already printed."""
    start = end = ""
    for line in log_tail.splitlines():
        if line.strip().startswith("Startpoint:"):
            start = line.strip()[len("Startpoint:"):].strip()
        elif line.strip().startswith("Endpoint:"):
            end = line.strip()[len("Endpoint:"):].strip()
    return f"{start} -> {end}" if start or end else ""


class Measurer:
    """Cache-aware, parallel measurement of many designs on one stage -- the cache
    partition, store-back and progress lines are `flux_cache.CachedBatch`'s (D436); this
    class runs the misses through a thread pool and turns results into `Scored`."""

    def __init__(self, cache, *, stage: str, clock_period_ps: float, workers: int,
                 on_progress: Callable[[str], None] = lambda _m: None,
                 run: Callable[..., dict[str, Any]] = measure_one) -> None:
        from flux_cache import CachedBatch

        self.cache, self.stage, self.clock_period_ps = cache, stage, clock_period_ps
        self.workers = max(1, workers)
        self.on_progress = on_progress
        self.run = run
        self._batch = CachedBatch(cache, on_progress=on_progress, noun="design",
                                  prefix=f"  {stage}: ", parallel=self.workers)

    @property
    def runs(self) -> int:
        return self._batch.runs

    @property
    def hits(self) -> int:
        return self._batch.hits

    def _run_all(self, designs: list[Design]) -> list[dict[str, Any]]:
        with cf.ThreadPoolExecutor(max_workers=min(self.workers, len(designs))) as pool:
            return list(pool.map(
                lambda d: self.run(d, stage=self.stage, clock_period_ps=self.clock_period_ps),
                designs))

    def score(self, designs: list[Design], latency: dict[str, int], provenance: str,
              refused: list[tuple[str, str]]) -> list[Scored]:
        results = self._batch.run(
            designs, identity=lambda d: _identity(d, self.stage, self.clock_period_ps),
            measure=self._run_all)
        out: list[Scored] = []
        for d, got in zip(designs, results):
            if "error" in got:
                refused.append((d.config.label, f"{self.stage} failed: {got['error'][:200]}"))
                continue
            out.append(self._scored(d, got, latency, provenance))
        return out

    @staticmethod
    def _scored(d: Design, got: dict[str, Any], latency: dict[str, int], provenance: str
                ) -> Scored:
        return Scored(config=d.config, provenance=provenance, score=Score(
            area_um2=float(got["area_um2"]), worst_slack_ps=float(got["worst_slack_ps"]),
            clock_period_ps=float(got["clock_period_ps"]), power_w=float(got["power_w"]),
            cell_count=int(got["cell_count"]), latency_cycles=latency.get(d.config.label, 0),
            flow_depth=str(got["flow_depth"])))


def tools_missing() -> list[str]:
    """Which of the three tools this study needs are not on PATH."""
    import shutil

    return [t for t in ("verilator", "yosys", "openroad") if shutil.which(t) is None]


def toolchain() -> dict[str, str]:
    from flux_evaluator_abi.toolchain import toolchain_fingerprint

    return toolchain_fingerprint(("verilator", "yosys", "openroad"))


__all__ = ["CONFIRM", "Measurer", "SCREEN", "measure_one", "toolchain", "tools_missing"]

"""ChampSim L2 prefetcher evaluator: real simulated IPC for one trace (docs/decisions.md D349).

ONE TRACE PER CALL, on purpose. The study's objective is a geomean speedup across three traces,
which makes "evaluate a configuration" sound like the natural unit. It is the wrong unit here for
two reasons. Ray fans out per call, and three traces per call caps a 64-core machine at 21
concurrent configurations instead of 64 concurrent runs. And the geomean is an OBJECTIVE, not a
measurement -- it needs the no-prefetcher baseline, which is a property of the study, not of the
tool. An evaluator reports what the tool said; `flux_prefetcher.objective` decides what it means.

What a run costs, measured on this machine: about 6 minutes for the study's 100M warmup + 150M
simulated instructions, essentially independent of the configuration. A rebuild of the simulator
costs 7 seconds by comparison, which is why the prefetcher's SOURCE is a reachable design space
too -- see `docs/decisions.md` D349 -- but this evaluator does not need one: the `multi` L2C slot
selects the prefetcher at run time from `--l2c_prefetcher_types`.

Metrics, with honest methods:
  `ipc`            MEASURED  -- what ChampSim simulated
  `cycles`         MEASURED
  `instructions`   MEASURED
  `storage_bytes`  ANALYTIC  -- the Bingo table model, free to compute, so carried along rather
                               than requiring a second evaluation to learn a design's cost
"""

from __future__ import annotations

import re
import subprocess
import tempfile
import time
from pathlib import Path

from flux_evaluator_abi import (
    Bottleneck, Budget, Candidate, Domain, Escalation, Estimate, Limiter, Method,
    Provenance, Result, Validity,
)
from flux_prefetcher.config import BingoConfig, render_ini, storage_bytes, validate

from .binary import resolve_binary, resolve_trace

EVALUATOR_ID = "champsim_bingo@0.1"

#: The study's own instruction counts (proj/run_benchmark.sh). Note that script passes each flag
#: TWICE -- 100M/500M then 100M/150M -- and ChampSim takes the last, which the shipped baselines
#: confirm ("simulation_instructions 150000000", "Finished CPU 0 instructions: 150000000"). Passed
#: once here, at the values that actually ran.
WARMUP_INSTRUCTIONS = 100_000_000
SIMULATION_INSTRUCTIONS = 150_000_000

#: THE TWO RUNGS, and why instruction counts are not a caller's choice.
#:
#: How long to simulate is a FIDELITY decision, not a preference: it decides whether a number is
#: a ranking hint or an answer. A short run costs about 7 seconds against 360, and
#: `experiments/screen_fidelity.py` measured its rank correlation with the full length at
#: Spearman +0.776 -- reliable enough to sort candidates, not to quote. Exposing the counts as a
#: flag pushed that judgement onto whoever typed the command, and the honest default (full length
#: everywhere) is 50x more expensive than the search needs for most of its work.
#:
#: So the study screens on SCREEN and decides on DECIDE, and reports which numbers came from
#: which.
#:
#: SCREEN WAS 2M+3M AND THAT WAS TOO SHORT TO DECIDE ANYTHING. What matters for a search is not
#: how optimistic a rung is — a constant offset cancels in every comparison — but how much that
#: offset VARIES between designs. Measured against confirmed full-length numbers for four stacks:
#:
#:     screen      mean optimism    spread     cost per simulation
#:     2M+3M          +0.0226       0.0191            ~7 s
#:     10M+15M        +0.0049       0.0018           ~35 s
#:     25M+40M        +0.0016       0.0008           ~80 s
#:
#: The composition gain the search exists to find is +0.0075 confirmed. At 2M+3M the spread is
#: 0.019 — two and a half times the effect — so the search was ranking noise. It put `+L1D ipcp`
#: FIRST at 1.0785 when full length has it LAST at 1.0426, and every invented prefetcher that
#: "beat bingo" on the screen lost at full length.
#:
#: 10M+15M costs five times more per simulation and cuts the comparison error by a factor of ten,
#: which is the trade that makes a search decision mean something.
SCREEN = (10_000_000, 15_000_000)
DECIDE = (WARMUP_INSTRUCTIONS, SIMULATION_INSTRUCTIONS)

_IPC = re.compile(r"^Core_0_IPC\s+([0-9.]+)\s*$", re.MULTILINE)
_FINISHED = re.compile(
    r"^Finished CPU\s+\d+\s+instructions:\s*(\d+)\s+cycles:\s*(\d+)", re.MULTILINE)


class NotExpressibleError(ValueError):
    """This candidate does not describe an L2 prefetcher configuration."""


class SimulationFailedError(RuntimeError):
    """ChampSim ran and did not produce a usable result.

    Distinct from `InvalidConfig` (rejected before running) and `ChampSimUnavailableError` (never
    ran at all) because the three want different responses: fix the candidate, fix the
    environment, or investigate. Collapsing them into one "evaluation failed" is what turns a
    broken toolchain into a search that quietly reports every design as bad.
    """


def config_of(candidate: Candidate) -> tuple[BingoConfig, list[str]]:
    """The `(BingoConfig, prefetcher types)` a candidate describes."""
    arch = candidate.arch
    if not isinstance(arch, dict) or "prefetcher" not in arch:
        raise NotExpressibleError(
            "champsim_bingo needs an arch dict with a `prefetcher` block "
            "({kind: 'bingo', types: ['bingo'], region_size: ..., ...})")
    spec = dict(arch["prefetcher"])
    types = list(spec.pop("types", ["bingo"]))
    spec.pop("kind", None)
    known = {f.name for f in BingoConfig.__dataclass_fields__.values()}
    unknown = set(spec) - known
    if unknown:
        raise NotExpressibleError(f"unknown prefetcher knobs: {sorted(unknown)}")
    return BingoConfig(**spec), types


def _trace_of(candidate: Candidate) -> tuple[Path, int, int]:
    wl = candidate.workload
    if not isinstance(wl, dict) or "trace" not in wl:
        raise NotExpressibleError(
            "champsim_bingo needs a workload dict with a `trace` path (one trace per call)")
    return (resolve_trace(wl["trace"]),
            int(wl.get("warmup_instructions", WARMUP_INSTRUCTIONS)),
            int(wl.get("simulation_instructions", SIMULATION_INSTRUCTIONS)))


def run_champsim(
    cfg: BingoConfig,
    trace: Path,
    *,
    types: list[str] | None = None,
    warmup_instructions: int = WARMUP_INSTRUCTIONS,
    simulation_instructions: int = SIMULATION_INSTRUCTIONS,
    binary: str | Path | None = None,
    timeout_s: float | None = None,
    partner_knobs: dict[str, object] | None = None,
) -> dict[str, float]:
    """Run one simulation and return `{ipc, cycles, instructions, wall_clock_s}`.

    `types=[]` runs with no L2 prefetcher at all -- the baseline every speedup is quoted against.
    The config file is still written and passed, because ChampSim reads knobs with `atoi()` and
    silently defaults a missing key: a baseline run under accidentally-different settings is worse
    than no baseline.
    """
    exe = resolve_binary(binary)
    validate(cfg)            # never hand ChampSim something bingo.cc will abort on
    selected = ["bingo"] if types is None else list(types)

    with tempfile.TemporaryDirectory(prefix="flux-champsim-") as tmp:
        # ONE ini for the whole stack. ChampSim reads a single `--config` file and every
        # prefetcher picks its own keys out of it, so a partner's knobs go in the same file as
        # Bingo's. Written after Bingo's, so an explicit partner value wins over anything the
        # simulator would otherwise default.
        ini = Path(tmp) / "bingo.ini"
        text = render_ini(cfg)
        if partner_knobs:
            from flux_prefetcher.partners import render_partner_ini

            text += render_partner_ini(dict(partner_knobs))
        ini.write_text(text)
        cmd = [
            str(exe),
            f"--warmup_instructions={warmup_instructions}",
            f"--simulation_instructions={simulation_instructions}",
            f"--config={ini}",
        ]
        # ONE FLAG PER PREFETCHER, not a comma list. `knobs.cc` does
        # `l2c_prefetcher_types.push_back(string(value))` on the whole value and never splits on
        # commas, so `--l2c_prefetcher_types=bingo,next_line` registers a single prefetcher named
        # "bingo,next_line" and ChampSim exits with "unsupported prefetcher type". Repeating the
        # flag is what fills the vector.
        cmd += [f"--l2c_prefetcher_types={name}" for name in selected]
        cmd += ["-traces", str(trace)]

        started = time.monotonic()
        try:
            from flux_profile import phase

            with phase("tool:champsim", why="no-prefetch baseline" if not selected
                       else "+".join(selected),
                       trace=trace.name, warm=warmup_instructions,
                       sim=simulation_instructions):
                proc = subprocess.run(cmd, capture_output=True, text=True,
                                      timeout=timeout_s)
        except subprocess.TimeoutExpired as exc:
            raise SimulationFailedError(
                f"ChampSim exceeded {timeout_s}s on {trace.name}. A full 100M+150M run takes "
                f"about 6 minutes; a budget under that will time out every candidate."
            ) from exc
        wall = time.monotonic() - started

    if proc.returncode != 0:
        # stderr too: "unsupported prefetcher type" goes there, and an error that quotes only
        # the banner from stdout sent a whole investigation down the wrong path.
        said = (proc.stderr.strip() or proc.stdout.strip())[-400:]
        raise SimulationFailedError(
            f"ChampSim exited {proc.returncode} on {trace.name} (binary {exe.name}): {said!s}")
    ipc = _IPC.search(proc.stdout)
    if ipc is None:
        raise SimulationFailedError(
            f"ChampSim produced no Core_0_IPC line for {trace.name} (ran {wall:.0f}s, exit 0). "
            f"Tail: {proc.stdout[-400:]!s}")
    finished = _FINISHED.search(proc.stdout)
    # Every `Core_0_*` line, kept: cache hit/miss counts and prefetch useful/useless/late are
    # what say WHY an IPC is what it is, and a design that only ever sees the IPC is designing
    # blind. Cheap -- it is a regex over output the run already produced.
    stats = {m.group(1): float(m.group(2))
             for m in re.finditer(r"^Core_0_(\w+)\s+([0-9.]+)\s*$", proc.stdout, re.MULTILINE)}
    return {
        "ipc": float(ipc.group(1)),
        "instructions": float(finished.group(1)) if finished else float("nan"),
        "cycles": float(finished.group(2)) if finished else float("nan"),
        "wall_clock_s": wall,
        "stats": stats,
    }


class ChampSimBingoEvaluator:
    """Evaluator ABI adapter (docs/evaluator-abi.md) over one ChampSim run.

    Holds no state beyond the binary path: every run writes its own config into its own temp
    directory, so any number of these can run concurrently under Ray against one shared binary.
    """

    def __init__(self, binary: str | Path | None = None) -> None:
        self.binary = binary

    def evaluate(self, candidate: Candidate, budget: Budget,
                 metrics: frozenset[str]) -> Result:
        cfg, types = config_of(candidate)
        trace, warmup, sim = _trace_of(candidate)
        measured = run_champsim(
            cfg, trace, types=types, warmup_instructions=warmup,
            simulation_instructions=sim, binary=self.binary,
            timeout_s=budget.wall_clock_s if budget.wall_clock_s else None)

        def est(value: float, unit: str, method: str) -> Estimate:
            return Estimate(value=value, ci_low=value, ci_high=value, unit=unit, method=method)

        return Result(
            metrics={
                "ipc": est(measured["ipc"], "instructions_per_cycle", Method.SIMULATED),
                "cycles": est(measured["cycles"], "cycles", Method.SIMULATED),
                "instructions": est(measured["instructions"], "instructions", Method.SIMULATED),
                "storage_bytes": est(float(storage_bytes(cfg)), "bytes", Method.ANALYTIC),
            },
            validity=Validity(ok=True, checker_version="bingo@0.1", violations=()),
            domain=Domain(in_domain=True),
            bottleneck=Bottleneck(limiter=Limiter.MEMORY),
            provenance=Provenance(
                evaluator=EVALUATOR_ID,
                inputs={
                    "trace": trace.name,
                    "types": ",".join(types) if types else "none",
                    "warmup_instructions": str(warmup),
                    "simulation_instructions": str(sim),
                    **{k: str(v) for k, v in cfg.knobs().items()},
                    "bingo_l2c_thresh": str(cfg.l2c_thresh),
                },
                wall_clock_s=measured["wall_clock_s"],
            ),
            escalation=Escalation(recommended=False),
        )

    def evaluate_batch(self, candidates: list[Candidate], budget: Budget,
                       metrics: frozenset[str]) -> list[Result]:
        """The ABI's sequential default. Real concurrency comes from wrapping this evaluator in
        `flux_chia_nodes.ChiaParallelEvaluator`, which dispatches each candidate as a Ray task --
        the same route every other backend in this repo takes to get parallel (D-.../parallel.py).
        """
        return [self.evaluate(c, budget, metrics) for c in candidates]

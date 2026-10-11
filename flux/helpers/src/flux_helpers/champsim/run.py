"""One ChampSim simulation: knobs as ini text, L2 prefetchers by name, one trace."""

from __future__ import annotations

import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable

from .binary import resolve_binary

_IPC = re.compile(r"^Core_0_IPC\s+([0-9.]+)\s*$", re.M)
_CUMULATIVE = re.compile(r"^CPU 0 cumulative IPC:\s*([0-9.]+)", re.M)
_FINISHED = re.compile(r"^Finished CPU\s+\d+\s+instructions:\s*(\d+)\s+cycles:\s*(\d+)", re.M)
_STAT = re.compile(r"^Core_0_(\w+)\s+([0-9.eE+-]+)\s*$", re.M)

#: The L2 prefetch counters surfaced as `l2_pf_<name>`, from `Core_0_L2C_prefetch_<name>`.
L2_PREFETCH = ("requested", "issued", "filled", "useful", "useless", "late")


class SimulationFailedError(RuntimeError):
    """ChampSim ran and did not produce a usable result."""


def simulate(binary: str | Path | None, ini_text: str, types: Iterable[str], trace: str | Path,
             warmup: int, sim: int, *, timeout_s: float | None = None) -> dict[str, Any]:
    """Run once; `{ipc, cycles, instructions, l2_pf_*, wall_clock_s, stats}`.

    `types` empty runs with no L2 prefetcher. `ini_text` goes in as `--config` when non-empty.
    """
    exe = resolve_binary(binary)
    trace = Path(trace)
    if not trace.is_file():
        raise FileNotFoundError(f"trace not found: {trace}")
    selected = list(types)
    with tempfile.TemporaryDirectory(prefix="flux-champsim-") as tmp:
        cmd = [str(exe), f"--warmup_instructions={warmup}", f"--simulation_instructions={sim}"]
        if ini_text.strip():
            ini = Path(tmp) / "knobs.ini"
            ini.write_text(ini_text)
            cmd.append(f"--config={ini}")
        # one flag per prefetcher: knobs.cc does not split on commas
        cmd += [f"--l2c_prefetcher_types={name}" for name in selected]
        cmd += ["-traces", str(trace)]
        started = time.monotonic()
        try:
            from flux_profile import phase

            with phase("tool:champsim", why="+".join(selected) or "no-prefetch baseline",
                       trace=trace.name, warm=warmup, sim=sim):
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
        except subprocess.TimeoutExpired as exc:
            raise SimulationFailedError(f"ChampSim exceeded {timeout_s}s on {trace.name}") from exc
        wall = time.monotonic() - started
    if proc.returncode != 0:
        said = (proc.stderr.strip() or proc.stdout.strip())[-400:]
        raise SimulationFailedError(f"ChampSim exited {proc.returncode} on {trace.name} ({exe.name}): {said}")
    return parse(proc.stdout, trace.name) | {"wall_clock_s": wall}


def parse(stdout: str, what: str = "the run") -> dict[str, Any]:
    """The numbers out of ChampSim's stdout."""
    ipc = _IPC.search(stdout) or _CUMULATIVE.search(stdout)
    if ipc is None:
        raise SimulationFailedError(f"ChampSim printed no IPC for {what}. Tail: {stdout[-400:]}")
    finished = _FINISHED.search(stdout)
    stats = {m.group(1): float(m.group(2)) for m in _STAT.finditer(stdout)}
    return {
        "ipc": float(ipc.group(1)),
        "instructions": float(finished.group(1)) if finished else float("nan"),
        "cycles": float(finished.group(2)) if finished else float("nan"),
        **{f"l2_pf_{k}": stats.get(f"L2C_prefetch_{k}", 0.0) for k in L2_PREFETCH},
        "stats": stats,
    }

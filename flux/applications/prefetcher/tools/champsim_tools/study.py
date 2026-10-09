"""An artifact measured on every trace in a directory: IPC, speedup over the baseline, L2 counters.

The artifact is an `.ini` for the prebuilt binary (its `l2c_prefetcher_types = a,b` line picks the
prefetchers and is stripped before ChampSim reads the file) or a `.h` prefetcher built in
(`build.build_header`) and run with its own name first.
"""

from __future__ import annotations

import math
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Iterable

from . import traces as tr
from .baseline import baseline_ipc
from .binary import resolve_binary
from .build import build_header
from .run import simulate

_TYPES = re.compile(r"^\s*l2c_prefetcher_types\s*=\s*(.*?)\s*$", re.M)


def split_ini(text: str) -> tuple[str, list[str]]:
    """(ini without its type lines, the L2 prefetcher types they named)."""
    types = [t.strip() for m in _TYPES.finditer(text) for t in m.group(1).split(",") if t.strip()]
    return _TYPES.sub("", text), types


class BuildFailedError(RuntimeError):
    def __init__(self, first_error: str) -> None:
        super().__init__(first_error)
        self.first_error = first_error


def prepare(artifact: str | Path, with_types: Iterable[str] = (),
            config: str | Path | None = None) -> tuple[Path, str, list[str]]:
    """(binary, ini text, prefetcher types) for an `.ini` or a C++ (`.h`, `.cpp`) artifact.

    `config` is an extra `.ini` read the same way: a header's partners need their knobs (Bingo
    with ChampSim's defaults aborts).
    """
    path = Path(artifact)
    extra, extra_types = split_ini(Path(config).read_text()) if config else ("", [])
    if path.suffix in (".h", ".hpp", ".hh", ".cpp", ".cc", ".cxx"):     # a document with `language: cpp` writes .cpp
        got = build_header(path.read_text())
        if not got.ok:
            raise BuildFailedError(got.first_error or "the build failed")
        return got.binary, extra, [got.name, *extra_types, *with_types]
    ini, types = split_ini(path.read_text())
    return resolve_binary(), ini.rstrip("\n") + "\n" + extra, [*types, *extra_types, *with_types]


def measure(artifact: str | Path, traces_dir: str | Path, warmup: int, sim: int,
            with_types: Iterable[str] = (), jobs: int | None = None,
            config: str | Path | None = None) -> dict[str, float]:
    """`ipc_<t>`, `baseline_ipc_<t>`, `speedup_<t>`, `l2_pf_issued_<t>`, `l2_pf_useful_<t>` per trace,
    and `geomean_speedup`. Runs and baselines go concurrently, `jobs` at a time (default: one per trace)."""
    binary, ini, types = prepare(artifact, with_types, config)
    paths = tr.stage(tr.resolve(traces_dir))
    with ThreadPoolExecutor(max_workers=jobs or len(paths)) as pool:
        runs = {n: pool.submit(simulate, binary, ini, types, p, warmup, sim) for n, p in paths.items()}
        bases = {n: pool.submit(baseline_ipc, binary, p, warmup, sim) for n, p in paths.items()}
        out: dict[str, float] = {}
        for n in paths:
            got, base = runs[n].result(), bases[n].result()
            out |= {f"ipc_{n}": got["ipc"], f"baseline_ipc_{n}": base, f"speedup_{n}": got["ipc"] / base,
                    f"l2_pf_issued_{n}": got["l2_pf_issued"], f"l2_pf_useful_{n}": got["l2_pf_useful"]}
    speedups = [out[f"speedup_{n}"] for n in paths]
    out["geomean_speedup"] = math.exp(sum(math.log(s) for s in speedups) / len(speedups))
    return out

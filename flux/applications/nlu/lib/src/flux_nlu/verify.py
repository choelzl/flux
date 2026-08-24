"""The NLU simulation harness (D408): Verilator sweeps, raw bits in, raw bits out.

Purpose-built rather than reusing `flux_codegen_rtl_harness`: that harness judges
pass/fail against exact expected values, and this study's verdict is QUANTITATIVE --
ULP distances over up to 65536 inputs per operator, computed in numpy against the
declared reference. So the C++ driver here does the dumbest possible thing: stream
uint16 patterns in, stream the DUT's uint16 outputs back, and let `fp16.ulp_report`
be the judge. Exhaustion is cheap (a 64Ki sweep is milliseconds), which is the whole
reason FP16 correctness can be a proof.

THE INTERFACE CONTRACT every generated design meets (the prompts state it verbatim):

  shared unit:  module nlu      (input wire clk, input wire [15:0] x,
                                 input wire [2:0] op, output wire [15:0] y);
  per-op unit:  module nlu_<op> (input wire clk, input wire [15:0] x,
                                 output wire [15:0] y);

`clk` is always a port; a combinational design (latency 0) simply ignores it. A
pipelined design of declared latency L accepts one x per cycle and answers L cycles
later -- no handshake, no stall, no reset: a transcendental unit is a pure pipeline,
and the harness clocks in N+L cycles and reads the last N answers.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

__all__ = ["CompileError", "SweepSim", "build_sim", "tools_missing"]


class CompileError(RuntimeError):
    """Verilator rejected the source; `str(exc)` carries the tail the repair prompt feeds on."""


def tools_missing() -> list[str]:
    return [t for t in ("verilator",) if shutil.which(t) is None]


_DRIVER = r"""
#include "V{top}.h"
#include "verilated.h"
#include <cstdio>
#include <cstdint>
#include <vector>

int main(int argc, char** argv) {{
    Verilated::commandArgs(argc, argv);
    V{top} dut;
    std::vector<uint16_t> xs;
    {{ FILE* f = fopen(argv[1], "rb"); uint16_t v;
       while (fread(&v, 2, 1, f) == 1) xs.push_back(v); fclose(f); }}
    const long n = (long)xs.size();
    std::vector<uint16_t> ys((size_t)n);
    const int latency = {latency};
{op_assign}
    if (latency == 0) {{
        for (long i = 0; i < n; i++) {{
            dut.x = xs[(size_t)i]; dut.eval();
            ys[(size_t)i] = (uint16_t)dut.y;
        }}
    }} else {{
        // latency L == the design's register count: x driven in cycle t is on y
        // after the edge of cycle t+L-1 (edge t counts as the first).
        for (long c = 0; c < n + latency - 1; c++) {{
            dut.x = xs[(size_t)(c < n ? c : n - 1)];
            dut.clk = 0; dut.eval();
            dut.clk = 1; dut.eval();
            long i = c - latency + 1;
            if (i >= 0 && i < n) ys[(size_t)i] = (uint16_t)dut.y;
        }}
    }}
    {{ FILE* f = fopen(argv[2], "wb");
       fwrite(ys.data(), 2, (size_t)n, f); fclose(f); }}
    return 0;
}}
"""


class SweepSim:
    """One compiled DUT, runnable over arbitrary input sets."""

    def __init__(self, binary: Path, workdir: Path) -> None:
        self._bin = binary
        self._dir = workdir

    def run(self, xs: np.ndarray, *, timeout_s: float = 300.0) -> np.ndarray:
        inp = self._dir / "in.bin"
        out = self._dir / "out.bin"
        inp.write_bytes(xs.astype("<u2").tobytes())
        r = subprocess.run([str(self._bin), str(inp), str(out)],
                           capture_output=True, text=True, timeout=timeout_s)
        if r.returncode != 0:
            raise RuntimeError(f"simulation exited {r.returncode}: {r.stderr[-400:]}")
        got = np.frombuffer(out.read_bytes(), dtype="<u2")
        if got.size != xs.size:
            raise RuntimeError(f"simulation wrote {got.size} outputs for {xs.size} inputs")
        return got.astype(np.uint16)


def build_sim(source: str, *, top: str, latency: int, opcode: int | None,
              workdir: str | Path | None = None,
              timeout_s: float = 300.0) -> SweepSim:
    """Verilate `source` with the sweep driver baked for (`latency`, `opcode`).

    `opcode=None` is the per-op interface (no `op` port). The build directory is
    keyed by a content hash so a study rebuilding the same design for another
    operator reuses nothing wrongly and everything rightly."""
    base = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="flux-nlu-"))
    key = hashlib.sha256(
        f"{source}|{top}|{latency}|{opcode}".encode()).hexdigest()[:16]
    bdir = base / f"sim-{key}"
    binary = bdir / "obj_dir" / f"V{top}"
    if binary.is_file():
        return SweepSim(binary, bdir)
    bdir.mkdir(parents=True, exist_ok=True)
    (bdir / "dut.sv").write_text(source)
    op_assign = f"    dut.op = {opcode};" if opcode is not None else ""
    (bdir / "sweep.cpp").write_text(
        _DRIVER.format(top=top, latency=int(latency), op_assign=op_assign))
    r = subprocess.run(
        ["verilator", "--cc", "dut.sv", "--top-module", top, "--exe", "sweep.cpp",
         "--build", "-j", "0", "-Wno-fatal", "--quiet"],
        cwd=bdir, capture_output=True, text=True, timeout=timeout_s)
    if r.returncode != 0 or not binary.is_file():
        raise CompileError((r.stderr or r.stdout)[-1500:])
    return SweepSim(binary, bdir)

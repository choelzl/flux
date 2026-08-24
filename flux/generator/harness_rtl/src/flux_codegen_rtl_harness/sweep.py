"""A SWEEP simulator (D567, review 2 R13): one Verilated DUT, driven over arbitrary input
sets by a C++ driver, for the studies that check a combinational or pipelined function on
every input word (the NLU's 65,536 FP16 inputs) rather than on a list of vectors -- the
harness's other driver (`compile_and_run`, testbench.sv from a spec) is that one.

`build_sweep_sim` writes `dut.sv`, checks it with slang first when the machine has it (the
strictest open-source SystemVerilog front end, with diagnostics a repair prompt can act on;
Verilator's "syntax error, unexpected IDENTIFIER" cost the NLU campaign whole rounds), then
Verilates it with the driver baked for the DUT's ports, latency and opcode. The build
directory is keyed by a content hash, so the same design is built once.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from .errors import CompileError

__all__ = ["SweepSim", "build_sweep_sim"]

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
            dut.{in_port} = xs[(size_t)i]; dut.eval();
            ys[(size_t)i] = (uint16_t)dut.{out_port};
        }}
    }} else {{
        // latency L == the design's register count: an input driven in cycle t is on the
        // output after the edge of cycle t+L-1 (edge t counts as the first).
        for (long c = 0; c < n + latency - 1; c++) {{
            dut.{in_port} = xs[(size_t)(c < n ? c : n - 1)];
            dut.{clock} = 0; dut.eval();
            dut.{clock} = 1; dut.eval();
            long i = c - latency + 1;
            if (i >= 0 && i < n) ys[(size_t)i] = (uint16_t)dut.{out_port};
        }}
    }}
    {{ FILE* f = fopen(argv[2], "wb");
       fwrite(ys.data(), 2, (size_t)n, f); fclose(f); }}
    return 0;
}}
"""


class SweepSim:
    """One compiled DUT, runnable over arbitrary 16-bit input sets."""

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


def build_sweep_sim(source: str, *, top: str, latency: int, opcode: int | None = None,
                    in_port: str = "x", out_port: str = "y", op_port: str = "op", clock: str = "clk",
                    workdir: str | Path | None = None, prefix: str = "flux-sweep-",
                    timeout_s: float = 300.0) -> SweepSim:
    """Verilate `source` with the sweep driver baked for its ports, `latency` (its register
    count) and `opcode` (None: no opcode port is driven). A `CompileError` carries the tool's
    own diagnostics: slang's when the strict check refuses, Verilator's otherwise."""
    base = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix=prefix))
    key = hashlib.sha256(f"{source}|{top}|{latency}|{opcode}|{in_port}|{out_port}|{op_port}|{clock}".encode()).hexdigest()[:16]
    bdir = base / f"sim-{key}"
    binary = bdir / "obj_dir" / f"V{top}"
    if binary.is_file():
        return SweepSim(binary, bdir)
    bdir.mkdir(parents=True, exist_ok=True)
    (bdir / "dut.sv").write_text(source)
    if shutil.which("slang") is not None:
        r = subprocess.run(["slang", "dut.sv", "--top", top, "-Wno-unused"],
                           cwd=bdir, capture_output=True, text=True, timeout=timeout_s)
        if r.returncode != 0:
            raise CompileError("slang (strict SystemVerilog) refused:\n" + (r.stdout + r.stderr)[-1500:],
                               returncode=r.returncode, tool="slang")
    op_assign = f"    dut.{op_port} = {opcode};" if opcode is not None else ""
    (bdir / "sweep.cpp").write_text(_DRIVER.format(top=top, latency=int(latency), op_assign=op_assign,
                                                   in_port=in_port, out_port=out_port, clock=clock))
    r = subprocess.run(
        ["verilator", "--cc", "dut.sv", "--top-module", top, "--exe", "sweep.cpp",
         "--build", "-j", "0", "-Wno-fatal", "--quiet"],
        cwd=bdir, capture_output=True, text=True, timeout=timeout_s)
    if r.returncode != 0 or not binary.is_file():
        raise CompileError((r.stderr or r.stdout)[-1500:], returncode=r.returncode)
    return SweepSim(binary, bdir)

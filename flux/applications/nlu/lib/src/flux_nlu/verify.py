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

from pathlib import Path


__all__ = ["CompileError", "SweepSim", "build_sim", "tools_missing"]


from flux_codegen_rtl_harness import CompileError, SweepSim  # noqa: F401 -- the harness's, re-exported (D567)


def tools_missing() -> list[str]:
    from flux_evaluator_abi.preflight import missing_tools

    return list(missing_tools({"verilator": "the sweep simulator"}))


def build_sim(source: str, *, top: str, latency: int, opcode: int | None,
              workdir: str | Path | None = None, timeout_s: float = 300.0) -> SweepSim:
    """The harness's sweep simulator on this world's ports (`x` -> `y`, `op`; D567): every
    FP16 input word through the DUT, `latency` registers deep, `opcode` on the shared
    interface or None on the per-op one."""
    from flux_codegen_rtl_harness import build_sweep_sim

    return build_sweep_sim(source, top=top, latency=latency, opcode=opcode, workdir=workdir,
                           prefix="flux-nlu-", timeout_s=timeout_s)

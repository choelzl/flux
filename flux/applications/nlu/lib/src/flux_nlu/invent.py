"""The NLU study's model roles (D408): test author, designer, repairer.

The framework's half of the bargain: it states the interface contract, hands over the
curated method knowledge, the record's read-back, the operator table and the standing
refusals -- and then the MODEL decides everything the study is about: which method
implements which operator, shared datapath or per-op units, combinational or how
deeply pipelined. Every reply is judged by tools (Verilator, the exhaustive ULP
check, yosys, OpenROAD); nothing a prompt says is ever quoted as a result.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .fp16 import OPCODES

__all__ = ["design_prompt", "parse_design", "repair_prompt", "test_author_prompt"]

_CONTRACT = """\
INTERFACE CONTRACT (the harness instantiates exactly this; deviation = compile refusal):
* shared style : module nlu      (input wire clk, input wire [15:0] x,
                                  input wire [2:0] op, output wire [15:0] y);
  opcodes: {opcodes}
* per-op style : one module per operator, named nlu_<op>:
                 module nlu_<op> (input wire clk, input wire [15:0] x,
                                  output wire [15:0] y);
* Combinational (latency 0) designs ignore clk (the port must still exist).
* A pipelined design of latency L takes one x every cycle and answers exactly L
  cycles later -- no handshake, no reset, no stalls; internal registers only.
* Synthesizable SystemVerilog: no initial blocks driving logic, no delays, no DPI;
  ROM/LUT contents as case statements or localparam arrays are fine.
* x and y are IEEE FP16 bit patterns. Subnormals are real inputs and real outputs."""

_DESIGN_SHAPE = """\
Reply with ONE line
  DESIGN: {"name": "<short-name>", "style": "shared"|"per-op", "latency": <int>,
           "method": "<dominant method>", "methods": {"<op>": "<method>", ...}}
then the complete SystemVerilog in one ```verilog fence (all modules in the one
fence; a per-op design includes every requested operator's module)."""


def design_prompt(*, ops: tuple[str, ...], ulp_budget: int, knowledge: str,
                  record_ctx: str = "", human: str | None = None,
                  standings: str = "", refusals: list[str] = (),
                  authored_note: str = "") -> str:
    ref = "\n".join(f"  * {r}" for r in list(refusals)[-6:]) or "  (none yet)"
    parts = [p for p in (
        human,
        record_ctx,
        f"Design a hardware Non-Linear Unit for FP16. Operators required: "
        f"{', '.join(ops)}. HARD GATE: every operator must be within "
        f"{ulp_budget} ULP of the FP16 reference on ALL 65536 inputs -- the "
        "harness checks exhaustively, so a corner case is not a corner, it is a "
        "refusal with the failing input attached. Objectives after the gate: "
        "small area, high fmax (both measured on ASAP7 by real tools).",
        "You choose everything: method per operator (or one shared method), shared "
        "datapath vs per-op units, combinational vs pipelined and how deep. "
        "Different choices land on different points of the area/fmax frontier -- "
        "propose the point you believe in, and say so in the name.",
        knowledge,
        _CONTRACT.format(opcodes=", ".join(f"{k}={v}" for k, v in OPCODES.items())),
        standings,
        f"REFUSED so far (do not repeat these mistakes):\n{ref}",
        authored_note,
        _DESIGN_SHAPE,
    ) if p]
    return "\n\n".join(parts)


def parse_design(reply: str, *, ops: tuple[str, ...]) -> tuple[dict[str, Any] | None, str | None]:
    """(candidate, None) or (None, refusal reason). The candidate carries the model's
    declared knobs plus the source; the harness trusts the STRUCTURE (it can check
    it) and none of the claims (the tools check those)."""
    m = re.search(r"DESIGN:\s*(\{.*?\})\s*$", reply, re.M)
    if not m:
        return None, "no DESIGN: header line"
    try:
        head = json.loads(m.group(1))
    except Exception as exc:  # noqa: BLE001
        return None, f"unparseable DESIGN header ({exc})"
    fence = re.search(r"```(?:system)?verilog\s*\n(.*?)```", reply, re.S | re.I)
    if not fence:
        return None, "no ```verilog fence"
    source = fence.group(1)
    style = head.get("style")
    if style not in ("shared", "per-op"):
        return None, f"style must be shared|per-op, got {style!r}"
    try:
        latency = int(head.get("latency"))
    except (TypeError, ValueError):
        return None, "latency must be an integer"
    if not 0 <= latency <= 32:
        return None, f"latency {latency} outside 0..32"
    needed = (["nlu"] if style == "shared" else [f"nlu_{op}" for op in ops])
    missing = [n for n in needed
               if not re.search(rf"\bmodule\s+{n}\b", source)]
    if missing:
        return None, f"source is missing module(s): {', '.join(missing)}"
    return {
        "name": str(head.get("name") or "unnamed")[:40],
        "style": style, "latency": latency,
        "method": str(head.get("method") or "unstated")[:40],
        "methods": {str(k): str(v)[:40]
                    for k, v in dict(head.get("methods") or {}).items()},
        "source": source,
    }, None


def repair_prompt(candidate: dict[str, Any], failures: str) -> str:
    return (
        f"Your NLU design `{candidate['name']}` was refused:\n\n{failures}\n\n"
        "Fix it. Keep the declared style and latency (or update the DESIGN line if "
        "they must change); change as little else as possible.\n\n"
        + _CONTRACT.format(opcodes=", ".join(f"{k}={v}" for k, v in OPCODES.items()))
        + "\n\n" + _DESIGN_SHAPE
        + "\n\nYour previous source:\n\n```verilog\n" + candidate["source"] + "```\n"
    )


def test_author_prompt(*, ops: tuple[str, ...], human: str | None = None) -> str:
    parts = [p for p in (
        human,
        "You are the TEST AUTHOR for an FP16 hardware non-linear unit. For each "
        f"operator ({', '.join(ops)}) name the inputs most likely to break a "
        "hardware approximation: range-reduction seams, saturation thresholds, "
        "subnormals, exact powers of two, values where the function crosses a "
        "representable boundary, both signs of zero. 8 to 24 inputs per operator, "
        "as FP16 bit patterns in hex.",
        'Reply with ONLY JSON: {"vectors": {"<op>": ["0x....", ...], ...}, '
        '"why": "<one sentence>"}',
    ) if p]
    return "\n\n".join(parts)

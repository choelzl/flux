"""A model's RTL reply, read (D557, review 2 R13): the fenced module out of the prose, the
lint pragmas a generated module is given, and the rules screen that refuses what no tool
should be spent on. Two worlds had written these; the harness holds them once.
"""

from __future__ import annotations

import re

__all__ = ["LINT_PRAGMA", "fenced_module", "lint_relaxed", "sv_refusal"]

#: What a generated module is given before Verilator sees it: -Wall would refuse the sign
#: extensions and the unused bits a correct multiplier is full of.
LINT_PRAGMA = "/* verilator lint_off WIDTH */\n/* verilator lint_off UNUSEDSIGNAL */\n"

_FENCE = re.compile(r"```(?:verilog|systemverilog|sv)?\s*\n(.*?)```", re.DOTALL)


def lint_relaxed(source: str) -> str:
    return source if source.startswith(LINT_PRAGMA) else LINT_PRAGMA + source


def fenced_module(name: str, reply: str) -> str | None:
    """The text from `module <name>` to its `endmodule`, out of the first ```verilog fence
    (or the bare reply), or None when no module of that name is in it."""
    m = _FENCE.search(reply)
    body = m.group(1) if m else reply
    if f"module {name}" not in body or "endmodule" not in body:
        return None
    start = body.index(f"module {name}")
    end = body.rindex("endmodule") + len("endmodule")
    return body[start:end].strip() + "\n"


def sv_refusal(source: str, *, combinational: bool = True) -> str | None:
    """What the rules forbid that the text contains -- checked before any tool runs: sequential
    logic where a combinational block was asked for, SystemVerilog size casts Yosys's front
    end rejects, system tasks nothing synthesises."""
    if combinational and re.search(r"always\s*@\s*\(\s*posedge|always_ff|\breg\b.*<=", source):
        return "sequential logic: the module must be combinational"
    if re.search(r"\d+'\s*\(", source):
        return "size casts like 8'(x) are SystemVerilog; Yosys's front end rejects them"
    if "$" in re.sub(r"\$signed|\$unsigned", "", source):
        return "system tasks are not synthesizable"
    return None

"""Verilog/SystemVerilog reserved-word checking (docs/decisions.md D51) — a real, found gap: the
first genuinely multi-module composition demo built with this framework (an accumulator ALU) used
`"reg"` as an instance name, a reserved Verilog keyword since Verilog-1995, and Verilator rejected
it with a raw syntax error (`unexpected reg, expecting '('`) deep in a generated file the caller
never wrote by hand. Every other composition-spec mistake in this repo (a missing net, a dtype
conflict, an unused port) is caught at spec-parse time with a clear message — a reserved-word
collision is exactly the same *class* of catchable mistake and wasn't being caught, purely an
oversight, not a deliberate scope limit.

Deliberately Verilog-specific: a SystemC `DesignSpec` may legally use identifiers Verilog
reserves (e.g. `"wire"` is a real, unremarkable C++ identifier) — this word set only applies
where Verilog code is actually generated. The CHECK over it is shared (D453); only the words
are the language's.
"""

from __future__ import annotations

from flux_codegen_harness_spec import reserved_check

# IEEE 1800-2017 SystemVerilog reserved keywords — not exhaustive (~250 total), but covers every
# keyword a real design/testbench identifier is plausibly chosen from, verified against the real
# bug this module exists because of (`reg`) plus the classic Verilog-1995/2001 keyword set and
# SystemVerilog's own additions most likely to collide with a caller's naming choices.
VERILOG_RESERVED_WORDS = frozenset({
    "always", "always_comb", "always_ff", "always_latch", "and", "assign", "automatic",
    "begin", "bit", "buf", "bufif0", "bufif1", "byte",
    "case", "casex", "casez", "cell", "chandle", "class", "clocking", "cmos", "config", "const",
    "constraint", "context", "continue", "cover", "covergroup", "coverpoint", "cross",
    "deassign", "default", "defparam", "design", "disable", "dist", "do",
    "edge", "else", "end", "endcase", "endclass", "endclocking", "endconfig", "endfunction",
    "endgenerate", "endgroup", "endinterface", "endmodule", "endpackage", "endprimitive",
    "endprogram", "endproperty", "endspecify", "endsequence", "endtable", "endtask", "enum",
    "event", "expect", "export", "extends", "extern",
    "final", "first_match", "for", "force", "foreach", "forever", "fork", "forkjoin", "function",
    "generate", "genvar", "global",
    "highz0", "highz1",
    "if", "iff", "ifnone", "ignore_bins", "illegal_bins", "import", "incdir", "include",
    "initial", "inout", "input", "inside", "instance", "int", "integer", "interconnect",
    "interface", "intersect",
    "join", "join_any", "join_none",
    "large", "let", "liblist", "library", "local", "localparam", "logic", "longint",
    "macromodule", "matches", "medium", "modport", "module",
    "nand", "negedge", "nettype", "new", "nexttime", "nmos", "nor", "noshowcancelled", "not",
    "notif0", "notif1", "null",
    "or", "output",
    "package", "packed", "parameter", "pmos", "posedge", "primitive", "priority", "program",
    "property", "protected", "pull0", "pull1", "pulldown", "pullup", "pulsestyle_ondetect",
    "pulsestyle_onevent", "pure",
    "rand", "randc", "randcase", "randsequence", "rcmos", "real", "realtime", "ref", "reg",
    "reject_on", "release", "repeat", "restrict", "return", "rnmos", "rpmos", "rtran",
    "rtranif0", "rtranif1",
    "s_always", "s_eventually", "s_nexttime", "s_until", "s_until_with", "scalared",
    "sequence", "shortint", "shortreal", "showcancelled", "signed", "small", "soft", "solve",
    "specify", "specparam", "static", "string", "strong", "strong0", "strong1", "struct",
    "super", "supply0", "supply1", "sync_accept_on", "sync_reject_on",
    "table", "tagged", "task", "this", "throughout", "time", "timeprecision", "timeunit",
    "tran", "tranif0", "tranif1", "tri", "tri0", "tri1", "triand", "trior", "trireg", "type",
    "typedef",
    "union", "unique", "unique0", "unsigned", "until", "until_with", "untyped", "use",
    "uwire",
    "var", "vectored", "virtual", "void",
    "wait", "wait_order", "wand", "weak", "weak0", "weak1", "while", "wildcard", "wire", "with",
    "within", "wor",
    "xnor", "xor",
})


#: The check itself is `flux_codegen_harness_spec.reserved_check` (D453): identical logic in
#: both harnesses, and only the WORD SET is language-specific -- a SystemC spec may legally use
#: identifiers Verilog reserves, and the reverse.
check_not_reserved = reserved_check(
    VERILOG_RESERVED_WORDS, language="Verilog/SystemVerilog keyword", tool="Verilator",
    decision="D51")


__all__ = ["VERILOG_RESERVED_WORDS", "check_not_reserved"]

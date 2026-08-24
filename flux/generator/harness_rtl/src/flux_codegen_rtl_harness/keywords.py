"""Verilog/SystemVerilog reserved-word checking (D51), so a name like `reg` is refused at
spec-parse time instead of surfacing as a raw syntax error in a generated file.

The check itself is shared (D453); only the word set is the language's.
"""

from __future__ import annotations

from flux_codegen_harness_spec import reserved_check

# IEEE 1800-2017 SystemVerilog reserved keywords: not all ~250, but the Verilog-1995/2001 set
# plus the SystemVerilog additions most likely to collide with a design's identifiers.
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


#: The check is `flux_codegen_harness_spec.reserved_check` (D453); only the word set is
#: language-specific.
check_not_reserved = reserved_check(
    VERILOG_RESERVED_WORDS, language="Verilog/SystemVerilog keyword", tool="Verilator",
    decision="D51")


__all__ = ["VERILOG_RESERVED_WORDS", "check_not_reserved"]

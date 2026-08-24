"""Deterministic RTL hygiene before the strict frontend (docs/decisions.md D473).

The contract told the model, in capitals, that a `wire` cannot be assigned inside `always`,
and the model kept doing it: "cannot assign to a net within a procedural context" and "'wire'
is not a data type" were a fifth of every slang refusal on the live record, each one a model
round trip spent on a rule a script can apply. SystemVerilog has one declaration that takes
BOTH a continuous `assign` and a procedural write -- `logic` -- so every `wire` and `reg` in
the model's text becomes `logic`, and the rule stops being the model's problem.

The one trap: `wire n = expr;` is a CONTINUOUS assignment, `logic n = expr;` would be a one-time
variable initialisation (the signal then never follows its inputs -- a design that compiles and
is wrong everywhere). So a `wire` with an initialiser becomes `logic n; assign n = expr;` -- on
the SAME line, so slang's line numbers still point at the model's own lines and the patch
window (D422) stays aligned. `reg r = init;` keeps its meaning under `logic`.

The other port of call is the PORT LIST: the sweep driver sets `dut.clk` in C++, so a module
that dropped the `clk` port (the contract says every module has one; a combinational design
"ignores" it, and the model reads that as "omits it") fails the Verilator build after slang
passed it -- a whole round for an unused input. `ensure_clk_port` adds `input logic clk` as
the first port when it is missing.

Only declarations and the port list are touched; comments are left alone; nothing else in
the text moves.
"""

from __future__ import annotations

import re

__all__ = ["ensure_clk_port", "explain_compile_error", "normalize_rtl"]

# `wire [dims] a = e, b, c = f;` at any scope; dims optional; `signed` optional.
_WIRE_DECL = re.compile(
    r"^(?P<indent>[ \t]*)wire\b(?P<dims>(?:\s+signed)?(?:\s*\[[^\]\n]*\])*)\s+"
    r"(?P<body>[^;\n]*?)\s*;(?P<tail>[ \t]*(?://.*)?)$")
_TOKEN = re.compile(r"\b(wire|reg)\b")


def _split_declarators(body: str) -> list[tuple[str, str | None]]:
    """`a = f(x, y), b, c = 1` -> [(a, 'f(x, y)'), (b, None), (c, '1')]: commas inside
    brackets and parentheses belong to the expression, not the list."""
    out: list[tuple[str, str | None]] = []
    depth = 0
    cur = ""
    for ch in body + ",":
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            piece = cur.strip()
            cur = ""
            if not piece:
                continue
            if "=" in piece:
                name, expr = piece.split("=", 1)
                out.append((name.strip(), expr.strip()))
            else:
                out.append((piece, None))
        else:
            cur += ch
    return out


_ANY_DECL = re.compile(
    r"^(?P<indent>[ \t]*)(?P<kind>wire|reg)\b(?P<dims>(?:\s+signed)?(?:\s*\[[^\]\n]*\])*)\s+"
    r"(?P<body>[^;\n]*?)\s*;(?P<tail>[ \t]*(?://.*)?)$")
_BLOCK_OPEN = re.compile(r"^\s*(always\b|always_comb\b|always_ff\b|initial\b|function\b|task\b)")


def normalize_rtl(source: str) -> tuple[str, list[str]]:
    """(normalised source, notes). Notes name what changed, for the log; an empty
    list means the text was already in shape.

    A `wire`/`reg` declared INSIDE an always/initial/function/task block -- illegal, and
    the second most common shape in sampled designs -- is HOISTED: its declaration goes
    onto the block's header line as `logic`, and a blocking assignment stays in its place
    (so a `wire t = e;` in an always block computes `t = e;` where it stood). Every line
    keeps its number."""
    lines = source.split("\n")
    nets = regs = splits = hoisted = 0
    out: list[str] = []
    # the procedural block we are in: (kind, header line index, begin/end depth)
    block: tuple[str, int, int] | None = None
    hoist: dict[int, list[str]] = {}
    for line in lines:
        code, sep, comment = line.partition("//")
        stripped = code.strip()
        opened = _BLOCK_OPEN.match(code) if block is None else None
        if opened:
            block = (opened.group(1), len(out), 0)
        m = _ANY_DECL.match(line)
        if m and not line.lstrip().startswith("//") and block is not None and not opened:
            decls = _split_declarators(m.group("body"))
            names = [n for n, _e in decls]
            if names and all(re.fullmatch(r"[A-Za-z_]\w*", n) for n in names):
                kind, at, _d = block
                dims = " ".join(m.group("dims").split())
                hoist.setdefault(at, []).append(f"logic{' ' + dims if dims else ''} {', '.join(names)};")
                stays = "".join(f"{n} = {e}; " for n, e in decls if e is not None).strip()
                out.append(m.group("indent") + stays + m.group("tail"))
                hoisted += 1
                if m.group("kind") == "wire":
                    nets += 1
                else:
                    regs += 1
                continue
        m = _WIRE_DECL.match(line)
        if m and not line.lstrip().startswith("//"):
            decls = _split_declarators(m.group("body"))
            names = [n for n, _e in decls]
            if names and all(re.fullmatch(r"[A-Za-z_]\w*(?:\s*\[[^\]]*\])?", n) for n in names):
                dims = m.group("dims")
                head = f"{m.group('indent')}logic{dims} {', '.join(names)};"
                assigns = "".join(f" assign {n} = {e};" for n, e in decls if e is not None)
                out.append(head + assigns + m.group("tail"))
                nets += 1
                splits += sum(1 for _n, e in decls if e is not None)
                continue
        if "wire" in code or "reg" in code:
            def swap(t: re.Match) -> str:
                nonlocal nets, regs
                if t.group(1) == "wire":
                    nets += 1
                else:
                    regs += 1
                return "logic"
            code = _TOKEN.sub(swap, code)
        out.append(code + sep + comment)
        if block is not None:
            kind, at, depth = block
            depth += len(re.findall(r"\bbegin\b", code)) - len(re.findall(r"\bend\b", code))
            closed = (re.search(r"\bendfunction\b", code) if kind == "function" else
                      re.search(r"\bendtask\b", code) if kind == "task" else
                      (depth <= 0 and (re.search(r"\bend\b", code) or
                                       (not re.search(r"\bbegin\b", code) and stripped.endswith(";")
                                        and not opened))))
            block = None if closed else (kind, at, depth)
    for at, decls in hoist.items():
        header = out[at]
        indent = header[:len(header) - len(header.lstrip())]
        if header.lstrip().startswith(("function", "task")):
            # after the header's terminating `;` (the local declarations of a function
            # come first in its body); a header spanning lines gets them on its last line
            i = at
            while i < len(out) and ";" not in out[i].partition("//")[0]:
                i += 1
            if i < len(out):
                code, sep, comment = out[i].partition("//")
                out[i] = code.rstrip() + " " + " ".join(decls) + (" " + sep + comment if sep else "")
                continue
        out[at] = indent + " ".join(decls) + " " + header.lstrip()
    notes: list[str] = []
    if nets or regs:
        notes.append(f"{nets} wire + {regs} reg declaration(s) -> logic"
                     + (f", {splits} continuous init(s) split into assign" if splits else "")
                     + (f", {hoisted} declared inside a block hoisted to its header" if hoisted else ""))
    return "\n".join(out), notes


_PORT_LIST = re.compile(r"(\bmodule\s+(?P<name>\w+)\s*(?:#\s*\([^)]*\)\s*)?\()(?P<ports>.*?)(\)\s*;)", re.S)


def ensure_clk_port(source: str, module: str) -> tuple[str, str]:
    """(source, note): `input logic clk` added as the first port of `module` when its port
    list has no `clk`; the note is empty when nothing was needed."""
    for m in _PORT_LIST.finditer(source):
        if m.group("name") != module:
            continue
        ports = m.group("ports")
        if re.search(r"\bclk\b", ports):
            return source, ""
        if not ports.strip():
            fixed = f"{m.group(1)}input logic clk{m.group(4)}"
        elif "\n" in ports:
            fixed = f"{m.group(1)}\n  input logic clk,{ports}{m.group(4)}"
        else:
            fixed = f"{m.group(1)}input logic clk, {ports.lstrip()}{m.group(4)}"
        return source[:m.start()] + fixed + source[m.end():], f"{module}: clk port added (the driver clocks every module)"
    return source, ""


# What slang's wording means for THIS design, in the words a fix needs (D473). slang names
# the construct; for the shapes the model produces most, the construct is not the fault.
_HINTS: list[tuple[str, str]] = [
    (r"reference to non-constant variable .* in a constant expression|expected a declaration name",
     "a procedural statement (if/else, case, `name = value;`) sits at MODULE scope: put it "
     "inside `always @* begin ... end` (module scope allows only declarations, `assign`, "
     "`always`, `function` and `localparam`)"),
    (r"return statement is only valid inside task and function blocks",
     "there is no `return` in an always block: use if/else so that every path assigns the output"),
    (r"scalar type cannot be indexed|cannot refer to element \d+ of 'logic'|cannot select range of",
     "a signal declared 1 bit wide is being indexed: give it its width, `logic [W-1:0] name;`"),
    (r"use of undeclared identifier '(\w+)'",
     "'{0}' is used but never declared: declare it at module scope as `logic [W-1:0] {0};`, "
     "or, if it is a ROM, request it in `tables` under that name"),
    (r"cannot have multiple continuous assignments to variable '(\w+)'",
     "'{0}' is driven by two `assign`s: a signal has ONE driver -- fold both into one "
     "expression (`assign {0} = cond ? a : b;`) or compute it in a single always block"),
    (r"expected vector literal digits",
     "an incomplete sized literal (`16'h` with no digits): every literal complete, e.g. 16'h3C00"),
    (r"declaration must come before all statements in the block",
     "declare every signal at module scope, not inside always/function blocks"),
    (r"expected statement|expected '\)'|expected ';'",
     "a syntax slip at the quoted line: check the parentheses, the `;` and that every "
     "`begin` has its `end`"),
]


def explain_compile_error(text: str, source: str = "") -> str:
    """The compiler's message plus, for each slang error shape the model keeps producing,
    one sentence naming the actual fault -- appended once per shape, so a message with
    twelve identical errors gets one hint, not twelve. A source with no `endmodule` is
    named first, whatever slang said about it: sampled designs stopped mid-comment
    ("// Given the constraint of") three times in eight, deliberating in the source
    instead of writing it, and slang's "expected 'end'" says nothing about that."""
    hints: list[str] = []
    if source and not re.search(r"\bendmodule\b", source):
        last = next((ln.strip() for ln in reversed(source.splitlines()) if ln.strip()), "")
        hints.append("the source has NO `endmodule` -- it stops part-way, at: "
                     f"`{last[:80]}`. Decide before you write: the source is the finished "
                     "module only, deliberation goes in `method`, at most one short comment "
                     "per block, and the text ends with `endmodule`")
    for pattern, hint in _HINTS:
        m = re.search(pattern, text)
        if m and hint not in hints:
            hints.append(hint.format(*m.groups()) if m.groups() else hint)
    if not hints:
        return text
    return text.rstrip() + "\n\nWHAT TO FIX:\n" + "\n".join(f"* {h}" for h in hints)

"""Real, typed failure modes for the RTL codegen harness (docs/decisions.md D43): a real
Verilator rejection is distinct from a DUT simply failing its test vectors, which is normal
`HarnessRunResult` data, not an exception.

`InvalidSpecError` is `flux_codegen_harness_spec`'s (D453) -- a bad spec is a bad spec in any
language, and it used to be imported from the SystemC harness, which made this package depend on
the other language's for its own error type.
"""

from __future__ import annotations

from flux_codegen_harness_spec import InvalidSpecError  # the shared spec error (D453)


class CompileError(RuntimeError):
    """Real Verilator build failure. Carries the real compiler/linter stderr so a caller (e.g.
    interfaces/chia_nodes' generate-repair loop) can feed it back to the LLM."""

    def __init__(self, stderr: str, *, returncode: int, tool: str = "verilator") -> None:
        self.stderr = stderr
        self.returncode = returncode
        self.tool = tool
        super().__init__(f"{tool} exited {returncode}:\n{stderr[-4000:]}")


#: Slips a model makes in SystemVerilog and what the fix is (D552): said beside the quoted
#: line, because the compiler's message alone sent six repairs after the wrong thing.
_HINTS: tuple[tuple[str, str, str], ...] = (
    (r"unexpected ',', expecting '\}'", r"\{\s*\d+\s*\{",
     "a replication inside a concatenation needs braces of its own: write {{N{x}}, y}, not {N{x}, y}"),
    (r"unexpected ',', expecting '\}'", r"\d+'[bdh]",
     "a sized literal is fine in a concatenation ({16'd0, x}); look for a replication {N{...}} without its own braces on this line"),
    (r"Can't find definition of variable", r".",
     "the name is used before any declaration; declare the wire (with its width) above its first use"),
    (r"Operator ASSIGN expects .* on the Assign RHS", r".",
     "the widths of the two sides differ; make them the same width or slice explicitly"),
    (r"expecting IDENTIFIER", r"\blogic\b|\bwire\b",
     "a declaration is malformed; one name per declaration, the width before the name"),
)


def explain_diagnostic(message: str, source: str, *, prefix_lines: int = 0, file: str = "dut.sv") -> str:
    """The compiler's first diagnostic, re-said for the author of `source` (D552): the line
    number counted in THEIR text (`prefix_lines` were added in front of it), the offending
    line quoted with a caret at the column, and a hint when the slip is a known one. The
    first %Error line as it was when nothing can be mapped."""
    import re

    first = next((ln.strip() for ln in message.splitlines() if ln.lstrip().startswith(("%Error", "%Warning"))),
                 message.strip().splitlines()[0] if message.strip() else "")
    m = re.search(rf"{re.escape(file)}:(\d+):(\d+):\s*(.*)", first)
    if not m:
        return first[:300]
    line_no, col, what = int(m.group(1)) - int(prefix_lines), int(m.group(2)), m.group(3).strip()
    lines = source.splitlines()
    if not 1 <= line_no <= len(lines):
        return first[:300]
    text = lines[line_no - 1]
    caret = " " * max(0, col - 1) + "^"
    out = f"{what} -- line {line_no} of your module, column {col}:\n    {text.rstrip()}\n    {caret}"
    for pattern, on_line, hint in _HINTS:
        if re.search(pattern, what) and re.search(on_line, text):
            out += f"\n  hint: {hint}"
            break
    return out


__all__ = ["InvalidSpecError", "CompileError", "explain_diagnostic"]

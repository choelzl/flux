"""The golden test: an RTL design against golden.py's vectors, through Verilator (D967, D968).

    fluxpreset golden DESIGN.sv --golden golden.py     # "N failing of M"; exit 0, 1, or 3 (no build)

The vectors come from the golden model (`flux_loop.golden_model`: `PORTS`, `golden()`, `VECTORS`,
`COUNT`, `SEED`, `EXHAUSTIVE`, `CLOCK`, `LATENCY`, `TOLERANCE_ULP`); a test bench reads them as a
`$readmemh` table and Python compares the outputs. `--extra F.sv` for a module the design
instantiates. Needs verilator on PATH.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from flux_loop.golden_model import EXHAUSTIVE_MAX_BITS, Golden, golden_vectors, load_golden, ulp_distance  # noqa: F401

__all__ = ["EXHAUSTIVE_MAX_BITS", "Check", "Golden", "check_rtl", "explain_diagnostic", "lint_relaxed", "load_golden",
           "main", "module_of"]


def catalog() -> list[dict]:
    """This preset's catalog entries (D968)."""
    from ._entries import HDL, N_FAILING, param
    return [{"id": "rtl-golden", "role": "check", "title": "Test against a golden model",
             "what": "Verilator runs the module on golden.py's vectors and counts the wrong outputs.",
             "run": "fluxpreset golden {artifact} --golden {golden}",
             "params": {"golden": param("Golden model", "{home}/golden.py")},
             "needs": ["verilator"], "pass": N_FAILING, "languages": HDL, "kinds": ["rtl"], "short": "golden"}]

#: What a generated module is given before Verilator sees it: -Wall would refuse the sign
#: extensions and the unused bits a correct multiplier is full of.
LINT_PRAGMA = "/* verilator lint_off WIDTH */\n/* verilator lint_off UNUSEDSIGNAL */\n"
VERILATOR_FLAGS = ["-Wall", "-Wno-DECLFILENAME", "-Wno-UNUSEDSIGNAL", "-Wno-UNUSEDPARAM"]
MAX_CYCLES = 10_000


def lint_relaxed(source: str) -> str:
    return source if source.startswith(LINT_PRAGMA) else LINT_PRAGMA + source


def module_of(source: str, given: str | None = None) -> str:
    if given:
        return given
    m = re.search(r"^\s*module\s+([A-Za-z_]\w*)", source, re.M)
    if not m:
        raise SystemExit("no `module <name>` in the design; say --module")
    return m.group(1)


def _clean(source: str) -> str:
    """Verilator refuses a missing last newline, Yosys `endmodule;`."""
    source = re.sub(r"\bendmodule\s*;", "endmodule", source)
    return source if source.endswith("\n") else source + "\n"


@dataclass(frozen=True)
class Check:
    total: int
    failing: int
    lines: tuple[str, ...] = ()     # the first failures: what came out, what went in, what was expected
    latency: int | None = None      # measured cycles (a design with start/done)
    error: str = ""                 # why it did not build, or the latency refused
    cycles: tuple[int, ...] = ()    # each vector's cycles, start to done (D971: `fluxpreset rtl cycles`)

    @property
    def ok(self) -> bool:
        return self.failing == 0 and not self.error

    @property
    def why(self) -> str:
        if self.error:
            return self.error
        return f"{self.total - self.failing}/{self.total} vectors passed" + (f": {self.lines[0]}" if self.lines else "")


def _testbench(g: Golden, module: str, n: int, vcd: bool = False) -> str:
    """Drives each row of vectors.hex (the inputs, packed) and prints `OUT <row> <cycles> <outputs in hex>`;
    Python compares. A clocked design gets `clk`/`rst_n` (held low two edges); one with LATENCY
    also `start`/`done`, its cycles counted from the edge that took the inputs."""
    ins = [p for p in g.ports if p["dir"] == "in"]
    outs = [p for p in g.ports if p["dir"] == "out"]
    decl = lambda p: f"  logic [{int(p['bits']) - 1}:0] {p['name']};"   # noqa: E731
    pins = [f".{p['name']}({p['name']})" for p in g.ports]
    sync = g.clocked
    handshake = sync and g.latency is not None
    if sync:
        pins = [".clk(clk)", ".rst_n(rst_n)"] + ([".start(start)", ".done(done)"] if handshake else []) + pins
    width = sum(int(p["bits"]) for p in ins)
    body = ["    {" + ", ".join(p["name"] for p in ins) + "} = tab[v];"]
    if handshake:
        body += ["    start = 1'b1; @(posedge clk); #1; start = 1'b0; cycles = 0;",
                 f"    while (done !== 1'b1 && cycles < {MAX_CYCLES}) begin @(posedge clk); #1; cycles = cycles + 1; end"]
    elif sync:
        body += ["    @(posedge clk); #1;"]
    else:
        body += ["    #1;"]
    fmt = " ".join("%h" for _ in outs)
    body += [f'    $display("OUT %0d %0d {fmt}", v, cycles, {", ".join(p["name"] for p in outs)});']
    return "\n".join([
        "`timescale 1ns/1ps", "module testbench;",
        *(decl(p) for p in ins + outs),
        *(["  logic clk;", "  logic rst_n;", "  initial clk = 1'b0;", "  always #5 clk = ~clk;"] if sync else []),
        *(["  logic start;", "  logic done;", "  initial start = 1'b0;"] if handshake else []),
        f"  logic [{width - 1}:0] tab [0:{n - 1}];", "  int v;", "  int cycles = 0;",
        f"  {module} dut ({', '.join(pins)});",
        "  initial begin",
        '    $readmemh("vectors.hex", tab);',
        *(['    $dumpfile("activity.vcd");', "    $dumpvars(0, dut);"] if vcd else []),
        *(["    rst_n = 1'b0; @(posedge clk); @(posedge clk); #1; rst_n = 1'b1;"] if sync else []),
        f"    for (v = 0; v < {n}; v = v + 1) begin", *body, "    end", "    $finish;", "  end", "endmodule", ""])


def _value(hexs: str, bits: int, signed: bool) -> int | None:
    if re.search(r"[xXzZ]", hexs):
        return None
    v = int(hexs, 16) & ((1 << bits) - 1)
    return v - (1 << bits) if signed and bits > 1 and v >> (bits - 1) else v


#: Slips a model makes in SystemVerilog and the fix (D552), shown beside the quoted line
HINTS = (
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
    """Verilator's first diagnostic re-said for the author of `source` (D552): the line counted
    in their text, quoted with a caret at the column, and a hint for a known slip; the first
    %Error line as it was when nothing maps."""
    first = next((ln.strip() for ln in message.splitlines() if ln.lstrip().startswith(("%Error", "%Warning"))),
                 message.strip().splitlines()[0] if message.strip() else "")
    m = re.search(rf"{re.escape(file)}:(\d+):(\d+):\s*(.*)", first)
    lines = source.splitlines()
    if not m or not 1 <= int(m.group(1)) - prefix_lines <= len(lines):
        return first[:300]
    line_no, col, what = int(m.group(1)) - prefix_lines, int(m.group(2)), m.group(3).strip()
    text = lines[line_no - 1]
    out = f"{what} -- line {line_no} of your module, column {col}:\n    {text.rstrip()}\n    {' ' * max(0, col - 1)}^"
    hint = next((h for pat, on_line, h in HINTS if re.search(pat, what) and re.search(on_line, text)), None)
    return out + (f"\n  hint: {hint}" if hint else "")


def check_rtl(source: str, g: Golden, *, module: str | None = None, rows: list[dict[str, Any]] | None = None,
              extra_sources: dict[str, str] | None = None, timeout_s: float = 120.0,
              relaxed: bool = False, show: int = 5, claim: bool = True, vcd: Path | None = None) -> Check:
    """Verilator on `source` against the golden's vectors (`rows` when made already). `relaxed`
    turns width and unused warnings off: a generated module is judged on its values. `claim=False`
    measures the latency rather than holding the design to the golden's LATENCY. `vcd`: the run's switching
    activity is written there (scope `testbench/dut`, D972)."""
    try:
        module = module_of(source, module)
    except SystemExit:
        return Check(0, 0, error="no `module <name>` in the source")
    rows = rows if rows is not None else golden_vectors(g)
    ins = [p for p in g.ports if p["dir"] == "in"]
    outs = [p for p in g.ports if p["dir"] == "out"]
    signed = {p["name"]: not p.get("unsigned") and int(p["bits"]) > 1 for p in g.ports}
    text = lint_relaxed(source) if relaxed else source
    prefix = text.count("\n", 0, len(text) - len(source)) if relaxed else 0
    with tempfile.TemporaryDirectory(prefix="flux-rtl-test-") as d:
        work = Path(d)
        (work / "dut.sv").write_text(_clean(text))
        for stem, src in (extra_sources or {}).items():
            (work / f"{stem}.sv").write_text(_clean(src))
        (work / "testbench.sv").write_text(_testbench(g, module, len(rows), vcd=vcd is not None))
        packed = []
        for r in rows:
            word = 0
            for p in ins:
                word = (word << int(p["bits"])) | (int(r["inputs"][p["name"]]) & ((1 << int(p["bits"])) - 1))
            packed.append(f"{word:x}")
        (work / "vectors.hex").write_text("\n".join(packed) + "\n")
        srcs = ["testbench.sv", "dut.sv", *(f"{s}.sv" for s in (extra_sources or {}))]
        build = subprocess.run(["verilator", "--binary", "--timing", *(["--trace"] if vcd is not None else []), *VERILATOR_FLAGS, "-j", "1",
                                "--top-module", "testbench", *srcs],
                               cwd=work, capture_output=True, text=True, timeout=timeout_s)
        if build.returncode != 0:
            why = "did not compile: " + explain_diagnostic(build.stderr, source, prefix_lines=prefix)
            pin = re.search(r"[Mm]issing pin.*'(\w+)'|PINMISSING.*'(\w+)'", build.stderr)
            if pin and not g.clocked:
                why = (f"the module has a `{pin.group(1) or pin.group(2)}` port and the golden model declares no CLOCK, "
                       "so the test bench cannot drive it: write the module with the golden's ports only, or "
                       "the golden needs CLOCK and LATENCY. " + why)
            return Check(len(rows), len(rows), error=why)
        run = subprocess.run([str(work / "obj_dir" / "Vtestbench")], cwd=work, capture_output=True, text=True,
                             timeout=timeout_s, stdin=subprocess.DEVNULL)
        if vcd is not None and (work / "activity.vcd").exists():
            import shutil

            shutil.copy(work / "activity.vcd", vcd)
    got = {int(m.group(1)): (int(m.group(2)), m.group(3).split())
           for m in re.finditer(r"^OUT (\d+) (\d+) ?(.*)$", run.stdout, re.M)}
    fails, cycles = [], []
    for i, r in enumerate(rows):
        if i not in got:
            fails.append(f"VECTOR {i} FAIL no output (the simulation stopped: {(run.stderr or run.stdout).strip()[-200:]})")
            continue
        n, hexs = got[i]
        cycles.append(n)
        vals = {p["name"]: _value(h, int(p["bits"]), signed[p["name"]]) for p, h in zip(outs, hexs)}
        bad = g.latency is not None and g.clocked and n >= MAX_CYCLES
        for p in outs:
            want, have, bits = int(r["expected"][p["name"]]), vals.get(p["name"]), int(p["bits"])
            tol = g.ulp.get(p["name"])
            if have is None or (tol is None and (have - want) % (1 << bits)) or \
                    (tol is not None and ulp_distance(have & ((1 << bits) - 1), want & ((1 << bits) - 1), bits) > int(tol)):
                bad = True
        if bad:
            said = " ".join(f"{k}={'x' if v is None else v}" for k, v in vals.items())
            fails.append(f"VECTOR {i} FAIL {said}" + (f" (done never rose in {MAX_CYCLES} cycles)" if n >= MAX_CYCLES else "")
                         + " -- for " + ", ".join(f"{k}={x}" for k, x in r["inputs"].items())
                         + " expected " + ", ".join(f"{k}={x}" for k, x in r["expected"].items()))
    latency = None
    if g.clocked and g.latency is not None and cycles and not fails:
        latency = max(cycles) + 1        # the edges after the one that took the inputs, and that one
        if claim and latency != g.latency:
            return Check(len(rows), 0, (), latency, error=f"claims {g.latency} cycle(s) of latency, measured {latency}",
                         cycles=tuple(c + 1 for c in cycles))
    return Check(len(rows), len(fails), tuple(fails[:show]), latency, cycles=tuple(c + 1 for c in cycles))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="check an RTL design against golden.py's vectors (Verilator)")
    ap.add_argument("artifact")
    ap.add_argument("--golden", required=True)
    ap.add_argument("--module")
    ap.add_argument("--extra", action="append", default=[], help="another module file the design instantiates")
    ap.add_argument("--show", type=int, default=8)
    ap.add_argument("--timeout", type=float, default=300.0)
    a = ap.parse_args(argv)
    extra = {Path(f).stem: Path(f).read_text() for f in a.extra}
    got = check_rtl(Path(a.artifact).read_text(), load_golden(a.golden), module=a.module, extra_sources=extra or None,
                    timeout_s=a.timeout, show=a.show)
    print("\n".join(([got.error] if got.error else []) + list(got.lines)
                    + ([f"latency={got.latency}"] if got.latency is not None else [])
                    + [f"{got.failing if not got.error else got.total} failing of {got.total}"]))
    # exit 3: it did not build (a build failure to the gate); 1: it built and failed (D594)
    return 0 if got.ok else 3 if got.error.startswith(("did not compile", "the module has", "no `module")) else 1


if __name__ == "__main__":
    raise SystemExit(main())

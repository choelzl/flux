"""Golden models: their values and vectors, and the test of a design against them.

    python -m flux_loop.golden_model DESIGN.sv --golden golden.py     # "N failing of M"; exit 0, 1, or 3 (no build)

The golden model declares `PORTS` ([{name, dir, bits}], signed unless `unsigned: true`) and
`golden(**inputs) -> {output: value}`; optionally `VECTORS`, `COUNT`, `SEED`, `EXHAUSTIVE`,
`CLOCK` (the design has `clk` and `rst_n`), `LATENCY` (it also has `start` and `done`; the
cycles are checked) and `TOLERANCE_ULP` ({output: n}, a float output's allowed error). The test
runs the vectors through Verilator (on PATH when it runs); a Python prototype is checked by
`flux_loop.golden_proto` (D951). One test for every RTL application (D967: not a copy in each)."""

from __future__ import annotations

import argparse
import importlib.util
import random
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

__all__ = ["EXHAUSTIVE_MAX_BITS", "Check", "Golden", "check_rtl", "explain_diagnostic", "golden_vectors", "lint_relaxed",
           "load_golden", "module_of", "ulp_distance"]


@dataclass(frozen=True)
class Golden:
    ports: tuple[dict[str, Any], ...]
    fn: Callable[..., dict[str, int]]
    vectors: tuple[dict[str, Any], ...] = ()   # explicit rows: inputs, or {inputs, expected}
    count: int = 32                            # random rows after the corners
    seed: Any = 0
    clocked: bool = False
    latency: int | None = None                 # a clocked design's cycles, checked when given
    behavior: str = ""
    ulp: dict = field(default_factory=dict)    # output port -> the ULPs it may be off by
    exhaustive: bool = False                   # every input combination, not corners and samples (D865)

    def __post_init__(self) -> None:
        if not self.ports or not callable(self.fn):
            raise ValueError("a golden model is ports = [{name, dir, bits}, ...] and fn(**inputs) -> {output: value}")
        for p in self.ports:
            if not {"name", "dir", "bits"} <= set(p) or p["dir"] not in ("in", "out"):
                raise ValueError(f"port {p!r} needs name, dir (in|out) and bits")
        outs = {p["name"]: int(p["bits"]) for p in self.ports if p["dir"] == "out"}
        for name, n in (self.ulp or {}).items():
            if name not in outs:
                raise ValueError(f"TOLERANCE_ULP names {name!r}, which is not an output port ({', '.join(outs)})")
            if outs[name] not in _FLOAT_WIDTHS:
                raise ValueError(f"TOLERANCE_ULP on {name!r}: a {outs[name]}-bit port is not an IEEE float (16, 32 or 64 bits)")
            if int(n) < 0:
                raise ValueError(f"TOLERANCE_ULP on {name!r} must be 0 or more")
        bits = sum(int(p["bits"]) for p in self.ports if p["dir"] == "in")
        if self.exhaustive and bits > EXHAUSTIVE_MAX_BITS:
            raise ValueError(f"EXHAUSTIVE over {bits} input bits is 2**{bits} vectors; at most {EXHAUSTIVE_MAX_BITS} bits")

    @classmethod
    def from_module(cls, mod: Any) -> "Golden":
        """A `golden.py`: `PORTS`, `golden(**inputs)`; `VECTORS`, `COUNT`, `SEED`, `CLOCK`,
        `LATENCY`, `BEHAVIOR`, `EXHAUSTIVE` when it says them."""
        return cls(ports=tuple(getattr(mod, "PORTS", None) or ()), fn=getattr(mod, "golden", None),
                   vectors=tuple(getattr(mod, "VECTORS", None) or ()), count=int(getattr(mod, "COUNT", 32)),
                   seed=getattr(mod, "SEED", 0), clocked=bool(getattr(mod, "CLOCK", None)),
                   latency=getattr(mod, "LATENCY", None), ulp=dict(getattr(mod, "TOLERANCE_ULP", None) or {}),
                   exhaustive=bool(getattr(mod, "EXHAUSTIVE", False)),
                   behavior=str(getattr(mod, "BEHAVIOR", "") or getattr(getattr(mod, "golden", None), "__doc__", "") or ""))

    @property
    def unsigned(self) -> dict[str, int]:
        return {p["name"]: int(p["bits"]) for p in self.ports if p.get("unsigned")}


#: The most input bits an EXHAUSTIVE golden may have: 2**20 vectors (D865).
EXHAUSTIVE_MAX_BITS = 20


def _corners(bits: int, unsigned: bool) -> list[int]:
    if unsigned:
        return [0, 1, (1 << bits) - 1, 1 << (bits - 1)]
    return [-(1 << (bits - 1)), (1 << (bits - 1)) - 1, 0, -1, 1]


def golden_vectors(g: Golden) -> list[dict[str, Any]]:
    """Explicit rows when the model gives them; every input combination when it says EXHAUSTIVE
    (D865); else every input's corners (each against the others' corners, pairwise), then `count`
    random rows from `seed`; `expected` from `fn`."""
    def row(inputs: dict[str, int], expected: dict[str, int] | None = None) -> dict[str, Any]:
        return {"inputs": dict(inputs), "expected": {k: int(v) for k, v in (expected or g.fn(**inputs)).items()}}

    if g.vectors:
        return [row(r["inputs"], r.get("expected")) if "inputs" in r else row(r) for r in g.vectors]
    ins = [p for p in g.ports if p["dir"] == "in"]
    if g.exhaustive:
        import itertools

        def values(p: dict[str, Any]) -> range:
            n = int(p["bits"])
            return range(0, 1 << n) if p.get("unsigned") else range(-(1 << (n - 1)), 1 << (n - 1))

        return [row(dict(zip([p["name"] for p in ins], combo)))
                for combo in itertools.product(*(values(p) for p in ins))]
    rows: list[dict[str, Any]] = []
    seen: set = set()

    def add(inputs: dict[str, int]) -> None:
        key = tuple(sorted(inputs.items()))
        if key not in seen:
            seen.add(key)
            rows.append(row(inputs))

    corners = {p["name"]: _corners(int(p["bits"]), bool(p.get("unsigned"))) for p in ins}
    base = {p["name"]: 0 for p in ins}
    for p in ins:
        for c in corners[p["name"]]:
            add({**base, p["name"]: c})
    for i, p in enumerate(ins):
        for q in ins[i + 1:]:
            for c in corners[p["name"]]:
                for d in corners[q["name"]]:
                    add({**base, p["name"]: c, q["name"]: d})
    rng = random.Random(g.seed)
    target, tries = len(rows) + g.count, 0
    while len(rows) < target and tries < g.count * 20:
        tries += 1
        add({p["name"]: (rng.randrange(0, 1 << int(p["bits"])) if p.get("unsigned")
                         else rng.randrange(-(1 << (int(p["bits"]) - 1)), 1 << (int(p["bits"]) - 1))) for p in ins})
    return rows


#: IEEE binary widths -> exponent bits (half, single, double).
_FLOAT_WIDTHS = {16: 5, 32: 8, 64: 11}


def ulp_distance(a: int, b: int, bits: int) -> float:
    """How many representable values apart two IEEE bit patterns are: 0 for equal
    patterns, for +0/-0, and for two NaNs of any payload; infinity when one is a NaN."""
    e = _FLOAT_WIDTHS[bits]
    mask, man = (1 << bits) - 1, bits - 1 - e
    a, b = a & mask, b & mask

    def nan(v: int) -> bool:
        return (v >> man) & ((1 << e) - 1) == (1 << e) - 1 and v & ((1 << man) - 1) != 0

    if nan(a) or nan(b):
        return 0.0 if nan(a) and nan(b) else float("inf")

    def ordered(v: int) -> int:
        mag = v & ((1 << (bits - 1)) - 1)
        return -mag if v >> (bits - 1) else mag

    return float(abs(ordered(a) - ordered(b)))


def load_golden(path: str | Path) -> Golden:
    """golden.py's model: its PORTS, golden() and settings (D967: the test's, beside the vectors)."""
    p = Path(path)
    spec = importlib.util.spec_from_file_location(f"golden_{abs(hash(str(p)))}", p)
    if spec is None or not p.is_file():
        raise SystemExit(f"golden model {p} is not a file")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    try:
        return Golden.from_module(mod)
    except ValueError as exc:
        raise SystemExit(f"{p}: {exc}") from exc


# ---- the test: the golden's vectors through Verilator -----------------------------------------

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

    @property
    def ok(self) -> bool:
        return self.failing == 0 and not self.error

    @property
    def why(self) -> str:
        if self.error:
            return self.error
        return f"{self.total - self.failing}/{self.total} vectors passed" + (f": {self.lines[0]}" if self.lines else "")


def _testbench(g: Golden, module: str, n: int) -> str:
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
              relaxed: bool = False, show: int = 5) -> Check:
    """Verilator on `source` against the golden's vectors (`rows` when made already). `relaxed`
    turns width and unused warnings off: a generated module is judged on its values."""
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
        (work / "testbench.sv").write_text(_testbench(g, module, len(rows)))
        packed = []
        for r in rows:
            word = 0
            for p in ins:
                word = (word << int(p["bits"])) | (int(r["inputs"][p["name"]]) & ((1 << int(p["bits"])) - 1))
            packed.append(f"{word:x}")
        (work / "vectors.hex").write_text("\n".join(packed) + "\n")
        srcs = ["testbench.sv", "dut.sv", *(f"{s}.sv" for s in (extra_sources or {}))]
        build = subprocess.run(["verilator", "--binary", "--timing", *VERILATOR_FLAGS, "-j", "1",
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
                             timeout=timeout_s)
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
        if latency != g.latency:
            return Check(len(rows), 0, (), latency, error=f"claims {g.latency} cycle(s) of latency, measured {latency}")
    return Check(len(rows), len(fails), tuple(fails[:show]), latency)


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

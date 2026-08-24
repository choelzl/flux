"""A golden model, its vectors and the check of RTL against it: used by `flux rtl test` and by
worlds for their own designs.

A `Golden` is the ports (`{name, dir, bits}`, signed unless `unsigned: true`) and a function
`fn(**inputs) -> {output: value}`; optionally explicit input rows, a random `count` and
`seed`, and for a clocked design its `latency` (cycles from the edge that takes the inputs to
the valid output). `golden_vectors` makes the rows (explicit ones, else pairwise corners and
`count` random); `check_rtl` Verilates the source against them and returns a `Check`: failing
count, first failure lines with inputs and expected values, measured latency, or the compile
error explained.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import Any, Callable

__all__ = ["Check", "Golden", "check_rtl", "golden_vectors", "ulp_distance"]


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

    @classmethod
    def from_module(cls, mod: Any) -> "Golden":
        """A `golden.py`: `PORTS`, `golden(**inputs)`; `VECTORS`, `COUNT`, `SEED`, `CLOCK`,
        `LATENCY`, `BEHAVIOR` when it says them."""
        return cls(ports=tuple(getattr(mod, "PORTS", None) or ()), fn=getattr(mod, "golden", None),
                   vectors=tuple(getattr(mod, "VECTORS", None) or ()), count=int(getattr(mod, "COUNT", 32)),
                   seed=getattr(mod, "SEED", 0), clocked=bool(getattr(mod, "CLOCK", None)),
                   latency=getattr(mod, "LATENCY", None), ulp=dict(getattr(mod, "TOLERANCE_ULP", None) or {}),
                   behavior=str(getattr(mod, "BEHAVIOR", "") or getattr(getattr(mod, "golden", None), "__doc__", "") or ""))

    @property
    def unsigned(self) -> dict[str, int]:
        return {p["name"]: int(p["bits"]) for p in self.ports if p.get("unsigned")}


def _corners(bits: int, unsigned: bool) -> list[int]:
    if unsigned:
        return [0, 1, (1 << bits) - 1, 1 << (bits - 1)]
    return [-(1 << (bits - 1)), (1 << (bits - 1)) - 1, 0, -1, 1]


def golden_vectors(g: Golden) -> list[dict[str, Any]]:
    """Explicit rows when the model gives them; else every input's corners (each against the
    others' corners, pairwise), then `count` random rows from `seed`; `expected` from `fn`."""
    def row(inputs: dict[str, int], expected: dict[str, int] | None = None) -> dict[str, Any]:
        return {"inputs": dict(inputs), "expected": {k: int(v) for k, v in (expected or g.fn(**inputs)).items()}}

    if g.vectors:
        return [row(r["inputs"], r.get("expected")) if "inputs" in r else row(r) for r in g.vectors]
    ins = [p for p in g.ports if p["dir"] == "in"]
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


def _within(g: Golden, line: str, row: dict[str, Any]) -> bool:
    """A harness FAIL line whose every output is within the golden's ULP tolerance."""
    widths = {p["name"]: int(p["bits"]) for p in g.ports if p["dir"] == "out"}
    got = {k: int(v) for k, v in re.findall(r"\b(\w+)=(-?\d+)\b", line.split(" FAIL ", 1)[-1])}
    for name, want in row["expected"].items():
        if name not in got:
            return False
        n = g.ulp.get(name)
        if n is None:
            if (got[name] - int(want)) % (1 << widths[name]) != 0:
                return False
        elif ulp_distance(got[name], int(want), widths[name]) > int(n):
            return False
    return True


def _as_harness(g: Golden, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The harness's testbench reads every int port signed: an unsigned value with its top
    bit set goes over as the same bits read signed."""
    uns = g.unsigned
    if not uns:
        return rows

    def conv(r: dict[str, int]) -> dict[str, int]:
        return {k: (v - (1 << uns[k]) if k in uns and v >= 1 << (uns[k] - 1) else v) for k, v in r.items()}

    return [{"inputs": conv(r["inputs"]), "expected": conv(r["expected"])} for r in rows]


def _from_harness(g: Golden, line: str) -> str:
    uns = g.unsigned
    return re.sub(r"\b(\w+)=(-\d+)\b", lambda m: f"{m.group(1)}={int(m.group(2)) + (1 << uns[m.group(1)])}"
                  if m.group(1) in uns else m.group(0), line)


@dataclass(frozen=True)
class Check:
    total: int
    failing: int
    lines: tuple[str, ...] = ()     # the first failures: what came out, what went in, what was expected
    latency: int | None = None      # measured cycles (a clocked design)
    error: str = ""                 # the compile error explained, or the latency refused

    @property
    def ok(self) -> bool:
        return self.failing == 0 and not self.error

    @property
    def why(self) -> str:
        """One line for a refusal or a repair prompt."""
        if self.error:
            return self.error
        return f"{self.total - self.failing}/{self.total} vectors passed" + (f": {self.lines[0]}" if self.lines else "")


def _harness_port(p: dict[str, Any]) -> dict[str, Any]:
    """A golden port as a harness port: 1 bit is a `bool` (a plain `logic`, 0 or 1); wider is an
    int of that width. A 1-bit int would be signed (0 or -1), so the harness has no such port."""
    if int(p["bits"]) == 1:
        return {"name": p["name"], "dir": p["dir"], "dtype": "bool"}
    return {"name": p["name"], "dir": p["dir"], "dtype": "int", "bits": int(p["bits"])}


def check_rtl(source: str, g: Golden, *, module: str | None = None, rows: list[dict[str, Any]] | None = None,
              extra_sources: dict[str, str] | None = None, timeout_s: float = 120.0,
              relaxed: bool = False, show: int = 5) -> Check:
    """Verilator on `source` against the golden model's vectors (`rows`, when the caller made
    them already). `relaxed` turns width warnings off: a model-written module is judged on its
    values, not its style."""
    from flux_codegen_harness_spec import design_spec_from_dict

    from .build import compile_and_run
    from .errors import CompileError, explain_diagnostic
    from .reply import LINT_PRAGMA, lint_relaxed

    if module is None:
        m = re.search(r"^\s*module\s+([A-Za-z_]\w*)", source, re.M)
        if not m:
            return Check(0, 0, error="no `module <name>` in the source")
        module = m.group(1)
    rows = rows if rows is not None else golden_vectors(g)
    spec = {"schema_version": "0.1.0", "id": f"golden/{module}", "module_name": module,
            "ports": [_harness_port(p) for p in g.ports],
            "behavior": g.behavior or "the golden model", "test_vectors": _as_harness(g, rows),
            **({"is_clocked": True, "measures_latency": g.latency is not None} if g.clocked else {})}
    text = lint_relaxed(source) if relaxed else source
    prefix = LINT_PRAGMA.count("\n") if relaxed and not source.startswith(LINT_PRAGMA) else 0
    try:
        run = compile_and_run(text, design_spec_from_dict(spec), timeout_s=timeout_s, extra_sources=extra_sources)
    except CompileError as exc:
        text_exc = str(exc)
        why = "did not compile: " + explain_diagnostic(text_exc, source, prefix_lines=prefix)
        pin = re.search(r"missing pin: '(\w+)'", text_exc)
        if pin and not g.clocked:
            # the test bench is built from the golden's ports; a module with a clock the golden
            # does not declare cannot be driven: say which of the two must change
            why = (f"the module has a `{pin.group(1)}` port and the golden model declares no CLOCK, so the test "
                   "bench is combinational and cannot drive it: write the module without a clock (only the "
                   "golden's ports), or the golden needs CLOCK and LATENCY. " + why)
        return Check(len(rows), len(rows), error=why)
    fails = list(run.failing_vector_lines or [])
    if g.ulp and fails:
        # a float output may be off by the golden's TOLERANCE_ULP: the harness compares exactly,
        # so failed vectors are read back and those within the band pass
        kept = []
        for ln in fails:
            m = re.match(r"VECTOR (\d+) FAIL", _from_harness(g, ln))
            if not (m and int(m.group(1)) < len(rows) and _within(g, _from_harness(g, ln), rows[int(m.group(1))])):
                kept.append(ln)
        fails = kept
    lines = []
    for ln in fails[:show]:
        m = re.match(r"VECTOR (\d+) FAIL", ln)
        extra = ""
        if m and int(m.group(1)) < len(rows):
            v = rows[int(m.group(1))]
            extra = (" -- for " + ", ".join(f"{k}={x}" for k, x in v["inputs"].items())
                     + " expected " + ", ".join(f"{k}={x}" for k, x in v["expected"].items()))
        lines.append(_from_harness(g, ln) + extra)
    failing = len(fails) if g.ulp else len(rows) - int(run.passed_vectors)
    if not run.all_passed and failing == 0 and (not g.ulp or not run.failing_vector_lines):
        failing = len(rows)
        lines = lines or [(run.compile_stderr or run.stderr or "no vector passed")[:300]]
    latency = None
    if g.clocked and g.latency is not None and run.cycles_per_vector and failing == 0:
        # The harness counts the edges after the one that took the inputs (D115): one register
        # stage reports 0, so `latency` measures latency - 1
        latency = max(run.cycles_per_vector) + 1
        if latency != g.latency:
            return Check(len(rows), 0, tuple(lines), latency,
                         error=f"claims {g.latency} cycle(s) of latency, measured {latency}")
    return Check(len(rows), failing, tuple(lines), latency)

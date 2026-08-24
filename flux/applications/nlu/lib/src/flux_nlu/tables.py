"""The table oracle (D420): the model NAMES a table, the harness COMPUTES it.

Measured need: the campaign's best `exp` design knew the algorithm -- range-reduce by
log2(e), table 2^f on the fraction, reconstruct through the exponent; the multiply by
0x5C55 was in the source -- and produced two distinct outputs across the whole core
band, because it could not derive 32 correctly-rounded table constants in-context.
Its comments argued for a 32-entry table, then a 16-entry one, then gave up. That is
numeric derivation, which the rig already does exactly for the judge; so the rig
does it here too, and the bargain holds: the MODEL still chooses the method, the
segmentation, the sample point and the fixed-point format; the harness only rounds.

A request is a small typed record (schema-constrained, so no arbitrary code), rendered
as a synthesizable SystemVerilog `function` with a `case` ROM and inserted into the
model's module before its `endmodule`. The model calls it as `NAME(idx)`.
"""

from __future__ import annotations

import math
import re
from typing import Any, Callable

import numpy as np

__all__ = ["FUNCS", "TableSpec", "inject_tables", "parse_table_specs", "render_table",
           "table_schema"]

# What the oracle can tabulate. An enum in the schema: the model picks a name, never
# writes an expression. Each maps a float64 array to a float64 array.
FUNCS: dict[str, Callable[[np.ndarray], np.ndarray]] = {
    "exp2": lambda x: np.exp2(x),
    "exp": lambda x: np.exp(x),
    "ln": lambda x: np.log(x),
    "log2": lambda x: np.log2(x),
    "recip": lambda x: 1.0 / x,
    "rsqrt": lambda x: 1.0 / np.sqrt(x),
    "sqrt": lambda x: np.sqrt(x),
    "sigmoid": lambda x: 1.0 / (1.0 + np.exp(-x)),
    "tanh": lambda x: np.tanh(x),
    "gelu": lambda x: 0.5 * x * (1.0 + _erf(x / math.sqrt(2.0))),
    "erf": lambda x: _erf(x),
}

FORMATS = ("fp16", "ufixed", "sfixed")
MAX_ENTRIES = 1024
MAX_WIDTH = 32


def _erf(x: np.ndarray) -> np.ndarray:
    try:
        from scipy.special import erf  # type: ignore

        return erf(x)
    except Exception:  # noqa: BLE001
        return np.vectorize(math.erf)(x)


class TableSpec:
    """One requested table. `fmt` is fp16 (raw bit pattern, 16 wide), ufixed (unsigned,
    `frac_bits` fractional bits, width chosen to fit) or sfixed (two's complement,
    `frac_bits` fractional bits). `sample` says where in each of the `entries` equal
    sub-intervals of [lo, hi) the function is evaluated: left edge (index-addressed
    2^f tables), mid (minimises max error for piecewise-constant), right."""

    def __init__(self, *, name: str, func: str, lo: float, hi: float, entries: int,
                 fmt: str = "ufixed", frac_bits: int = 10, sample: str = "left",
                 width: int | None = None) -> None:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,31}", name):
            raise ValueError(f"table name {name!r} is not a plain identifier")
        if func not in FUNCS:
            raise ValueError(f"unknown table function {func!r}; one of {sorted(FUNCS)}")
        if not (2 <= int(entries) <= MAX_ENTRIES):
            raise ValueError(f"entries must be 2..{MAX_ENTRIES}, got {entries}")
        if not (math.isfinite(lo) and math.isfinite(hi) and hi > lo):
            raise ValueError(f"domain must be finite with hi > lo, got [{lo}, {hi})")
        if fmt not in FORMATS:
            raise ValueError(f"format must be one of {FORMATS}, got {fmt!r}")
        if sample not in ("left", "mid", "right"):
            raise ValueError(f"sample must be left|mid|right, got {sample!r}")
        self.name, self.func, self.lo, self.hi = name, func, float(lo), float(hi)
        self.entries, self.fmt, self.frac_bits = int(entries), fmt, int(frac_bits)
        self.sample, self.width = sample, width
        self.adjusted: list[str] = []       # what encode() changed about the request (D473)

    @property
    def index_bits(self) -> int:
        return max(1, math.ceil(math.log2(self.entries)))

    def sample_points(self) -> np.ndarray:
        step = (self.hi - self.lo) / self.entries
        off = {"left": 0.0, "mid": 0.5, "right": 1.0}[self.sample]
        return self.lo + (np.arange(self.entries) + off) * step

    def values(self) -> np.ndarray:
        with np.errstate(all="ignore"):
            return FUNCS[self.func](self.sample_points().astype(np.float64))

    def encode(self) -> tuple[np.ndarray, int]:
        """(integer words, width). fp16 -> the IEEE bit patterns; fixed -> round-half-
        even to `frac_bits`, width the smallest that holds every value (or `width`)."""
        v = self.values()
        if self.fmt == "fp16":
            bits = v.astype(np.float16).view(np.uint16).astype(np.int64)
            return bits, 16
        scaled = v * (1 << self.frac_bits)
        if not np.all(np.isfinite(scaled)):
            raise ValueError(f"{self.func} is not finite on [{self.lo}, {self.hi}) "
                             "-- narrow the domain")
        q = np.rint(scaled).astype(np.int64)             # numpy rint is half-to-even
        # ADJUSTED, NOT REFUSED (D473): a ufixed request over a negative range becomes
        # sfixed, and a width too small for the values is widened -- the harness knows
        # both facts and a refusal only bought another model round to learn them. The
        # rendered header and `self.adjusted` say what changed, so the model's next edit
        # is the receiving signal's width, not a re-request.
        if self.fmt == "ufixed" and (q < 0).any():
            self.fmt = "sfixed"
            self.adjusted.append(f"{self.name}: negative values on the domain -> sfixed "
                                 "(two's complement)")
        if self.fmt == "ufixed":
            need = max(1, int(q.max()).bit_length())
        else:
            mag = int(max(abs(int(q.min())), abs(int(q.max()))))
            need = mag.bit_length() + 1
        width = self.width or need
        if width < need:
            self.adjusted.append(f"{self.name}: width {width} cannot hold the values -> "
                                 f"widened to {need}")
            width = need
        if width > MAX_WIDTH:
            raise ValueError(f"width {width} exceeds the maximum {MAX_WIDTH}")
        self.width = width
        if self.fmt == "sfixed":
            q = np.where(q < 0, q + (1 << width), q)
        return q, width

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "func": self.func, "lo": self.lo, "hi": self.hi,
                "entries": self.entries, "format": self.fmt,
                "frac_bits": self.frac_bits, "sample": self.sample,
                "width": self.width}


def render_table(spec: TableSpec) -> str:
    """A synthesizable `function automatic` with a case ROM. Comment lines carry the
    provenance a reader (or the model, next turn) needs to use it correctly."""
    words, width = spec.encode()
    k = spec.index_bits
    fmt = ("FP16 bit patterns" if spec.fmt == "fp16" else
           f"{'unsigned' if spec.fmt == 'ufixed' else 'signed two\'s-complement'} "
           f"fixed-point, {spec.frac_bits} fractional bits, {width} wide")
    lines = [
        f"  // TABLE {spec.name}: {spec.func}(x) for x in [{spec.lo:g}, {spec.hi:g}), "
        f"{spec.entries} entries, sampled at the {spec.sample} of each sub-interval;",
        f"  //   entry i covers x = {spec.lo:g} + i*{(spec.hi - spec.lo) / spec.entries:g}; "
        f"values are {fmt}. Computed and rounded by the harness (D420).",
        f"  function automatic [{width - 1}:0] {spec.name}(input [{k - 1}:0] i);",
        "    case (i)",
    ]
    for i, w in enumerate(words.tolist()):
        lines.append(f"      {k}'d{i}: {spec.name} = {width}'h{int(w):0{(width + 3) // 4}x};")
    lines += [f"      default: {spec.name} = {width}'h{0:0{(width + 3) // 4}x};",
              "    endcase", "  endfunction"]
    return "\n".join(lines)


def inject_tables(source: str, module: str, rendered: list[str]) -> str:
    """Insert rendered functions inside `module` (before its `endmodule`). Any earlier
    oracle table with the same name is replaced, so re-requests do not duplicate -- and so
    is a function the MODEL wrote under that name (D473): it asked for the exact table
    and sketched one itself in the same reply, and the duplicate definition cost a repair
    turn; the oracle's is the one that is right."""
    if not rendered:
        return source
    for block in rendered:
        m = re.match(r"\s*// TABLE (\w+):", block)
        if m:
            name = m.group(1)
            source = re.sub(
                rf"\n[ \t]*// TABLE {name}:.*?\n[ \t]*endfunction[ \t]*",
                "", source, flags=re.S)
            source = re.sub(
                rf"\n[ \t]*function\b[^\n;]*?\b{name}\s*\(.*?\n[ \t]*endfunction[ \t]*",
                "", source, flags=re.S)
    start = re.search(rf"\bmodule\s+{re.escape(module)}\b", source)
    if not start:
        raise ValueError(f"module {module} not found; cannot place tables")
    end = re.compile(r"\bendmodule\b").search(source, start.end())
    if not end:
        raise ValueError(f"module {module} has no endmodule; cannot place tables")
    # Idempotent: a patch re-applies its candidate's tables every turn, and an extra blank
    # line per turn is text that grows for no reason.
    return (source[:end.start()].rstrip() + "\n\n" + "\n\n".join(rendered) + "\n"
            + source[end.start():])


def table_schema() -> dict:
    """The oracle's request shape, embedded in the design and patch schemas."""
    return {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "func": {"type": "string", "enum": sorted(FUNCS)},
                "lo": {"type": "number"},
                "hi": {"type": "number"},
                "entries": {"type": "integer", "minimum": 2, "maximum": MAX_ENTRIES},
                "format": {"type": "string", "enum": list(FORMATS)},
                "frac_bits": {"type": "integer", "minimum": 0, "maximum": 30},
                "sample": {"type": "string", "enum": ["left", "mid", "right"]},
                "width": {"type": "integer", "minimum": 1, "maximum": MAX_WIDTH},
            },
            "required": ["name", "func", "lo", "hi", "entries"],
        },
    }


def parse_table_specs(raw: Any) -> tuple[list[TableSpec], list[str]]:
    """(specs, refusals) from a reply's `tables` field; each bad entry is refused with
    its reason and the rest still go through."""
    specs: list[TableSpec] = []
    refused: list[str] = []
    for i, t in enumerate(raw or [], 1):
        if not isinstance(t, dict):
            refused.append(f"table {i}: not an object")
            continue
        try:
            specs.append(TableSpec(
                name=str(t.get("name", "")), func=str(t.get("func", "")),
                lo=float(t.get("lo", 0.0)), hi=float(t.get("hi", 1.0)),
                entries=int(t.get("entries", 0)), fmt=str(t.get("format", "ufixed")),
                frac_bits=int(t.get("frac_bits", 10)),
                sample=str(t.get("sample", "left")),
                width=(int(t["width"]) if t.get("width") is not None else None)))
        except (TypeError, ValueError) as exc:
            refused.append(f"table {i} ({t.get('name', '?')}): {exc}")
    return specs, refused

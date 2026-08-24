"""`DesignSpec` -- the declarative, language-agnostic input a generated-design harness is told
(D39, shared in D453). Loosely follows the IR conventions (`docs/ir.md`: `schema_version`, `id`,
a typed structural list, `attrs`-shaped fields) without `flux_ir`'s canonicalisation or hashing:
a validated dict in, a dataclass out.

`ports` and `test_vectors` drive a deterministically generated driver (each harness's
`driver_gen.py`) that does the VCD tracing and pass/fail checking. Only the DUT's internal
behaviour is left to a model: the checker is never the thing being checked.

Dtypes are `"int"` and `"bool"` only; wider types (structs, packed unions) are not supported.
How a dtype and width spell in a language belongs to that language's harness
(`flux_codegen_rtl_harness.compose._verilog_type`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import InvalidSpecError

#: The two port directions a harness can drive and check.
VALID_DIRS = frozenset({"in", "out"})
#: The dtypes a spec may declare; their spelling per language is each harness's (D453).
VALID_DTYPES = frozenset({"int", "bool"})


@dataclass(frozen=True)
class Port:
    name: str
    dir: str  # "in" | "out"
    dtype: str  # "int" | "bool"
    # Array-valued port (D120/D121): `dims=(N,)` is one unpacked array rather than N ports;
    # `dims=(B, C)` is two-dimensional, like an operand memory (`i_mem[B][C]`). Inputs and
    # outputs both. `None` means a scalar port.
    dims: tuple[int, ...] | None = None
    # Bit width for an `int` port (D202). `None` means 32. Needed because a wide reduction
    # (e.g. 64 lanes of 16-bit products) overflows 32 bits, and the golden reference, computed
    # at unbounded precision in Python, would disagree with correct RTL (D193). Rejected on `bool`.
    bits: int | None = None

    @property
    def width(self) -> int:
        """Concrete bit width: the declared `bits`, or the default for this dtype."""
        if self.bits is not None:
            return self.bits
        return 1 if self.dtype == "bool" else 32

    @property
    def is_array(self) -> bool:
        return self.dims is not None

    @property
    def depth(self) -> int | None:
        """The single dimension of a 1-D array port. `None` for scalars; raises for 2-D rather than
        silently returning the first dimension."""
        if self.dims is None:
            return None
        if len(self.dims) != 1:
            raise InvalidSpecError(
                f"port {self.name!r} is {len(self.dims)}-dimensional {self.dims}; use `.dims`"
            )
        return self.dims[0]

    @property
    def element_count(self) -> int:
        n = 1
        for d in self.dims or ():
            n *= d
        return n


@dataclass(frozen=True)
class TestVector:
    inputs: dict[str, Any]
    expected: dict[str, Any]


@dataclass(frozen=True)
class DesignSpec:
    schema_version: str
    id: str
    module_name: str
    ports: tuple[Port, ...]
    behavior: str
    test_vectors: tuple[TestVector, ...]
    is_clocked: bool = False
    # Latency-measuring mode (D115): the DUT computes over multiple cycles and the harness counts
    # them. Implies `is_clocked`. Adds harness-owned `start`/`done` ports on the same terms as
    # `clk`/`rst_n` (the spec never names them). Off by default.
    measures_latency: bool = False


# Widths a generated design can use: at least 2 (a 1-bit signed integer holds only 0 and -1) and
# at most 64, where SystemVerilog integer arithmetic and the C++ reference stay exact (D202).
_MIN_PORT_BITS, _MAX_PORT_BITS = 2, 64


def parse_bits(name: str, raw_port: dict[str, Any], dtype: str) -> int | None:
    raw = raw_port.get("bits")
    if raw is None:
        return None
    if dtype != "int":
        raise InvalidSpecError(
            f"port {name!r}: `bits` applies to dtype='int' only, not {dtype!r} — a bool port is "
            "one bit by definition"
        )
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise InvalidSpecError(f"port {name!r}: bits={raw!r} must be an integer")
    if not _MIN_PORT_BITS <= raw <= _MAX_PORT_BITS:
        raise InvalidSpecError(
            f"port {name!r}: bits={raw} is outside [{_MIN_PORT_BITS}, {_MAX_PORT_BITS}]"
        )
    return raw


def _parse_dims(name: str, raw_port: dict[str, Any]) -> tuple[int, ...] | None:
    """`dims: [B, C]` is the general spelling; `depth: N` is the 1-D convenience (D120/D121)."""
    dims, depth = raw_port.get("dims"), raw_port.get("depth")
    if dims is not None and depth is not None:
        raise InvalidSpecError(f"port {name!r}: give either dims or depth, not both")
    if depth is not None:
        dims = [depth]
    if dims is None:
        return None
    if not isinstance(dims, (list, tuple)) or not dims:
        raise InvalidSpecError(f"port {name!r}: dims={dims!r} must be a non-empty list of integers")
    if len(dims) > 2:
        raise InvalidSpecError(
            f"port {name!r}: dims={list(dims)} has {len(dims)} dimensions; 1-D and 2-D are "
            "supported (2-D is what a real operand memory needs). Higher ranks are unbuilt, not "
            "silently flattened."
        )
    for d in dims:
        if isinstance(d, bool) or not isinstance(d, int) or d < 1:
            raise InvalidSpecError(f"port {name!r}: dims={list(dims)} must all be integers >= 1")
    return tuple(dims)


def _check_array_shape(where: str, port: Port, value: Any, *, base_axis: int = 0) -> None:
    """Shape-check a nested list against a port's `dims`, at parse time: otherwise generated
    Verilog indexes past an array's end and the failure reads as a design bug."""
    dims = port.dims or ()
    if not dims:
        return
    size, rest = dims[0], dims[1:]
    # `base_axis` so a nested row reports the axis the caller would recognise.
    if not isinstance(value, (list, tuple)):
        raise InvalidSpecError(
            f"{where}: port {port.name!r} axis {base_axis} must be a list of {size}, not "
            f"{type(value).__name__}"
        )
    if len(value) != size:
        raise InvalidSpecError(
            f"{where}: port {port.name!r} axis {base_axis} expects {size} entries, got {len(value)}"
        )
    # Every row is checked: a ragged list would pass its first row and generate wrong code.
    if rest:
        row_port = Port(port.name, port.dir, port.dtype, rest)
        for row in value:
            _check_array_shape(where, row_port, row, base_axis=base_axis + 1)


def design_spec_from_dict(doc: dict[str, Any]) -> DesignSpec:
    """Validate and parse a plain dict (e.g. loaded from YAML) into a `DesignSpec`. Raises
    `InvalidSpecError` for anything structurally wrong; never coerces."""
    module_name = doc.get("module_name")
    if not module_name or not str(module_name).isidentifier():
        raise InvalidSpecError(
            f"module_name={module_name!r} must be a non-empty identifier")

    raw_ports = doc.get("ports") or []
    if not raw_ports:
        raise InvalidSpecError("ports must be non-empty — a DUT with no ports can't be driven or checked")

    ports: list[Port] = []
    seen_names: set[str] = set()
    for p in raw_ports:
        name, dir_, dtype = p.get("name"), p.get("dir"), p.get("dtype")
        if not name or not str(name).isidentifier():
            raise InvalidSpecError(f"port name={name!r} must be a non-empty identifier")
        if name in seen_names:
            raise InvalidSpecError(f"duplicate port name={name!r}")
        if dir_ not in VALID_DIRS:
            raise InvalidSpecError(f"port {name!r}: dir={dir_!r} must be one of {sorted(VALID_DIRS)}")
        if dtype not in VALID_DTYPES:
            raise InvalidSpecError(f"port {name!r}: dtype={dtype!r} must be one of {sorted(VALID_DTYPES)}")
        dims = _parse_dims(name, p)
        if dims is not None and dtype != "int":
            raise InvalidSpecError(f"port {name!r}: array ports must have dtype='int', not {dtype!r}")
        bits = parse_bits(name, p, dtype)
        seen_names.add(name)
        ports.append(Port(name=name, dir=dir_, dtype=dtype, dims=dims, bits=bits))

    in_names = {p.name for p in ports if p.dir == "in"}
    out_names = {p.name for p in ports if p.dir == "out"}
    if not out_names:
        # A DUT with nothing to observe can never be verified; without this the RTL driver emits
        # an empty `if () begin` and the error points at generated code instead of the spec.
        raise InvalidSpecError(
            "ports must include at least one output — a DUT with no observable output can't be "
            "checked against any test vector"
        )

    measures_latency = bool(doc.get("measures_latency", False))
    if measures_latency and not doc.get("is_clocked", False):
        raise InvalidSpecError(
            "measures_latency=True requires is_clocked=True — cycles can only be counted against "
            "a clock (docs/decisions.md D115)."
        )

    raw_vectors = doc.get("test_vectors") or []
    if not raw_vectors:
        raise InvalidSpecError("test_vectors must be non-empty — a DUT with no vectors can never be verified")

    vectors: list[TestVector] = []
    for i, v in enumerate(raw_vectors):
        inputs, expected = v.get("inputs") or {}, v.get("expected") or {}
        missing_in = in_names - inputs.keys()
        if missing_in:
            raise InvalidSpecError(f"test_vectors[{i}]: missing inputs for {sorted(missing_in)}")
        missing_out = out_names - expected.keys()
        if missing_out:
            raise InvalidSpecError(f"test_vectors[{i}]: missing expected for {sorted(missing_out)}")
        for port in ports:
            if not port.is_array:
                continue
            source = inputs if port.dir == "in" else expected
            _check_array_shape(f"test_vectors[{i}]", port, source[port.name])
        vectors.append(TestVector(inputs=dict(inputs), expected=dict(expected)))

    behavior = doc.get("behavior")
    if not behavior or not str(behavior).strip():
        raise InvalidSpecError("behavior must be a non-empty description — it's the LLM's only spec of what to build")

    return DesignSpec(
        schema_version=doc.get("schema_version", "0.1.0"),
        id=doc.get("id", module_name),
        module_name=module_name,
        ports=tuple(ports),
        behavior=str(behavior),
        test_vectors=tuple(vectors),
        is_clocked=bool(doc.get("is_clocked", False)),
        measures_latency=measures_latency,
    )


__all__ = ["VALID_DIRS", "VALID_DTYPES", "DesignSpec", "Port", "TestVector",
           "design_spec_from_dict", "parse_bits"]

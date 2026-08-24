"""`DesignSpec` — the declarative, design-agnostic input to the SystemC codegen harness
(docs/decisions.md D39). Follows this repo's existing IR conventions loosely (`docs/ir.md`:
`schema_version` + `id` + a typed structural list + `attrs`-shaped fields) without pulling in the
full `flux_ir` machinery (canonicalization, content hashing) — v0.1 scope, a plain validated dict
in, a plain dataclass out.

Deliberately declarative, not LLM-authored: `ports` and `test_vectors` drive a *deterministically
generated* SystemC driver (`driver_gen.py`) that does the actual VCD tracing and pass/fail
checking. Only the DUT module's *internal behavior* is left for an LLM to fill in
(`flows/chia_nodes/generate_systemc.py`) — the thing doing the checking is never the thing being
checked, the same independence `flux_chia_nodes.validity` already applies to evaluated
candidates.

v0.1 dtype support is deliberately small: `"int"` and `"bool"`, the two C++ builtin types every
`sc_in`/`sc_out` template instantiates trivially without a custom `sc_trace` overload. Wider types
(structs, sc_uint<N>) are a real, un-implemented extension, not silently supported.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .errors import InvalidSpecError

_VALID_DIRS = {"in", "out"}
_DTYPE_TO_CPP = {"int": "int", "bool": "bool"}


@dataclass(frozen=True)
class Port:
    name: str
    dir: str  # "in" | "out"
    dtype: str  # "int" | "bool"
    # Array-valued port (docs/decisions.md D120, generalized in D121): `dims=(N,)` makes this one
    # unpacked array rather than N separate ports; `dims=(B, C)` makes it two-dimensional, which
    # is what a real operand memory looks like (`i_mem[B][C]`, `o_mem[B][K]`). Inputs and outputs
    # both. `None` means a plain scalar port, so every pre-D120 spec is untouched.
    dims: tuple[int, ...] | None = None
    # Bit width for an `int` port (docs/decisions.md D202). `None` means the historical default of
    # 32, so every pre-existing spec is unchanged. It exists because the width was previously
    # implicit and fixed: `derive_design_spec` had to *refuse* a 16-bit workload outright, since a
    # 64-lane dot product of 16-bit operands overflows a 32-bit accumulator and the golden
    # reference — computed in Python at unbounded precision — would then disagree with correct RTL
    # (D193). Meaningless for `bool`, and rejected there rather than ignored.
    bits: int | None = None

    @property
    def width(self) -> int:
        """Concrete bit width: the declared `bits`, or the historical default for this dtype."""
        if self.bits is not None:
            return self.bits
        return 1 if self.dtype == "bool" else 32

    @property
    def cpp_type(self) -> str:
        """C++ type for this port. A sized `int` port becomes `sc_int<N>` (docs/decisions.md
        D203); an unsized one stays plain `int`, so every spec written before widths existed
        generates byte-identical code.

        `sc_int` rather than a wider native type because SystemC's own fixed-width integer is what
        truncates at N bits — the point of declaring a width is that the reference model and the
        DUT agree on overflow, which a plain `long long` would not give.
        """
        if self.bits is None:
            return _DTYPE_TO_CPP[self.dtype]
        return f"sc_int<{self.bits}>"

    @property
    def is_array(self) -> bool:
        return self.dims is not None

    @property
    def depth(self) -> int | None:
        """The single dimension of a 1-D array port. `None` for scalars; raises for 2-D, where
        asking for "the" depth is a question with no answer — better than silently returning the
        first dimension to a caller who has not thought about the second."""
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
    # Latency-measuring mode (docs/decisions.md D115): the DUT computes over MULTIPLE cycles and
    # the harness measures how many. Implies `is_clocked`. Adds harness-owned `start`/`done`
    # handshake ports on the same terms as `clk`/`rst_n` — the spec never names them, the
    # generator is only told they exist. Off by default, so every existing combinational and
    # one-vector-per-cycle spec is untouched.
    measures_latency: bool = False


# Widths a generated design can actually use. Bounded below by 2 (a 1-bit signed integer has only
# the values 0 and -1, which no caller means) and above by 64, where SystemVerilog's own default
# integer arithmetic and this harness's C++ reference both stay exact (docs/decisions.md D202).
_MIN_PORT_BITS, _MAX_PORT_BITS = 2, 64


def _parse_bits(name: str, raw_port: dict[str, Any], dtype: str) -> int | None:
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
    """`dims: [B, C]` is the general spelling; `depth: N` is the 1-D convenience (docs/decisions.md
    D120/D121). Accepting both keeps every D120-era spec valid without a second concept."""
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
    """Shape-check a nested list against a port's `dims`. Checked here, at parse time, because the
    alternative is generated Verilog that indexes past the end of an array — a simulation-time
    failure that reads as a design bug rather than a malformed spec."""
    dims = port.dims or ()
    if not dims:
        return
    size, rest = dims[0], dims[1:]
    # `base_axis` so a nested row reports the axis the *caller* would recognize — a message about
    # "axis 0" for what the reader thinks of as the inner dimension sends them to the wrong place.
    if not isinstance(value, (list, tuple)):
        raise InvalidSpecError(
            f"{where}: port {port.name!r} axis {base_axis} must be a list of {size}, not "
            f"{type(value).__name__}"
        )
    if len(value) != size:
        raise InvalidSpecError(
            f"{where}: port {port.name!r} axis {base_axis} expects {size} entries, got {len(value)}"
        )
    # Every row is checked, not just the first — a ragged nested list would otherwise pass its
    # first row and generate wrong code for the rest.
    if rest:
        row_port = Port(port.name, port.dir, port.dtype, rest)
        for row in value:
            _check_array_shape(where, row_port, row, base_axis=base_axis + 1)


def design_spec_from_dict(doc: dict[str, Any]) -> DesignSpec:
    """Validate and parse a plain dict (e.g. loaded from YAML) into a `DesignSpec`. Raises
    `InvalidSpecError` for anything structurally wrong — never silently coerces."""
    module_name = doc.get("module_name")
    if not module_name or not str(module_name).isidentifier():
        raise InvalidSpecError(f"module_name={module_name!r} must be a non-empty C++ identifier")

    raw_ports = doc.get("ports") or []
    if not raw_ports:
        raise InvalidSpecError("ports must be non-empty — a DUT with no ports can't be driven or checked")

    ports: list[Port] = []
    seen_names: set[str] = set()
    for p in raw_ports:
        name, dir_, dtype = p.get("name"), p.get("dir"), p.get("dtype")
        if not name or not str(name).isidentifier():
            raise InvalidSpecError(f"port name={name!r} must be a non-empty C++ identifier")
        if name in seen_names:
            raise InvalidSpecError(f"duplicate port name={name!r}")
        if dir_ not in _VALID_DIRS:
            raise InvalidSpecError(f"port {name!r}: dir={dir_!r} must be one of {sorted(_VALID_DIRS)}")
        if dtype not in _DTYPE_TO_CPP:
            raise InvalidSpecError(f"port {name!r}: dtype={dtype!r} must be one of {sorted(_DTYPE_TO_CPP)}")
        dims = _parse_dims(name, p)
        if dims is not None and dtype != "int":
            raise InvalidSpecError(f"port {name!r}: array ports must have dtype='int', not {dtype!r}")
        bits = _parse_bits(name, p, dtype)
        seen_names.add(name)
        ports.append(Port(name=name, dir=dir_, dtype=dtype, dims=dims, bits=bits))

    in_names = {p.name for p in ports if p.dir == "in"}
    out_names = {p.name for p in ports if p.dir == "out"}
    if not out_names:
        # Same reasoning as the empty-`test_vectors` check below: a DUT with nothing to observe
        # can never be verified. Without this the RTL driver emits a literally empty condition
        # (`if () begin`) and the caller gets a Verilator syntax error pointing at generated code
        # they did not write, instead of the real problem in the spec they did.
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

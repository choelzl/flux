"""Fabrics built from VENDORED IP rather than generated switches (docs/decisions.md D276).

`interconnect/vendor/` carries PULP's `xbar_varlat` (a logarithmic interconnect whose
`rr_arb_tree` muxes the data as it arbitrates) and an OBI-typed wrapper around it. This module
makes those sources synthesisable (`vendored_sources_text`) and says which topologies the
vendored switch can serve (`supports`); `fabric.py` instantiates it as the switch of an
otherwise generated fabric, so a generated fabric and a proven one are measured through the
same harness and compared on the same table.

The standalone assembler this module used to carry (a whole vendored fabric emitted and placed
by itself) is gone as of D458: `fabric.py`'s `switch="vendored"` path builds and places the same
thing through the one flow every other fabric goes through, which is what made the two numbers
comparable in the first place.

Why it exists is measured, not assumed: at the identical topology — seven 4x4 switches feeding
four 7x8 switches — this repo's generated switch closed several times slower than the vendored
IP, whose whole three-level request path is combinational. Tuning a hand-rolled arbiter against
a proven one is not the best use of anyone's time; holding the generator to the proven one's
number is.

**Scope, stated up front.** Address-slice routing needs every stage's fan-out to be a power of
two, and the fan-outs must multiply to the bank count. That covers the regular fabrics (the
staged and butterfly families) and excludes the irregular ones this repo can also express — a
Clos with seven middle switches has no bit-slice that names its port. Those keep the generated
switch, and `supports()` says which is which rather than failing at elaboration.
"""

from __future__ import annotations

import re
from pathlib import Path

from .fabric import canonical_stages
from .topology import Topology

VENDOR_DIR = Path(__file__).resolve().parents[2] / "vendor"

# Compile order matters: macro headers and packages before anything that uses them, leaf cells
# before the modules that instantiate them.
VENDOR_SOURCES = (
    "common_cells/assertions.svh",
    "common_cells/cf_math_pkg.sv",
    "common_cells/lzc.sv",
    "common_cells/rr_arb_tree.sv",
    "cluster_interconnect/addr_dec_resp_mux_varlat.sv",
    "cluster_interconnect/xbar_varlat.sv",
    "obi_pkg.sv",
    "crossbar.sv",
)


def vendor_files() -> list[Path]:
    """The vendored sources, in compile order. Missing files raise here rather than inside a
    tool invocation, where the error would be a Verilator parse failure ten steps later."""
    paths = [VENDOR_DIR / name for name in VENDOR_SOURCES]
    missing = [p for p in paths if not p.exists()]
    if missing:
        raise FileNotFoundError(
            f"vendored interconnect IP is not present: {[str(p) for p in missing]} — see "
            f"{VENDOR_DIR / 'PROVENANCE.md'}")
    return paths


# Yosys's SystemVerilog frontend is a subset, and the vendored IP uses three constructs
# outside it. Rather than edit the vendored files — they stay byte-identical to upstream, which
# is what makes the provenance checkable — the rewrites are applied at LOAD, the same shape as
# the upstream project's own `patches/` directory. Each is behaviour-preserving for the way
# THIS repo instantiates the IP, and each says why:
#
#   `unsigned'(x)`      -> `(x)`      the casts here are all in constant/elaboration
#                                     expressions where signedness cannot change the value
#   `parameter type T`  -> removed    Yosys has no type parameters. Both of them (`DataType`,
#                                     `idx_t`) are dependent parameters that `xbar_varlat`
#                                     never overrides — it passes only NumIn, DataWidth and
#                                     ExtPrio — so each is exactly its default here
#   `return expr;`      -> `f = expr` the Verilog-2001 function-return form
#
# If a vendored file changes so a rewrite no longer applies, loading RAISES rather than
# silently synthesising something else.
_TYPE_PARAMS: tuple[tuple[str, str, str], ...] = (
    ("rr_arb_tree.sv", "DataType", "logic [DataWidth-1:0]"),
    ("rr_arb_tree.sv", "idx_t", "logic [IdxWidth-1:0]"),
)

_LINE_PATCHES: tuple[tuple[str, str, str], ...] = (
    # Yosys cannot take `$bits` of a TYPE. obi_req_t is req + we + be + addr + wdata, so the
    # same width is written from the macros that define it — exact, and still correct if the
    # data width is overridden at elaboration.
    ("crossbar.sv", "$bits(obi_req_t)", "(2 + `OBI_BE_WIDTH + 32 + `OBI_DATA_WIDTH)"),
    ("cf_math_pkg.sv",
     "return (num_idx > 32'd1) ? ($clog2(num_idx)) : 32'd1;",
     "idx_width = (num_idx > 32'd1) ? ($clog2(num_idx)) : 32'd1;"),
    ("cf_math_pkg.sv",
     "return (value != 0) && (value & (value - 1)) == 0;",
     "is_power_of_2 = (value != 0) && ((value & (value - 1)) == 0);"),
)


def _yosys_compatible(name: str, body: str) -> str:
    """Rewrite one vendored file into Yosys's SystemVerilog subset."""
    body = body.replace("unsigned'(", "(")
    # Yosys takes neither a package-scoped `import` on a module header nor, reliably,
    # package-qualified types in ports. The OBI package holds nothing but two typedefs, so
    # flattening it to file scope and dropping the imports is equivalent and leaves every
    # type name unchanged.
    drop_package = name == "obi_pkg.sv"   # scoped: cf_math_pkg is a real package and stays
    body = "\n".join(
        line for line in body.splitlines()
        if not (drop_package and line.strip() in ("package obi_pkg;", "endpackage"))
        and "import obi_pkg::*;" not in line)
    for target, type_name, concrete in _TYPE_PARAMS:
        if name != target:
            continue
        declaration = re.search(rf"^\s*parameter type {type_name}\s*=.*$", body, re.M)
        if not declaration:
            raise ValueError(
                f"{name}: expected a `parameter type {type_name}` declaration to remove — the "
                "vendored file changed and this rewrite no longer describes it")
        body = body.replace(declaration.group(0),
                            f"    // `parameter type {type_name}` removed for Yosys; it is "
                            f"always {concrete} as instantiated here")
        # a type name only ever appears where a type may: a port, a signal, or a cast
        body = re.sub(rf"\b{type_name}'\(", "(", body)
        body = re.sub(rf"(?<![\w.]){type_name}(?=\s)", concrete, body)
    for target, before, after in _LINE_PATCHES:
        if name == target:
            if before not in body:
                raise ValueError(
                    f"{name}: rewrite {before!r} no longer applies — the vendored file changed")
            body = body.replace(before, after)
    return body


def vendored_sources_text(*, for_yosys_builtin: bool = False) -> str:
    """All vendored RTL as one string with `include` directives RESOLVED.

    With a real SystemVerilog front end (`yosys-slang`) the text goes through UNCHANGED apart
    from the include resolution that concatenation forces. `for_yosys_builtin=True` additionally
    applies the rewrites in `_yosys_compatible`, which is a fallback and says so: it got five of
    the six constructs the built-in reader cannot take, and the sixth is not a rewrite.

    The flow takes a single source blob, and an `include` inside a concatenation looks for a
    file that is no longer beside it. Since the include list here is exactly the vendored set,
    ordering the files correctly and dropping the directives is equivalent and keeps the
    vendored text otherwise byte-identical to upstream.
    """
    out = []
    for path in vendor_files():
        body = "\n".join(line for line in path.read_text().splitlines()
                          if not line.lstrip().startswith("`include"))
        if for_yosys_builtin:
            body = _yosys_compatible(path.name, body)
        out.append(f"// ---- vendored: {path.name} ----\n{body}")
    return "\n".join(out)


def supports(topo: Topology) -> tuple[bool, str]:
    """(can this be built from the vendored IP, why not). A predicate rather than an exception
    because the answer is routinely 'no' for perfectly good fabrics, and a search needs to ask
    cheaply."""
    try:
        stages = canonical_stages(topo)
    except ValueError as exc:
        return False, str(exc)
    reach = 1
    for index, stage in enumerate(stages):
        fan_out = stage["out"]
        if fan_out & (fan_out - 1):
            return False, (f"stage {index + 1} fans out to {fan_out}, not a power of two — "
                           "address-slice routing cannot name that port")
        reach *= fan_out
    if reach != topo.banks:
        return False, (f"per-stage fan-outs multiply to {reach}, not the {topo.banks} banks — "
                       "the bank index is not the concatenation of the stage digits")
    return True, ""



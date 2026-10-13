# Writing the datapath in pyCircuit 6.1

`pycircuit` is on PATH (nixchip's pyCircuit 6.1.0). `pycircuit build FILE --out-dir OUT --target cpp`
compiles a design with a testbench into a C++ model; the checks do that for you
(`fluxpreset pto check DESIGN --units TADD --spec spec`, `--build-only` to see that it compiles).

pyCircuit compiles the body of `build` itself (a JIT over its Python source): it is a SUBSET of
Python. Loops over Python constants unroll; Python `if` on a hardware signal is an error (use
`mux`); slicing a Python list inside `build` is refused (loop over the whole tuple instead).

A design that compiles and passes TADD, TSUB, TAND, TOR, TXOR and TNOT on the integer types:

```python
from __future__ import annotations

from pycircuit import CycleAwareCircuit, CycleAwareDomain, cas, mux, wire_of

TADD, TAND, TNOT, TOR, TSUB, TXOR = 1, 2, 14, 15, 24, 25      # codes from `fluxpreset pto ops`
INTEGER_TYPES = (16, 17, 18, 19, 22, 23, 24, 25)               # S64 S32 S16 S8 U64 U32 U16 U8


def build(m: CycleAwareCircuit, domain: CycleAwareDomain) -> None:
    op = cas(domain, m.input("op", width=8), cycle=0)          # every input wrapped as a cycle-aware signal
    dtype = cas(domain, m.input("dtype", width=5), cycle=0)
    a = cas(domain, m.input("a", width=64), cycle=0)
    b = cas(domain, m.input("b", width=64), cycle=0)
    is_int = domain.create_const(0, width=1)
    for code in INTEGER_TYPES:
        is_int = is_int | (dtype == code)                       # compare with a Python int
    y = a + b                                                   # wraps at 64 bits: right for every width
    y = mux(op == TSUB, a - b, y)                               # mux(select, if_true, if_false)
    y = mux(op == TAND, a & b, y)
    y = mux(op == TOR, a | b, y)
    y = mux(op == TXOR, a ^ b, y)
    y = mux(op == TNOT, ~a, y)
    known = (op == TADD) | (op == TSUB) | (op == TAND) | (op == TOR) | (op == TXOR) | (op == TNOT)
    m.output("y", wire_of(y))                                   # outputs take the wire of a signal
    m.output("fault", wire_of(~(known & is_int)))


build.__pycircuit_name__ = "pto_elementwise"
```

On a cycle-aware signal: `+ - * & | ^ ~ << >>`, `== != < <= > >=`, `x[lo:hi]` and `x[i]` (bits),
`x.trunc(width=w)`, `x.zext(width=w)`, `x.sext(width=w)`, `x.select(t, f)`, and `cat(...)`,
`mux(sel, t, f)` from `pycircuit`. Signedness is yours: compare signed values by flipping the sign
bits, or sign-extend first. The type's width matters: an 8-bit type's sum is the low 8 bits, its
sign bit is bit 7, a shift count is masked to the width (see the unit's clauses).

Floating types (FP64/FP32/FP16/BF16 and the 8-bit formats) follow the spec's ASL exactly, NaN
encodings and signed zeros included: `fluxpreset pto ref UNIT --spec spec` caches every reference
vector under ~/.cache/flux/pto-ref, and `fluxpreset pto check` prints each wrong one with what was
expected. pyCircuit's own sources and examples are in the package
(`$(dirname $(readlink -f $(which pycircuit)))/../share/pycircuit/python/pycircuit`).

"""A C or C++ design's function against a Python reference (D972), like `fluxpreset cases`.

    fluxpreset ccases DESIGN.c --call add --sig "long(long,long)" --reference ref.py:add --random 200
    # each mismatch on a line, then "N failing of M"; exit 0, 1, or 3 (the design did not build)

The design is built as a shared library (`--build`, default `cc -O2 -shared -fPIC -o {out} {artifact}`;
C++ with `c++` and the function `extern "C"`) and its function called through ctypes with the
signature `--sig "RET(ARG,...)"` -- scalar types only: char, short, int, long, long long, their
unsigned forms, int8..int64, uint8..uint64, float, double, bool, size_t. The cases are the same as
`fluxpreset cases`'s: `--cases` JSON, the reference module's `CASES`, `--random N` of its
`inputs(rng)`; a result is compared with the reference function's or `expected`.
"""

from __future__ import annotations

import argparse
import ctypes
import re
import shlex
import subprocess
import tempfile
from pathlib import Path

from .cases import add_case_arguments, compare

TYPES = {"char": ctypes.c_char, "signed char": ctypes.c_byte, "unsigned char": ctypes.c_ubyte, "short": ctypes.c_short,
         "unsigned short": ctypes.c_ushort, "int": ctypes.c_int, "unsigned": ctypes.c_uint, "unsigned int": ctypes.c_uint,
         "long": ctypes.c_long, "unsigned long": ctypes.c_ulong, "long long": ctypes.c_longlong,
         "unsigned long long": ctypes.c_ulonglong, "int8_t": ctypes.c_int8, "int16_t": ctypes.c_int16,
         "int32_t": ctypes.c_int32, "int64_t": ctypes.c_int64, "uint8_t": ctypes.c_uint8, "uint16_t": ctypes.c_uint16,
         "uint32_t": ctypes.c_uint32, "uint64_t": ctypes.c_uint64, "float": ctypes.c_float, "double": ctypes.c_double,
         "bool": ctypes.c_bool, "_Bool": ctypes.c_bool, "size_t": ctypes.c_size_t, "void": None}


def catalog() -> list[dict]:
    """This preset's catalog entry (D972)."""
    from ._entries import N_FAILING, param
    return [{"id": "c-cases", "role": "check", "title": "Compare a C function with a reference",
             "what": "Builds the design as a shared library and calls one function (a scalar signature) on cases, each "
                     "result compared with a Python reference function's or the expected value.",
             "run": "fluxpreset ccases {artifact} --call {call} --sig {sig} --reference {reference} --random {random}",
             "params": {"call": param("Function", "main"), "sig": param("Signature", "int(int)"),
                        "reference": param("Reference (file:function)", "{home}/reference.py"), "random": param("Random cases", 100)},
             "needs": ["cc"], "pass": N_FAILING, "languages": ["c", "cpp"], "kinds": ["program"], "short": "cases"}]


def signature(sig: str) -> tuple[object, list[object]]:
    m = re.fullmatch(r"\s*([\w ]+?)\s*\(\s*(.*?)\s*\)\s*", sig)
    if not m:
        raise ValueError(f"--sig is RET(ARG,...), e.g. \"long(long,long)\", not {sig!r}")
    names = [m.group(1)] + ([x.strip() for x in m.group(2).split(",")] if m.group(2) and m.group(2) != "void" else [])
    unknown = [n for n in names if n not in TYPES]
    if unknown:
        raise ValueError(f"--sig: {', '.join(unknown)} is no type it knows ({', '.join(sorted(k for k in TYPES if k))})")
    return TYPES[names[0]], [TYPES[n] for n in names[1:]]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fluxpreset ccases", description=__doc__.split("\n\n")[0])
    ap.add_argument("artifact")
    ap.add_argument("--sig", required=True, help='the function\'s signature, e.g. "long(long,long)"')
    ap.add_argument("--build", default="cc -O2 -shared -fPIC -o {out} {artifact}",
                    help="how the shared library is built ({out}, {artifact})")
    ap.add_argument("--tol", type=float, default=0.0, help="relative tolerance for floats")
    ap.add_argument("--timeout", type=float, default=300.0)
    add_case_arguments(ap)
    a = ap.parse_args(argv)
    try:
        restype, argtypes = signature(a.sig)
    except ValueError as exc:
        ap.error(str(exc))
    # the library stays loaded until the process ends: on a network mount its folder cannot go sooner
    with tempfile.TemporaryDirectory(prefix="flux-ccases-", ignore_cleanup_errors=True) as d:
        lib = Path(d) / "design.so"
        cmd = shlex.split(a.build.replace("{out}", shlex.quote(str(lib))).replace("{artifact}", shlex.quote(a.artifact)))
        r = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=a.timeout)
        if r.returncode != 0 or not lib.exists():
            print("did not build: " + "\n".join((r.stderr or r.stdout).strip().splitlines()[:20]))
            print("1 failing")
            return 3
        try:
            fn = getattr(ctypes.CDLL(str(lib)), a.call)
        except (OSError, AttributeError) as exc:
            print(f"did not load: {exc} (a C++ function needs extern \"C\")")
            print("1 failing")
            return 3
        fn.restype, fn.argtypes = restype, argtypes
        return compare(fn, a)


if __name__ == "__main__":
    raise SystemExit(main())

"""A SystemC prototype (D635): the model writes the algorithm as a synthesizable `SC_MODULE`; a
generated testbench drives it with the golden model's vectors, compiled against libsystemc
(`SYSTEMC_HOME`). The comparison and its report are the Python prototype's
(`golden_proto.compare`)."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .types import Verdict

__all__ = ["SYSTEMC_RULES", "check", "icsc", "systemc_home", "testbench", "translate"]

SYSTEMC_RULES = (
    "RULES so it synthesises: one header defining `SC_MODULE(<name>)` with an `sc_in<sc_uint<N>>` per "
    "input port and an `sc_out<sc_uint<N>>` per output port (bit patterns: cast to `sc_int<N>` inside "
    "where a port is signed), one `SC_METHOD` sensitive to every input that computes every output. "
    "Integers and bit operations only (`sc_uint`, `sc_int`, `sc_biguint`, shifts, masks, `range()`, "
    "multiply, add, compare); no `float` or `double`, no dynamic memory, no I/O; `for` only with "
    "constant bounds; tables as `static const` arrays. No `sc_main`: the harness writes it and checks "
    "every vector exactly as the gate does.")

#: Where the compiler and libsystemc are, and how long a build may take.
BUILD_TIMEOUT_S = 180.0


def systemc_home() -> Path | None:
    home = os.environ.get("SYSTEMC_HOME")
    return Path(home) if home and Path(home, "include", "systemc.h").is_file() else None


def testbench(module: str, g: Any) -> str:
    """`sc_main` for `module`: reads one vector per line (the inputs in port order), writes
    the outputs in port order."""
    ins = [p for p in g.ports if p["dir"] == "in"]
    outs = [p for p in g.ports if p["dir"] == "out"]
    sig = "\n".join(f"  sc_signal<sc_uint<{p['bits']}>> {p['name']};" for p in ins + outs)
    bind = " ".join(f"dut.{p['name']}({p['name']});" for p in ins + outs)
    read = ", ".join(f"&v_{p['name']}" for p in ins)
    fmt = " ".join("%llu" for _ in ins)
    write = " ".join(f"{p['name']}.write(v_{p['name']});" for p in ins)
    show = ' << " " << '.join(f"{p['name']}.read().to_uint64()" for p in outs)
    decl = "unsigned long long " + ", ".join(f"v_{p['name']}" for p in ins) + ";"
    return f"""#include <systemc.h>
#include <cstdio>
#include <iostream>
#include "design.h"
int sc_main(int argc, char* argv[]) {{
{sig}
  {module} dut("dut");
  {bind}
  FILE* f = fopen(argv[1], "r");
  {decl}
  while (fscanf(f, "{fmt}", {read}) == {len(ins)}) {{
    {write}
    sc_start(1, SC_NS);
    std::cout << {show} << "\\n";
  }}
  return 0;
}}
"""


def check(code: str, g: Any, rows: list[dict[str, Any]], module: str, timeout_s: float = 120.0) -> Verdict:
    """The prototype built against libsystemc and run on every vector; score: the failing vectors."""
    from .golden_proto import compare

    n = float(len(rows))
    if not re.search(rf"SC_MODULE\s*\(\s*{re.escape(module)}\s*\)", code):
        return Verdict(False, n, f"the prototype defines no `SC_MODULE({module})`")
    floats = sorted(set(re.findall(r"\b(float|double)\b", code)))
    if floats:
        return Verdict(False, n, f"the prototype uses {' and '.join(floats)}, which hardware does not: compute "
                       "in fixed point with sc_uint/sc_int and put float math into constant tables")
    home = systemc_home()
    if home is None:
        return Verdict(False, n, "SystemC is not installed here: SYSTEMC_HOME does not name it")
    lib = next((d for d in (home / "lib", home / "lib-linux64", home / "lib64") if d.is_dir()), home / "lib")
    outs = [p["name"] for p in g.ports if p["dir"] == "out"]
    with tempfile.TemporaryDirectory(prefix="flux-sc-") as d:
        (Path(d) / "design.h").write_text(code)
        (Path(d) / "tb.cpp").write_text(testbench(module, g))
        (Path(d) / "vectors.txt").write_text(
            "\n".join(" ".join(str(int(r["inputs"][k]) % (1 << bits)) for k, bits in
                               ((p["name"], int(p["bits"])) for p in g.ports if p["dir"] == "in")) for r in rows) + "\n")
        try:
            cc = subprocess.run(["g++", "-std=c++17", "-O1", f"-I{home / 'include'}", "tb.cpp", "-o", "tb",
                                 f"-L{lib}", f"-Wl,-rpath,{lib}", "-lsystemc"],
                                cwd=d, capture_output=True, text=True, timeout=BUILD_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return Verdict(False, n, f"the prototype took longer than {BUILD_TIMEOUT_S:g}s to compile")
        if cc.returncode != 0:
            errors = [ln for ln in cc.stderr.splitlines() if "error" in ln][:8] or cc.stderr.splitlines()[-8:]
            return Verdict(False, n, "the prototype does not compile:\n" + "\n".join(errors))
        try:
            run = subprocess.run(["./tb", "vectors.txt"], cwd=d, capture_output=True, text=True, timeout=timeout_s,
                                 env={**os.environ, "SC_COPYRIGHT_MESSAGE": "DISABLE"})
        except subprocess.TimeoutExpired:
            return Verdict(False, n, f"the prototype ran longer than {timeout_s:g}s on {len(rows)} vectors")
    lines = [ln.split() for ln in run.stdout.splitlines() if ln.strip() and ln.split()[0].isdigit()]
    if run.returncode != 0 or len(lines) != len(rows):
        return Verdict(False, n, f"the simulation stopped after {len(lines)} of {len(rows)} vectors:\n"
                       + (run.stderr or run.stdout)[-1200:])
    return compare(g, rows, [dict(zip(outs, map(int, ln))) for ln in lines])


# ---- the verified prototype, translated to SystemVerilog by ICSC (D636) -------------------
#: How long ICSC may take to build and run its elaborator on one module.
TRANSLATE_TIMEOUT_S = 300.0


def icsc() -> Path | None:
    """Intel's SystemC compiler: `ICSC_HOME` (nixchip's `icsc`, set by `nix develop .#systemc`)."""
    home = os.environ.get("ICSC_HOME")
    return Path(home) if home and Path(home, "include", "sc_tool", "SCTool.h").is_file() else None


def _system_includes(env: dict[str, str]) -> list[str]:
    """The compiler's own include directories (gcc's internal ones aside), for ICSC's Clang pass."""
    run = subprocess.run(["c++", "-xc++", "-E", "-v", "/dev/null"], capture_output=True, text=True, env=env)
    text = run.stderr.split("#include <...> search starts here:")[-1].split("End of search list.")[0]
    return [f"-isystem{d.strip()}" for d in text.splitlines() if d.strip() and "/lib/gcc/" not in d]


def translate(code: str, module: str, g: Any, timeout_s: float = TRANSLATE_TIMEOUT_S) -> tuple[str, str]:
    """The prototype `SC_MODULE(module)` as SystemVerilog, by ICSC: an `sc_main` binds one
    signal per golden port and calls `sc_start()`; compiled as ICSC's "unity" file against
    libSCTool (its cmake `svc_target` without cmake), running it elaborates and translates.
    Returns `(sv, "")`, or `("", why)` when ICSC is absent or refuses the module, or when its
    ports are not the golden PORTS (names, directions, widths)."""
    home = icsc()
    if home is None:
        return "", "ICSC is not here (no ICSC_HOME): run in `nix develop .#systemc`"
    ports = [p for p in g.ports if p["dir"] == "in"] + [p for p in g.ports if p["dir"] == "out"]
    sig = "\n".join(f"  sc_signal<sc_uint<{p['bits']}>> {p['name']};" for p in ports)
    bind = " ".join(f"dut.{p['name']}({p['name']});" for p in ports)
    top = f'#include "design.h"\nint sc_main(int, char*[]) {{\n{sig}\n  {module} dut("dut");\n  {bind}\n  sc_start();\n  return 0;\n}}\n'
    # the dev shell's NIX_CFLAGS_COMPILE names pkgs.systemc's headers: ICSC's patched ones only
    env = {k: v for k, v in os.environ.items() if k not in ("NIX_CFLAGS_COMPILE", "NIX_LDFLAGS")}
    clang = next(iter(sorted((home / "lib" / "clang").glob("*/include"))), None)
    with tempfile.TemporaryDirectory(prefix="flux-icsc-") as d:
        (Path(d) / "design.h").write_text(code)
        (Path(d) / "top.cpp").write_text(top)
        out = Path(d) / "out.sv"
        inc = [f"-I{home}/include", f"-I{home}/include/sctcommon", f"-I{d}"]
        args = " ".join([f"{d}/unity.cpp", "-sv_out", str(out), "--", "-D__SC_TOOL__", "-D__SC_TOOL_ANALYZE__",
                         "-DNDEBUG", "-DSC_ALLOW_DEPRECATED_IEEE_API", "-Wno-logical-op-parentheses", "-std=c++20",
                         "-nostdinc", *inc, *_system_includes(env), *([f"-isystem{clang}"] if clang else [])])
        (Path(d) / "unity.cpp").write_text(f'#include <sc_tool/SCTool.h>\nconst char* __sctool_args_str = R"({args})";\n'
                                           f'#include "{d}/top.cpp"\n')
        try:
            run = subprocess.run(["c++", "-std=c++20", "-O1", "-D__SC_TOOL__", "-DSC_ALLOW_DEPRECATED_IEEE_API", *inc,
                                  "unity.cpp", "-o", "sctool", f"-L{home}/lib", f"-Wl,-rpath,{home}/lib", "-lSCTool",
                                  "-lSysCRTTI", "-lsc_elab_proto", "-lsystemc", "-lpthread"],
                                 cwd=d, capture_output=True, text=True, timeout=timeout_s, env=env)
            if run.returncode == 0:
                run = subprocess.run(["./sctool"], cwd=d, capture_output=True, text=True, timeout=timeout_s, env=env)
        except subprocess.TimeoutExpired:
            return "", f"ICSC took longer than {timeout_s:g}s"
        sv = out.read_text() if out.is_file() else ""
    if run.returncode != 0 or not sv.strip():
        text = (run.stdout + run.stderr).splitlines()
        errors = [ln for ln in text if "error" in ln.lower()][:8] or text[-8:]
        return "", "ICSC did not translate it:\n" + "\n".join(errors)
    head = re.search(rf"\bmodule\s+{re.escape(module)}\b[^(]*\((.*?)\);", sv, re.S)
    if head is None:
        return "", f"ICSC emitted no module `{module}`"
    got = {m[3]: ("in" if m[0] == "input" else "out", int(m[2]) + 1 if m[2] else 1)
           for m in re.findall(r"\b(input|output)\s+logic\s*(\[(\d+):0\])?\s*(\w+)", head[1])}
    want = {p["name"]: (p["dir"], int(p["bits"])) for p in g.ports}
    if got != want:
        return "", f"ICSC's ports {got} are not the golden PORTS {want}"
    # ICSC relies on SV's width rules (`res | 64'(8'd1 <<< i)`), which Verilator's lint would
    # refuse as WIDTH errors; the semantics are SV's and the gate proves every vector
    return "/* verilator lint_off WIDTH */\n" + sv, ""

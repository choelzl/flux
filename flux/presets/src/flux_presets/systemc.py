"""A SystemC design as SystemVerilog (D973): ICSC translates it, so every RTL preset can judge it.

    fluxpreset systemc DESIGN.h --top adder --golden golden.py                 # a check: "0 failing", exit 3 if not
    fluxpreset systemc DESIGN.h --top adder --golden golden.py --then "golden --golden golden.py"
    fluxpreset systemc DESIGN.h --top adder --golden golden.py --then "rtl measure --stage synth --clock-ps 1000"

DESIGN.h holds `SC_MODULE(top)` with one port per golden PORT (names, directions, widths: the
golden model says how to bind them). Alone, it is a check: the design translates (exit 0) or not
(exit 3, ICSC's errors). With `--then`, the translated SystemVerilog is handed to that preset as its
design and its output and exit code are the result. Needs ICSC (the Flux dev shell has it).
"""

from __future__ import annotations

import argparse
import importlib
import shlex
import tempfile
from pathlib import Path


def catalog() -> list[dict]:
    """This preset's catalog entries (D973)."""
    from ._entries import param
    golden = param("Golden model (its PORTS)", "{home}/golden.py")
    return [{"id": "systemc-translate", "role": "check", "title": "SystemC translates to RTL (ICSC)",
             "what": "ICSC translates the SystemC module to SystemVerilog: passes when it does, and when the ports are the "
                     "golden model's.",
             "run": "fluxpreset systemc {artifact} --top {top} --golden {golden}",
             "params": {"top": param("Top module", "top"), "golden": golden}, "needs": [],
             "pass": "passes when it prints `0 failing`; exit 3 = did not translate", "languages": ["cpp", "systemc"],
             "kinds": ["rtl"], "short": "translate"},
            {"id": "systemc-golden", "role": "check", "title": "SystemC against a golden model",
             "what": "ICSC translates the SystemC module, then Verilator runs the RTL on golden.py's vectors.",
             "run": "fluxpreset systemc {artifact} --top {top} --golden {golden} --then \"golden --golden {golden}\"",
             "params": {"top": param("Top module", "top"), "golden": golden}, "needs": ["verilator"],
             "pass": "passes when it prints `0 failing`; exit 3 = did not translate", "languages": ["cpp", "systemc"],
             "kinds": ["rtl"], "short": "golden"}]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fluxpreset systemc", description=__doc__.split("\n\n")[0])
    ap.add_argument("artifact")
    ap.add_argument("--top", required=True, help="the SC_MODULE to translate")
    ap.add_argument("--golden", required=True, help="golden.py: its PORTS bind the module")
    ap.add_argument("--then", default="", help='a preset and its options, run on the translated RTL ("rtl measure --stage synth")')
    ap.add_argument("--timeout", type=float, default=600.0)
    a = ap.parse_args(argv)
    from flux_loop.golden_model import load_golden
    from flux_loop.systemc_proto import translate

    sv, why = translate(Path(a.artifact).read_text(), a.top, load_golden(a.golden), timeout_s=a.timeout)
    if not sv:
        print(f"did not translate: {why}")
        print("1 failing")
        return 3
    if not a.then.strip():
        print("0 failing")
        return 0
    then = shlex.split(a.then)
    with tempfile.TemporaryDirectory(prefix="flux-systemc-") as d:
        rtl = Path(d) / f"{a.top}.sv"
        rtl.write_text(sv)
        name, rest = then[0], then[1:]
        cut = 1 if rest and not rest[0].startswith("-") else 0     # `rtl measure ...`: the design after its subcommand
        return int(importlib.import_module(f"flux_presets.{name}").main([*rest[:cut], str(rtl), *rest[cut:]]) or 0)


if __name__ == "__main__":
    raise SystemExit(main())

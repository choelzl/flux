"""`flux rtl lint|test|proto|measure` (D579, D582, D653): the RTL tools as commands a document can name,
so an RTL problem needs only a document and a Python golden model, no world package.

    flow:
      test:
        lint: flux rtl lint {artifact}
        golden: flux rtl test {artifact} --golden {home}/golden.py
      measure:
        screen: flux rtl measure {artifact} --stage synth --clock-ps 1000
        confirm: flux rtl measure {artifact} --stage place --clock-ps 1000

The golden model (`golden.py`) declares `PORTS` -- `[{name, dir, bits}, ...]`, ints, signed
unless `unsigned: true` -- and `golden(**inputs) -> {output: value}`; optionally `VECTORS`,
`COUNT` (random vectors, default 32), `SEED`, `CLOCK` (a clocked design), `LATENCY` (its
cycles, checked) and `TOLERANCE_ULP` ({port: n}: allowed ULP error on a float output). The
commands wrap `flux_codegen_rtl_harness.check_rtl` and `flux_evaluator_openroad.measure_rtl`.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
from pathlib import Path
from typing import Any

__all__ = ["cmd_rtl_lint", "cmd_rtl_measure", "cmd_rtl_proto", "cmd_rtl_test", "load_golden"]


def load_golden(path: str | Path) -> Any:
    """A `golden.py` as the harness's `Golden`."""
    from flux_codegen_rtl_harness import Golden

    p = Path(path)
    if not p.is_file():
        raise SystemExit(f"golden model {p} is not a file")
    spec = importlib.util.spec_from_file_location(f"golden_{abs(hash(str(p)))}", p)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    try:
        return Golden.from_module(mod)
    except ValueError as exc:
        raise SystemExit(f"{p}: {exc}") from exc


def _module_name(source: str, given: str | None) -> str:
    if given:
        return given
    m = re.search(r"^\s*module\s+([A-Za-z_]\w*)", source, re.M)
    if not m:
        raise SystemExit("no `module <name>` in the artifact; say --module")
    return m.group(1)


def cmd_rtl_test(args: argparse.Namespace) -> int:
    from flux_codegen_rtl_harness import check_rtl

    source = Path(args.artifact).read_text()
    extra = {Path(f).stem: Path(f).read_text() for f in (args.extra or [])}
    got = check_rtl(source, load_golden(args.golden), module=_module_name(source, args.module),
                    extra_sources=extra or None, timeout_s=float(args.timeout), show=int(args.show))
    if got.error:
        print(got.error)
    for ln in got.lines:
        print(ln)
    if got.latency is not None:
        print(f"latency={got.latency}")
    print(f"{got.failing if not got.error else got.total} failing of {got.total}")
    # exit 3: did not compile (a build failure to the gate); 1: compiled and failed (D594)
    return 0 if got.ok else 3 if got.error and got.error.startswith(("did not compile", "the module has", "no `module")) else 1


def cmd_rtl_lint(args: argparse.Namespace) -> int:
    """Verilator lint, the hardware defects only (D653): each printed, then `N failing`; exit 3
    when the source does not parse."""
    from flux_codegen_rtl_harness import lint_rtl

    source = Path(args.artifact).read_text()
    extra = {Path(f).stem: Path(f).read_text() for f in (args.extra or [])}
    try:
        module = _module_name(source, args.module)
    except SystemExit as exc:
        print(f"did not parse: {exc}\n1 failing")
        return 3
    got = lint_rtl(source, module, extra_sources=extra or None, timeout_s=float(args.timeout))
    if got.error:
        print(f"did not parse: {got.error}\n1 failing")
        return 3
    for ln in got.defects:
        print(ln)
    print(f"{len(got.defects)} failing")
    return 0 if got.ok else 1


def cmd_rtl_proto(args: argparse.Namespace) -> int:
    """The prototype stage's own check (D618), as a command a coding agent can run."""
    from flux_loop.golden_proto import TABLE_MAX, check, exhaustive, golden_vectors_of

    g = load_golden(args.golden)
    rows = exhaustive(g) or golden_vectors_of(g)
    v = check(Path(args.prototype).read_text(), g, rows, timeout_s=float(args.timeout),
              table_max=int(args.table_max or TABLE_MAX))
    print(v.why)
    print(f"{0 if v.ok else int(v.score)} failing of {len(rows)}")
    return 0 if v.ok else 1


def cmd_rtl_measure(args: argparse.Namespace) -> int:
    from flux_evaluator_openroad import measure_rtl

    source = Path(args.artifact).read_text()
    got = measure_rtl(source, _module_name(source, args.module), stage=args.stage,
                      clock_period_ps=float(args.clock_ps), clock_port=None if args.clock_port == "none" else args.clock_port,
                      reset_port=None if args.reset_port == "none" else args.reset_port, repair_design=bool(args.repair_design),
                      timeout_s=float(args.timeout))
    first = ("fmax_mhz", "area_um2", "power_w", "cell_count", "path_ps")     # the headline, then the rest
    nums = {k: got[k] for k in (*first, *sorted(got)) if isinstance(got.get(k), (int, float))}
    print(" ".join(f"{k}={v:.6g}" if isinstance(v, float) else f"{k}={v}" for k, v in nums.items())
          + f" stage={args.stage} flow_depth={got['flow_depth']}")
    if got.get("critical_path"):
        print("critical_path=" + got["critical_path"])
    return 0

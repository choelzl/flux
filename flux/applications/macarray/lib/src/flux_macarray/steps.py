"""The PE study's phases as commands (D798): what `applications/macarray/problem.yaml` and
`invent.problem.yaml` run in their boxes, so the study is a document and these scripts.

    python -m flux_macarray.steps gen ARTIFACT MULTIPLIER REDUCER PIPELINE   # the PE's RTL
    python -m flux_macarray.steps check ARTIFACT PIPELINE                   # Verilator vs golden vectors
    python -m flux_macarray.steps mult-check ARTIFACT [--keep DIR]          # an invented multiplier
    python -m flux_macarray.steps mult-screen ARTIFACT [--clock-ps P]       # it, in the standard PE

Each takes `--lanes N` and `--workload FILE` (the precision; default the GEMM example), and the
PE commands `--invented DIR`: the multipliers the invention problem kept, by file name.
A check prints `N failing` (exit 1 when N > 0, 3 when it does not build); a screen prints
`name=value` metrics.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

from .config import PeConfig, Shape
from .verify import DEFAULT_WORKLOAD, golden_vectors, pe_golden, shape_from_workload

#: The module an invented multiplier is written as; a kept one is renamed by its content.
INVENTED_MODULE = "mult_inv"


def _shape(args: argparse.Namespace) -> Shape:
    from flux_ir import load_document

    workload = load_document(args.workload or DEFAULT_WORKLOAD)
    return shape_from_workload(workload, args.lanes, accumulate=not args.no_accumulate)


def _invented(folder: str | None) -> dict[str, str]:
    """name -> source of every kept multiplier in `folder` (its file names are its modules)."""
    d = Path(folder) if folder else None
    return {f.stem: f.read_text() for f in sorted(d.glob("*.sv"))} if d is not None and d.is_dir() else {}


def _vectors(shape: Shape, args: argparse.Namespace) -> list:
    return golden_vectors(shape, seed=f"{Path(args.workload or DEFAULT_WORKLOAD).stem}:{shape.describe()}:{args.seed}")


def gen(args: argparse.Namespace) -> int:
    from .rtl import generate

    shape = _shape(args)
    design = generate(PeConfig(args.multiplier, args.reducer, int(args.pipeline)), shape, invented=_invented(args.invented))
    Path(args.artifact).write_text(design.all_sources)
    return 0


def check(args: argparse.Namespace) -> int:
    from flux_codegen_rtl_harness import check_rtl

    shape = _shape(args)
    cfg = PeConfig("behavioral", "tree", int(args.pipeline))          # the golden needs only the latency
    got = check_rtl(Path(args.artifact).read_text(), pe_golden(shape, cfg, _vectors(shape, args)), module="mac_pe")
    if got.ok:
        print("0 failing")
        return 0
    print(got.why)
    print("1 failing")
    return 3 if "compile" in (got.why or "").lower() else 1


def _renamed(source: str) -> tuple[str, str]:
    """A kept multiplier's name and source: `mult_inv` renamed by its content, so two runs that
    invent the same module keep one, and kept modules never clash."""
    name = "inv_" + hashlib.sha256(source.encode()).hexdigest()[:10]
    return name, re.sub(rf"\bmodule\s+{INVENTED_MODULE}\b", f"module {name}", source, count=1)


def mult_check(args: argparse.Namespace) -> int:
    from flux_codegen_rtl_harness import check_rtl, lint_relaxed

    from .invent import multiplier_golden, refusal_reason

    source = Path(args.artifact).read_text()
    why = refusal_reason(source)
    if why:
        print(why)
        print("1 failing")
        return 3
    got = check_rtl(source, multiplier_golden(_shape(args)), module=INVENTED_MODULE, relaxed=True)
    if not got.ok:
        print(got.why)
        print("1 failing")
        return 1
    if args.keep:                                    # offered to the PE study's space
        name, kept = _renamed(lint_relaxed(source))
        keep = Path(args.keep)
        keep.mkdir(parents=True, exist_ok=True)
        (keep / f"{name}.sv").write_text(kept)
        print(f"kept as {name}: {keep / (name + '.sv')}")
    print("0 failing")
    return 0


def mult_screen(args: argparse.Namespace) -> int:
    from flux_evaluator_openroad import measure_rtl

    from .rtl import generate

    name, source = _renamed(Path(args.artifact).read_text())
    design = generate(PeConfig(name, "tree", 0), _shape(args), invented={name: source})
    got = measure_rtl(design.all_sources, design.module_name, stage="synth", clock_period_ps=args.clock_ps,
                      timeout_s=600.0)
    if not isinstance(got, dict) or "error" in got:
        print(f"screen failed: {(got or {}).get('error', 'no result')}", file=sys.stderr)
        return 1
    from .measure import pe_score

    score = pe_score(got, 0)
    print(f"fmax_mhz={score.fmax_mhz:.1f} area_um2={score.area_um2:.2f} power_w={score.power_w:.6g} "
          f"cell_count={score.cell_count} path_ps={score.path_ps:.1f}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m flux_macarray.steps", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--lanes", type=int, default=8)
        sp.add_argument("--workload", default=None)
        sp.add_argument("--no-accumulate", action="store_true")
        sp.add_argument("--seed", type=int, default=0)
        sp.add_argument("--invented", default=None, help="the folder of kept multipliers")

    g = sub.add_parser("gen")
    g.add_argument("artifact"); g.add_argument("multiplier"); g.add_argument("reducer"); g.add_argument("pipeline")
    common(g)
    g.set_defaults(func=gen)
    c = sub.add_parser("check")
    c.add_argument("artifact"); c.add_argument("pipeline")
    common(c)
    c.set_defaults(func=check)
    mc = sub.add_parser("mult-check")
    mc.add_argument("artifact"); mc.add_argument("--keep", default=None)
    common(mc)
    mc.set_defaults(func=mult_check)
    ms = sub.add_parser("mult-screen")
    ms.add_argument("artifact"); ms.add_argument("--clock-ps", type=float, default=1000.0)
    common(ms)
    ms.set_defaults(func=mult_screen)
    args = p.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())

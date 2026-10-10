"""App-local ZigZag/Timeloop evaluation, printing metric=value lines for a loop stage.
`python evaluate.py ARCH WORKLOAD [--backend zigzag|timeloop]`; measure.py adds an area estimate."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))


def evaluate(arch: dict, workload: dict, backend: str = "zigzag") -> dict[str, float]:
    """Cycles and energy of `workload` on `arch`; SystemExit with the reasons when the backend
    finds the design not valid (D897: an invalid mapping measured nothing)."""
    from flux_evaluator_abi import Budget, Candidate

    if backend == "zigzag":
        from zigzag_tools import ZigZagEvaluator as Evaluator
    else:
        from timeloop_tools import TimeloopEvaluator as Evaluator
    result = Evaluator().evaluate(Candidate(arch=arch, workload=workload), Budget(),
                                  frozenset({"latency_cycles", "energy_pj"}))
    if not result.validity.ok:
        raise SystemExit("not valid: " + "; ".join(f"{v.kind} {v.detail}".strip() for v in result.validity.violations))
    return {name: value.value for name, value in result.metrics.items()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", help="Architecture IR YAML")
    parser.add_argument("workload", help="Workload IR YAML")
    parser.add_argument("--backend", choices=("zigzag", "timeloop"), default="zigzag")
    args = parser.parse_args(argv)
    got = evaluate(yaml.safe_load(Path(args.artifact).read_text()), yaml.safe_load(Path(args.workload).read_text()),
                   args.backend)
    for name, value in got.items():
        print(f"{name}={value:g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

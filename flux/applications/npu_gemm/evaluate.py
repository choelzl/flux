"""App-local ZigZag/Timeloop evaluation, printing metric=value lines for a loop stage."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", help="Architecture IR YAML")
    parser.add_argument("workload", help="Workload IR YAML")
    parser.add_argument("--backend", choices=("zigzag", "timeloop"), default="zigzag")
    args = parser.parse_args(argv)
    from flux_evaluator_abi import Budget, Candidate

    if args.backend == "zigzag":
        from zigzag_tools import ZigZagEvaluator

        evaluator = ZigZagEvaluator()
    else:
        from timeloop_tools import TimeloopEvaluator

        evaluator = TimeloopEvaluator()
    candidate = Candidate(arch=yaml.safe_load(Path(args.artifact).read_text()),
                          workload=yaml.safe_load(Path(args.workload).read_text()))
    result = evaluator.evaluate(candidate, Budget(), frozenset({"latency_cycles", "energy_pj"}))
    if not result.validity.ok:
        print("not valid: " + "; ".join(v.detail for v in result.validity.violations), file=sys.stderr)
        return 1
    for name, value in result.metrics.items():
        print(f"{name}={value.value:g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

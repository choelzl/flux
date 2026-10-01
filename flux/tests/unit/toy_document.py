"""A toy worldless document for the loop's generic behaviour: a table size, its ways, a stack
with a conditional partner knob, and a stage that is this file run as a script printing
deterministic metrics. Speedup rises with the table and saturates; bytes rise linearly, so the
speed/bytes frontier has a cheap end and a fast end. No tools, no simulator.

    python toy_document.py table=256 ways=4 stack=a,b degree=2      # speedup=... bytes=...

`stack=crash` exits non-zero, a design the stage cannot measure. With `$FLUX_TOY_LOG` set, every
run appends its arguments there, so a test can count what was really measured.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve()
SPACE = {"table": [4096, 2048, 1024, 512, 256, 128, 64, 32, 16], "ways": [16, 8, 4, 2, 1],
         "stack": ["a", "a,b", "a,c"], "degree": {"values": [2, 1, 4, 8], "when": {"stack": ["a,b"]}}}
SEED = {"table": 256, "ways": 4, "stack": "a", "degree": 2}
STAGE = ["{python}", str(HERE), "table={table}", "ways={ways}", "stack={stack}", "degree={degree}"]


def landscape(k: dict[str, Any]) -> dict[str, float]:
    """The metrics of one point: what the stage prints, computed here for the tests to compare."""
    table, ways, degree = int(k["table"]), int(k["ways"]), int(k["degree"])
    partner = {"a": 0.0, "a,b": 0.01 + 0.001 * degree, "a,c": 0.004}[k["stack"]]
    speedup = 1.0 + 0.06 * (min(table, 1024) / 1024) ** 0.5 + 0.005 * ways / 16 + partner
    return {"speedup": round(speedup, 6), "bytes": float(table * ways * 8 + (512 if k["stack"] != "a" else 0))}


def toy(**kw: Any) -> dict[str, Any]:
    """The document, with sections replaced by `kw`."""
    doc = {"id": "toy", "statement": "a table, its ways and a partner", "gate": {"test": ["true"]},
           "space": dict(SPACE), "seeds": [dict(SEED)],
           "stages": [{"name": "screen", "command": STAGE, "metrics": ["speedup", "bytes"]}],
           "objectives": [{"metric": "speedup", "direction": "maximize"}, {"metric": "bytes", "direction": "minimize"}],
           "budget": {"steps": 40, "finalists": 0, "prototype": False}}
    doc.update(kw)
    return doc


def run_toy(doc: dict[str, Any], db: str = "", **request: Any):
    """(problem, LoopResult) of one pass over `doc` with no model."""
    from flux_loop import LoopRequest, PromptProblem, TaskSpec, run_loop

    prob = PromptProblem(TaskSpec.from_dict(doc))
    # D738: the whole search in one pass (a pass carries one design by default)
    req = {"db": db, "steps": 40, "finalists": 0, "screen_only": True, "prototype": False, "batch": 10_000, **request}
    return prob, run_loop(prob, LoopRequest(**req), log=lambda _m: None)


if __name__ == "__main__":
    knobs = dict(a.split("=", 1) for a in sys.argv[1:])
    if os.environ.get("FLUX_TOY_LOG"):
        with open(os.environ["FLUX_TOY_LOG"], "a") as f:
            f.write(" ".join(sys.argv[1:]) + "\n")
    if knobs["stack"] == "crash":
        sys.exit("the stack crashed")
    for name, value in landscape(knobs).items():
        print(f"{name}={value}")

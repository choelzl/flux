"""The golden model of the NLU's `sigmoid` (D802): 1/(1+exp(-x)) in double precision, rounded to FP16
once -- what the design must compute, and nothing about how."""

import math

import numpy as np

PORTS = [
    {"name": "x", "dir": "in", "bits": 16, "unsigned": True},
    {"name": "y", "dir": "out", "bits": 16, "unsigned": True},
]
SEED = 1
COUNT = 1000
TOLERANCE_ULP = {"y": 1}


def golden(x: int) -> dict:
    v = float(np.uint16(x).view(np.float16))
    if math.isnan(v):
        return {"y": 0x7E00}
    try:
        y = 1.0 / (1.0 + math.exp(-v)) if v > -745 else 0.0
    except OverflowError:
        y = math.inf
    with np.errstate(all="ignore"):
        return {"y": int(np.float16(y).view(np.uint16))}

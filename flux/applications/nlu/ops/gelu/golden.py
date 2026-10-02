"""The golden model of the NLU's `gelu` (D802): 0.5*x*(1+erf(x/sqrt(2))) in double precision, rounded to FP16
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
        y = -0.0 if v == -math.inf else 0.5 * v * (1.0 + math.erf(v / math.sqrt(2.0)))
    except OverflowError:
        y = math.inf
    with np.errstate(all="ignore"):
        return {"y": int(np.float16(y).view(np.uint16))}

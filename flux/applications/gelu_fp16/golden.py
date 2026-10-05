"""The golden model of `gelu_fp16`: what the design must compute, and nothing about how."""

import numpy as np
import math

PORTS = [
    {"name": "x", "dir": "in", "bits": 16, "unsigned": True},
    {"name": "y", "dir": "out", "bits": 16, "unsigned": True},
]
SEED = 1
COUNT = 1000
CLOCK = True       # pipelined: `done` rises LATENCY cycles after `start` (D864)
LATENCY = 2
TOLERANCE_ULP = {"y": 1}


def gelu_tanh(x_float: float) -> float:
    """Compute GELU using the tanh approximation in full precision."""
    return 0.5 * x_float * (1.0 + math.tanh(math.sqrt(2.0 / math.pi) * (x_float + 0.044715 * x_float ** 3)))


def golden(x: int) -> dict:
    """Interpret x as FP16, compute tanh-approx GELU, return as FP16 bits."""
    x_val = float(np.uint16(x).view(np.float16))
    
    # Special cases explicitly, to match the contract and IEEE behaviour
    if math.isinf(x_val):
        if x_val > 0:
            return {"y": int(np.float16(np.inf).view(np.uint16))}  # +inf
        else:
            return {"y": int(np.float16(-0.0).view(np.uint16))}  # -0.0 (0x8000)
    
    if math.isnan(x_val):
        return {"y": x}  # Pass through NaN bits
    
    y_val = gelu_tanh(x_val)
    
    y_fp16 = np.float16(y_val)
    
    return {"y": int(y_fp16.view(np.uint16))}

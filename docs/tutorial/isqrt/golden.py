"""The golden model of isqrt: what the module must compute, and nothing about how."""

import math

PORTS = [
    {"name": "x", "dir": "in", "bits": 16, "unsigned": True},
    {"name": "r", "dir": "out", "bits": 8, "unsigned": True},
]
COUNT = 200


def golden(x: int) -> dict:
    return {"r": math.isqrt(x)}

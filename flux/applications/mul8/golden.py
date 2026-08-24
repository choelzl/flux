"""The golden model of `mul8` (D579): what the RTL must compute, and nothing about how."""

PORTS = [
    {"name": "a", "dir": "in", "bits": 8},
    {"name": "w", "dir": "in", "bits": 8},
    {"name": "p", "dir": "out", "bits": 16},
]
SEED = 1
COUNT = 24


def golden(a: int, w: int) -> dict:
    """signed int8 x int8 -> int16"""
    return {"p": a * w}

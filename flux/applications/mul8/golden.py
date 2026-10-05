"""The golden model of `mul8` (D579): what the RTL must compute, and nothing about how."""

PORTS = [
    {"name": "a", "dir": "in", "bits": 8},
    {"name": "w", "dir": "in", "bits": 8},
    {"name": "p", "dir": "out", "bits": 16},
]
# D865: all 65,536 input pairs; 49 sampled ones passed a design wrong on 1,024 of them
EXHAUSTIVE = True


def golden(a: int, w: int) -> dict:
    """signed int8 x int8 -> int16"""
    return {"p": a * w}

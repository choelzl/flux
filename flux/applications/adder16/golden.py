"""The golden model of `adder16` (D581): what the RTL must compute, and nothing about how."""

PORTS = [
    {"name": "a", "dir": "in", "bits": 16, "unsigned": True},
    {"name": "b", "dir": "in", "bits": 16, "unsigned": True},
    {"name": "s", "dir": "out", "bits": 17, "unsigned": True},
]
SEED = 1
COUNT = 32


def golden(a: int, b: int) -> dict:
    """unsigned 16 + 16 -> 17 bits"""
    return {"s": a + b}

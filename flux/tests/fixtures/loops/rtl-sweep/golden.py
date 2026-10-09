"""The golden model of __NAME__: what the module must compute, and nothing about how."""

PORTS = [
    {"name": "a", "dir": "in", "bits": 16, "unsigned": True},
    {"name": "y", "dir": "out", "bits": 5, "unsigned": True},
]
COUNT = 200


def golden(a: int) -> dict:
    return {"y": bin(a).count("1")}

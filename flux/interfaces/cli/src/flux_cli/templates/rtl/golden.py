"""The golden model of __NAME__: what the module must compute, and nothing about how.
`flux rtl test` drives these ports with the corners of every input, pairwise, and COUNT
random vectors, and compares every output with golden()."""

PORTS = [
    {"name": "a", "dir": "in", "bits": 8, "unsigned": True},
    {"name": "b", "dir": "in", "bits": 8, "unsigned": True},
    {"name": "s", "dir": "out", "bits": 9, "unsigned": True},
]
COUNT = 200


def golden(a: int, b: int) -> dict:
    return {"s": a + b}

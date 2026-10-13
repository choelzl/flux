"""A stand-in for pto-spec's test loader in Flux's tests: a point per requested id."""
from pathlib import Path


def load_focused_test_points(root: Path, test_ids):
    return tuple(test_ids)


def matrix(points):
    return [{"id": p} for p in points]

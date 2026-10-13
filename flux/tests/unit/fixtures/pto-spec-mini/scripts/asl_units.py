"""A stand-in for pto-spec's source order in Flux's tests: every ASL file, sorted."""
from pathlib import Path


def generate_source_order(root: Path) -> tuple[str, ...]:
    return tuple(str(p.relative_to(root)) for p in sorted((root / "asl").rglob("*.asl")))

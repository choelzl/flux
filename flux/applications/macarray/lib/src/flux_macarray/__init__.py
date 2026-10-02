"""MAC processing-element microarchitecture study (D365, D798): the pieces the commands of
`flux_macarray.steps` -- the phases of `applications/macarray/problem.yaml` -- are made of."""

from .config import DEFAULT, MULTIPLIERS, PIPELINES, REDUCERS, PeConfig, Shape
from .objective import Score, Scored, decide, frontier, gmacs_per_mm2, spread
from .rtl import Design, generate
from .verify import DEFAULT_WORKLOAD, golden_vectors, pe_golden, shape_from_workload, verify

__all__ = [
    "DEFAULT", "DEFAULT_WORKLOAD", "Design", "MULTIPLIERS", "PIPELINES", "PeConfig", "REDUCERS",
    "Score", "Scored", "Shape", "decide", "frontier", "generate", "gmacs_per_mm2", "golden_vectors",
    "pe_golden", "shape_from_workload", "spread", "verify",
]

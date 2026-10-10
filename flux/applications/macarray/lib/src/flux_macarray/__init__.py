"""MAC processing-element microarchitecture study (D365, D798): the pieces the commands of
`flux_macarray.steps` -- the phases of `applications/macarray/problem.yaml` -- are made of."""

from .config import MULTIPLIERS, REDUCERS, PeConfig, Shape
from .objective import Score
from .rtl import Design, generate
from .verify import DEFAULT_WORKLOAD, golden_vectors, pe_golden, shape_from_workload

__all__ = [
    "DEFAULT_WORKLOAD", "Design", "MULTIPLIERS", "PeConfig", "REDUCERS", "Score", "Shape", "generate",
    "golden_vectors", "pe_golden", "shape_from_workload",
]

"""MAC processing-element microarchitecture study (docs/decisions.md D365, D533): the world
`applications/macarray/problem.yaml` names, and the pieces it is made of."""

from .config import DEFAULT, MULTIPLIERS, PIPELINES, REDUCERS, PeConfig, Shape
from .invent import INVENTED_DIR, Invention, library
from .objective import Score, Scored, decide, frontier, gmacs_per_mm2, spread
from .rtl import Design, generate
from .verify import DEFAULT_WORKLOAD, golden_vectors, pe_golden, shape_from_workload, verify
from .world import MacRequest, World

__all__ = [
    "DEFAULT", "DEFAULT_WORKLOAD", "Design", "INVENTED_DIR", "Invention", "MULTIPLIERS",
    "MacRequest", "PIPELINES", "PeConfig", "REDUCERS", "Score", "Scored", "Shape",
    "World", "decide", "frontier", "generate", "gmacs_per_mm2", "golden_vectors", "library",
    "pe_golden", "shape_from_workload", "spread", "verify",
]

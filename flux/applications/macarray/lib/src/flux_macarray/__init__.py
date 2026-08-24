"""MAC processing-element microarchitecture study (docs/decisions.md D365)."""

from .config import (DEFAULT, MAPPINGS, MULTIPLIERS, PIPELINES, REDUCERS, InvalidConfig, PeConfig, Shape,
                     is_valid, space, validate)
from .flow import MacRequest, MacResult, run_study
from .invent import INVENTED_DIR, Invention, library
from .objective import Score, Scored, decide, frontier, gmacs_per_mm2, spread
from .rtl import Design, generate
from .verify import DEFAULT_WORKLOAD, Verdict, golden_vectors, pe_spec, shape_from_workload, verify

__all__ = [
    "DEFAULT", "DEFAULT_WORKLOAD", "Design", "INVENTED_DIR", "InvalidConfig", "Invention",
    "MAPPINGS", "MULTIPLIERS", "MacRequest", "MacResult", "PIPELINES", "PeConfig", "REDUCERS", "Score",
    "Scored", "Shape", "Verdict", "decide", "frontier", "generate", "gmacs_per_mm2",
    "golden_vectors", "is_valid", "library", "pe_spec", "run_study", "shape_from_workload",
    "space", "spread", "validate", "verify",
]

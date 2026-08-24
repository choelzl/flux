"""Noxim NoC evaluator (docs/decisions.md D32): real 2D-mesh NoC simulation, a second,
independent NoC simulator alongside `evaluators/booksim`, for conformance-checking purposes.
"""

from __future__ import annotations

from .adapter import NoximEvaluator
from .architecture_translator import architecture_ir_to_noxim_args, noxim_cli_args
from .errors import NotExpressibleError

__all__ = [
    "NoximEvaluator",
    "NotExpressibleError",
    "architecture_ir_to_noxim_args",
    "noxim_cli_args",
]

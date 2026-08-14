"""Booksim2 NoC evaluator (docs/decisions.md D5/D6): real 2D/3D k-ary n-cube NoC simulation. Also
real chiplet inter-die (D2D) interconnect simulation via Booksim2's own `anynet` topology
(docs/decisions.md D66)."""

from __future__ import annotations

from .adapter import BooksimEvaluator
from .architecture_translator import (
    ChipletTopology,
    architecture_ir_to_booksim_config,
    architecture_ir_to_chiplet_anynet,
    dump_booksim_config,
)
from .errors import NotExpressibleError

__all__ = [
    "BooksimEvaluator",
    "ChipletTopology",
    "NotExpressibleError",
    "architecture_ir_to_booksim_config",
    "architecture_ir_to_chiplet_anynet",
    "dump_booksim_config",
]

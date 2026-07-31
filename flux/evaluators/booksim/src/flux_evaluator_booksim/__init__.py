"""Booksim2 NoC evaluator (docs/00-decisions.md D5/D6): real 2D/3D k-ary n-cube NoC simulation."""

from __future__ import annotations

from .adapter import BooksimEvaluator
from .architecture_translator import architecture_ir_to_booksim_config, dump_booksim_config
from .errors import NotExpressibleError

__all__ = [
    "BooksimEvaluator",
    "NotExpressibleError",
    "architecture_ir_to_booksim_config",
    "dump_booksim_config",
]

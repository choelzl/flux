"""Bingo L2 prefetcher configuration space, storage model, and search (docs/decisions.md D349)."""

from .config import (
    DEFAULT, KNOBS, BingoConfig, InvalidConfig, invalid_reason, is_valid,
    render_ini, storage_bits, storage_bytes, validate,
)

__all__ = [
    "DEFAULT", "KNOBS", "BingoConfig", "InvalidConfig", "invalid_reason", "is_valid",
    "render_ini", "storage_bits", "storage_bytes", "validate",
]

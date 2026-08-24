"""What the campaign's own record says each knob is worth (D369).

The pairwise arithmetic moved to `flux_extract` (mentor/extract, D397) because it was
never prefetcher-specific; this module is the Bingo adapter -- it knows which knobs a
config exposes and which two move together -- and keeps the original public API so
D369's tests keep passing unchanged, which is the proof the generalisation lost
nothing.
"""

from __future__ import annotations

from flux_extract import Law as KnobInsight  # noqa: F401  (public API preserved)
from flux_extract import laws_text, pairwise_laws

from .config import BingoConfig


def _knob_values(cfg: BingoConfig) -> dict[str, float]:
    return {**{k: float(v) for k, v in cfg.knobs().items()},
            "bingo_l2c_thresh": cfg.l2c_thresh}


def pairwise_insights(known: list[tuple[BingoConfig, float]], *, min_pairs: int = 2,
                      top: int = 6) -> list[KnobInsight]:
    """Knob-direction effects from every measured one-knob pair (see flux_extract)."""
    rows = [(_knob_values(cfg), g) for cfg, g in known]
    return pairwise_laws(
        rows, min_pairs=min_pairs, top=top, metric="geomean",
        coupled=[frozenset({"bingo_region_size", "bingo_pattern_len"})])


def insights_text(insights: list[KnobInsight]) -> str:
    """The prompt block. Directions and magnitudes, never prescriptions."""
    text = laws_text(insights)
    return text.replace(
        "from earlier measurements;",
        "from earlier measurements of THESE traces;") if text else ""


__all__ = ["KnobInsight", "insights_text", "pairwise_insights"]

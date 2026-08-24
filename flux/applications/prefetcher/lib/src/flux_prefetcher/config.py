"""The Bingo L2 prefetcher's configuration space: what is legal, and what it costs in hardware.

This is the ONE definition of both, and it is load-bearing in a way a scoring script is not.
`prefetcher/bingo.cc` opens with

    assert((knob::bingo_region_size >> LOG2_BLOCK_SIZE) == knob::bingo_pattern_len);

so an illegal configuration does not score badly — it ABORTS the simulator six minutes into a run
that was going to cost six minutes. Every invariant below is enforced before a candidate is ever
handed to ChampSim, which is why the screen is not an optimisation here but a correctness gate.

The storage model follows the per-table comments in `inc/bingo.h`, and it is the stage-2 objective:

    Filter Table        entry = [region tag, offset, PC, valid, LRU]
    Accumulation Table  entry = [region tag, footprint map, offset, PC, valid, LRU]
    Pattern Hist. Table entry = [PC+addr tag, footprint map, valid, LRU]
    Prefetch Streamer   entry = [region tag, fill-level map (2 bit/block), valid, LRU]

Every table is set-associative: tag = key_width - lg(sets), LRU = lg(ways).

Ported from the project's own `score_memory.py`, whose numbers this reproduces exactly
(`tests/unit/test_bingo_storage_model.py` checks that against the shipped script's arithmetic
rather than against a number someone copied across).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

# ---- Fixed hardware assumptions (from inc/bingo.h) ---------------------------
ADDR_BITS_REGION = 48     # region tag key: 48-bit physical address (FT/AT "37" = 48 - lg(2048))
ADDR_BITS_STREAMER = 64   # streamer comment uses a 64-bit key ("53" = 64 - lg(2048))
FILL_LEVEL_BITS = 2       # streamer stores a prefetch fill level per block ("64" = 2 * 32)
FIXED_WAYS = 16           # FT, AT and streamer are hard-coded 16-way in bingo.h
VALID_BITS = 1
BLOCK_SIZE = 64           # 64-byte cache lines (LOG2_BLOCK_SIZE = 6)
PAGE_SIZE = 4096          # a spatial region cannot exceed a page (prefetches never cross pages)
MAX_WIDTH = 30            # bingo.h computes (1 << width) in a 32-bit int: wider is undefined
MAX_ENTRIES = 1 << 24     # sanity cap on table entries

#: knob -> (min, max). The ten INTEGER knobs; `bingo_l2c_thresh` is a float and is handled below.
KNOBS: dict[str, tuple[int, int]] = {
    "bingo_region_size":      (BLOCK_SIZE, PAGE_SIZE),
    "bingo_pattern_len":      (1, PAGE_SIZE // BLOCK_SIZE),
    "bingo_pc_width":         (0, MAX_WIDTH),
    "bingo_min_addr_width":   (0, MAX_WIDTH),
    "bingo_max_addr_width":   (0, MAX_WIDTH),
    "bingo_ft_size":          (1, MAX_ENTRIES),
    "bingo_at_size":          (1, MAX_ENTRIES),
    "bingo_pht_size":         (1, MAX_ENTRIES),
    "bingo_pht_ways":         (1, MAX_ENTRIES),
    "bingo_pf_streamer_size": (1, MAX_ENTRIES),
}

#: `bingo_l2c_thresh` gates how eagerly the L2 issues a prefetch. It is the eleventh tunable knob
#: the project names, it is a float, and it costs NO storage — so it is free in stage 2, which is
#: worth knowing when a search is trying to buy speedup back under an area cap.
L2C_THRESH_RANGE = (0.0, 1.0)


class InvalidConfig(ValueError):
    """A configuration the simulator would reject, assert on, or silently misread.

    Raised rather than returned: `score_memory.py` failed closed with an exit code because it was
    a command; here the same failure has to carry WHY, so a proposer can be told what it broke.
    """


@dataclass(frozen=True)
class BingoConfig:
    """One point in the Bingo configuration space.

    Field names are the simulator's own knob names minus the `bingo_` prefix, so `render_ini()`
    is a mechanical mapping and there is no second naming scheme to keep in step.
    """

    region_size: int = 2048
    pattern_len: int = 32
    pc_width: int = 16
    min_addr_width: int = 5
    max_addr_width: int = 16
    ft_size: int = 64
    at_size: int = 128
    pht_size: int = 4096
    pht_ways: int = 16
    pf_streamer_size: int = 128
    l2c_thresh: float = 0.80

    # ---- knob-name plumbing ---------------------------------------------------
    def knobs(self) -> dict[str, int]:
        """The ten integer knobs under their simulator names."""
        return {f"bingo_{f}": getattr(self, f) for f in _INT_FIELDS}

    def replace(self, **changes: Any) -> BingoConfig:
        return replace(self, **changes)

    @classmethod
    def from_knobs(cls, knobs: dict[str, Any]) -> "BingoConfig":
        """The inverse of `knobs()` (plus `bingo_l2c_thresh`): a recorded candidate, read back."""
        values: dict[str, Any] = {}
        for f in _INT_FIELDS:
            if f"bingo_{f}" in knobs:
                values[f] = int(knobs[f"bingo_{f}"])
        if "bingo_l2c_thresh" in knobs:
            values["l2c_thresh"] = float(knobs["bingo_l2c_thresh"])
        return cls(**values)


_INT_FIELDS = (
    "region_size", "pattern_len", "pc_width", "min_addr_width", "max_addr_width",
    "ft_size", "at_size", "pht_size", "pht_ways", "pf_streamer_size",
)

#: The configuration the project ships, and the point every speedup is quoted against.
DEFAULT = BingoConfig()


# ---- validity ----------------------------------------------------------------
def _require(condition: bool, why: str) -> None:
    if not condition:
        raise InvalidConfig(why)


def lg(n: int) -> int:
    """log2(n), for powers of two only.

    Not a convenience: `bingo.h` indexes tables by shifting, so a non-power-of-two size is not
    "slightly wrong", it is a different data structure than the one the storage model describes.
    """
    _require(n >= 1 and n & (n - 1) == 0, f"{n} is not a power of two")
    return n.bit_length() - 1


def table_bits(entries: int, ways: int, key_bits: int, payload_bits: int) -> int:
    """Storage of a set-associative table: entries * (tag + payload + valid + LRU)."""
    _require(1 <= ways <= entries, f"ways {ways} outside 1..{entries}")
    _require(entries % ways == 0, f"{entries} entries do not divide into {ways} ways")
    sets = entries // ways
    tag_bits = key_bits - lg(sets)
    _require(tag_bits >= 0, f"{sets} sets need more index bits than the {key_bits}-bit key has")
    return entries * (tag_bits + payload_bits + VALID_BITS + lg(ways))


def validate(cfg: BingoConfig) -> None:
    """Raise `InvalidConfig` unless ChampSim would accept and correctly run this configuration.

    Order matters for the message quality, not the verdict: ranges first (a proposer that invented
    a number gets told which one), then the simulator's own asserts, then the table geometry.
    """
    for name, value in cfg.knobs().items():
        lo, hi = KNOBS[name]
        _require(isinstance(value, int) and not isinstance(value, bool),
                 f"{name} must be an integer, got {value!r}")
        _require(lo <= value <= hi, f"{name}={value} outside {lo}..{hi}")
    lo, hi = L2C_THRESH_RANGE
    _require(lo <= cfg.l2c_thresh <= hi, f"bingo_l2c_thresh={cfg.l2c_thresh} outside {lo}..{hi}")

    # bingo.cc's own assert, reproduced here so it fires in microseconds instead of six minutes in.
    _require(cfg.region_size // BLOCK_SIZE == cfg.pattern_len,
             f"region_size {cfg.region_size} implies pattern_len "
             f"{cfg.region_size // BLOCK_SIZE}, not {cfg.pattern_len} "
             "(bingo.cc asserts this and ABORTS)")
    _require(cfg.max_addr_width >= cfg.min_addr_width,
             f"max_addr_width {cfg.max_addr_width} < min_addr_width {cfg.min_addr_width}")
    _require(cfg.pc_width + cfg.min_addr_width > 0,
             "pc_width + min_addr_width must exceed 0 (the PHT would have no key)")
    storage_bits(cfg)   # the table geometry itself, which can still fail


def is_valid(cfg: BingoConfig) -> bool:
    """`validate` as a predicate, for filtering a proposal batch without try/except at each site."""
    try:
        validate(cfg)
    except InvalidConfig:
        return False
    return True


def invalid_reason(cfg: BingoConfig) -> str | None:
    """Why this configuration is illegal, or None. What a proposer gets told after a bad guess."""
    try:
        validate(cfg)
    except InvalidConfig as exc:
        return str(exc)
    return None


# ---- the stage-2 objective ---------------------------------------------------
def storage_bits(cfg: BingoConfig) -> int:
    """Total hardware storage of the four Bingo tables, in bits."""
    offset_bits = lg(cfg.pattern_len)                       # block offset inside a region
    region_key = ADDR_BITS_REGION - lg(cfg.region_size)
    streamer_key = ADDR_BITS_STREAMER - lg(cfg.region_size)
    pht_key = cfg.pc_width + cfg.max_addr_width

    filter_table = table_bits(cfg.ft_size, FIXED_WAYS, region_key,
                              offset_bits + cfg.pc_width)
    accumulation_table = table_bits(cfg.at_size, FIXED_WAYS, region_key,
                                    cfg.pattern_len + offset_bits + cfg.pc_width)
    pattern_history_table = table_bits(cfg.pht_size, cfg.pht_ways, pht_key,
                                       cfg.pattern_len)
    prefetch_streamer = table_bits(cfg.pf_streamer_size, FIXED_WAYS, streamer_key,
                                   FILL_LEVEL_BITS * cfg.pattern_len)
    return filter_table + accumulation_table + pattern_history_table + prefetch_streamer


def storage_bytes(cfg: BingoConfig) -> int:
    """`storage_bits` rounded up, matching `score_memory.py`'s reported unit."""
    return (storage_bits(cfg) + 7) // 8


# ---- what ChampSim actually reads --------------------------------------------
#: Keys the shipped `bingo.ini` carries that this search does NOT vary. They are rendered anyway:
#: the simulator parses with `atoi()` and silently substitutes a default for a missing key, so a
#: partial file would run happily under settings nobody chose.
#:
#: WHY EACH IS FROZEN — three different reasons, and only one of them is "it did not help":
#:
#: `bingo_l1d_thresh` MUST STAY ABOVE 1.0. The three thresholds are a cascade (bingo.cc:323): a
#: pattern's confidence `p` fills into L1D above `l1d_thresh`, else L2 above `l2c_thresh`, else
#: LLC above `llc_thresh`. Confidence never exceeds 1.0, so 1.01 makes the L1D branch
#: unreachable — and that is load-bearing, not conservative. Lower it and an L2 prefetcher starts
#: issuing L1 fills, which this ChampSim fork cannot account for:
#:     [L1D_MSHR] return_data ... cannot find a matching entry!
#:     src/cache.cc:1627: CACHE::return_data(PACKET*): Assertion `0' failed
#: Measured on both the `no multi no 1` and `multi multi no 1` builds, so it is the simulator, not
#: the L1D prefetcher slot. The shipped 1.01 is a crash workaround wearing the clothes of a knob.
#:
#: `bingo_pc_address_fill_level` is "L2" for the same reason at one end ("L1" aborts) and because
#: "LLC" measured far worse at the other (geomean 1.0305 against 1.0746).
#:
#: `bingo_llc_thresh` is the only one frozen merely on evidence: 0.30 and 0.60 both ran fine and
#: both scored worse than the shipped 0.05, at one point in the space. It is a legitimate
#: candidate for searching, unlike the two above.
FIXED_INI = {
    "bingo_debug_level": "0",
    "bingo_l1d_thresh": "1.01",
    "bingo_llc_thresh": "0.05",
    "bingo_pc_address_fill_level": "L2",
}


def render_ini(cfg: BingoConfig) -> str:
    """The `.ini` text ChampSim is handed via `--config=`.

    Validated first, on purpose: rendering an illegal configuration produces a file that looks
    perfectly well-formed and aborts the simulator on read.
    """
    validate(cfg)
    lines = [f"{name} = {value}" for name, value in cfg.knobs().items()]
    lines.append(f"bingo_l2c_thresh = {cfg.l2c_thresh}")
    lines.extend(f"{k} = {v}" for k, v in sorted(FIXED_INI.items()))
    return "\n".join(lines) + "\n"

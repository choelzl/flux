"""A ChampSim `.ini` for the L2 prefetcher slot: check it, measure it. A knob the file leaves out
takes its shipped value (`bingo_default.ini`); `bingo_pattern_len` follows `bingo_region_size`.

    python bingo.py check ARTIFACT [--max-storage B]      # `0 failing` or `1 failing: <why>`
    python bingo.py measure ARTIFACT --traces DIR --warmup N --sim M

`bingo.cc` asserts `region_size / 64 == pattern_len` and aborts, so an illegal configuration is
refused here before the simulator runs. The storage model follows the per-table comments in
`inc/bingo.h` (filter, accumulation, pattern history, prefetch streamer); every table is
set-associative, tag = key - lg(sets), LRU = lg(ways).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

BLOCK_SIZE = 64           # LOG2_BLOCK_SIZE = 6
PAGE_SIZE = 4096          # a region never crosses a page
ADDR_BITS_REGION = 48     # filter/accumulation key: a 48-bit physical address
ADDR_BITS_STREAMER = 64   # the streamer's key is 64-bit
FILL_LEVEL_BITS = 2       # the streamer keeps a fill level per block
FIXED_WAYS = 16           # filter, accumulation and streamer tables are 16-way in bingo.h
MAX_WIDTH = 30            # bingo.h computes (1 << width) in a 32-bit int
MAX_ENTRIES = 1 << 24

#: knob -> (min, max): the integer knobs ChampSim reads.
RANGES = {
    "bingo_region_size": (BLOCK_SIZE, PAGE_SIZE), "bingo_pattern_len": (1, PAGE_SIZE // BLOCK_SIZE),
    "bingo_pc_width": (0, MAX_WIDTH), "bingo_min_addr_width": (0, MAX_WIDTH), "bingo_max_addr_width": (0, MAX_WIDTH),
    "bingo_ft_size": (1, MAX_ENTRIES), "bingo_at_size": (1, MAX_ENTRIES), "bingo_pht_size": (1, MAX_ENTRIES),
    "bingo_pht_ways": (1, MAX_ENTRIES), "bingo_pf_streamer_size": (1, MAX_ENTRIES),
}

class Invalid(ValueError):
    pass


def _require(ok: bool, why: str) -> None:
    if not ok:
        raise Invalid(why)


def lg(n: int) -> int:
    _require(n >= 1 and n & (n - 1) == 0, f"{n} is not a power of two")
    return n.bit_length() - 1


def table_bits(entries: int, ways: int, key_bits: int, payload_bits: int) -> int:
    _require(1 <= ways <= entries, f"ways {ways} outside 1..{entries}")
    _require(entries % ways == 0, f"{entries} entries do not divide into {ways} ways")
    tag = key_bits - lg(entries // ways)
    _require(tag >= 0, f"{entries // ways} sets need more index bits than the {key_bits}-bit key has")
    return entries * (tag + payload_bits + 1 + lg(ways))


def storage_bits(k: dict[str, int]) -> int:
    """The four Bingo tables, in bits."""
    region, pattern, pc = k["bingo_region_size"], k["bingo_pattern_len"], k["bingo_pc_width"]
    offset = lg(pattern)
    return (table_bits(k["bingo_ft_size"], FIXED_WAYS, ADDR_BITS_REGION - lg(region), offset + pc)
            + table_bits(k["bingo_at_size"], FIXED_WAYS, ADDR_BITS_REGION - lg(region), pattern + offset + pc)
            + table_bits(k["bingo_pht_size"], k["bingo_pht_ways"], pc + k["bingo_max_addr_width"], pattern)
            + table_bits(k["bingo_pf_streamer_size"], FIXED_WAYS, ADDR_BITS_STREAMER - lg(region),
                         FILL_LEVEL_BITS * pattern))


def storage_bytes(k: dict[str, int]) -> int:
    return (storage_bits(k) + 7) // 8


def invalid_reason(k: dict[str, object]) -> str | None:
    """Why ChampSim would reject, abort on or misread these knobs, or None."""
    try:
        for name, (lo, hi) in RANGES.items():
            _require(name in k, f"{name} is missing")
            _require(lo <= int(k[name]) <= hi, f"{name}={k[name]} outside {lo}..{hi}")
        _require(0.0 <= float(k.get("bingo_l2c_thresh", 0.0)) <= 1.0, "bingo_l2c_thresh outside 0..1")
        ints = {n: int(k[n]) for n in RANGES}
        _require(ints["bingo_region_size"] // BLOCK_SIZE == ints["bingo_pattern_len"],
                 f"region_size {ints['bingo_region_size']} implies pattern_len "
                 f"{ints['bingo_region_size'] // BLOCK_SIZE}, not {ints['bingo_pattern_len']} (bingo.cc aborts)")
        _require(ints["bingo_max_addr_width"] >= ints["bingo_min_addr_width"], "max_addr_width < min_addr_width")
        _require(ints["bingo_pc_width"] + ints["bingo_min_addr_width"] > 0,
                 "pc_width + min_addr_width must exceed 0 (the PHT would have no key)")
        storage_bits(ints)
    except (Invalid, ValueError) as exc:
        return str(exc)
    return None


def read_ini(path: str | Path) -> dict[str, str]:
    return {m.group(1): m.group(2) for m in re.finditer(r"^\s*(\w+)\s*=\s*(.*?)\s*$", Path(path).read_text(), re.M)}


def full(path: str | Path) -> dict[str, str]:
    """The file's knobs over the shipped ones, pattern_len derived when the file does not say it."""
    mine = read_ini(path)
    k = {**read_ini(Path(__file__).with_name("bingo_default.ini")), **mine}
    if "bingo_pattern_len" not in mine and "bingo_region_size" in mine:
        k["bingo_pattern_len"] = str(int(mine["bingo_region_size"]) // BLOCK_SIZE)
    return k


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(prog="bingo.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("artifact")
    c.add_argument("--max-storage", type=int)
    m = sub.add_parser("measure")
    m.add_argument("artifact")
    m.add_argument("--traces", required=True)
    m.add_argument("--warmup", type=int, required=True)
    m.add_argument("--sim", type=int, required=True)
    m.add_argument("--jobs", type=int)
    args = ap.parse_args(argv)
    knobs = full(args.artifact)
    if args.cmd == "check":
        why = invalid_reason(knobs)
        if why is None and args.max_storage is not None:
            size = storage_bytes({n: int(knobs[n]) for n in RANGES})
            why = f"{size} B is over the {args.max_storage} B budget" if size > args.max_storage else None
        print(f"1 failing: {why}" if why else "0 failing")
        return 0
    from flux_evaluator_champsim.study import measure

    import tempfile

    with tempfile.TemporaryDirectory() as d:          # what ChampSim reads: the file with the shipped rest
        ini = Path(d) / Path(args.artifact).name
        ini.write_text("".join(f"{n} = {v}\n" for n, v in knobs.items()))
        got = measure(str(ini), args.traces, args.warmup, args.sim, jobs=args.jobs)
    print(f"geomean_speedup={got.pop('geomean_speedup'):.6g}")
    print(f"storage_bytes={storage_bytes({n: int(knobs[n]) for n in RANGES})}")
    for n, v in sorted(got.items()):
        print(f"{n}={v:.6g}" if not float(v).is_integer() else f"{n}={int(v)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""A ChampSim `.ini` for the L2 prefetcher slot: check it, measure it. A knob the file leaves out
takes its shipped value (`bingo_default.ini`, a partner's from knobs.md); `bingo_pattern_len` follows
`bingo_region_size`. `check` refuses what knobs.md does not allow (D872).

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

#: Kept at the shipped value (knobs.md): the L1D/LLC thresholds and the fill level are not this study's.
FIXED = {"bingo_debug_level": "0", "bingo_l1d_thresh": "1.01", "bingo_llc_thresh": "0.05",
         "bingo_pc_address_fill_level": "L2"}
CRASH = ("scooby", "mlop", "next_line")        # crash beside Bingo (knobs.md)
_DEG, _N = (0, 64), (1, MAX_ENTRIES)
#: partner -> {knob: (shipped, min, max)}: the knobs knobs.md lists, shipped values from Pythia's
#: `config/<partner>.ini`. A knob is read only with its partner in the stack; power7 runs a stride and
#: a streamer of its own, so it reads their knobs too.
PARTNERS: dict[str, dict[str, tuple[int, int, int]]] = {
    "sms": {"sms_pref_degree": (4, *_DEG), "sms_pht_size": (2048, *_N), "sms_pht_assoc": (16, *_N),
            "sms_region_size": (4096, BLOCK_SIZE, PAGE_SIZE), "sms_ft_size": (64, *_N), "sms_at_size": (32, *_N),
            "sms_pref_buffer_size": (256, *_N)},
    "ampm": {"ampm_pref_degree": (4, *_DEG), "ampm_pred_degree": (4, *_DEG), "ampm_pb_size": (64, *_N),
             "ampm_pref_buffer_size": (256, *_N)},
    "stride": {"stride_pref_degree": (2, *_DEG), "stride_num_trackers": (256, *_N)},
    "streamer": {"streamer_pref_degree": (5, *_DEG), "streamer_num_trackers": (64, *_N)},
    "spp_ppf_dev": {"ppf_perc_threshold_hi": (-5, -256, 256), "ppf_perc_threshold_lo": (-15, -256, 256)},
    "power7": {"power7_default_streamer_degree": (4, *_DEG), "power7_explore_epoch": (20000, 1, 1 << 31),
               "power7_exploit_epoch": (200000, 1, 1 << 31)},
    "sandbox": {"sandbox_pref_degree": (4, *_DEG), "sandbox_num_access_in_phase": (256, *_N),
                "sandbox_bloom_filter_size": (2048, *_N), "sandbox_num_cycle_offsets": (4, 0, 16)},
    "spp_dev2": {}, "ipcp": {},
}
PARTNERS["power7"] = {**PARTNERS["power7"], **PARTNERS["stride"], **PARTNERS["streamer"]}

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


# The partners' storage (D873). Pythia's partners are simulator code with no storage comments, so
# each is modelled from the fields its source keeps, at these widths: a 48-bit physical address (as
# Bingo's tables), so a line is 42 bits and a 4 KB page 36; the PC whole at 48 bits, since the
# partners match it unhashed; and a fully associative table (a deque searched whole) holds the full
# key, a valid bit and an LRU/FIFO position of ceil(lg entries) bits per entry.
PHYS_BITS, PC_BITS = 48, 48
LINE_BITS, PAGE_BITS = PHYS_BITS - 6, PHYS_BITS - 12
COUNTER_BITS = 64


def _clg(n: int) -> int:
    return (n - 1).bit_length()


def assoc_bits(entries: int, key_bits: int, payload_bits: int) -> int:
    return entries * (key_bits + payload_bits + 1 + _clg(entries))


def _sms(p: dict[str, int]) -> int:
    """sms.h: filter (page, pc, trigger offset), accumulation (+ pattern, age), the pattern table
    keyed by pc . trigger offset (sms.cc create_signature) holding the pattern, and the prefetch
    buffer of line addresses (sms_enable_pref_buffer is on)."""
    blocks = p["sms_region_size"] // BLOCK_SIZE
    page, off = PHYS_BITS - lg(p["sms_region_size"]), lg(blocks)
    return (assoc_bits(p["sms_ft_size"], page, PC_BITS + off)
            + assoc_bits(p["sms_at_size"], page, PC_BITS + off + blocks)
            + table_bits(p["sms_pht_size"], p["sms_pht_assoc"], PC_BITS + off, blocks)
            + p["sms_pref_buffer_size"] * LINE_BITS)


def _stride(p: dict[str, int]) -> int:
    """stride.h Tracker: pc, last line address, an int32 stride."""
    return assoc_bits(p["stride_num_trackers"], PC_BITS, LINE_BITS + 32)


def _streamer(p: dict[str, int]) -> int:
    """streamer.h Stream_Tracker: page, last offset in it, direction (+1/-1/0), a confidence bit."""
    return assoc_bits(p["streamer_num_trackers"], PAGE_BITS, 6 + 2 + 1)


def _sandbox(p: dict[str, int]) -> int:
    """sandbox.h: the bloom filter's bits, 32 candidate offsets (+-1..16, 6 bits), 16 scores and the
    phase's demand and hit counters (each at most num_access_in_phase), the 4-bit pointer."""
    count = p["sandbox_num_access_in_phase"].bit_length()
    return p["sandbox_bloom_filter_size"] + 32 * 6 + 16 * count + 2 * count + 4


#: Knobless partners, from their headers' constants (bits).
SPP_ST = 256 * (1 + 16 + 6 + 12 + 8)        # signature table: valid, tag, last offset, signature, LRU
SPP_GHR = 8 * (1 + 12 + 7 + 6 + 7) + 2 * 10 # global history: valid, sig, confidence, offset, delta; 2 counters
SPP_DEV2_BITS = (SPP_ST + 512 * (4 + 4 * (7 + 4))     # pattern table: c_sig + 4 x (delta, c_delta)
                 + 1024 * (1 + 1 + 6) + SPP_GHR)       # prefetch filter: valid, useful, remainder
PPF_FEATURES = 11 + 12 + 12 + 12 + 10 + 12 + 10 + 11 + 7   # lg of each PERC_DEPTH: the indices kept
PPF_BITS = (SPP_ST + 2048 * (4 + 4 * (7 + 4))          # ppf_dev_helper.h: PT_SET 2048
            + (2048 + 4 * 4096 + 1024 + 1024 + 2048 + 128) * 5      # perceptron weights, 5-bit
            + 1024 * (1 + 1 + 6 + PPF_FEATURES + 9)     # filter: valid, useful, remainder, features, sum
            + 1024 * (1 + 8 + PPF_FEATURES + 9)         # reject filter
            + SPP_GHR + 3 * PC_BITS + 6 * PAGE_BITS)    # last three IPs, six pages tracked
IPCP_BITS = 1024 * (6 + 1 + 2 + 7)                      # ipcp_L2.h IP_TRACKER: tag, valid, class, stride

#: partner -> its storage in bits, from its knobs.
PARTNER_BITS = {
    "sms": _sms,
    "ampm": lambda p: assoc_bits(p["ampm_pb_size"], PAGE_BITS, 64),     # page, 64-line bitmap; buffer off
    "stride": _stride,
    "streamer": _streamer,
    "power7": lambda p: _stride(p) + _streamer(p) + 16 * COUNTER_BITS,  # cycle_stats[14], 2 counters
    "sandbox": _sandbox,
    "spp_dev2": lambda p: SPP_DEV2_BITS,
    "spp_ppf_dev": lambda p: PPF_BITS,
    "ipcp": lambda p: IPCP_BITS,
}


def design_storage_bytes(k: dict[str, object]) -> int:
    """Bingo's tables and every partner's in the stack."""
    p = partner_knobs(k)
    bits = storage_bits({n: int(k[n]) for n in RANGES}) + sum(PARTNER_BITS[t](p) for t in stack(k)[1:])
    return (bits + 7) // 8


def stack(k: dict[str, object]) -> list[str]:
    """The L2 prefetchers `l2c_prefetcher_types` names, in order."""
    return [t.strip() for t in str(k.get("l2c_prefetcher_types", "")).split(",") if t.strip()]


def _pow2(n: int) -> bool:
    return n >= 1 and n & (n - 1) == 0


def _same(name: str, value: object) -> bool:
    keep = FIXED[name]
    try:
        return float(str(value)) == float(keep)
    except ValueError:
        return str(value).strip() == keep


def _contract(k: dict[str, object]) -> None:
    """What the contract forbids (D872): a stack without Bingo first, a crashing or unlisted partner,
    a knob knobs.md does not list (`simulation_instructions` or `dram_io_freq` change the run, not
    the prefetcher, and the no-prefetcher baseline never sees them), a partner's knob without the
    partner, a fixed knob changed."""
    types = stack(k)
    _require(types[:1] == ["bingo"], f"l2c_prefetcher_types = {','.join(types) or '(empty)'}: bingo must come first")
    _require(len(set(types)) == len(types), f"l2c_prefetcher_types names a prefetcher twice: {','.join(types)}")
    for t in types[1:]:
        _require(t not in CRASH, f"{t} crashes beside bingo (knobs.md)")
        _require(t in PARTNERS, f"{t} is not a partner knobs.md lists ({', '.join(PARTNERS)})")
    bingo = {"l2c_prefetcher_types", "bingo_l2c_thresh", *RANGES, *FIXED}
    for name in k:
        owners = [p for p, knobs in PARTNERS.items() if name in knobs]
        _require(name in bingo or owners, f"{name} is not a knob knobs.md lists")
        _require(name in bingo or any(p in types for p in owners),
                 f"{name} is read only with {' or '.join(owners)} in l2c_prefetcher_types")
    for name in FIXED:
        _require(name not in k or _same(name, k[name]), f"{name}={k.get(name)}: knobs.md keeps it at {FIXED[name]}")
    p = partner_knobs(k)
    for t in types[1:]:
        for name, (_, lo, hi) in PARTNERS[t].items():
            _require(lo <= p[name] <= hi, f"{name}={p[name]} outside {lo}..{hi}")
    if "sms" in types:
        sets = p["sms_pht_size"] // p["sms_pht_assoc"]
        _require(_pow2(p["sms_region_size"]), f"sms_region_size {p['sms_region_size']} is not a power of two")
        _require(p["sms_pht_size"] == sets * p["sms_pht_assoc"] and _pow2(sets),
                 f"sms_pht_size {p['sms_pht_size']} is not a power-of-two number of {p['sms_pht_assoc']}-way sets")
    if "sandbox" in types:
        _require(p["sandbox_bloom_filter_size"] >= p["sandbox_num_access_in_phase"],
                 "sandbox_bloom_filter_size < sandbox_num_access_in_phase gives the bloom filter no hash function")
    if "spp_ppf_dev" in types:
        _require(p["ppf_perc_threshold_lo"] <= p["ppf_perc_threshold_hi"], "ppf_perc_threshold_lo > ppf_perc_threshold_hi")


def partner_knobs(k: dict[str, object]) -> dict[str, int]:
    """Every knob of the stack's partners, as ints: the file's value, else the shipped one."""
    return {name: int(k.get(name, shipped)) for t in stack(k)[1:] if t in PARTNERS
            for name, (shipped, _, _) in PARTNERS[t].items()}


def invalid_reason(k: dict[str, object]) -> str | None:
    """Why the contract refuses these knobs, or ChampSim would reject, abort on or misread them, or None."""
    try:
        _contract(k)
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
        design_storage_bytes(k)
    except (Invalid, ValueError) as exc:
        return str(exc)
    return None


def read_ini(path: str | Path) -> dict[str, str]:
    return {m.group(1): m.group(2) for m in re.finditer(r"^\s*(\w+)\s*=\s*(.*?)\s*$", Path(path).read_text(), re.M)}


def full(path: str | Path) -> dict[str, str]:
    """The file's knobs over the shipped ones, pattern_len derived when the file does not say it. A
    partner in the stack gets its shipped knobs too: ChampSim's compiled defaults are not the values
    knobs.md lists (stride_num_trackers 64, not 256)."""
    mine = read_ini(path)
    k = {**read_ini(Path(__file__).with_name("bingo_default.ini")), **mine}
    if "bingo_pattern_len" not in mine and "bingo_region_size" in mine:
        k["bingo_pattern_len"] = str(int(mine["bingo_region_size"]) // BLOCK_SIZE)
    for t in stack(k)[1:]:
        for name, (shipped, _, _) in PARTNERS.get(t, {}).items():
            k.setdefault(name, str(shipped))
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
            size = design_storage_bytes(knobs)
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
    print(f"storage_bytes={design_storage_bytes(knobs)}")
    for n, v in sorted(got.items()):
        print(f"{n}={v:.6g}" if not float(v).is_integer() else f"{n}={int(v)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

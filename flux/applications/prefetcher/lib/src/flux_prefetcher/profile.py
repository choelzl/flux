"""What the traces actually look like, so a model designs for THESE workloads and not a slogan.

Until now every prompt in this study described the workload in one sentence — "FFT-heavy 5G
baseband with strided array traversals" — written by a person from a folder name. The traces are
380 MB of actual memory accesses and the simulator reports exactly how much of them each prefetcher
covers. Neither reached the model. This module turns both into a page of numbers a model can
reason from.

Two views, because they answer different questions:

  STATIC   what the access stream IS: how concentrated across PCs, how often a PC repeats a stride,
           how far apart consecutive accesses land, how much lands in the same page. Parsed from
           the trace records directly — 64 bytes each — with no simulator involved.

  DYNAMIC  what a given prefetcher STILL MISSES on it: L2 coverage, accuracy, lateness, the load
           misses that remain per thousand instructions. Parsed from the simulator's own report of
           one screen-rung run. This is the number a design should be trying to move.

Numbers are kept coarse on purpose. A model given twelve decimal places invents precision; one
given "62% of loads come from 8 PCs, 71% of those repeat a constant stride, 23% of L2 load misses
survive bingo" has something to design against.
"""

from __future__ import annotations

import gzip
import re
import struct
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

#: input_instr, Pythia's ChampSim (inc/instruction.h): ip, is_branch, branch_taken, 2 destination
#: registers, 4 source registers, 2 destination memory addresses, 4 source memory addresses.
_RECORD = struct.Struct("<QBB2B4B2Q4Q")
RECORD_BYTES = _RECORD.size            # 64
assert RECORD_BYTES == 64

BLOCK = 64
PAGE = 4096


@dataclass
class StaticProfile:
    """The shape of one trace's memory-access stream."""

    trace: str
    records: int
    mem_accesses: int
    loads: int
    stores: int
    distinct_pcs: int
    top8_pc_share: float            # fraction of accesses from the 8 busiest PCs
    constant_stride_share: float    # accesses whose delta from the same PC's previous equals the one before
    stride_next_line: float         # of constant-stride accesses, delta of exactly one line
    stride_small: float             # 2..8 lines
    stride_large: float             # more than 8 lines (often cross-page)
    same_page_as_previous: float    # consecutive accesses (any PC) in the same 4 KB page
    delta_pair_repeats: float       # (delta[i-1], delta[i]) pair seen before for that PC
    lines_reused_within_1k: float   # line access whose line was touched in the last 1024 accesses

    def summary(self) -> str:
        return (
            f"{self.trace}: {self.mem_accesses:,} memory accesses in {self.records:,} "
            f"instructions ({self.loads:,} loads, {self.stores:,} stores). "
            f"{self.distinct_pcs:,} distinct PCs touch memory; the busiest 8 account for "
            f"{self.top8_pc_share:.0%}. "
            f"{self.constant_stride_share:.0%} of accesses repeat their PC's previous stride "
            f"(of those: {self.stride_next_line:.0%} next-line, {self.stride_small:.0%} 2-8 lines, "
            f"{self.stride_large:.0%} >8 lines). "
            f"{self.same_page_as_previous:.0%} of consecutive accesses stay in the same 4 KB page. "
            f"{self.delta_pair_repeats:.0%} of accesses repeat a (previous delta, delta) pair for "
            f"their PC — delta-history predictability. "
            f"{self.lines_reused_within_1k:.0%} of line accesses re-touch a line seen in the last "
            f"1,024 accesses."
        )


def static_profile(trace: str | Path, records: int = 2_000_000,
                   skip: int = 0) -> StaticProfile:
    """Parse the first `records` instructions of a trace and describe its access stream.

    Two million records is about a second of parsing and is enough for stable percentages; the
    percentages move by a point or two between the first and the tenth million.
    """
    path = Path(trace)
    per_pc_last: dict[int, int] = {}
    per_pc_delta: dict[int, int] = {}
    per_pc_pairs: dict[int, set] = defaultdict(set)
    pc_counts: Counter = Counter()
    recent_lines: dict[int, int] = {}

    n = mem = loads = stores = 0
    const_stride = next_line = small = large = 0
    same_page = pair_repeat = reused = 0
    prev_page = None
    order = 0

    with gzip.open(path, "rb") as fh:
        if skip:
            fh.read(skip * RECORD_BYTES)
        while n < records:
            raw = fh.read(RECORD_BYTES)
            if len(raw) < RECORD_BYTES:
                break
            n += 1
            fields = _RECORD.unpack(raw)
            ip = fields[0]
            dst_mem = fields[8:10]
            src_mem = fields[10:14]
            for addr, is_load in [(a, False) for a in dst_mem if a] + [(a, True) for a in src_mem if a]:
                mem += 1
                order += 1
                loads += is_load
                stores += not is_load
                pc_counts[ip] += 1
                line = addr >> 6
                page = addr >> 12

                if prev_page is not None and page == prev_page:
                    same_page += 1
                prev_page = page

                last = per_pc_last.get(ip)
                if last is not None:
                    delta = line - last
                    prev_delta = per_pc_delta.get(ip)
                    if prev_delta is not None:
                        if delta == prev_delta and delta != 0:
                            const_stride += 1
                            mag = abs(delta)
                            if mag == 1:
                                next_line += 1
                            elif mag <= 8:
                                small += 1
                            else:
                                large += 1
                        if (prev_delta, delta) in per_pc_pairs[ip]:
                            pair_repeat += 1
                        per_pc_pairs[ip].add((prev_delta, delta))
                        if len(per_pc_pairs[ip]) > 64:
                            per_pc_pairs[ip].clear()
                    per_pc_delta[ip] = delta
                per_pc_last[ip] = line

                seen_at = recent_lines.get(line)
                if seen_at is not None and order - seen_at <= 1024:
                    reused += 1
                recent_lines[line] = order
                if len(recent_lines) > 8192:
                    cutoff = order - 4096
                    recent_lines = {k: v for k, v in recent_lines.items() if v > cutoff}

                # No pointer-chase measure, deliberately. The trace carries addresses, not the
                # values loaded, so "this address was recently used as an address" is the only
                # proxy available -- and on a stream with 92% line reuse it measures reuse, not
                # chasing. A number that cannot distinguish the two would mislead a design.

    top8 = sum(c for _, c in pc_counts.most_common(8))
    cs = max(const_stride, 1)
    return StaticProfile(
        trace=path.name.split(".")[0], records=n, mem_accesses=mem, loads=loads, stores=stores,
        distinct_pcs=len(pc_counts), top8_pc_share=top8 / max(mem, 1),
        constant_stride_share=const_stride / max(mem, 1),
        stride_next_line=next_line / cs, stride_small=small / cs, stride_large=large / cs,
        same_page_as_previous=same_page / max(mem, 1),
        delta_pair_repeats=pair_repeat / max(mem, 1),
        lines_reused_within_1k=reused / max(mem, 1),
    )


@dataclass
class DynamicProfile:
    """What one prefetcher stack still leaves on the table, from the simulator's own report."""

    trace: str
    stack: str
    ipc: float
    l2_load_mpki: float             # L2 load misses per thousand instructions, WITH the stack
    llc_load_mpki: float            # of which went all the way to DRAM
    coverage: float                 # useful prefetches / (useful + remaining load misses)
    accuracy: float                 # useful / filled
    late_share: float               # late / useful — arrived, but after the demand
    prefetch_per_kilo: float        # prefetches issued per thousand instructions

    def summary(self) -> str:
        return (
            f"{self.trace} with {self.stack}: L2 load misses still {self.l2_load_mpki:.1f} per "
            f"1,000 instructions ({self.llc_load_mpki:.1f} of them reach DRAM). Prefetch coverage "
            f"{self.coverage:.0%}, accuracy {self.accuracy:.0%}, {self.late_share:.0%} of useful "
            f"prefetches arrived late; {self.prefetch_per_kilo:.1f} issued per 1,000 instructions."
        )


_STAT = re.compile(r"^Core_0_(\w+)\s+([0-9.]+)\s*$", re.MULTILINE)


def parse_stats(stdout: str) -> dict[str, float]:
    """Every `Core_0_*` line ChampSim prints, as a dict."""
    return {m.group(1): float(m.group(2)) for m in _STAT.finditer(stdout)}


def dynamic_profile(stats: dict[str, float], trace: str, stack: str) -> DynamicProfile:
    """Turn a run's stats into the numbers a design should be trying to move."""
    instr = max(stats.get("instructions", 1.0), 1.0)
    useful = stats.get("L2C_prefetch_useful", 0.0)
    filled = stats.get("L2C_prefetch_filled", 0.0)
    load_miss = stats.get("L2C_load_miss", 0.0)
    return DynamicProfile(
        trace=trace, stack=stack, ipc=stats.get("IPC", 0.0),
        l2_load_mpki=1000.0 * load_miss / instr,
        llc_load_mpki=1000.0 * stats.get("LLC_load_miss", 0.0) / instr,
        coverage=useful / max(useful + load_miss, 1.0),
        accuracy=useful / max(filled, 1.0),
        late_share=stats.get("L2C_prefetch_late", 0.0) / max(useful, 1.0),
        prefetch_per_kilo=1000.0 * stats.get("L2C_prefetch_issued", 0.0) / instr,
    )


def profile_text(static: list[StaticProfile], dynamic: list[DynamicProfile]) -> str:
    """The page a prompt carries. Coarse on purpose; see the module docstring."""
    lines = ["WHAT THE TRACES LOOK LIKE (parsed from the access stream, first 2M instructions each):"]
    lines += [f"  * {s.summary()}" for s in static]
    if dynamic:
        lines.append("")
        lines.append("WHAT THE CURRENT PREFETCHER STILL MISSES (measured by the simulator):")
        lines += [f"  * {d.summary()}" for d in dynamic]
    lines.append("")
    lines.append(
        "Read these before designing. A per-PC table sized for hundreds of entries is useless "
        "against tens of thousands of PCs; a stride prefetcher earns little where constant strides "
        "are rare; and coverage is the number a new design has to raise -- it is the share of "
        "would-be misses the current stack already catches, so 1 minus coverage is the opening.")
    return "\n".join(lines)

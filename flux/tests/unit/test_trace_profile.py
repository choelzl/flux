"""The evidence page: what the traces look like, and what a prefetcher still misses on them.

Every prompt this study sent used to describe the workload in one sentence written from a folder
name. The traces are on disk and the simulator reports exactly what each prefetcher misses; these
tests pin the arithmetic that turns both into numbers a model can design against.
"""

from __future__ import annotations

import gzip
import struct
from pathlib import Path

FLUX_ROOT = Path(__file__).resolve().parents[2]

from flux_prefetcher.profile import (  # noqa: E402
    RECORD_BYTES, dynamic_profile, parse_stats, profile_text, static_profile,
)


def _record(ip: int, src: tuple[int, ...] = (), dst: tuple[int, ...] = ()) -> bytes:
    """One `input_instr` as the tracer writes it."""
    src = tuple(src) + (0,) * (4 - len(src))
    dst = tuple(dst) + (0,) * (2 - len(dst))
    return struct.pack("<QBB2B4B2Q4Q", ip, 0, 0, 0, 0, 0, 0, 0, 0, *dst, *src)


def _trace(tmp_path: Path, records: list[bytes]) -> Path:
    path = tmp_path / "t.simout_champsim.gz"
    with gzip.open(path, "wb") as fh:
        fh.write(b"".join(records))
    return path


def test_the_record_is_sixty_four_bytes():
    """inc/instruction.h: ip, is_branch, taken, 2 dst regs, 4 src regs, 2 dst mem, 4 src mem."""
    assert RECORD_BYTES == 64
    assert len(_record(0x400000)) == 64


def test_a_constant_stride_stream_is_recognised(tmp_path):
    """One PC walking memory in 128-byte steps: every access after the second repeats its stride."""
    line = 64
    recs = [_record(0x1000, src=(0x10000 + i * 2 * line,)) for i in range(100)]
    p = static_profile(_trace(tmp_path, recs), records=100)
    assert p.mem_accesses == 100 and p.loads == 100 and p.distinct_pcs == 1
    assert p.constant_stride_share > 0.95
    assert p.stride_small > 0.95, "a 2-line stride is 'small', not next-line or large"
    assert p.top8_pc_share == 1.0


def test_a_random_stream_is_not(tmp_path):
    import random

    rng = random.Random(1)
    recs = [_record(rng.randrange(1, 5000) * 4, src=(rng.randrange(1 << 20) << 6,))
            for _ in range(2000)]
    p = static_profile(_trace(tmp_path, recs), records=2000)
    assert p.constant_stride_share < 0.05
    assert p.top8_pc_share < 0.05
    assert p.same_page_as_previous < 0.05


def test_stores_and_loads_are_counted_separately(tmp_path):
    recs = [_record(0x1000, src=(0x10000,), dst=(0x20000,)) for _ in range(10)]
    p = static_profile(_trace(tmp_path, recs), records=10)
    assert p.loads == 10 and p.stores == 10 and p.mem_accesses == 20


def test_stats_parse_from_real_champsim_output():
    out = ("Core_0_instructions 300000\nCore_0_IPC 0.557944\n"
           "Core_0_L2C_load_miss 2755\nCore_0_L2C_prefetch_useful 1693\n"
           "Core_0_L2C_prefetch_filled 2292\nCore_0_L2C_prefetch_late 74\n"
           "Core_0_L2C_prefetch_issued 5114\nCore_0_LLC_load_miss 1763\n")
    s = parse_stats(out)
    assert s["IPC"] == 0.557944 and s["L2C_load_miss"] == 2755


def test_coverage_is_the_share_of_would_be_misses_caught():
    """The number a design has to raise. Measured 38% for shipped Bingo on fdd_su_v1_0."""
    stats = {"instructions": 300000, "IPC": 0.56, "L2C_load_miss": 2755,
             "L2C_prefetch_useful": 1693, "L2C_prefetch_filled": 2292,
             "L2C_prefetch_late": 74, "L2C_prefetch_issued": 5114, "LLC_load_miss": 1763}
    d = dynamic_profile(stats, "fdd_su_v1_0", "bingo")
    assert abs(d.coverage - 1693 / (1693 + 2755)) < 1e-9
    assert abs(d.accuracy - 1693 / 2292) < 1e-9
    assert abs(d.l2_load_mpki - 1000 * 2755 / 300000) < 1e-9
    assert abs(d.late_share - 74 / 1693) < 1e-9


def test_the_page_reads_as_percentages_not_decimals():
    """A model given twelve decimal places invents precision."""
    stats = {"instructions": 1000, "IPC": 1.0, "L2C_load_miss": 10, "L2C_prefetch_useful": 20,
             "L2C_prefetch_filled": 25, "L2C_prefetch_late": 1, "L2C_prefetch_issued": 30,
             "LLC_load_miss": 2}
    text = profile_text([], [dynamic_profile(stats, "t", "bingo")])
    assert "coverage 67%" in text and "accuracy 80%" in text
    assert "0.666" not in text
    assert "1 minus coverage is the opening" in text


def test_both_prompts_carry_the_evidence():
    from flux_codegen_champsim_prefetcher import build_prompt as invent_prompt
    from flux_prefetcher.propose import build_prompt as knob_prompt

    evidence = "WHAT THE TRACES LOOK LIKE\n  * 23,725 distinct PCs touch memory"
    assert "23,725 distinct PCs" in knob_prompt({"a": 1.0}, 3, trace_profile=evidence)
    assert "23,725 distinct PCs" in invent_prompt("x", beat="bingo", beat_geomean=1.0,
                                                  trace_profile=evidence)
    assert "23,725" not in knob_prompt({"a": 1.0}, 3), "no profile, no section"

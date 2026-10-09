"""The checker: is a mapping conflict-free for every start address? Exhaustive and vectorised.

This is the authority: a mapping from a solver (sampled constraints) or a model is believed only
after every start address in the space is checked. A 20-bit space, one stride, N=8 is 2^20 x 8
numpy lookups, a few milliseconds, so sampling is unnecessary.

The verdict carries the worst case (stride, fewest distinct banks out of N, a start address
showing it) so a refusal names a counter-example.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .mapping import Mapping
from .problem import MappingRequest


@dataclass(frozen=True)
class StrideVerdict:
    stride: int
    conflict_free: bool
    worst_distinct: int             # fewest distinct banks any window of N reached
    worst_start: int                # a start address achieving it
    conflicting_starts: int         # how many start addresses conflict at all
    total_starts: int
    stage: str = "bank"             # which resource this verdict is about
    worst_load: int = 0             # most accesses one resource carried in the worst window
    capacity: int = 1


@dataclass(frozen=True)
class Verdict:
    conflict_free: bool
    per_stride: tuple[StrideVerdict, ...]

    @property
    def clean_fraction(self) -> float:
        """The worst resource's fraction of conflict-free start addresses. 1.0 = solved.

        The progress axis (D373): partial conflict-freedom is measurable progress.
        """
        fractions = [1.0 - v.conflicting_starts / v.total_starts
                     for v in self.per_stride if v.total_starts]
        return min(fractions) if fractions else 0.0

    @property
    def worst(self) -> StrideVerdict | None:
        bad = [v for v in self.per_stride if not v.conflict_free]
        return min(bad, key=lambda v: v.worst_distinct) if bad else None

    def summary(self, n: int) -> str:
        if self.conflict_free:
            stages = {v.stage for v in self.per_stride}
            extra = f" and {len(stages) - 1} crossbar stage(s)" if len(stages) > 1 else ""
            return f"conflict-free for all strides at the bank{extra}"
        w = self.worst
        share = w.conflicting_starts / max(w.total_starts, 1)
        if w.stage == "bank":
            return (f"stride {w.stride}: worst window reaches only {w.worst_distinct} of {n} "
                    f"banks (start 0x{w.worst_start:x}); {share:.0%} of start addresses conflict")
        return (f"stride {w.stride} at {w.stage}: one resource carries {w.worst_load} accesses "
                f"against a capacity of {w.capacity} (start 0x{w.worst_start:x}); "
                f"{share:.0%} of start addresses conflict there")


def check_stride(mapping: Mapping, stride: int, n: int, bank_bits: int,
                 address_bits: int) -> StrideVerdict:
    """Every start address `a` in the space; the window a, a+s, ..., a+(n-1)s must hit n banks."""
    space = 1 << address_bits
    mask = np.uint64(space - 1)
    starts = np.arange(space, dtype=np.uint64)
    # banks of every element of every window: shape (n, space)
    banks = np.stack([mapping.banks_of((starts + np.uint64(k * stride)) & mask, bank_bits)
                      for k in range(n)])
    # distinct banks per window: sort along the window axis, count changes
    sorted_banks = np.sort(banks, axis=0)
    distinct = 1 + np.count_nonzero(np.diff(sorted_banks, axis=0), axis=0)
    worst = int(distinct.min())
    where = int(np.argmin(distinct))
    conflicting = int(np.count_nonzero(distinct < n))
    return StrideVerdict(stride=stride, conflict_free=conflicting == 0, worst_distinct=worst,
                         worst_start=where, conflicting_starts=conflicting, total_starts=space)


def _select_bits(values: np.ndarray, bits: tuple[int, ...]) -> np.ndarray:
    out = np.zeros_like(values, dtype=np.uint64)
    for i, b in enumerate(bits):
        out |= ((values >> np.uint64(b)) & np.uint64(1)) << np.uint64(i)
    return out


def check_stage(mapping: Mapping, stride: int, n: int, bank_bits: int, address_bits: int,
                stage) -> StrideVerdict:
    """A crossbar stage: no resource may carry more than `capacity` of a window's accesses.

    With `lanes`, the load is counted within each chunk of consecutive accesses that share one
    input crossbar; the worst chunk of the worst window is the verdict.
    """
    space = 1 << address_bits
    mask = np.uint64(space - 1)
    starts = np.arange(space, dtype=np.uint64)
    banks = np.stack([mapping.banks_of((starts + np.uint64(k * stride)) & mask, bank_bits)
                      for k in range(n)])
    selected = _select_bits(banks, tuple(stage.bits))
    load = np.ones(space, dtype=np.int64)
    distinct = np.full(space, n, dtype=np.int64)
    for chunk in stage.groups(n):
        resource = np.sort(selected[list(chunk)], axis=0)
        # the longest run of equal resource values in each window = the most loaded resource
        change = np.diff(resource, axis=0) != 0
        run = np.ones(space, dtype=np.int64)
        for k in range(1, len(chunk)):
            run = np.where(change[k - 1], 1, run + 1)
            load = np.maximum(load, run)
        distinct = np.minimum(distinct, 1 + np.count_nonzero(change, axis=0))
    over = load > stage.capacity
    worst = int(load.max())
    where = int(np.argmax(load))
    return StrideVerdict(stride=stride, conflict_free=not bool(over.any()),
                         worst_distinct=int(distinct.min()),
                         worst_start=where, conflicting_starts=int(np.count_nonzero(over)),
                         total_starts=space, stage=stage.name or f"stage{list(stage.bits)}",
                         worst_load=worst, capacity=stage.capacity)


def check(mapping: Mapping, request: MappingRequest) -> Verdict:
    """Bank-level for every stride, then every crossbar stage for every stride."""
    per = [check_stride(mapping, s, request.concurrent, request.bank_bits, request.address_bits)
           for s in request.strides]
    for st in request.stages:
        per += [check_stage(mapping, s, request.concurrent, request.bank_bits,
                            request.address_bits, st) for s in request.strides]
    return Verdict(conflict_free=all(v.conflict_free for v in per), per_stride=tuple(per))


def check_verilog(source: str, mapping: Mapping, request: MappingRequest, *, count: int = 256,
                  seed: int = 0, module: str = "bankmap"):
    """The artifact itself, Verilated, against `banks_of` (D876): the exhaustive check above is
    of the numpy model, and Verilog that computes something else (a product cut to 32 bits) would
    pass it and build bank 0. The address corners, the windows of every stride from them, and
    `count` random addresses; a `rtl_check.Check` (`.ok`, `.why`)."""
    from .rtl_check import Golden, check_rtl

    ab, bb = request.address_bits, request.bank_bits
    space = 1 << ab
    corners = [0, 1, space - 1, space >> 1, (space >> 1) - 1]
    addrs = {(c + k * s) % space for c in corners for s in request.strides for k in range(request.concurrent)}
    addrs |= {int(x) for x in np.random.default_rng(seed).integers(0, space, size=count, dtype=np.uint64)}
    at = np.array(sorted(addrs), dtype=np.uint64)
    banks = mapping.banks_of(at, bb)
    ports = ({"name": "addr", "dir": "in", "bits": ab, "unsigned": True},
             {"name": "bank", "dir": "out", "bits": bb, **({"unsigned": True} if bb > 1 else {})})
    rows = tuple({"inputs": {"addr": int(a)}, "expected": {"bank": int(b)}} for a, b in zip(at, banks))
    golden = Golden(ports=ports, vectors=rows, behavior=f"bank = {mapping.describe()}",
                    fn=lambda addr: {"bank": int(mapping.banks_of(np.array([addr], dtype=np.uint64), bb)[0])})
    return check_rtl(source, golden, module=module)

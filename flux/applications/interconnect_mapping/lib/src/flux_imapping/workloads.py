"""Access-input generation: seeded, operation-shaped, and split so nothing overfits.

The evaluation section of the problem statement asks for access inputs WITH operation
info and a guard against overfitting to them. Both live here: traffic is generated from
operation templates (GEMM through the MU with its port split, vector ops through the
VU, DMA streams), every parameter drawn from a seeded rng -- and a solution is TUNED on
the train split but JUDGED on a disjoint holdout split (different seeds, same
distribution). A hash that memorized the train tensors' strides shows up immediately as
a train/holdout gap in the report.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .model import BLOCK_OF, Memory, Mode, TensorLayout, TileAccess, VECTOR_MODES

# Port budgets from the problem statement.
MU_A_R, MU_B_R, MU_C_R, MU_OUT_W = 16, 8, 4, 16
VU_R = VU_W = 4
DMA_R = DMA_W = 4

_MATRIX_MODES = [m for m in Mode if m not in VECTOR_MODES]


def _round_dims(rng: np.random.Generator, mode: Mode, mem: Memory) -> tuple[int, int, int]:
    """Dims 1..64 (problem statement), rounded UP to the mode's legality: inner dim a
    multiple of E, block modes a multiple of the block."""

    def draw() -> int:
        return int(rng.integers(8, 65))   # 1..7 made most tensors trivially small

    r, c, l = draw(), draw(), draw()
    e = mem.elems_per_row

    def up(v: int, k: int) -> int:
        return ((v + k - 1) // k) * k

    if mode in VECTOR_MODES:
        c = 1
        if mode is Mode.Loop_Row:
            r = up(r, e)
        else:
            l = up(l, e)
    elif mode in BLOCK_OF:
        b = BLOCK_OF[mode]
        r, c = up(r, b), up(c, b)
    else:
        from .model import _PLAIN_ORDER
        inner = _PLAIN_ORDER[mode][2]
        if inner == "c":
            c = up(c, e)
        elif inner == "r":
            r = up(r, e)
        else:
            l = up(l, e)
    return r, c, l


@dataclass(frozen=True, slots=True)
class Workload:
    """`steps` is a list of system steps; each step is the list of TileAccesses that
    want the same cycle. `tensors` is every layout placed, for Cost B accounting."""

    steps: list[list[TileAccess]]
    tensors: list[TensorLayout]
    seed: int


class _Placer:
    """Bump allocator in elements, E-aligned; layouts never overlap, which the model
    requires for the injectivity argument to mean anything."""

    def __init__(self, mem: Memory) -> None:
        self._next = 0
        self._e = mem.elems_per_row

    def place(self, r: int, c: int, l: int, mode: Mode,
              pad_inner_to: int | None = None) -> TensorLayout:
        t = TensorLayout(r=r, c=c, l=l, mode=mode, base=self._next,
                         pad_inner_to=pad_inner_to)
        size = ((t.stored_elems + self._e - 1) // self._e) * self._e
        self._next += size
        return t


def _pow2_tile(rng: np.random.Generator, hi: int) -> int:
    return int(2 ** rng.integers(0, max(1, hi.bit_length())))


def _tile_walk(rng: np.random.Generator, t: TensorLayout, rt: int, ct: int, lt: int,
               ports: int, write: bool, max_positions: int = 4,
               unit: str = "mu") -> list[TileAccess]:
    """A few positions of the natural tile sweep over the tensor (GEMM hint: A tiles
    move horizontally, B vertically -- the caller picks the axis order by transposing
    rt/ct); truncated so workloads stay small."""
    out: list[TileAccess] = []
    positions = [(r0, c0, l0)
                 for l0 in range(0, t.l, lt)
                 for r0 in range(0, t.r, rt)
                 for c0 in range(0, t.c, ct)]
    if len(positions) > max_positions:
        idx = rng.choice(len(positions), size=max_positions, replace=False)
        positions = [positions[i] for i in sorted(idx)]
    for r0, c0, l0 in positions:
        out.append(TileAccess(layout=t, r0=r0, c0=c0, l0=l0,
                              rt=rt, ct=ct, lt=lt, ports=ports, write=write, unit=unit))
    return out


def generate(seed: int, *, mem: Memory | None = None, ops: int = 8,
             vu_probability: float = 0.7, dma_probability: float = 0.6) -> Workload:
    """One workload: `ops` MU operations (GEMM-shaped port usage), each contributing
    system steps; VU and DMA traffic joins a step with the given probabilities -- that
    is what makes the system-conflict category non-empty. Tuned to PEAK pressure after
    the first cut measured ~1 cycle/access everywhere (D380): tiles up to 16x16 (64
    rows -- above the port floor by design), longer walks, heavier unit overlap. An
    evaluation that nothing fails is not an evaluation."""
    mem = mem or Memory()
    rng = np.random.default_rng(seed)
    placer = _Placer(mem)
    tensors: list[TensorLayout] = []
    steps: list[list[TileAccess]] = []

    def matrix(mode_pool=_MATRIX_MODES) -> TensorLayout:
        mode = mode_pool[int(rng.integers(0, len(mode_pool)))]
        t = placer.place(*_round_dims(rng, mode, mem), mode)
        tensors.append(t)
        return t

    def vector() -> TensorLayout:
        mode = VECTOR_MODES[int(rng.integers(0, 2))]
        t = placer.place(*_round_dims(rng, mode, mem), mode)
        tensors.append(t)
        return t

    for _ in range(ops):
        a, b, out = matrix(), matrix(), matrix()
        bias = matrix() if rng.random() < 0.3 else None
        # Tiles big enough to exceed the port floor (a 16x16 tile is 64 rows through
        # 16 ports: 4 cycles MINIMUM), walked at 8 positions, not 4.
        rt = int(2 ** rng.integers(1, 5))          # 2..16
        ct = int(2 ** rng.integers(2, 5))          # 4..16
        lt = 1
        a_walk = _tile_walk(rng, a, rt, ct, lt, MU_A_R, write=False, max_positions=8)
        b_walk = _tile_walk(rng, b, ct, rt, lt, MU_B_R, write=False, max_positions=8)
        o_walk = _tile_walk(rng, out, rt, ct, lt, MU_OUT_W, write=True, max_positions=8)
        dma_stream = None
        if rng.random() < dma_probability:
            dma_stream = matrix()
            d_walk = _tile_walk(rng, dma_stream, min(4, dma_stream.r),
                                min(32, dma_stream.c), 1, DMA_R, write=False,
                                max_positions=8, unit="dma")
        for i in range(min(len(a_walk), len(b_walk), len(o_walk))):
            step = [a_walk[i], b_walk[i], o_walk[i]]
            if bias is not None:
                step.append(TileAccess(layout=bias, r0=0, c0=0, l0=0, rt=rt, ct=ct,
                                       lt=1, ports=MU_C_R, write=False, unit="mu"))
            if rng.random() < vu_probability:
                v_in, v_out = vector(), vector()
                step.append(TileAccess(layout=v_in, r0=0, c0=0, l0=0,
                                       rt=min(32, v_in.r), ct=1, lt=1,
                                       ports=VU_R, write=False, unit="vu"))
                step.append(TileAccess(layout=v_out, r0=0, c0=0, l0=0,
                                       rt=min(32, v_out.r), ct=1, lt=1,
                                       ports=VU_W, write=True, unit="vu"))
            if dma_stream is not None and i < len(d_walk):
                step.append(d_walk[i])
            steps.append(step)
    return Workload(steps=steps, tensors=tensors, seed=seed)


def train_holdout(base_seed: int, n_train: int = 3, n_holdout: int = 3,
                  **kw) -> tuple[list[Workload], list[Workload]]:
    """Disjoint seed ranges, same generator: tune on the first list, JUDGE on the
    second. The report prints both so a gap is visible, never averaged away."""
    train = [generate(base_seed + i, **kw) for i in range(n_train)]
    holdout = [generate(base_seed + 1000 + i, **kw) for i in range(n_holdout)]
    return train, holdout

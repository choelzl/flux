"""The memory-system and tensor-layout model: who touches which bank-row, exactly.

Everything downstream (conflict counting, hash search, proofs) reduces to one function:
the set of bank-row addresses a tile access touches. That set is pure geometry --
tensor dims, storage mode, tile shape, origin -- so it is computed here, vectorised,
with no policy mixed in (docs/decisions.md D378).

Geometry (the problem statement's constants, parameterized): B = 2^m banks, bank-row =
E = 2^e elements of 32 bits, one read-or-write port per bank. Element addresses are in
units of elements; a bank-row address is `element_addr // E` (the inner dimension of
every storage mode is a multiple of E and origins are E-aligned, so a bank-row never
straddles two tensors' rows).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

import numpy as np


class Mode(IntEnum):
    """Storage modes: the name reads outer -> inner (rightmost dimension is fastest).

    Block modes glue a RBxCB block; H/V is the fill order OUTSIDE the block (inside,
    H increments columns first, V rows first). Vector modes have no Col dimension.
    """

    Loop_Row_Col = 0
    Loop_Col_Row = 1
    Row_Col_Loop = 2
    Col_Row_Loop = 3
    Row_Loop_Col = 4
    Col_Loop_Row = 5
    Loop_2x2_H = 6
    Loop_2x2_V = 7
    Loop_4x4_H = 8
    Loop_4x4_V = 9
    Loop_Row = 10
    Row_Loop = 11


BLOCK_OF = {Mode.Loop_2x2_H: 2, Mode.Loop_2x2_V: 2, Mode.Loop_4x4_H: 4, Mode.Loop_4x4_V: 4}
VECTOR_MODES = (Mode.Loop_Row, Mode.Row_Loop)
# outer -> inner axis order for the six plain modes, axes named 'l', 'r', 'c'
_PLAIN_ORDER = {
    Mode.Loop_Row_Col: ("l", "r", "c"),
    Mode.Loop_Col_Row: ("l", "c", "r"),
    Mode.Row_Col_Loop: ("r", "c", "l"),
    Mode.Col_Row_Loop: ("c", "r", "l"),
    Mode.Row_Loop_Col: ("r", "l", "c"),
    Mode.Col_Loop_Row: ("c", "l", "r"),
}


@dataclass(frozen=True, slots=True)
class Memory:
    m: int = 5  # bank bits: B = 32
    e: int = 2  # element bits per bank-row: E = 4 x 32b = 128b rows

    @property
    def banks(self) -> int:
        return 1 << self.m

    @property
    def elems_per_row(self) -> int:
        return 1 << self.e


@dataclass(frozen=True, slots=True)
class TensorLayout:
    """A stored tensor: dims are the TRUE data dims; `pad_inner_to` optionally pads the
    inner dimension's storage pitch beyond its natural multiple-of-E size (a skewing
    lever some solutions pull -- that padding is exactly Cost B and is accounted here,
    not hidden). `base` is an element address, E-aligned.
    """

    r: int
    c: int
    l: int
    mode: Mode
    base: int = 0
    pad_inner_to: int | None = None  # storage pitch of the inner dimension, in elements

    def __post_init__(self) -> None:
        mem_e = 4  # the multiple-of-E rule is stated for E=4; layouts carry it explicitly
        if self.base % mem_e:
            raise ValueError(f"base {self.base} is not aligned to E={mem_e}")
        if self.mode in VECTOR_MODES:
            inner = self.r if self.mode is Mode.Loop_Row else self.l
            if inner % mem_e:
                raise ValueError(f"vector inner dim {inner} not a multiple of E={mem_e}")
        elif self.mode in BLOCK_OF:
            b = BLOCK_OF[self.mode]
            if self.r % b or self.c % b:
                raise ValueError(
                    f"{self.mode.name} needs R,C multiples of {b}, got {self.r}x{self.c}")
        else:
            inner = {"c": self.c, "r": self.r, "l": self.l}[_PLAIN_ORDER[self.mode][2]]
            if inner % mem_e:
                raise ValueError(
                    f"{self.mode.name} inner dim {inner} not a multiple of E={mem_e}")
        if self.pad_inner_to is not None and self.pad_inner_to < self._inner_size():
            raise ValueError("pad_inner_to smaller than the inner dimension itself")

    def _inner_size(self) -> int:
        if self.mode in VECTOR_MODES:
            return self.r if self.mode is Mode.Loop_Row else self.l
        if self.mode in BLOCK_OF:
            return BLOCK_OF[self.mode] ** 2  # the block is the inner unit
        return {"c": self.c, "r": self.r, "l": self.l}[_PLAIN_ORDER[self.mode][2]]

    @property
    def inner_pitch(self) -> int:
        """Storage pitch of the inner unit, in elements (== inner size unless padded)."""
        return self.pad_inner_to if self.pad_inner_to is not None else self._inner_size()

    @property
    def true_elems(self) -> int:
        if self.mode in VECTOR_MODES:
            return self.r * self.l
        return self.r * self.c * self.l

    @property
    def stored_elems(self) -> int:
        """Footprint including pad_inner_to holes -- the numerator of Cost B."""
        if self.pad_inner_to is None:
            return self.true_elems
        return self.true_elems // self._inner_size() * self.pad_inner_to

    def element_addrs(self, rr: np.ndarray, cc: np.ndarray, ll: np.ndarray) -> np.ndarray:
        """Element addresses for coordinate arrays (broadcastable, int64). Coordinates
        must lie inside the tensor -- callers clip compute-tile padding lanes first,
        because padded lanes generate no memory access (problem statement)."""
        rr = np.asarray(rr, dtype=np.int64)
        cc = np.asarray(cc, dtype=np.int64)
        ll = np.asarray(ll, dtype=np.int64)
        pitch = self.inner_pitch
        mode = self.mode
        if mode in VECTOR_MODES:
            if mode is Mode.Loop_Row:   # outer Loop, inner Row
                idx = ll * pitch + rr
            else:                        # Row_Loop: outer Row, inner Loop
                idx = rr * pitch + ll
            return self.base + idx
        if mode in BLOCK_OF:
            b = BLOCK_OF[mode]
            nb_r, nb_c = self.r // b, self.c // b
            br, bc = rr // b, cc // b
            wr, wc = rr % b, cc % b
            if mode in (Mode.Loop_2x2_H, Mode.Loop_4x4_H):
                within = wr * b + wc            # inside: columns first
                block = br * nb_c + bc          # outside: horizontally first
            else:
                within = wc * b + wr            # inside: rows first
                block = bc * nb_r + br          # outside: vertically first
            per_l = nb_r * nb_c * pitch
            return self.base + ll * per_l + block * pitch + within
        order = _PLAIN_ORDER[mode]
        coord = {"r": rr, "c": cc, "l": ll}
        size = {"r": self.r, "c": self.c, "l": self.l}
        inner, mid, outer = coord[order[2]], coord[order[1]], coord[order[0]]
        mid_size = size[order[1]]
        idx = (outer * mid_size + mid) * pitch + inner
        return self.base + idx

    def describe(self) -> str:
        pad = f", inner padded to {self.pad_inner_to}" if self.pad_inner_to else ""
        return f"{self.r}x{self.c}x{self.l} {self.mode.name} @ {self.base}{pad}"


@dataclass(frozen=True, slots=True)
class TileAccess:
    """One client's request in one logical access: a RTxCTxLT slice of a tensor at an
    origin, read or written through up to `ports` bank ports. Origins need not be
    tile-aligned to the tensor edge; out-of-range lanes are compute padding and touch
    no memory."""

    layout: TensorLayout
    r0: int
    c0: int
    l0: int
    rt: int
    ct: int
    lt: int
    ports: int
    write: bool = False
    unit: str = ""             # "mu" / "vu" / "dma" -- who issues it (category accounting)

    def rows(self, mem: Memory) -> np.ndarray:
        """Distinct bank-row addresses this access touches (sorted, int64)."""
        rr = np.arange(self.r0, min(self.r0 + self.rt, self.layout.r), dtype=np.int64)
        if self.layout.mode in VECTOR_MODES:
            cc = np.array([0], dtype=np.int64)
        else:
            cc = np.arange(self.c0, min(self.c0 + self.ct, self.layout.c), dtype=np.int64)
        ll = np.arange(self.l0, min(self.l0 + self.lt, self.layout.l), dtype=np.int64)
        if rr.size == 0 or cc.size == 0 or ll.size == 0:
            return np.empty(0, dtype=np.int64)
        grid = self.layout.element_addrs(
            rr[:, None, None], cc[None, :, None], ll[None, None, :])
        return np.unique(grid >> mem.e)

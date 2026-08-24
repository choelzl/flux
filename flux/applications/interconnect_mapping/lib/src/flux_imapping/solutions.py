"""The candidate solutions: hash + placement + schedule + fabric, each honest about
which conflict category it targets, what metadata it needs, and what it costs.

A Solution is four levers pulled together (problem statement's design goals):
- `hash_of(layout)`: the (bank_id, line_id) function. Injectivity is CHECKED, not
  assumed: for the XOR family, (bank, line=row>>m) is injective iff the m x m GF(2)
  submatrix over the low m address bits is invertible (`injective()` below computes
  exactly that) -- two distinct rows then never collide in both bank and line, the
  functional-consistency requirement.
- placement transform: bank-grouping or pitch-padding applied to every tensor. This is
  where Cost B comes from, and it is measured (stored vs true elements), never waved at.
- schedule: "shared" (everyone hits the banks; conflicts are visible) or "timeslot"
  (units take turns; system conflicts vanish by construction, latency and buffer area
  pay for it).
- fabric: which switching circuit carries it -- now a first-class axis in fabric.py
  (capacity-tree blocking model + structural pricing), crossed with these policies by
  the flow so every reported design point is honestly a PAIR (map policy x fabric).

Consistency rule (write-with-one-tile, read-with-another): the hash may depend ONLY on
per-tensor metadata (mode, pitch, base, group), never on the tile shape of the access.
`hash_of` receives the layout and nothing else, so the rule holds by construction; the
unit test pins it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from flux_bankmap.mapping import Mapping, Modulo, XorFold, modulo_baseline

from .conflict import BankHash
from .model import Memory, Mode, TensorLayout

# From the problem statement: 28 read + 24 write client ports, 128-bit rows.
CLIENT_PORTS = 28 + 24
ROW_BITS = 128


def injective(mapping: Mapping, bank_bits: int) -> bool:
    """(bank, line) never collides for distinct rows iff, with line = row >> m, the
    bank function restricted to the low m bits is a bijection for every fixed high
    part. For the GF(2)-linear family that is: the m x m submatrix over address bits
    0..m-1 is invertible. Modulo is the identity submatrix; arbitrary XorFolds must
    pass this gate before they are allowed anywhere near an evaluation."""
    if isinstance(mapping, Modulo):
        return True
    if isinstance(mapping, XorFold):
        mat = mapping.matrix
        if mat.shape[0] < bank_bits:
            return False
        sub = mat[:bank_bits, :bank_bits].copy() % 2
        # GF(2) Gaussian elimination
        for col in range(bank_bits):
            pivot = next((r for r in range(col, bank_bits) if sub[r, col]), None)
            if pivot is None:
                return False
            if pivot != col:
                sub[[col, pivot]] = sub[[pivot, col]]
            for r in range(bank_bits):
                if r != col and sub[r, col]:
                    sub[r] ^= sub[col]
        return True
    raise TypeError(f"no injectivity rule for {type(mapping).__name__}")


def swizzle_for(layout: TensorLayout, mem: Memory) -> XorFold:
    """Metadata-driven per-tensor hash: fold the address bits at the tensor's own
    power-of-2 stride positions into the bank bits. The metadata this needs -- and a
    real system must carry in the tensor descriptor -- is exactly log2 of the
    row-to-row pitch in bank-rows. Non-power-of-2 pitches already break the stride
    resonance by themselves and get the plain interleave."""
    pitch_rows = max(1, layout.inner_pitch >> mem.e)
    taps = []
    for i in range(mem.m):
        t = [i]
        if pitch_rows & (pitch_rows - 1) == 0 and pitch_rows > 1:
            s = pitch_rows.bit_length() - 1
            t.append(i + s)          # fold the bit that a row-step flips
        taps.append(tuple(t))
    return XorFold(taps=tuple(taps), name=f"swizzle<{pitch_rows}>")


@dataclass(frozen=True, slots=True)
class Solution:
    """One MAP POLICY: hash + placement + schedule (+ the storage/buffer levers it
    pulls). The interconnect topology is the other half of a design point; the flow
    crosses the two and every report line names the pair."""

    name: str
    hash_of: Callable[[TensorLayout], BankHash] = field(compare=False)
    schedule: str = "shared"            # or "timeslot"
    extra_latency: float = 0.0          # pipeline/slot/buffer latency per access
    buffer_bits: int = 0                # reorder/prefetch storage this policy adds
    transform: Callable[[TensorLayout, int], TensorLayout] | None = field(
        default=None, compare=False)    # (layout, index) -> re-placed layout
    targets: tuple[str, ...] = ()       # which conflict categories this attacks
    metadata: tuple[str, ...] = ()      # what a real system must know for it to work
    assumptions: tuple[str, ...] = ()   # restrictions imposed (compiler pass, etc.)

    def describe(self) -> str:
        return (f"{self.name}: targets {', '.join(self.targets) or 'nothing (baseline)'}"
                f"; needs {', '.join(self.metadata) or 'no metadata'}"
                + (f"; assumes {'; '.join(self.assumptions)}" if self.assumptions else ""))


def _global_hash(mapping: Mapping, mem: Memory) -> Callable[[TensorLayout], BankHash]:
    h = BankHash(mapping=mapping, bank_bits=mem.m)
    return lambda layout: h


def _pad_odd(layout: TensorLayout, _i: int) -> TensorLayout:
    """Pitch-skew: pad the inner pitch so its size in bank-rows is ODD -- power-of-2
    strides then walk all banks under plain modulo. Cost B is the padding, measured."""
    e = 4
    pitch = layout.inner_pitch
    if (pitch // e) % 2 == 1:
        return layout
    return TensorLayout(r=layout.r, c=layout.c, l=layout.l, mode=layout.mode,
                        base=layout.base, pad_inner_to=pitch + e)


def _group_by_tensor(mem: Memory, group_bits: int, mapping: Mapping):
    """Space separation: tensor i lives in bank group i mod 2^group_bits. Different
    tensors then CANNOT conflict (system category dies by construction); each tensor
    sees only B/2^group_bits banks, so its own conflicts and port ceiling worsen."""
    groups = 1 << group_bits

    def hash_of(layout: TensorLayout) -> BankHash:
        return BankHash(mapping=mapping, bank_bits=mem.m, group_bits=group_bits,
                        group_base=(layout.base // 4) % groups)
    return hash_of


def catalog(mem: Memory) -> list[Solution]:
    """The curated starting field. The flow may add searched XOR variants; everything
    here runs with no model and no solver."""
    assert injective(modulo_baseline(mem.m), mem.m)
    sols = [
        Solution(
            name="S0-modulo-xbar",
            hash_of=_global_hash(Modulo(0), mem),
            targets=(),
            metadata=(),
        ),
        Solution(
            name="S1-xor-global",
            hash_of=_global_hash(
                XorFold(taps=tuple((i, i + mem.m) for i in range(mem.m)),
                        name="xor-fold-m"), mem),
            targets=("intra-operand",),
            metadata=(),
            assumptions=("helps power-of-2 pitches; inert for odd pitches",),
        ),
        Solution(
            name="S2-swizzle-meta",
            hash_of=lambda layout, _m=mem: BankHash(
                mapping=swizzle_for(layout, _m), bank_bits=_m.m),
            targets=("intra-operand",),
            metadata=("per-tensor descriptor: storage mode + log2(inner pitch)",),
            assumptions=("descriptor must reach the hash unit with the address",),
        ),
        Solution(
            name="S3-bank-group",
            hash_of=_group_by_tensor(mem, 2, Modulo(0)),
            schedule="shared",
            targets=("intra-unit", "system"),
            metadata=("per-tensor bank-group id (placement-assigned)",),
            assumptions=("placement/compiler assigns groups; capacity fragments per group",),
        ),
        Solution(
            name="S4-timeslot",
            hash_of=_global_hash(
                XorFold(taps=tuple((i, i + mem.m) for i in range(mem.m)),
                        name="xor-fold-m"), mem),
            buffer_bits=CLIENT_PORTS * ROW_BITS * 4,  # per-port 4-deep fetch buffers
            schedule="timeslot",
            extra_latency=1.5,  # mean slot wait for 3 units, round-robin
            targets=("intra-unit", "system"),
            metadata=("units must tolerate slotted issue (know accesses 1-2 cycles ahead)",),
            assumptions=("adds pipeline latency; buffers hold prefetched rows",),
        ),
        Solution(
            name="S7-rw-stagger",
            hash_of=_global_hash(
                XorFold(taps=tuple((i, i + mem.m) for i in range(mem.m)),
                        name="xor-fold-m"), mem),
            schedule="stagger",
            buffer_bits=24 * 128,   # write data held one phase
            targets=("intra-unit", "system"),
            metadata=(),
            assumptions=("writes land one phase late; RAW within an op already "
                         "pipelined, so no correctness cost",),
        ),
        Solution(
            name="S9-ab-stagger",
            hash_of=_global_hash(
                XorFold(taps=tuple((i, i + mem.m) for i in range(mem.m)),
                        name="xor-fold-m"), mem),
            schedule="ab_stagger",
            buffer_bits=8 * 128,    # B operand held one phase
            targets=("intra-unit",),
            metadata=(),
            assumptions=("MU pipelines its B input one phase; tests D382's "
                         "read-read-interference hypothesis",),
        ),
        Solution(
            name="S5-pad-skew",
            hash_of=_global_hash(Modulo(0), mem),
            transform=_pad_odd,
            targets=("intra-operand",),
            metadata=("allocator must pad inner pitch to an odd row count",),
            assumptions=("storage padding is real waste, counted in Cost B",),
        ),
    ]
    # The build-time correctness gate: every solution's hash must be injective on the
    # bank bits it actually controls, for a representative spread of tensor pitches --
    # a non-injective hash is functional inconsistency, not a slow design.
    probes = [TensorLayout(r=8, c=c, l=2, mode=Mode.Loop_Row_Col, base=0)
              for c in (4, 8, 16, 32, 64)]
    for s in sols:
        for probe in probes:
            h = s.hash_of(probe)
            if not injective(h.mapping, h.effective_bits):
                raise AssertionError(f"{s.name}: non-injective hash for {probe.describe()}")
    return sols


def solution_to_dict(s: Solution) -> dict[str, Any]:
    return {
        "name": s.name, "schedule": s.schedule, "buffer_bits": s.buffer_bits,
        "targets": list(s.targets),
        "metadata": list(s.metadata), "assumptions": list(s.assumptions),
    }

"""What a caller asks for: strides, a concurrency, a bank count, an address width.

A kernel issues N accesses at once at a, a+s, ..., a+(N-1)s, possibly for several strides s. A
mapping is conflict-free for (s, N) if for every start address a those N addresses land in N
distinct banks. "Every" is why this needs an exhaustive checker, not a sample: the kernel does
not choose where its arrays are placed. The classic failure is `bank = addr mod B` with a stride
that is a multiple of B.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class InvalidRequest(ValueError):
    pass


@dataclass(frozen=True)
class Stage:
    """One resource-sharing point on the way to the bank, e.g. a switch stage of a crossbar.

    Two accesses to different banks can still collide at a stage if they share its resource. A
    stage is the bank-index bits that identify its resource plus how many concurrent accesses
    one resource carries (`capacity`). Bank-level conflict-freeness, `Stage(bits=all,
    capacity=1)`, is always checked; stages add to it.
    """

    bits: tuple[int, ...]           # bank-index bit positions (0 = least significant); () = one
    capacity: int = 1
    name: str = ""
    #: The stage's input side: capacity binds within each group of lanes, not across the window.
    #: "chunk" groups consecutive lanes (lane // lanes, a split first stage); "mod" groups lanes
    #: equal modulo `lanes` (an omega shuffle); None: one crossbar sees every access (D364).
    lanes: int | None = None
    lane_key: str = "chunk"
    #: lane_key="free": the solver chooses which lanes share each of `blocks` input crossbars,
    #: jointly with the mapping (D372); the solved wiring is written back as `partition`.
    blocks: int | None = None
    partition: tuple[tuple[int, ...], ...] | None = None

    @property
    def unsolved(self) -> bool:
        """A free wiring the solver has not chosen yet: no pair is constrained (D372)."""
        return self.lane_key == "free" and self.partition is None

    def resources(self) -> int:
        return 1 << len(self.bits)

    def groups(self, n: int) -> list[tuple[int, ...]]:
        """The window positions that can meet at one of this stage's resources, as lane groups."""
        if self.partition is not None:
            return [tuple(k for k in block if k < n) for block in self.partition
                    if any(k < n for k in block)]
        if self.unsolved:
            # Unsolved free assignment: the solver owns the constraint; do not invent one here.
            return [(k,) for k in range(n)]
        if not self.lanes:
            return [tuple(range(n))]
        if self.lane_key == "mod":
            return [tuple(range(r, n, self.lanes)) for r in range(min(self.lanes, n))]
        return [tuple(range(i, min(i + self.lanes, n))) for i in range(0, n, self.lanes)]

    chunks = groups                     # the D363 name

    def pair_offsets(self, n: int) -> set[int]:
        """Window-position differences l-k of every pair that shares a lane group.

        A pair of accesses at difference j*stride always sits at positions (k, k+j) of some
        window, and whether those two positions share a group depends on j alone for both
        groupings -- so this set, times the strides, is the stage's must-differ set.
        """
        return {l - k for g in self.groups(n) for i, k in enumerate(g) for l in g[i + 1:]}

    def widest_group(self, n: int) -> int:
        return max((len(g) for g in self.groups(n)), default=0)

    def describe(self) -> str:
        label = self.name or (f"stage on bank bits {list(self.bits)}" if self.bits
                              else "stage (one resource)")
        per = ""
        if self.partition is not None:
            per = f" with lanes wired as {[list(b) for b in self.partition]}"
        elif self.lane_key == "free":
            per = (f" across {self.blocks} crossbar(s) of {self.lanes} input(s), the wiring "
                   "FREE: searched jointly with the mapping")
        elif self.lanes:
            per = (f" within each group of {self.lanes} consecutive lanes" if self.lane_key == "chunk"
                   else f" among lanes that agree modulo {self.lanes}")
        return (f"{label}: {self.resources()} resource(s), each carrying up to "
                f"{self.capacity} access(es) per cycle{per}")


@dataclass(frozen=True)
class MappingRequest:
    """One bank-mapping study: the document's `params:`. The record lives in the loop's `--db`."""

    strides: tuple[int, ...]            # in WORDS (one word = one address unit here)
    concurrent: int                     # N accesses issued together
    banks: int                          # B, a power of two
    address_bits: int = 20              # the address space the guarantee must hold over
    problem: str | None = None          # the requirement in words, for a model
    llm_round: int = 6                  # mapping structures a model may propose
    z3_seconds: int = 60                # solver budget per attempt
    max_xor_inputs: int | None = None   # hardware bound: address bits folded into one bank bit
    stages: tuple[Stage, ...] = ()      # extra sharing points on the way to the bank (crossbar)
    #: The interconnect in words and what its stages assume, for the report (D364).
    topology: str = ""
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.strides or any(s <= 0 for s in self.strides):
            raise InvalidRequest("strides must be positive integers")
        if self.banks < 2 or self.banks & (self.banks - 1):
            raise InvalidRequest(f"banks must be a power of two >= 2, got {self.banks}")
        if not 1 <= self.concurrent <= self.banks:
            raise InvalidRequest(
                f"{self.concurrent} concurrent accesses cannot be conflict-free across "
                f"{self.banks} banks: at most {self.banks} can be")
        if not 4 <= self.address_bits <= 32:
            raise InvalidRequest("address_bits must be within 4..32")
        for st in self.stages:
            if any(not 0 <= b < self.bank_bits for b in st.bits):
                raise InvalidRequest(f"{st.describe()}: bits must be bank-index bits "
                                     f"0..{self.bank_bits - 1}")
            if len(set(st.bits)) != len(st.bits) or st.capacity < 1:
                raise InvalidRequest(f"{st.describe()}: bits must be distinct and capacity >= 1")
            if st.lanes is not None and st.lanes < 1:
                raise InvalidRequest(f"{st.describe()}: lanes must be >= 1")
            if st.lane_key not in ("chunk", "mod", "free"):
                raise InvalidRequest(f"{st.describe()}: lane_key must be 'chunk', 'mod' or "
                                     "'free'")
            if st.lane_key == "free":
                if st.capacity != 1 or not st.lanes or not st.blocks:
                    raise InvalidRequest(f"{st.describe()}: a free assignment needs capacity 1, "
                                         "`lanes` (inputs per crossbar) and `blocks` (crossbars)")
                if st.blocks * st.lanes < self.concurrent:
                    raise InvalidRequest(
                        f"{st.describe()}: {st.blocks} crossbar(s) of {st.lanes} input(s) carry "
                        f"at most {st.blocks * st.lanes} accesses; {self.concurrent} were asked")
            if st.partition is not None:
                seen_pos = [k for block in st.partition for k in block]
                # Extra positions beyond the window are fine: `groups()` trims to N.
                if len(seen_pos) != len(set(seen_pos)) or not set(
                        range(self.concurrent)) <= set(seen_pos):
                    raise InvalidRequest(f"{st.describe()}: the partition must cover window "
                                         f"positions 0..{self.concurrent - 1} exactly once")
            at_once = st.widest_group(self.concurrent)
            if at_once > st.resources() * st.capacity:
                raise InvalidRequest(
                    f"{st.describe()} can carry at most {st.resources() * st.capacity} "
                    f"concurrent accesses; {at_once} were asked for")

    @property
    def bank_bits(self) -> int:
        return self.banks.bit_length() - 1

    def describe(self) -> str:
        base = (f"{self.concurrent} concurrent accesses across {self.banks} banks, strides "
                f"{list(self.strides)}, over a {self.address_bits}-bit address space")
        if self.topology:
            base += f"; interconnect: {self.topology}"
        if self.stages:
            base += "; " + "; ".join(st.describe() for st in self.stages)
        return base

    #: The document's `params:` keys. The interconnect arrives as `topology` (refined by
    #: `stage_capacities`, `lanes`) or explicit `stages`, and becomes `Stage`s (D364).
    PARAMS = ("strides", "concurrent", "banks", "address_bits", "z3_seconds", "max_xor_inputs",
              "topology", "stage_capacities", "lanes", "stages", "llm_round", "problem")

    @classmethod
    def from_params(cls, params: dict[str, Any]) -> "MappingRequest":
        """The request the document's `params:` mean; an unknown key is a load error, not ignored."""
        from .topology import Topology, parse as parse_topology

        p = dict(params or {})
        unknown = sorted(set(p) - set(cls.PARAMS))
        if unknown:
            raise InvalidRequest(f"params {unknown} are not the bank-mapping study's; known: "
                                 f"{', '.join(cls.PARAMS)}")
        for key in ("strides", "concurrent"):
            if p.get(key) is None:
                raise InvalidRequest(f"params.{key} is required")
        banks = int(p.get("banks") or 8)
        explicit = list(p.get("stages") or [])
        spec = p.get("topology")
        caps = tuple(int(c) for c in p["stage_capacities"]) if p.get("stage_capacities") else None
        lanes = int(p["lanes"]) if p.get("lanes") else None
        if spec or not explicit:
            topo = parse_topology(str(spec or "crossbar"), banks, capacities=caps, lanes=lanes)
        else:
            # Explicit stages, no topology: drop the default crossbar's "no conflict point" note.
            topo = Topology(name="explicit stages")
        stages = list(topo.stages)
        for st in explicit:
            if not isinstance(st, dict) or not isinstance(st.get("bits"), list):
                raise InvalidRequest("params.stages: each stage is {bits: [bank-index bits], "
                                     "capacity: c, lanes: l, lane_key: chunk|mod|free, blocks: n}")
            stages.append(Stage(bits=tuple(int(b) for b in st["bits"]),
                                capacity=int(st.get("capacity") or 1), name=str(st.get("name") or ""),
                                lanes=int(st["lanes"]) if st.get("lanes") else None,
                                lane_key=str(st.get("lane_key") or "chunk"),
                                blocks=int(st["blocks"]) if st.get("blocks") else None))
        return cls(strides=tuple(int(s) for s in p["strides"]), concurrent=int(p["concurrent"]),
                   banks=banks, address_bits=int(p.get("address_bits") or 20),
                   problem=str(p["problem"]) if p.get("problem") else None,
                   llm_round=int(p.get("llm_round") if p.get("llm_round") is not None else 6),
                   z3_seconds=int(p.get("z3_seconds") or 60),
                   max_xor_inputs=int(p["max_xor_inputs"]) if p.get("max_xor_inputs") else None,
                   stages=tuple(stages), topology=topo.name, notes=tuple(topo.notes))

"""What a caller asks for: strides, a concurrency, a bank count, an address width.

THE PROBLEM. A memory with B banks serves up to B accesses in one cycle if they land in B
different banks. A kernel issues N accesses at once whose addresses differ by a stride s --
a[i], a[i]+s, a[i]+2s, ... -- and the same kernel may use several strides (a row walk, a column
walk, a diagonal). The mapping from address to bank decides whether those N land in N banks
(conflict-free, one cycle) or pile into fewer (serialised). The classic failure is the obvious
mapping, `bank = addr mod B`, meeting a stride that is a multiple of B: every access hits the
same bank and N accesses take N cycles.

A mapping is CONFLICT-FREE for (s, N) if for EVERY start address a, the N addresses a + k*s for
k in 0..N-1 map to N distinct banks. "Every" is the whole point and is why this needs a checker
and not a sample: a mapping that is conflict-free for 99% of starts stalls the kernel on the
other 1%, and the kernel does not get to choose where its arrays are placed.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class InvalidRequest(ValueError):
    pass


@dataclass(frozen=True)
class Stage:
    """One resource-sharing point on the way to the bank, e.g. a switch stage of a crossbar.

    A staged crossbar does not deliver a request straight to its bank. The first stage routes
    on some bits of the bank index into a GROUP; the links from that stage into a group can
    carry only so many requests per cycle; a later stage routes on the remaining bits inside the
    group. Two accesses that reach different banks can still collide at stage one if they share
    a group and the group's links are full.

    So a stage is: which BANK-INDEX BITS identify its resource, and how many concurrent accesses
    one resource can carry (`capacity`). Bank-level conflict-freeness is the special case
    `Stage(bits=all, capacity=1)`, and it is always checked; stages add to it.
    """

    bits: tuple[int, ...]           # bank-index bit positions (0 = least significant); () = one
    capacity: int = 1
    name: str = ""
    #: The stage's INPUT side. A first stage built from several small crossbars -- seven 4x4s
    #: feeding four 7x8s -- does not see the whole window: lanes 0..3 share one 4x4, lanes 4..7
    #: the next. Its capacity then binds within each group of lanes, not across the window.
    #: `lane_key` says how lanes group: "chunk" puts consecutive lanes together (lane // lanes
    #: -- a split first stage), "mod" puts lanes that agree modulo `lanes` together (lane %
    #: lanes -- the shuffle between stages of an omega network). None: one crossbar sees every
    #: access (D363, D364).
    lanes: int | None = None
    lane_key: str = "chunk"
    #: lane_key="free": WHICH lanes share an input crossbar is the designer's (and therefore
    #: the solver's) to choose -- `blocks` crossbars of `lanes` inputs each, searched jointly
    #: with the mapping (D372). A solved assignment is written back as `partition`, which from
    #: then on is the concrete wiring every rung checks against.
    blocks: int | None = None
    partition: tuple[tuple[int, ...], ...] | None = None

    def resources(self) -> int:
        return 1 << len(self.bits)

    def groups(self, n: int) -> list[tuple[int, ...]]:
        """The window positions that can meet at one of this stage's resources, as lane groups."""
        if self.partition is not None:
            return [tuple(k for k in block if k < n) for block in self.partition
                    if any(k < n for k in block)]
        if self.lane_key == "free":
            # Unsolved free assignment: no pair is constrained YET. The solver owns the
            # constraint; a checker asked before an assignment exists must not invent one.
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


def crossbar_stages(bank_bits: int, layout: str, capacities: tuple[int, ...] | None = None,
                    lanes: int | None = None) -> tuple[Stage, ...]:
    """Stages for a `GxH[xK...]` layout, e.g. "2x4" over 8 banks: stage 1 routes on the top bit
    into 2 groups, stage 2 on the low two bits into 4 banks within a group.

    The product must equal the bank count. Capacities default to 1 for every stage; a stage
    with parallel links gets a larger one. The LAST stage's resource is the bank itself, so it
    is not emitted as a stage: bank-level checking already covers it. `lanes` is the input
    width of the FIRST stage's crossbars: "7 4x4s feeding 4 7x8s" over 32 banks is `4x8` with
    `lanes=4` -- each 4x4 must send its four accesses to four different 7x8s.
    """
    sizes = [int(x) for x in layout.lower().split("x")]
    if any(s < 1 or s & (s - 1) for s in sizes):
        raise InvalidRequest(f"crossbar layout {layout!r}: every factor must be a power of two")
    total = 1
    for s in sizes:
        total *= s
    if total != (1 << bank_bits):
        raise InvalidRequest(f"crossbar layout {layout!r} spans {total} banks, not {1 << bank_bits}")
    caps = list(capacities or [1] * len(sizes))
    if len(caps) != len(sizes):
        raise InvalidRequest("one capacity per stage")
    # stage i selects on the next `width_i` bits from the top down
    stages: list[Stage] = []
    hi = bank_bits
    for i, s in enumerate(sizes[:-1]):
        width = s.bit_length() - 1
        lo = hi - width
        stages.append(Stage(bits=tuple(range(lo, hi)), capacity=caps[i],
                            name=f"stage {i + 1} ({s} groups)", lanes=lanes if i == 0 else None))
        hi = lo
    return tuple(stages)


@dataclass(frozen=True)
class MappingRequest:
    """One bank-mapping study."""

    strides: tuple[int, ...]            # in WORDS (one word = one address unit here)
    concurrent: int                     # N accesses issued together
    banks: int                          # B, a power of two
    address_bits: int = 20              # the address space the guarantee must hold over
    db: str = "demo-bankmap.db"
    problem: str | None = None          # the requirement in words, for a model
    llm_round: int = 6                  # mapping structures a model may propose
    z3_seconds: int = 60                # solver budget per attempt
    max_xor_inputs: int | None = None   # hardware bound: address bits folded into one bank bit
    stages: tuple[Stage, ...] = ()      # extra sharing points on the way to the bank (crossbar)
    #: What the interconnect is, in words, and what its stages assume -- a Clos with m >= n
    #: adds no constraint under per-cycle routing, and the report should say so (D364).
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
                # Extra positions beyond the window are fine -- a wiring chosen for N=8 still
                # describes the hardware when the descent asks about N=7; `groups()` trims.
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


@dataclass(frozen=True)
class MappingResult:
    """What the study concluded, and what it could not."""

    decision: object | None = None            # a Mapping, or None
    conflict_free: bool = False
    hardware_cost: int | None = None          # two-input XOR gates in the decision
    candidates: list = field(default_factory=list)      # every (mapping, verdict) considered
    refused: list[tuple[str, str]] = field(default_factory=list)
    #: Every checked mapping in order, as {quality, cost, label, phase, solved} -- quality is
    #: the worst resource's clean fraction of start addresses -- for the progress figure (D373).
    progress: list = field(default_factory=list)
    provenance: dict = field(default_factory=dict)
    lessons: list[str] = field(default_factory=list)
    not_established: list[str] = field(default_factory=list)

    @property
    def met_requirement(self) -> bool:
        return self.conflict_free

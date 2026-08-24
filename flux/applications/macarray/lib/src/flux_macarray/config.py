"""The MAC processing element's design space: what is inside one PE, not how many there are.

The array's shape is given (D365): `lanes` products summed per cycle at the workload's
precision. The search covers how each product is formed, how products are reduced, where the
pipeline registers sit, and whether the PE has an accumulator input. Every point is generated
by `rtl.py`, verified against golden vectors by Verilator, and judged on ASAP7 by Yosys and
OpenROAD; a model contributes by inventing multiplier structures the enumeration lacks
(`invent.py`), never by editing enumerated points.

Small on purpose (4 x 3 x 4 x 2 = 96 points): it is screened exhaustively.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

#: How one signed product is formed. `behavioral` is the `*` operator, left to the synthesis
#: tool (Yosys lowers it to a shift-and-add array); the others are explicit structures.
MULTIPLIERS: tuple[str, ...] = ("behavioral", "array", "booth4", "wallace")
#: How `lanes` products become one sum: a ripple chain of adders, a balanced binary tree, or a
#: carry-save tree that defers every carry to one final adder.
REDUCERS: tuple[str, ...] = ("chain", "tree", "csa")
#: Register stages on the path. 0 is combinational; 1 registers the products; 2 also registers
#: the output; 3 also cuts the reduction in half. Latency in cycles equals the number.
PIPELINES: tuple[int, ...] = (0, 1, 2, 3)
#: What the technology mapper is told to optimise. "delay" hands ABC the clock period (D278);
#: "area" withholds it. Same RTL, different netlist: area traded against frequency (D366).


@dataclass(frozen=True)
class Shape:
    """What the workload fixes: lanes and precision. `acc_bits` is derived, never chosen."""

    lanes: int
    in_bits: int
    w_bits: int
    accumulate: bool = True

    @property
    def product_bits(self) -> int:
        return self.in_bits + self.w_bits

    @property
    def acc_bits(self) -> int:
        """Wide enough for the worst-case sum of `lanes` products, plus one bit of headroom
        for an accumulator input when the PE has one (extends D202)."""
        bits = self.product_bits + (math.ceil(math.log2(self.lanes)) if self.lanes > 1 else 0)
        return bits + (1 if self.accumulate else 0)

    def describe(self) -> str:
        return (f"{self.lanes} lanes, int{self.in_bits} x int{self.w_bits} -> "
                f"int{self.acc_bits}" + (" with accumulator input" if self.accumulate else ""))


@dataclass(frozen=True)
class PeConfig:
    """One point of the space."""

    multiplier: str = "behavioral"
    reducer: str = "tree"
    pipeline: int = 0

    def knobs(self) -> dict[str, Any]:
        return {"multiplier": self.multiplier, "reducer": self.reducer, "pipeline": self.pipeline}

    @property
    def rtl_label(self) -> str:
        return f"{self.multiplier}-{self.reducer}-p{self.pipeline}"

    @property
    def label(self) -> str:
        return self.rtl_label

    @property
    def clocked(self) -> bool:
        return self.pipeline > 0


#: The incumbent: `flux_evaluator_openroad.derive.derive_design_spec`'s canonical datapath (the `*` operator
#: and a sum, combinational; D225). Every gain is quoted against it.
DEFAULT = PeConfig(multiplier="behavioral", reducer="tree", pipeline=0)


__all__ = ["DEFAULT", "MULTIPLIERS", "PIPELINES", "PeConfig", "REDUCERS", "Shape"]

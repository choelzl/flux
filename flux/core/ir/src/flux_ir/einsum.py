"""The Workload IR's own einsum expression, parsed once (D467).

`expr: "b c, c k -> b k"` is IR syntax, and five places were parsing it: the RTL, SystemC (via
RTL), Timeloop and ZigZag translators, ZigZag's mapping regime, and the ONNX exporter. Each
carried the same regex and the same dim algebra -- which operand dims are shared (the reduction),
which is the batch, which is the output -- and a grammar parsed in five places is a grammar that
drifts in five places. A three-input einsum, a transposed output, a new dim syntax: one change
here, not five.

What is deliberately NOT here is the refusals. Every backend has its own limits ("mac_array.sv is
bilinear", "only 'einsum' ops translate to Timeloop today") and its own wording for them, and
`NotExpressibleError` belongs to the evaluator ABI, which sits ABOVE the IR. So this module
answers questions -- is it this grammar, which dims are shared, is it a plain GEMM -- and the
caller raises its own refusal in its own words.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["EXPR", "Einsum", "Gemm", "parse_einsum"]

#: The two-input einsum grammar: dims separated by spaces, operands by a comma, output after
#: `->`. Anchored, so trailing junk is not silently ignored.
EXPR = re.compile(r"^\s*([\w\s]+?)\s*,\s*([\w\s]+?)\s*->\s*([\w\s]+?)\s*$")


@dataclass(frozen=True)
class Gemm:
    """A two-input einsum read as a plain matrix product: `batch x reduction` times
    `reduction x output`. The names are the IR's own dim names, not a backend's."""

    batch: str
    reduction: str
    output: str


@dataclass(frozen=True)
class Einsum:
    """One parsed expression: the dim names of each input operand and of the output."""

    in1: tuple[str, ...]
    in2: tuple[str, ...]
    out: tuple[str, ...]

    @property
    def two_dimensional(self) -> bool:
        """Whether both input operands have exactly two dims -- the plain-GEMM shape."""
        return len(self.in1) == 2 and len(self.in2) == 2

    @property
    def shared(self) -> tuple[str, ...]:
        """Dims both input operands carry, sorted: the reduction candidates. Exactly one is
        what a matrix product means; none or several is something else."""
        return tuple(sorted(set(self.in1) & set(self.in2)))

    def expected_out(self) -> tuple[str, ...] | None:
        """The output dims a plain GEMM would have, `(batch, output)`, or None when this is not
        that shape -- so a caller can say what it expected and what it found."""
        if not self.two_dimensional or len(self.shared) != 1:
            return None
        reduction = self.shared[0]
        batch = next((d for d in self.in1 if d != reduction), None)
        output = next((d for d in self.in2 if d != reduction), None)
        return None if batch is None or output is None else (batch, output)

    def gemm(self) -> Gemm | None:
        """`(batch, reduction, output)` when this expression IS a plain matrix product with an
        untransposed output; None otherwise. A caller that wants to say WHICH condition failed
        asks `two_dimensional`, `shared` and `expected_out` in turn."""
        want = self.expected_out()
        if want is None or tuple(self.out) != want:
            return None
        return Gemm(batch=want[0], reduction=self.shared[0], output=want[1])


def parse_einsum(expr: str) -> Einsum | None:
    """A two-input einsum expression, or None when the text is not that grammar."""
    match = EXPR.match(expr or "")
    if match is None:
        return None
    in1, in2, out = (tuple(group.split()) for group in match.groups())
    if not in1 or not in2 or not out:
        return None
    return Einsum(in1=in1, in2=in2, out=out)

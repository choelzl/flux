"""Real, typed failure modes for the SystemC codegen harness (docs/decisions.md D39) — distinct
from a DUT module simply failing its test vectors (that's a normal `HarnessRunResult`, not an
exception): these are cases the harness itself can't proceed from.
"""

from __future__ import annotations


class InvalidSpecError(ValueError):
    """A `DesignSpec` is structurally invalid (bad port dir/dtype, no ports, duplicate names) —
    caught before any g++ invocation, so a bad spec never wastes a compile."""


class CompileError(RuntimeError):
    """`g++` rejected the DUT + generated driver. Carries the real compiler stderr so a caller
    (e.g. flows/chia_nodes' generate-repair loop) can feed it back to the LLM."""

    def __init__(self, stderr: str, *, returncode: int) -> None:
        self.stderr = stderr
        self.returncode = returncode
        super().__init__(f"g++ exited {returncode}:\n{stderr[-4000:]}")

"""Real, typed failure modes for the RTL codegen harness (docs/decisions.md D43) — mirrors
`flux_codegen_systemc_harness.errors`'s shape exactly (same reasoning: a real Verilator rejection
is distinct from a DUT simply failing its test vectors, which is normal `HarnessRunResult` data,
not an exception).
"""

from __future__ import annotations

from flux_codegen_systemc_harness import InvalidSpecError  # re-exported, not forked — see module docstring


class CompileError(RuntimeError):
    """Real Verilator build failure. Carries the real compiler/linter stderr so a caller (e.g.
    flows/chia_nodes' generate-repair loop) can feed it back to the LLM."""

    def __init__(self, stderr: str, *, returncode: int) -> None:
        self.stderr = stderr
        self.returncode = returncode
        super().__init__(f"verilator exited {returncode}:\n{stderr[-4000:]}")


__all__ = ["InvalidSpecError", "CompileError"]

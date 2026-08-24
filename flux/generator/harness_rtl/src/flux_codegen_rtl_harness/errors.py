"""Real, typed failure modes for the RTL codegen harness (docs/decisions.md D43): a real
Verilator rejection is distinct from a DUT simply failing its test vectors, which is normal
`HarnessRunResult` data, not an exception.

`InvalidSpecError` is `flux_codegen_harness_spec`'s (D453) -- a bad spec is a bad spec in any
language, and it used to be imported from the SystemC harness, which made this package depend on
the other language's for its own error type.
"""

from __future__ import annotations

from flux_codegen_harness_spec import InvalidSpecError  # the shared spec error (D453)


class CompileError(RuntimeError):
    """Real Verilator build failure. Carries the real compiler/linter stderr so a caller (e.g.
    interfaces/chia_nodes' generate-repair loop) can feed it back to the LLM."""

    def __init__(self, stderr: str, *, returncode: int) -> None:
        self.stderr = stderr
        self.returncode = returncode
        super().__init__(f"verilator exited {returncode}:\n{stderr[-4000:]}")


__all__ = ["InvalidSpecError", "CompileError"]

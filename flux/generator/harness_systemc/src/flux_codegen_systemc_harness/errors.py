"""Real, typed failure modes for the SystemC codegen harness (docs/decisions.md D39) — distinct
from a DUT module simply failing its test vectors (that's a normal `HarnessRunResult`, not an
exception): these are cases the harness itself can't proceed from.
"""

from __future__ import annotations

# The spec error is the shared one (D453): a bad spec is a bad spec in any language, and one
# `except InvalidSpecError` now catches both harnesses' parsers rather than two sibling classes.
from flux_codegen_harness_spec import InvalidSpecError  # noqa: F401  (re-exported)


class CompileError(RuntimeError):
    """`g++` rejected the DUT + generated driver. Carries the real compiler stderr so a caller
    (e.g. interfaces/chia_nodes' generate-repair loop) can feed it back to the LLM."""

    def __init__(self, stderr: str, *, returncode: int) -> None:
        self.stderr = stderr
        self.returncode = returncode
        super().__init__(f"g++ exited {returncode}:\n{stderr[-4000:]}")

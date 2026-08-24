"""What a harness reports after building and running a generated design (D453).

One dataclass for both languages. A harness that does not measure latency leaves
`cycles_per_vector` empty (`total_cycles is None`).

A DUT failing its vectors is a result, not an exception: `all_passed` is the verdict, the
failing lines the evidence; `compiled=False` with `compile_stderr` is the third outcome, fed
back to the model by a repair loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HarnessRunResult:
    compiled: bool
    compile_stderr: str | None
    ran: bool
    total_vectors: int
    passed_vectors: int
    vcd_path: Path | None
    vcd_nonempty: bool
    stdout: str
    stderr: str
    failing_vector_lines: tuple[str, ...]
    # Measured cycles per test vector, in vector order; populated only for a `measures_latency`
    # spec (D115), empty otherwise.
    cycles_per_vector: tuple[int, ...] = ()

    @property
    def total_cycles(self) -> int | None:
        """Summed measured latency across vectors, or `None` (not 0) when not measured."""
        return sum(self.cycles_per_vector) if self.cycles_per_vector else None

    @property
    def all_passed(self) -> bool:
        return (self.compiled and self.ran and self.total_vectors > 0
                and self.passed_vectors == self.total_vectors)

    def to_dict(self) -> dict:
        """JSON-safe form: `vcd_path` (a `Path | None`) becomes a plain string or `None`."""
        return {
            "compiled": self.compiled,
            "compile_stderr": self.compile_stderr,
            "ran": self.ran,
            "total_vectors": self.total_vectors,
            "passed_vectors": self.passed_vectors,
            "vcd_path": str(self.vcd_path) if self.vcd_path else None,
            "vcd_nonempty": self.vcd_nonempty,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "failing_vector_lines": list(self.failing_vector_lines),
            "all_passed": self.all_passed,
            "cycles_per_vector": list(self.cycles_per_vector),
            "total_cycles": self.total_cycles,
        }


__all__ = ["HarnessRunResult"]

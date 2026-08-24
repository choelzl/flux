"""What a harness reports after building and running a generated design (D453).

One dataclass for both languages, because it was written twice and the RTL copy had gained a
field the SystemC one had not: measured cycles per vector (D115). The superset is the shared
shape -- a harness that does not measure latency leaves `cycles_per_vector` empty, which is
what `total_cycles is None` then says out loud.

A DUT failing its vectors lands here, not in an exception: `all_passed` is the verdict, the
failing lines are the evidence, and `compiled=False` with `compile_stderr` is the third outcome
(the one a repair loop feeds back to the model).
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
    # Measured cycles per test vector, in vector order -- populated only for a
    # `measures_latency` spec (docs/decisions.md D115), empty otherwise. This is the harness's
    # first *quantitative* output: every other field is pass/fail. It exists so a generated
    # design can eventually serve as a latency reference, not just a correctness one.
    cycles_per_vector: tuple[int, ...] = ()

    @property
    def total_cycles(self) -> int | None:
        """Summed measured latency across vectors, or `None` when latency wasn't measured --
        `None` rather than 0, since 0 is a legitimate measurement and "not measured" is not."""
        return sum(self.cycles_per_vector) if self.cycles_per_vector else None

    @property
    def all_passed(self) -> bool:
        return (self.compiled and self.ran and self.total_vectors > 0
                and self.passed_vectors == self.total_vectors)

    def to_dict(self) -> dict:
        """JSON-safe (MCP tool return values must be: the same real gotcha
        `ArchitectureDSEReport`/`ConformanceReport` needed `to_dict()` for, docs/decisions.md
        D7): `vcd_path` (a `Path | None`) becomes a plain string or `None`."""
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

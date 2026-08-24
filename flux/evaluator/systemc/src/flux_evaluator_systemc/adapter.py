"""SystemC coarse-grain backend adapter implementing the Flux Evaluator ABI (docs/evaluator-abi.md,
docs/calibration.md's fidelity chain): a fast functional-correctness + timing pre-check for the exact same
`mac_array` design `evaluator/rtl` simulates cycle-accurately, escalating there for the
authoritative number.

Reuses `evaluator/rtl`'s shape/architecture translators directly (`einsum_op_to_mac_array_shape`,
`architecture_ir_to_lanes`) and its golden-reference generator (`generate_test_vectors`) — same
"adapters, not forks" discipline as every other evaluator here, and the only way both adapters
can honestly claim to model the *same* design. One wrinkle from that reuse: those translators
raise `flux_evaluator_rtl.NotExpressibleError` (both `ValueError` subclasses, so `except
ValueError` still catches either), not this package's own — reserved for validation failures
specific to this adapter (`Candidate.mapping`, multi-op workloads).

v0.1 status: identical scope to `evaluator/rtl` (`Candidate.workload` — one two-operand
`einsum` op; `Candidate.arch` — `None` or a single-spatial-dim Architecture IR dict;
`Candidate.mapping` must be `None`) — deliberately, since it targets the identical
`mac_array`/`mac_array_coarse` design, not a separate one.

Unlike `evaluator/rtl`, no recompilation per shape: `mac_array_coarse.cpp`'s B/C/K/LANES are
runtime arguments, not Verilog compile-time parameters — the binary is built once per process
(`_ensure_binary`, cached) and reused across every shape. That's the actual value of a coarse-
grain model: not just a faster *simulation*, but a faster *iteration loop*.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

from typing import Any

from flux_evaluator_abi.tools import tails, ToolSource, build_step
from flux_evaluator_abi import Escalation, escalation_stage

# The check sequence and the Result both harness backends assemble live here now (D467);
# what is left below is this backend's own words, its SystemC build and its run.
from flux_evaluator_rtl import generate_test_vectors
from flux_evaluator_rtl.mac_array import MacArrayHarness

_REFERENCE_DIR = Path(__file__).resolve().parent / "reference"
_RESULT_RE = re.compile(r"^RESULT (PASS|FAIL) cycles=(\d+)(?: errors=(\d+))?", re.MULTILINE)


class SystemCEvaluator(MacArrayHarness):
    """Runs a real, compiled SystemC simulation of `mac_array_coarse.cpp` against a translated
    Workload/Architecture IR pair — the coarse-grain stage above analytic estimates and below
    `evaluator/rtl`'s cycle-accurate Verilator simulation.
    """

    name = "systemc"
    translates: frozenset[str] = frozenset()   # one fixed schedule, compute width only (D440)

    def __init__(self, *, timeout_s: float = 60.0, seed: int = 0) -> None:
        self.timeout_s = timeout_s
        self.seed = seed
        self._source = ToolSource("systemc", self._build)      # compiled once per process (D437)

    #: What this backend calls itself in a refusal, and what its Result carries (D467).
    checker_version = "systemc-coarse-self-check-v0.1"
    provenance_name = "systemc-coarse@mac_array-v0.1"
    reference_dir = _REFERENCE_DIR

    def _refuse_workload(self) -> str:
        return ("SystemCEvaluator v0.1 requires an inline Workload IR dict as "
                "Candidate.workload (no result-store hash resolution yet).")

    def _refuse_mapping(self) -> str:
        return ("SystemCEvaluator v0.1 does not translate Mapping IR: mac_array_coarse models "
                "the same single, fixed loop schedule as evaluator/rtl's mac_array.sv, not a "
                "configurable one \u2014 leave Candidate.mapping as None.")

    def _refuse_no_einsum(self, workload_id: Any) -> str:
        return (f"workload {workload_id!r} has no 'einsum' ops; SystemCEvaluator cannot "
                "simulate data_dependent or compute_kernel ops (docs/decisions.md D1).")

    def _refuse_many_einsum(self, workload_id: Any, count: int) -> str:
        return (f"workload {workload_id!r} has {count} einsum ops; SystemCEvaluator v0.1 "
                "evaluates exactly one op per call, same limit as evaluator/rtl/zigzag/"
                "timeloop.")

    def _refuse_arch(self) -> str:
        return ("SystemCEvaluator v0.1 only accepts Candidate.arch as None or an inline "
                "Architecture IR dict (translated via evaluator/rtl's "
                "architecture_translator.py).")

    def _refuse_ragged(self, k: int, lanes: int) -> str:
        return (f"K={k} is not a multiple of LANES={lanes}; mac_array_coarse has no support "
                "for a ragged final K-group (same limit as evaluator/rtl).")

    def _run(self, shape: dict[str, int], lanes: int,
             workload_hash: str) -> tuple[int, bool, int]:
        return self._run_systemc(shape, lanes, workload_hash)

    def _escalation(self, ok: bool) -> Escalation:
        """This backend is a pre-check: a PASS is worth confirming on the cycle-accurate one,
        a failure is already an answer (D448's checked stage name)."""
        return Escalation(
            recommended=False,
            next_stage=escalation_stage("rtl"),     # a registry name, checked (D448)
            reason=("coarse-grain pre-check only; escalate to evaluator/rtl for a "
                    "cycle-accurate, independently-simulated confirmation" if ok else None),
        )

    def _ensure_binary(self) -> Path:
        """Compile mac_array_coarse.cpp once per process and cache the binary — unlike
        evaluator/rtl, shape parameters are runtime arguments, so one build serves every shape.
        """
        return self._source.ensure()

    def _build(self, work_dir: Path) -> Path:
        binary_path = work_dir / "mac_array_coarse"
        build_step(["g++", "-std=c++17", "-O2", "-o", str(binary_path),
                    str(_REFERENCE_DIR / "mac_array_coarse.cpp"), "-lsystemc", "-lm"],
                   cwd=work_dir, what="SystemC build", timeout_s=self.timeout_s, expect=binary_path)
        return binary_path


    def _run_systemc(
        self, shape: dict[str, int], lanes: int, workload_hash: str
    ) -> tuple[int, bool, int]:
        binary_path = self._ensure_binary()
        with tempfile.TemporaryDirectory(prefix=f"flux-systemc-{workload_hash[:12]}-") as tmp:
            work = Path(tmp)
            i_hex, w_hex, o_hex = generate_test_vectors(shape, self.seed)
            (work / "i_mem.hex").write_text(i_hex)
            (work / "w_mem.hex").write_text(w_hex)
            (work / "expected.hex").write_text(o_hex)

            sim_proc = subprocess.run(
                [
                    str(binary_path),
                    str(shape["B"]), str(shape["C"]), str(shape["K"]), str(lanes),
                    str(work),
                ],
                capture_output=True, text=True, timeout=self.timeout_s,
            )
            match = _RESULT_RE.search(sim_proc.stdout)
            if not match:
                raise RuntimeError(
                    "Could not find a 'RESULT PASS/FAIL cycles=N' line in simulation output.\n"
                    f"{tails(sim_proc)}"
                )
            status, cycles_str, errors_str = match.groups()
            return int(cycles_str), status == "PASS", int(errors_str) if errors_str else 0

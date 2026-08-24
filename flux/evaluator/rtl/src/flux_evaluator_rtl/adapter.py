"""RTL-sim backend adapter implementing the Flux Evaluator ABI (docs/evaluator-abi.md, docs/calibration.md's
escalation stage) via a real Verilator simulation of a hand-written mac_array.sv.

Hand-written, not a configurable generator: proving the ABI integration end-to-end on something
small and controllable first, before pointing an adapter at a large third-party RTL generator
(e.g. Gemmini) — the same build-vs-reuse discipline evaluator/zigzag's and
evaluator/timeloop's own module docstrings apply to their own scope decisions
(docs/decisions.md D2).

v0.1 status: `Candidate.workload` — a single two-operand `einsum` op, the same class
evaluator/zigzag and evaluator/timeloop accept (workload_translator.py). `Candidate.arch` —
`None` (LANES=8, matching the reference RTL's own verification run) or an inline Architecture IR
document with exactly one compute dim (architecture_translator.py), whose size becomes LANES; K
(from the workload) must be an exact multiple of it — mac_array.sv has no ragged-final-group
support. `Candidate.mapping` must be `None`: this hand-written RTL has a single fixed loop
schedule (temporal kg/c/b, LANES-wide spatial MACs — see mac_array.sv's own docstring), not a
configurable one the way evaluator/zigzag's and evaluator/timeloop's mapping_translator.py
modules are.

Functional correctness is checked for real, every run: mac_array.sv's testbench self-compares
against a Python-computed reference GEMM over synthetic data (Workload IR carries shapes/dtypes,
not values, and this design's cycle count is entirely data-independent — a fixed schedule, no
data-dependent control flow — so the data only exists to give the simulation something to
verify against, not because it affects timing; confirmed empirically, not assumed, by running
the same shape with different seeds during development and observing identical cycle counts).
`Result.validity.ok` reflects this real check — unlike evaluator/zigzag's and
evaluator/timeloop's `ok=True` placeholder (neither has an independent checker yet), this
adapter's `checker_version` names a real one.
"""

from __future__ import annotations

import random
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from flux_evaluator_abi.tools import tails

# The ABI types the shared body assembles a Result from live in `mac_array.py` now (D467);
# what is left here is this backend's own words, its Verilator run and its test vectors.
from .mac_array import MacArrayHarness

_REFERENCE_DIR = Path(__file__).resolve().parent / "reference"
_RESULT_RE = re.compile(r"^RESULT (PASS|FAIL) cycles=(\d+)(?: errors=(\d+))?", re.MULTILINE)
_DATA_MAGNITUDE = 4  # keeps int32 accumulator sums well within the int16 output width


def _to_hex(value: int, width_bits: int) -> str:
    mask = (1 << width_bits) - 1
    return format(value & mask, f"0{width_bits // 4}x")


def generate_test_vectors(shape: dict[str, int], seed: int) -> tuple[str, str, str]:
    """Synthetic, deterministic (seeded) int8 test data for mac_array.sv's testbench — see
    module docstring for why the actual values don't affect the measured cycle count.

    Public (not `evaluator/rtl`-private) because `evaluator/systemc`'s coarse-grain model
    self-checks against the exact same golden reference — both adapters must agree on what
    "correct" means for the identical (workload, architecture) shape, not maintain two
    independently-computed references that could silently drift apart.
    """
    b_size, c_size, k_size = shape["B"], shape["C"], shape["K"]
    rng = random.Random(seed)
    operand_i = [[rng.randint(-_DATA_MAGNITUDE, _DATA_MAGNITUDE) for _ in range(c_size)] for _ in range(b_size)]
    operand_w = [[rng.randint(-_DATA_MAGNITUDE, _DATA_MAGNITUDE) for _ in range(k_size)] for _ in range(c_size)]
    operand_o = [
        [sum(operand_i[b][c] * operand_w[c][k] for c in range(c_size)) for k in range(k_size)]
        for b in range(b_size)
    ]

    i_hex = "\n".join(_to_hex(operand_i[b][c], 8) for b in range(b_size) for c in range(c_size))
    w_hex = "\n".join(_to_hex(operand_w[c][k], 8) for c in range(c_size) for k in range(k_size))
    o_hex = "\n".join(_to_hex(operand_o[b][k], 16) for b in range(b_size) for k in range(k_size))
    return i_hex + "\n", w_hex + "\n", o_hex + "\n"


class RTLEvaluator(MacArrayHarness):
    """Runs a real Verilator simulation of the vendored mac_array.sv against a translated
    Workload/Architecture IR pair.
    """

    name = "rtl"
    translates: frozenset[str] = frozenset()   # one fixed schedule, compute width only (D440)

    def __init__(self, *, timeout_s: float = 120.0, seed: int = 0) -> None:
        self.timeout_s = timeout_s
        self.seed = seed

    #: What this backend calls itself in a refusal, and what its Result carries (D467).
    checker_version = "rtl-testbench-self-check-v0.1"
    provenance_name = "rtl-verilator@mac_array-v0.1"
    reference_dir = _REFERENCE_DIR

    def _refuse_workload(self) -> str:
        return ("RTLEvaluator v0.1 requires an inline Workload IR dict as Candidate.workload "
                "(no result-store hash resolution yet).")

    def _refuse_mapping(self) -> str:
        return ("RTLEvaluator v0.1 does not translate Mapping IR: mac_array.sv is a single, "
                "fixed hand-written loop schedule, not a configurable one \u2014 leave "
                "Candidate.mapping as None.")

    def _refuse_no_einsum(self, workload_id: Any) -> str:
        return (f"workload {workload_id!r} has no 'einsum' ops; RTLEvaluator cannot simulate "
                "data_dependent or compute_kernel ops (docs/decisions.md D1).")

    def _refuse_many_einsum(self, workload_id: Any, count: int) -> str:
        return (f"workload {workload_id!r} has {count} einsum ops; RTLEvaluator v0.1 evaluates "
                "exactly one op per call (no multi-layer workloads yet \u2014 see "
                "evaluator/zigzag and evaluator/timeloop for the analogous limit).")

    def _refuse_arch(self) -> str:
        return ("RTLEvaluator v0.1 only accepts Candidate.arch as None or an inline "
                "Architecture IR dict (translated via architecture_translator.py).")

    def _refuse_ragged(self, k: int, lanes: int) -> str:
        return (f"K={k} is not a multiple of LANES={lanes}; mac_array.sv has no support for a "
                "ragged final K-group.")

    def _run(self, shape: dict[str, int], lanes: int,
             workload_hash: str) -> tuple[int, bool, int]:
        return self._run_verilator(shape, lanes, workload_hash)

    def _run_verilator(
        self, shape: dict[str, int], lanes: int, workload_hash: str
    ) -> tuple[int, bool, int]:
        with tempfile.TemporaryDirectory(prefix=f"flux-rtl-{workload_hash[:12]}-") as tmp:
            work = Path(tmp)
            i_hex, w_hex, o_hex = generate_test_vectors(shape, self.seed)
            (work / "i_mem.hex").write_text(i_hex)
            (work / "w_mem.hex").write_text(w_hex)
            (work / "expected.hex").write_text(o_hex)

            # -j 1, not 0 (auto-detect cores): -j 0's parallel build combined with --timing hits
            # a real Verilator 5.020 bug ("Internal Error: attempted to destroy locked Thread
            # Pool") on ~50% of runs, reproduced standalone outside this adapter. -j 1 was clean
            # over 17 consecutive runs; the slower single-threaded build is worth the reliability.
            build_proc = subprocess.run(
                [
                    "verilator", "--binary", "--build", "--timing",
                    "-Wall", "-Wno-DECLFILENAME", "-j", "1",
                    f"-GB={shape['B']}", f"-GC={shape['C']}", f"-GK={shape['K']}", f"-GLANES={lanes}",
                    str(_REFERENCE_DIR / "testbench.sv"), str(_REFERENCE_DIR / "mac_array.sv"),
                    "--top-module", "testbench",
                ],
                capture_output=True, text=True, cwd=work, timeout=self.timeout_s,
            )
            if build_proc.returncode != 0:
                raise RuntimeError(
                    f"Verilator build failed (exit={build_proc.returncode}).\n"
                    f"{tails(build_proc)}"
                )

            sim_binary = work / "obj_dir" / "Vtestbench"
            sim_proc = subprocess.run(
                [str(sim_binary)],
                capture_output=True, text=True, cwd=work, timeout=self.timeout_s,
            )
            match = _RESULT_RE.search(sim_proc.stdout)
            if not match:
                raise RuntimeError(
                    "Could not find a 'RESULT PASS/FAIL cycles=N' line in simulation output.\n"
                    f"{tails(sim_proc, stderr=False)}"
                )
            status, cycles_str, errors_str = match.groups()
            return int(cycles_str), status == "PASS", int(errors_str) if errors_str else 0

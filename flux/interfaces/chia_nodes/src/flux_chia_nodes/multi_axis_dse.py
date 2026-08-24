"""`flux_agentic_multi_axis_dse` — a new CHIA flow (docs/decisions.md D34), not another
sequential single-axis loop like `flux_agentic_dse_loop`'s five axes: this one dispatches three
*independent* agentic axis searches (architecture_width, memory_size, noc_topology) as real,
concurrent Ray tasks via `.chia_remote()` — the first genuine use of CHIA's distributed dispatch
for this repo's DSE loop family. Every existing composed CHIA node in `dse_loop.py`/`agentic.py`
calls its sub-steps in-process, deliberately ("this call is already the unit of dispatch") — this
one doesn't, on purpose, because there's a real reason to: three genuinely independent searches
with no data dependency between them are exactly the shape Ray's concurrent dispatch is for.

After all three return, the two that share an evaluator family (`architecture_width` and
`memory_size` both vary `compute_memory_arch`, evaluable by the same ZigZag/Timeloop-style
backend) are composed into one candidate and evaluated for real — checking whether two
*independently, blindly* optimized axes (each search never sees what the other axis found; both
start from the same shared baseline arch) land on the same answer as `AgenticJointStrategy`'s own
*coordinated* search over the combined space already found (docs/decisions.md D26/D28: width=32,
size_kb=1.25, 193018.0081255918 pJ) — a real, checkable question about whether separable
per-axis optimization actually matches joint optimization for this workload, not assumed either
way.

`noc_topology`'s winner is reported alongside, honestly *not* merged into the same composite
`Result`: no evaluator in this repo spans both a compute+memory hierarchy and a real
`interconnect.noc` block at once (ZigZag/Timeloop's translators only read `hierarchy`; Booksim2/
Noxim's only read `interconnect.noc`) — checked directly against
`ir/architecture/examples/*.yaml` (none of the ten existing examples has both a compute node and
real NoC `dimensions`), not assumed. Forcing a fake merged number would misrepresent what was
actually evaluated; reporting three real, separately-grounded results is the honest shape here.
"""

from __future__ import annotations

from flux_llm import default_local_model
import copy
import time
from dataclasses import dataclass
from typing import Any

from chia.base.ChiaFunction import ChiaFunction, get
from flux_cli.registry import make_evaluator
from flux_evaluator_abi import Budget, Candidate, Result
from flux_search_agentic import (
    AgenticArchitectureSearchReport,
    AgenticMemorySearchReport,
    AgenticNocSearchReport,
)

from .agentic import (
    flux_agentic_architecture_search,
    flux_agentic_memory_search,
    flux_agentic_noc_search,
)

_DEFAULT_LLM_MODEL = default_local_model()  # same default every sibling module in this package uses


def _compose_width_and_memory(
    base_arch: dict[str, Any], array_dim: str, width: int, level: str, size_kb: float
) -> dict[str, Any]:
    """Applies `architecture_width`'s winning width and `memory_size`'s winning size_kb to one
    fresh copy of `base_arch` — two independent mutations (a compute node's spatial dim vs. a
    named memory-hierarchy level's size_kb, the same fields `candidates.py`/`memory_candidates.py`
    each vary on their own), composed here since nothing upstream ever combines them. Whether the
    *evaluated result* of composing them matches what a coordinated joint search finds is exactly
    what this module exists to check — composing the IR itself is mechanical, not the interesting
    part.
    """
    arch = copy.deepcopy(base_arch)
    compute_node = next(n for n in arch["hierarchy"] if n.get("class") == "compute")
    compute_node["attrs"]["dims"][array_dim] = width
    mem_node = next(n for n in arch["hierarchy"] if n.get("level") == level)
    mem_node["attrs"]["size_kb"] = size_kb
    arch["id"] = f"{base_arch.get('id', 'arch')}-composite-width{width}-size{size_kb}"
    return arch


@dataclass(frozen=True, slots=True)
class MultiAxisDSEReport:
    """Three independent agentic searches, dispatched concurrently, plus a composed-and-checked
    result for the two that share an evaluator family. `dispatch_wall_clock_s` is the real,
    measured wall-clock for all three searches together — proving real concurrency happened (vs.
    a sequential fallback) isn't this field alone; it needs a same-run sequential baseline to
    compare against, which is exactly what
    `tests/integration/test_chia_flux_agentic_multi_axis_dse_live.py` measures directly, rather
    than this report fabricating a "what sequential would have cost" number it has no way to
    know without actually running that baseline too.
    """

    width_report: AgenticArchitectureSearchReport
    memory_report: AgenticMemorySearchReport
    noc_report: AgenticNocSearchReport
    composite_arch: dict[str, Any] | None
    composite_result: Result | None
    composite_error: str | None
    composite_metric: str
    dispatch_wall_clock_s: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "width_report": self.width_report.to_dict(),
            "memory_report": self.memory_report.to_dict(),
            "noc_report": self.noc_report.to_dict(),
            "composite_arch": self.composite_arch,
            "composite_result": self.composite_result.to_dict() if self.composite_result else None,
            "composite_error": self.composite_error,
            "composite_metric": self.composite_metric,
            "dispatch_wall_clock_s": self.dispatch_wall_clock_s,
        }


@ChiaFunction()
def flux_agentic_multi_axis_dse(
    workload: dict[str, Any],
    compute_memory_arch: dict[str, Any],
    noc_arch: dict[str, Any],
    compute_memory_backend: str,
    noc_backend: str,
    *,
    valid_widths: list[int],
    memory_level: str,
    valid_sizes_kb: list[float],
    valid_noc_variants: list[tuple[str, list[int]]],
    composite_metric: str = "energy_pj",
    max_iterations: int | None = None,
    seed: int = 0,
    llm_model: str = _DEFAULT_LLM_MODEL,
) -> MultiAxisDSEReport:
    """Dispatch `architecture_width`, `memory_size`, and `noc_topology` agentic searches as three
    real, concurrent Ray tasks (`.chia_remote()`, all three submitted before any `get()` — the
    real distributed-dispatch shape, not a sequential loop that happens to return three reports).
    `compute_memory_arch` needs a single compute node and a `memory_level`-named memory node
    (e.g. `ir/architecture/examples/simple-npu-1d-v1.yaml`); `noc_arch` needs a real
    `interconnect.noc` block (e.g. `noc-mesh-2d-v1.yaml`) — two separate base architectures, not
    one, because no existing example (or evaluator) here spans both (see module docstring).
    """
    t0 = time.monotonic()

    width_ref = flux_agentic_architecture_search.chia_remote(
        workload, compute_memory_arch, compute_memory_backend,
        valid_widths=valid_widths, metric="latency_cycles", minimize=True,
        max_iterations=max_iterations, seed=seed, llm_model=llm_model,
    )
    memory_ref = flux_agentic_memory_search.chia_remote(
        workload, compute_memory_arch, compute_memory_backend,
        level=memory_level, valid_sizes_kb=valid_sizes_kb, metric="energy_pj", minimize=True,
        max_iterations=max_iterations, seed=seed, llm_model=llm_model,
    )
    noc_ref = flux_agentic_noc_search.chia_remote(
        workload, noc_arch, noc_backend,
        valid_variants=valid_noc_variants, metric="latency_cycles", minimize=True,
        max_iterations=max_iterations, seed=seed, llm_model=llm_model,
    )
    # All three refs exist before this line — the three Ray tasks are already running
    # concurrently. get() on a list is a single ray.get() call underneath (confirmed by reading
    # chia.base.ChiaFunction.get's real source: a thin wrapper over ray.get, which accepts refs
    # from any remote function, not just the same one), so this blocks once for the slowest of
    # the three, not three times.
    width_report, memory_report, noc_report = get([width_ref, memory_ref, noc_ref])

    dispatch_wall_clock_s = time.monotonic() - t0

    composite_arch: dict[str, Any] | None = None
    composite_result: Result | None = None
    composite_error: str | None = None
    if width_report.best is not None and memory_report.best is not None:
        composite_arch = _compose_width_and_memory(
            compute_memory_arch,
            width_report.best.array_dim, width_report.best.width,
            memory_report.best.level, memory_report.best.size_kb,
        )
        evaluator = make_evaluator(compute_memory_backend)
        try:
            composite_result = evaluator.evaluate(
                Candidate(workload=workload, arch=composite_arch, mapping=None),
                Budget(), frozenset({composite_metric}),
            )
        except ValueError as exc:
            # Every adapter's NotExpressibleError subclasses ValueError (docs/evaluator-abi.md) —
            # the composed candidate is a real point neither individual search ever evaluated, so
            # it can fail an evaluator's own constraints (e.g. a real zigzag-dse bug on certain
            # spatial splits) even though both of its inputs individually succeeded. Reported
            # honestly, not silently treated as "no composite exists."
            composite_error = str(exc)
    else:
        composite_error = (
            "width_report.best or memory_report.best is None — at least one axis search found "
            "no valid candidate, so there is nothing to compose (see each sub-report for why)."
        )

    return MultiAxisDSEReport(
        width_report=width_report, memory_report=memory_report, noc_report=noc_report,
        composite_arch=composite_arch, composite_result=composite_result,
        composite_error=composite_error, composite_metric=composite_metric,
        dispatch_wall_clock_s=dispatch_wall_clock_s,
    )

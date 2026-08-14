"""`flux_rtl_generate_dse` — the Verilog sibling of `systemc_dse.py`'s `flux_systemc_generate_dse`
(docs/decisions.md D45), now with a real quality signal (docs/decisions.md D47): dispatches
`flux_generate_rtl_module` as a real, independent Ray task per variant via `.chia_remote()` (same
proven-concurrent shape D34/D41 established), reports which variants came out genuinely valid,
and — for every valid one — runs real Yosys synthesis (`codegen_rtl_harness.synth`) to report a
real, comparable gate-count, closing the gap D41/D45's own decision records named directly: DSE
that could only filter for correctness, not actually compare or rank variants.

Same caller-supplied-`DesignSpec`-only discipline as `systemc_dse.py` for generation itself — see
that module's docstring for the reasoning (not repeated here). Synthesis is a separate, additive
signal computed *after* verification, over the same already-checked source — it never gates
correctness (that's still `codegen_rtl_harness.compile_and_run`'s job alone), only ranks among
variants already proven correct.
"""

from __future__ import annotations

from flux_llm import default_local_model
import time
from dataclasses import dataclass, field
from typing import Any

from chia.base.ChiaFunction import ChiaFunction, get
from flux_codegen_rtl_harness import SynthesisError, SynthesisResult, ToolResultCache, synthesize_and_measure

from .generate_rtl import GenerationResult, flux_generate_rtl_module

_DEFAULT_MODEL = default_local_model()


@dataclass(frozen=True, slots=True)
class RtlDSEReport:
    variant_ids: tuple[str, ...]
    results: tuple[GenerationResult, ...]
    valid_variant_ids: tuple[str, ...]
    dispatch_wall_clock_s: float
    synthesis_results: dict[str, SynthesisResult | None] = field(default_factory=dict)

    @property
    def all_valid(self) -> bool:
        return len(self.valid_variant_ids) == len(self.variant_ids)

    @property
    def smallest_valid_variant_id(self) -> str | None:
        """The valid variant with the fewest synthesized cells — `None` if no valid variant
        synthesized successfully (verification passing doesn't guarantee synthesis does; a real,
        separate failure mode, not assumed away)."""
        measured = {vid: r.total_cells for vid, r in self.synthesis_results.items() if r is not None}
        return min(measured, key=measured.get) if measured else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "variant_ids": list(self.variant_ids),
            "results": [r.to_dict() for r in self.results],
            "valid_variant_ids": list(self.valid_variant_ids),
            "dispatch_wall_clock_s": self.dispatch_wall_clock_s,
            "all_valid": self.all_valid,
            "synthesis_results": {
                vid: (r.to_dict() if r is not None else None) for vid, r in self.synthesis_results.items()
            },
            "smallest_valid_variant_id": self.smallest_valid_variant_id,
        }


@ChiaFunction()
def flux_rtl_generate_dse(
    variant_specs: list[dict[str, Any]],
    *,
    model: str = _DEFAULT_MODEL,
    max_repair_attempts: int = 3,
    cache_db_path: str | None = None,
) -> RtlDSEReport:
    """Generate and verify every spec in `variant_specs` as a real, independent, concurrent Ray
    task, each going through `flux_generate_rtl_module`'s own generate-verify-repair loop over
    real Verilator. See `systemc_dse.flux_systemc_generate_dse`'s docstring for the generation
    shape (this mirrors it precisely, only the underlying generation node differs).

    For every variant that verifies successfully, also runs real Yosys synthesis on its final
    source and records the result in `RtlDSEReport.synthesis_results` — a real gate-count signal
    letting a caller actually rank valid variants, not just filter them. Synthesis runs
    in-process, after the concurrent generation phase (Yosys itself is fast relative to LLM
    generation + Verilator compile, so this isn't dispatched as its own Ray task).

    `cache_db_path` (docs/decisions.md D89) opts into real, content-hash-keyed
    synthesis caching: pass the same path across calls (e.g. a repeated DSE sweep, or a variant
    whose final generated source happens to match a prior run's) and a real Yosys re-run is
    skipped for any variant whose exact final source was already synthesized. Omit it (the
    default) for the original always-real-synthesis behavior. Generation itself is never cached
    — each variant's own LLM proposal is genuinely new work, not a repeatable lookup.
    """
    if not variant_specs:
        raise ValueError("variant_specs must be non-empty — a DSE loop over zero variants explores nothing")

    t0 = time.monotonic()

    refs = [
        flux_generate_rtl_module.chia_remote(spec, model=model, max_repair_attempts=max_repair_attempts)
        for spec in variant_specs
    ]
    results = get(refs)

    dispatch_wall_clock_s = time.monotonic() - t0

    variant_ids = tuple(spec.get("id", spec.get("module_name", f"variant-{i}")) for i, spec in enumerate(variant_specs))
    valid_ids = tuple(vid for vid, r in zip(variant_ids, results) if r.success)

    synthesis_cache = ToolResultCache(cache_db_path) if cache_db_path is not None else None
    try:
        synthesis_results: dict[str, SynthesisResult | None] = {}
        for vid, spec, r in zip(variant_ids, variant_specs, results):
            if not r.success:
                continue
            try:
                synthesis_results[vid] = synthesize_and_measure(
                    r.final_source, spec["module_name"], cache=synthesis_cache,
                )
            except SynthesisError:
                # A real, separate failure mode from verification failing — Verilator accepting a
                # module doesn't guarantee Yosys's generic synth flow does. Recorded as None, not
                # silently dropped, so a caller can see synthesis was attempted and failed.
                synthesis_results[vid] = None
    finally:
        if synthesis_cache is not None:
            synthesis_cache.close()

    return RtlDSEReport(
        variant_ids=variant_ids,
        results=tuple(results),
        valid_variant_ids=valid_ids,
        dispatch_wall_clock_s=dispatch_wall_clock_s,
        synthesis_results=synthesis_results,
    )

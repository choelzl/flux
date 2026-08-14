"""`flux_systemc_generate_dse` — ties D39's harness and D40's generation node into one real
generate→verify→iterate loop over multiple design variants (docs/decisions.md D41), dispatched
as real concurrent Ray tasks via `.chia_remote()`, the same shape `multi_axis_dse.py` (D34)
already established for this repo's DSE-loop family.

**Variants are caller-supplied `DesignSpec` dicts, not LLM-invented mid-loop.** A "tweak" here is
a full alternate spec (its own `behavior` description, and — if the tweak changes the interface —
its own `ports`/`test_vectors`), not a natural-language delta the LLM interprets on the fly. This
is a deliberate consequence of D39's own founding principle, not a new one invented here: the
thing doing the checking must never be the thing being checked. If an LLM proposed *both* a new
implementation *and* the test vectors used to verify it, a wrong implementation could still "pass"
by construction. Keeping every variant's test vectors caller-authored (the same way a human or an
upstream planning step would define what a DSE loop is even exploring) preserves that guarantee
across every variant, not just the first one — matching how every other DSE loop in this repo
(`search/architecture`, `search/agentic`'s five strategies) is handed its candidate space rather
than inventing it.

**No area/power/timing comparison across variants — a real, honest scope limit, not an oversight.**
Unlike `evaluators/rtl`/`evaluators/systemc`'s fixed `mac_array` design, generated modules have no
wired-up cycle-accurate or physical evaluator; D39's harness reports pass/fail and a real VCD
trace, nothing more. This loop's only real, checkable differentiator across variants is
correctness (did it compile and pass every vector) and generation cost (how many repair attempts
it took) — reported honestly as exactly that, not dressed up as a performance comparison it can't
back.
"""

from __future__ import annotations

from flux_llm import default_local_model
import time
from dataclasses import dataclass
from typing import Any

from chia.base.ChiaFunction import ChiaFunction, get

from .generate_systemc import GenerationResult, flux_generate_systemc_module

_DEFAULT_MODEL = default_local_model()  # same default every sibling module in this package uses


@dataclass(frozen=True, slots=True)
class SystemCDSEReport:
    variant_ids: tuple[str, ...]
    results: tuple[GenerationResult, ...]
    valid_variant_ids: tuple[str, ...]
    dispatch_wall_clock_s: float

    @property
    def all_valid(self) -> bool:
        return len(self.valid_variant_ids) == len(self.variant_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "variant_ids": list(self.variant_ids),
            "results": [r.to_dict() for r in self.results],
            "valid_variant_ids": list(self.valid_variant_ids),
            "dispatch_wall_clock_s": self.dispatch_wall_clock_s,
            "all_valid": self.all_valid,
        }


@ChiaFunction()
def flux_systemc_generate_dse(
    variant_specs: list[dict[str, Any]],
    *,
    model: str = _DEFAULT_MODEL,
    max_repair_attempts: int = 3,
) -> SystemCDSEReport:
    """Generate and verify every spec in `variant_specs` as a real, independent, concurrent Ray
    task (`.chia_remote()`, all dispatched before any `get()` — proven-real-concurrency shape,
    matching `multi_axis_dse.py`'s D34 precedent), each going through D40's own real
    generate-verify-repair loop. Returns a `SystemCDSEReport` naming which variants (by their
    `DesignSpec.id`) came out genuinely valid — compiled and passed every one of their own
    test vectors — not merely "the LLM produced output".
    """
    if not variant_specs:
        raise ValueError("variant_specs must be non-empty — a DSE loop over zero variants explores nothing")

    t0 = time.monotonic()

    refs = [
        flux_generate_systemc_module.chia_remote(spec, model=model, max_repair_attempts=max_repair_attempts)
        for spec in variant_specs
    ]
    # All refs exist before this line — every variant's generate-verify-repair loop is already
    # running concurrently. get() on a list is a single ray.get() call underneath (chia.base.
    # ChiaFunction.get is a thin wrapper over ray.get, origin-agnostic across different
    # ChiaFunctions — the same fact D34 already confirmed by reading the real source), so this
    # blocks once for the slowest variant, not once per variant.
    results = get(refs)

    dispatch_wall_clock_s = time.monotonic() - t0

    variant_ids = tuple(spec.get("id", spec.get("module_name", f"variant-{i}")) for i, spec in enumerate(variant_specs))
    valid_ids = tuple(vid for vid, r in zip(variant_ids, results) if r.success)

    return SystemCDSEReport(
        variant_ids=variant_ids,
        results=tuple(results),
        valid_variant_ids=valid_ids,
        dispatch_wall_clock_s=dispatch_wall_clock_s,
    )

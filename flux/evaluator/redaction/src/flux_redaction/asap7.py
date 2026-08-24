"""Real redaction of `flux_codegen_rtl_harness.asap7.Asap7SynthesisResult` (docs/decisions.md
D93) — the concrete, freshest real PDK-derived output this repo has (D92), and G15's own
motivating case: "Proprietary PDKs and IP cannot be sent to public frontier models." ASAP7 itself
is real, BSD-3-Clause, not actually confidential — this module's real value is proving the
mechanism against real, physically meaningful numbers, ready for a genuinely confidential
commercial PDK's own synthesis output the day this repo ever has one (same shape, same real
`Asap7SynthesisResult`-adjacent structure any liberty-based synthesis produces).

`sequential_area_um2`/`sequential_fraction` (already a real, dimensionless ratio, not a raw
physical quantity) are kept as-is in the redacted view — the "normalized metrics" strategy G15's
own fix names directly, not a lapse. `area_um2` (the real, absolute, PDK-derived physical
quantity) is the one field this module exists to never let through unredacted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .core import RankedCandidate, RelativeDelta, redact_ranking, redact_relative

if TYPE_CHECKING:
    from flux_codegen_rtl_harness import Asap7SynthesisResult


@dataclass(frozen=True, slots=True)
class RedactedAsap7Result:
    """No `area_um2` field, anywhere — structurally, not conventionally, non-leaking (see
    `core.py`'s own module docstring). `sequential_fraction` is real and kept, a genuinely
    different kind of quantity (already a ratio) from the real absolute area this type exists to
    withhold.
    """

    area: RelativeDelta
    sequential_fraction: float

    def to_dict(self) -> dict:
        return {"area": self.area.to_dict(), "sequential_fraction": self.sequential_fraction}


def redact_asap7_result(
    result: "Asap7SynthesisResult", baseline: "Asap7SynthesisResult",
) -> RedactedAsap7Result:
    """Real relative-delta redaction of a real ASAP7 synthesis result against a real baseline
    (e.g. the reference architecture's own real synthesis) — see `core.redact_relative`. Lower
    `area_um2` is always the real, physically better outcome (smaller real silicon), so
    `minimize=True` unconditionally, not caller-configurable — there's no real sense in which a
    larger real chip area would ever be the redaction-layer's own "better" direction.
    """
    return RedactedAsap7Result(
        area=redact_relative(result.area_um2, baseline.area_um2, minimize=True),
        sequential_fraction=result.sequential_fraction,
    )


def redact_asap7_ranking(
    candidates: list[tuple[str, "Asap7SynthesisResult"]],
) -> list[RankedCandidate]:
    """Real rank-ordering redaction across multiple real ASAP7 synthesis results — see
    `core.redact_ranking`. Smaller real area always ranks better."""
    return redact_ranking([(cid, r.area_um2) for cid, r in candidates], minimize=True)

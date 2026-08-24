# redaction/ — real redaction layer between evaluator outputs and model context

docs/gap-analysis.md G15, closed for real (docs/decisions.md D93/D94): "Proprietary PDKs and IP
cannot be sent to public frontier models... Fix: a redaction layer between evaluator outputs and
model context (normalized metrics, rank orderings, relative deltas instead of absolute numbers)."

See [docs/gap-analysis.md](../docs/gap-analysis.md), [docs/decisions.md D93](../docs/decisions.md),
and [docs/decisions.md D94](../docs/decisions.md).

## What's real

`core.py` — two real, generic mechanisms, over plain `float` values, not tied to any one
evaluator's own result type:

- **`redact_relative(value, baseline_value, minimize=True) -> RelativeDelta`** — the real
  "relative deltas instead of absolute numbers" strategy the gap's own fix names. Returns a real
  `(value - baseline) / baseline` fraction plus a real, derived `better_than_baseline` fact —
  never the two real absolute numbers it was computed from.
- **`redact_ranking(candidates, minimize=True) -> list[RankedCandidate]`** — the real "rank
  orderings ... instead of absolute numbers" strategy. Sorts real candidates by their real value,
  returns only `(candidate_id, rank)` pairs.

**Structurally, not conventionally, non-leaking**: `RelativeDelta`/`RankedCandidate` have no
field that could ever hold a real absolute value — a caller holding only one of these objects
cannot recover the real number it was computed from, by construction, not by policy or
convention. Verified directly: `dataclasses.fields()` on both real types, asserting no field name
or value resembles the real absolute inputs used to construct them.

`asap7.py` — the concrete, wired application against this repo's own real PDK-derived data
(docs/decisions.md D92, `flux_codegen_rtl_harness.asap7.Asap7SynthesisResult`): `redact_asap7_
result`/`redact_asap7_ranking` redact real `area_um2` (the real, physical, PDK-derived quantity)
via the two mechanisms above. `sequential_fraction` (already a real, dimensionless ratio, not a
raw physical quantity) is kept as-is in the redacted view — the "normalized metrics" strategy the
gap's own fix names directly, not an oversight.

**ASAP7 itself is real, BSD-3-Clause, not actually confidential** — this module's real value is
proving the mechanism against real, physically meaningful numbers, ready for a genuinely
confidential commercial PDK's own synthesis output the day this repo ever has one (real,
structurally-identical output shape any liberty-based synthesis produces).

Wired through the agent-facing surface this whole gap is actually about:
`flux_synthesize_with_asap7_redacted` (`flows/chia_nodes/`) and its MCP tool — the real,
end-to-end demonstration that an agent asking for a redacted comparison never receives the real
absolute `area_um2` anywhere in the response, only a real relative delta and a real rank.

`policy.py` (docs/decisions.md D94) — the real remaining piece after D93: a redacted surface
existing doesn't stop a caller from reaching for the raw one instead. `register_pdk`/
`is_confidential`/`require_not_confidential` — a real, explicit registry (`asap7` registered
`confidential=False`, checked directly against its own upstream `LICENSE`, D92) — and
`flux_synthesize_with_asap7` (the *raw*, unredacted node) now calls `require_not_confidential`
before returning anything: a real, structural refusal (`ConfidentialPdkError`) if a PDK is ever
registered confidential, not a policy a caller has to remember to honor.
`flux_synthesize_with_asap7_redacted` makes no such call — a redacted comparison is always safe,
confidential PDK or not, by construction. Verified end to end, not just as an isolated policy
primitive: a real test temporarily, synthetically re-registers `asap7` as confidential and
confirms the *actual* `flux_synthesize_with_asap7` CHIA node genuinely refuses, then restores the
real registration.

## Not implemented

Redaction for the generic Evaluator-ABI `Result` type (ZigZag/Timeloop's own `latency_cycles`/
`energy_pj`) — deliberately out of scope: those are derived from this repo's own open-source
analytic cost models, not from a real proprietary PDK, so they're not the real confidentiality
concern G15 names. `core.py`'s own mechanisms are generic enough to redact any real evaluator's
metrics the day a genuinely confidential one exists; only the concrete ASAP7 adapter and its
CHIA/MCP wiring were built here. No real confidential PDK exists in this sandbox to register as
`confidential=True` for real (D92's own finding, unchanged) — `policy.py`'s own tests exercise the
real enforcement mechanism against a synthetic, clearly-labeled test registration, not fabricated
confidential silicon data.

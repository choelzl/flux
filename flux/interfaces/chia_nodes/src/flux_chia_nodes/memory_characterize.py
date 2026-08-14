"""`flux_characterize_memory_level` — the missing glue between this repo's real, multi-level
Architecture IR documents and `evaluators/cacti`'s own single-macro contract (docs/decisions.md
D37). Every real architecture example here (`simple-npu-1d-v1.yaml`, `generic-riscv-soc-v1.yaml`,
...) has *multiple* `class=='memory'` hierarchy nodes (e.g. `dram` and `gbuf`), but
`CactiEvaluator` requires *exactly one* (CACTI characterizes a single physical macro, not a
memory system, D36) — so characterizing "just the gbuf level of this real SoC" currently means
hand-extracting it into a standalone arch dict first, exactly the boilerplate every one of
`evaluators/cacti`'s own tests already does by hand (`_gbuf_only_arch` in
`tests/integration/test_cacti_adapter_live.py`). This node makes that a real, reusable one-call
capability instead of copy-pasted test helper logic.

Deliberately *not* a conformance check or a merge with any other evaluator's `energy_pj`: CACTI's
`energy_pj` is a single memory *access*'s energy; ZigZag's/Timeloop's `energy_pj` is the whole
*workload*'s energy across every access plus compute — different quantities that happen to share
a metric name and unit. Conflating them would be comparing apples to oranges, not a real
conformance check. This node reports CACTI's real, standalone physical characterization of one
macro — genuinely useful on its own (docs/decisions.md D36's whole point), not force-fit into an
existing cross-evaluator comparison it doesn't actually support.

**Real incremental, dependency-tracked re-evaluation** (docs/decisions.md D79, docs/gap-analysis.md
G9's last open piece): `minimal_arch` below — already built, before this decision, purely to give
`backend`'s adapter a single-node document it can accept — turns out to *already be* the real
narrowed dependency this node's own computation actually has: CACTI characterizes exactly the one
named memory level and nothing else about `arch`, so two callers' full, multi-level architectures
that differ *anywhere outside* the named `level` produce byte-identical `minimal_arch` documents.
Passing an optional `store` wraps `evaluator` in `flux_store.CachingEvaluator` around *this
already-reduced* `Candidate`, not the caller's full `arch` — so a cache lookup here is keyed on
what CACTI actually reads, not on the full document a caller happens to pass. Changing an
unrelated hierarchy level (a different `pe_array` width, a different `dram` size, ...) is now a
real, provable no-op: the second call is a genuine cache hit, no real CACTI re-run, without any
new dependency-tracking machinery — `CachingEvaluator` (D19) already did all the real work; this
decision's only real contribution is calling it on the *reduced* Candidate that already existed.
"""

from __future__ import annotations

import copy
from typing import Any

from chia.base.ChiaFunction import ChiaFunction
from flux_cli.registry import make_evaluator
from flux_evaluator_abi import Budget, Candidate, Evaluator, Result
from flux_store import CachingEvaluator, ResultStore

_NO_WORKLOAD = {"schema_version": "0.1.0", "id": "memory-characterization/no-workload", "tensors": [], "ops": []}


@ChiaFunction()
def flux_characterize_memory_level(
    arch: dict[str, Any],
    level: str,
    word_width_bits: int,
    *,
    backend: str = "cacti",
    metrics: list[str] | None = None,
    store: ResultStore | None = None,
) -> Result:
    """Extract the named `class=='memory'` hierarchy level from `arch` (which may have several —
    every other level is dropped, not just ignored, since `backend`'s adapter may reject a
    multi-node hierarchy outright, as `evaluators/cacti` does), inject `word_width_bits` (a real
    physical property `size_kb` alone can't determine — see `evaluators/cacti/README.md`'s module
    docstring — that none of this repo's real examples carry yet), and characterize the result
    through `backend` (default `"cacti"`, but any single-macro-capable backend works the same
    way).

    `metrics=None` (the default) returns *every* metric `backend`'s adapter reports for this
    macro, not the generic cross-evaluator `DEFAULT_METRICS` baseline (`latency_cycles`/
    `energy_pj`) `flux_evaluate`/`flux_check_validity` default to — `evaluators/cacti` doesn't
    report `latency_cycles` at all and its two most interesting numbers, `area_mm2` and
    `power_w`, aren't in that baseline, so reusing it here would silently drop them.

    `store`, if given (docs/decisions.md D79), enables real incremental re-evaluation: a second
    call whose `arch` differs from a prior call's *only outside* the named `level` (or is a
    repeat of the exact same request) is served from `store` with no real evaluator call — see
    this module's own docstring for why the existing `minimal_arch` reduction already makes this
    correct, not merely fast.
    """
    node = next(
        (n for n in arch.get("hierarchy", []) if n.get("level") == level and n.get("class") == "memory"),
        None,
    )
    if node is None:
        raise ValueError(
            f"architecture {arch.get('id', '<no id>')!r} has no class=='memory' hierarchy node "
            f"named {level!r}."
        )
    node_copy = copy.deepcopy(node)
    node_copy.setdefault("attrs", {})["word_width_bits"] = word_width_bits
    minimal_arch = {
        "schema_version": arch.get("schema_version", "0.1.0"),
        "id": f"{arch.get('id', 'arch')}-{level}-only",
        "tech": arch.get("tech", {}),
        "hierarchy": [node_copy],
    }

    evaluator: Evaluator = make_evaluator(backend)
    if store is not None:
        evaluator = CachingEvaluator(evaluator, store, evaluator_prefix=backend)
    requested_metrics = frozenset(metrics) if metrics else frozenset()
    return evaluator.evaluate(
        Candidate(workload=_NO_WORKLOAD, arch=minimal_arch, mapping=None), Budget(), requested_metrics
    )

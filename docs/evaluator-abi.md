# Evaluator ABI (L4)

Package: `evaluator/abi/` (the contract), `evaluator/*` (adapters). Part of
[architecture.md](architecture.md)'s layering. ★ The central contract this whole project is
organized around.

## The call

```python
Result = evaluate(
    workload : WorkloadRef,        # hash or inline
    arch     : ArchRef,
    mapping  : MappingRef | None,  # None ⇒ evaluator may choose (declares that it did)
    budget   : Budget,             # wall_clock_s, usd, fidelity_floor
    metrics  : set[Metric],        # latency, energy, area, power, edp, temp_max, ...
) -> Result
```

## The return — this shape is the contract

```python
Result:
  metrics: {latency_cycles: Estimate, energy_pj: Estimate, area_mm2: Estimate, ...}
  # Estimate = {value, ci_low, ci_high, unit, method: analytic|simulated|measured}

  validity:   {ok: bool, violations: [Constraint], checker_version: str}
  domain:     {in_domain: bool, distance: float, nearest_calibration: id}
  bottleneck: {limiter: memory|compute|noc|dependency|thermal,
               per_level_utilisation: {...},
               roofline: {ai, peak, achieved},
               top_costs: [...]}                 # structured explanation, not prose
  provenance: {evaluator: "zigzag@3.1.0", calibration: "cal-2026-07-a",
               inputs: {workload_hash, arch_hash, mapping_hash},
               seed, wall_clock_s, usd_cost}
  escalation: {recommended: bool, next_stage: "verilator", reason: "ci width > 25%"}
```

Four things here are new relative to every tool surveyed in [landscape.md](history/landscape.md):
- `Estimate` carries an interval, not a scalar.
- `domain.in_domain` says whether the model is extrapolating.
- `bottleneck` is **structured**, so both a human and an agent get *why*, not just *what*.
- `validity` is computed by an **independent checker**, not by the cost model — the primary
  anti-reward-hacking mechanism. **Real** for the scope `validity/`'s two checks cover: declared
  Architecture-IR `constraints` (`area_mm2`/`tdp_w`/... `max`/`min` bounds) and a first-principles
  compute-bound latency roofline — via the `flux_check_validity` CHIA node/MCP tool ([decisions.md
  D10](decisions.md)). Every evaluator's own self-reported
  `Validity(ok=True, checker_version="none-v0.1")` is preserved (merged with, not replaced by) the
  independent finding.

## Batch mode

```python
evaluate_batch(candidates: list[Candidate], budget) -> list[Result]
```

The loop measures a stage's candidates in one call (`measure_batch`, [D446](decisions.md);
the pool of [D525](decisions.md) fans the misses out `workers` at a time) — a
one-call-per-candidate interface would make overhead dominate. Every adapter implements it, most
as a sequential loop internally.

**Reading a metric:** an evaluator may legally return a `Result` without a metric that was
requested, so `Result.metric(name)` returns a `MetricOutcome` — the value or the reason there is
none — rather than making every caller remember a guard. `result.metrics[name]` still works and is
still a plain dict for serialisation, but a missing key now raises `MissingMetricError` (a
`KeyError` subclass) naming what the evaluator did return. Six consumers had crashed on the bare
`KeyError` before this existed ([decisions.md D168](decisions.md), [D169](decisions.md),
[D170](decisions.md), [D201](decisions.md)).

**`arch=None` is a real input:** it means "use the evaluator's own default architecture", the same
shape `mapping=None` already had ("the evaluator may choose one, and must declare that it did").
An adapter either honours it or refuses with `NotExpressibleError` — never silently substitutes an
architecture the caller didn't ask for. Measured across every registered backend: `rtl`, `systemc`,
`timeloop` and `zigzag` fall back to their own default; the other nine refuse and name the
requirement ([decisions.md D173](decisions.md), checked by
`tests/integration/test_arch_none_conformance.py`).

**Length is part of the contract:** if `evaluate_batch` returns, it returns exactly one `Result`
per candidate, in the order given. An implementation that cannot evaluate a candidate raises for
the whole batch (per-item error isolation is not required at v0.1) rather than dropping it from
the list. Callers pair results to candidates positionally, so a short list re-pairs everything
after the gap with the wrong candidate — or silently deletes candidates from the caller's view of
its own sweep. This was left implicit until [decisions.md D165](decisions.md), where the omission
produced a confidently wrong DSE winner from a sweep that reported no errors.

## Backends

| Backend | Package | Status |
|---|---|---|
| `zigzag` | `evaluator/zigzag/` | Real. Translates a two-operand einsum + N-dimensional compute array + flat mapping into native ZigZag, runs the real `zigzag-dse` PyPI package. |
| `timeloop` | `evaluator/timeloop/` | Real. Same class of einsum op via the real `timeloopaccelergy/accelergy-timeloop-infrastructure` Docker image or the hermetic nix runner ([decisions.md D206](decisions.md)); 1-D and 2-D compute arrays (D215); sparsity via Timeloop's own `densities`/`sparse_optimizations` (D78). |
| `rtl` | `evaluator/rtl/` | Real. A hand-written `mac_array.sv`, compiled/run through real Verilator, self-checked against a Python golden reference every run. The first *measured*, not analytic, evaluator. |
| `openroad` | `evaluator/openroad/` | Real. Yosys maps the candidate's derived datapath onto ASAP7 and OpenROAD places (optionally routes, D229) it — measured `area_mm2`/`power_w`/`worst_slack_ps` from placed silicon ([decisions.md D225](decisions.md)–[D230](decisions.md)). |
| `champsim_bingo` | `applications/prefetcher/evaluator/` | Real. The prefetcher study's ChampSim/Pythia adapter, registered by name like every other backend ([decisions.md D426](decisions.md)). |

## Two faces of one measurement

The ABI is the interface for a candidate **the IR can express** — a Workload IR document, an
Architecture IR document, an optional Mapping IR — which is what makes backends
interchangeable: a search can swap `zigzag` for `timeloop` because both read the same
documents. It is reached by NAME, from a problem document's `stages`
(`evaluator: openroad`, [decisions.md D430](decisions.md)), `flux eval`, a CHIA node or an MCP tool.

Four worlds here measure something else. The macarray's candidate is generated SystemVerilog
plus a clock constraint; the NLU's is an FP16 operator module; the prefetcher's is a `bingo.ini`
configuration against three traces; the interconnect mapping's is a hash and a fabric under a
cycle law. None of those is an Architecture IR document, and inventing an IR encoding for each of them purely to pass
through `evaluate` would add a translation layer between a study and its own artifact, with
nothing on the other side able to interpret it. So they call the tool wrapper directly:
`run_synthesis_flow` / `run_ppa_flow` (`evaluator/openroad`), `run_champsim`
(`applications/prefetcher/evaluator`).

**The rule, and it is a one-implementation rule** ([decisions.md D451](decisions.md)): where a
tool has both faces, the ABI adapter and the tool function call the SAME measurement code. The
adapter's job on top of it is exactly two things — translating an IR document into the tool's
inputs, and wrapping the numbers in a `Result` (method, provenance, validity, escalation). What
must never happen is a second implementation of the measurement itself, because then the same
design measured through the two faces can disagree and nothing says which number is the silicon.
`tests/unit/test_one_measurement_two_faces.py` pins it for the two tools that have both faces:
`openroad` (`OpenRoadEvaluator.evaluate` and macarray/NLU/interconnect_mapping all reach
`run_ppa_flow`/`run_synthesis_flow`) and `champsim_bingo` (`ChampSimBingoEvaluator.evaluate` and
the prefetcher's batch backend both reach `run_champsim`).

**Adapters, not forks.** Each adapter translates Flux IR to the tool's native config and parses
its output back into `Result`. Where a mapping is inexpressible, the adapter fails loudly with
`NotExpressibleError` (surfaced as `not_expressible_in` in Mapping IR's `compatibility` block) —
never silently approximates. `NotExpressibleError` is the ABI's own class
(`flux_evaluator_abi.NotExpressibleError`); every adapter's `errors.py` re-exports it, so one
`except` written against the ABI catches every backend (D426). Every adapter carries its
registry name as `.name`, and the name-to-evaluator map is the ABI's too:
`evaluator/abi/src/flux_evaluator_abi/registry.py` (`make_evaluator`, `available_evaluators`,
`register_evaluator`, `evaluator_name_for`); `flux_cli.registry` is a facade over it. A backend
must be registered there to be reachable from a task document, a CHIA node or an MCP tool
regardless of the adapter's own correctness (this repo's own real gap, found and fixed when
wiring `booksim` in — see [decisions.md D6](decisions.md)).

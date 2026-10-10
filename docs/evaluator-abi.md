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

Four things here are new relative to every existing DSE tool:
- `Estimate` carries an interval, not a scalar.
- `domain.in_domain` says whether the model is extrapolating.
- `bottleneck` is **structured**, so both a human and an agent get *why*, not just *what*.
- `validity` is the evaluator's own report: ZigZag's and Timeloop's is `Validity(ok=True, checker_version="none-v0.1")`, a placeholder. In a
  problem document, correctness is the gate's, never an evaluator's.

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
architecture the caller didn't ask for ([decisions.md D173](decisions.md)).

**Length is part of the contract:** if `evaluate_batch` returns, it returns exactly one `Result`
per candidate, in the order given. An implementation that cannot evaluate a candidate raises for
the whole batch (per-item error isolation is not required at v0.1) rather than dropping it from
the list. Callers pair results to candidates positionally, so a short list re-pairs everything
after the gap with the wrong candidate — or silently deletes candidates from the caller's view of
its own sweep. This was left implicit until [decisions.md D165](decisions.md), where the omission
produced a confidently wrong DSE winner from a sweep that reported no errors.

## Backends

Core Flux registers no backend: the registry and the loop document's evaluator stages are gone
([D954](decisions.md)). Each adapter lives in the application that uses it and is called by that
application's own scripts. ZigZag and Timeloop are owned by `applications/npu_gemm/` (its
`evaluate.py --backend zigzag|timeloop` and `measure.py`); ChampSim is owned by
`applications/prefetcher/` (its `champsim.py` or `bingo.py` commands).

| Backend | Package | Status |
|---|---|---|
| `zigzag` | `applications/npu_gemm/tools/zigzag_tools/` | Real. Translates a two-operand einsum + N-dimensional compute array + flat mapping into native ZigZag, runs the real `zigzag-dse` PyPI package. |
| `timeloop` | `applications/npu_gemm/tools/timeloop_tools/` | Real. Same class of einsum op via the real `timeloopaccelergy/accelergy-timeloop-infrastructure` Docker image or the hermetic nix runner ([decisions.md D206](decisions.md)); 1-D and 2-D compute arrays (D215); sparsity via Timeloop's own `densities`/`sparse_optimizations` (D78). |
| `champsim` | `applications/prefetcher/tools/champsim_tools/` | Real. ChampSim (Pythia) on one trace: an `.ini` of knobs for the prebuilt binary or a C++ prefetcher header built in; IPC, cycles and the L2 prefetch counters, simulated. |

## Two faces of one measurement

The ABI is the interface for a candidate **the IR can express** — a Workload IR document, an
Architecture IR document, an optional Mapping IR — which is what makes backends
interchangeable: a search can swap `zigzag` for `timeloop` because both read the same
documents. A loop document never names an adapter: every stage is a command printing
`name=value`, and the command (npu_gemm's `measure.py`) constructs the adapter itself
([D954](decisions.md)).

Several studies here measure something else. The macarray's candidate is generated
SystemVerilog plus a clock constraint; the NLU's is an FP16 operator module; the prefetcher's is
an `.ini` or a C++ header against a directory of traces; the interconnect mapping's is a hash
and a fabric under a cycle law. None of those is an Architecture IR document, and inventing an IR encoding for each of them purely to pass
through `evaluate` would add a translation layer between a study and its own artifact, with
nothing on the other side able to interpret it. So they call the tool directly: Yosys and
OpenROAD through each RTL application's own `rtl.py` (`measure`, D948), `simulate` / `measure`
(`applications/prefetcher/tools/champsim_tools`, the prefetcher's stages through `python champsim.py run` and `bingo.py measure`).

**The rule, and it is a one-implementation rule** ([decisions.md D451](decisions.md)): where a
tool has both faces, the ABI adapter and the tool function call the SAME measurement code. The
adapter's job on top of it is exactly two things — translating an IR document into the tool's
inputs, and wrapping the numbers in a `Result` (method, provenance, validity, escalation). What
must never happen is a second implementation of the measurement itself, because then the same
design measured through the two faces can disagree and nothing says which number is the silicon.
`tests/unit/test_one_measurement_two_faces.py` pins it for ChampSim: `champsim.py run`
(`study.measure`) and the no-prefetcher baseline both reach `simulate`; its ABI adapter went in
D957, as no script called it. OpenROAD has one face since D948: an RTL application's `rtl.py`.

**Adapters, not forks.** Each adapter translates Flux IR to the tool's native config and parses
its output back into `Result`. Where a mapping is inexpressible, the adapter fails loudly with
`NotExpressibleError` (surfaced as `not_expressible_in` in Mapping IR's `compatibility` block) —
never silently approximates. `NotExpressibleError` is the ABI's own class
(`flux_evaluator_abi.NotExpressibleError`); every adapter's `errors.py` re-exports it, so one
`except` written against the ABI catches every backend (D426). Every adapter carries its name as
`.name`, which goes into the `Result`'s provenance.

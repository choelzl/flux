# Measurement records (L4)

Package: `evaluator/abi/` (`flux_evaluator_abi`). Part of [architecture.md](architecture.md)'s
layering. What is left of the Evaluator ABI: the shape a measurement is recorded in, the one
launcher every tool goes through, and the fingerprints a measurement is keyed by. Nothing
evaluates through it any more: every loop stage is a command printing `name=value`
([D954](decisions.md)), and the record wraps those numbers.

## The record — `Result`

```python
Result:
  metrics: {latency_cycles: Estimate, energy_pj: Estimate, area_mm2: Estimate, ...}
  # Estimate = {value, ci_low, ci_high, unit, method: analytic|simulated|measured}

  validity:   {ok: bool, violations: [Constraint], checker_version: str}
  domain:     {in_domain: bool, distance: float, nearest_calibration: id}
  bottleneck: {limiter: memory|compute|noc|dependency|thermal|none,
               per_level_utilisation: {...},
               roofline: {ai, peak, achieved},
               top_costs: [...]}                 # structured explanation, not prose
  provenance: {evaluator, calibration, inputs: {...}, seed, wall_clock_s, usd_cost}
  escalation: {recommended: bool, next_stage, reason}
```

Every trial row of a campaign store holds one (`flux_store.campaign`, `Result.from_dict`); the
records (`flux_records`) build it from a stage's `name=value` numbers: each value an `Estimate`
with a zero-width interval, `method` analytic or simulated as the stage declares, provenance
naming the stage, `Bottleneck(limiter=NONE)` (no claim). What the fields mean:
- `Estimate` carries an interval, not a scalar, and says how the number was obtained (`Method`).
- `domain.in_domain` says whether a model is extrapolating; `metric_domains` says which metric.
- `bottleneck` is **structured** (`Limiter`, `Roofline`), so both a human and an agent get *why*,
  not just *what*.
- `validity` is the producer's own report. In a problem document, correctness is the gate's,
  never a measurement's.

**Reading a metric:** a `Result` may lack a metric that was asked for, so `Result.metric(name)`
returns a `MetricOutcome` — the value or the reason there is none — rather than making every
caller remember a guard. `result.metrics[name]` still works and is still a plain dict for
serialisation, but a missing key raises `MissingMetricError` (a `KeyError` subclass) naming what
the record does hold; `refusal_for` gives the one standard sentence ([D168](decisions.md),
[D169](decisions.md), [D170](decisions.md), [D201](decisions.md)).

## Running a tool — `run_tool`

`run_tool(cmd, cwd=, timeout_s=, ...) -> ToolRun` is the one launcher (D429): the "not on PATH"
refusal, the timeout, the output's ends (`tails`, `TAIL_CHARS`) and a task saying what ran and
how (D709). The loop's step commands and probes launch through it.

## Toolchain fingerprints

`tool_fingerprint(binary)` names the build of a tool on PATH: `nix:<store hash>` under nix,
else `version:<first --version line>`, else `path:<resolved path>` (D316).
`toolchain_fingerprint()` maps each of `MEASURING_TOOLS` (openroad, yosys, verilator) present to
its fingerprint, plus the flow recipe (D564). They key the measurement cache
(`evaluator/cache/`, D340), a stage's evidence identity (`measured_as`, D898) and a document's
judge versions (D778), so a tool upgrade measures again rather than reusing a number the old
build produced.

## Two faces of one measurement

Where a tool is reached by two routes, both call the SAME measurement code
([D451](decisions.md)); a second implementation lets the same design disagree with itself.
`tests/unit/test_one_measurement_two_faces.py` pins it for ChampSim: `champsim.py run`
(`study.measure`) and the no-prefetcher baseline both reach `simulate`. OpenROAD has one face
since D948: an RTL application's `rtl.py`.

## Removed

The backend registry and the loop document's evaluator stages ([D954](decisions.md)); ChampSim's
ABI adapter, the unused tool helpers and the preflight (D957); the `evaluate()` call itself —
the `Evaluator` protocol, `SequentialBatch`, `Candidate`, `Budget`, the `ArchRef`/`WorkloadRef`/
`MappingRef` inputs and `NotExpressibleError` — with the last adapters, ZigZag and Timeloop, and
the `npu_gemm` application that used them (D958). No backend adapter exists anywhere.

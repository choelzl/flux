# Records and measurements

Packages: `core/stores` (`flux_store`), `mentor/records` (`flux_records`), and in `core/loop`
`flux_loop.toolrun`, `flux_loop.toolchain`, `flux_loop.measure_cache`, `flux_loop.calibrate`.
Part of [architecture.md](architecture.md)'s layering.

## The record: `CampaignStore`

One SQLite file per loop ([D217](decisions.md)): `campaigns`, `trials` (each with a `result_id`
into `results`), an append-only `campaign_events` log, and content-addressed `documents` (a
campaign's objective, a library digest). One trial is one transaction, so **the database is the
checkpoint**: resuming is running ([D367](decisions.md)). Every row carries its provenance -- the
revision, the toolchain, the trace directory, the prompt's hash, the seconds and the tokens
([D510](decisions.md), [D535](decisions.md)).

**A campaign is named by its document's `id`** (a sub-document's by `<parent>/<child>`), so an
edited document resumes its record and sibling campaigns of one document find each other
([D540](decisions.md)).

- **Writing:** `flux_records.Records` -- `trial(...)` for a measured or refused candidate (each
  metric tagged analytic or measured, [D446](decisions.md)), `conclusion` for what a run inferred
  (labelled INFERENCE, kept apart from measurements, [D445](decisions.md)), the ledger's events
  ([D509](decisions.md)).
- **Reading back:** `flux_loop.records._reload` -- the proven parts, the best designs, the
  prototypes, re-verified by version ([D510](decisions.md)).
- **Other readers:** `flux report` ([D512](decisions.md)), the web's Results, the TUI's results
  tab, `flux_records.extract` (laws and duels from controlled pairs), `flux_records.mining`
  (facts with provenance), the sibling lookup, and `flux status`/`stop` through the
  registration under the trace root ([D513](decisions.md)).

A record written before D964 keeps its unused `workload_hash`/`arch_hash` columns; a writer fills
them empty rather than rewriting the table.

## A measurement: `Result`

```python
Result:
  metrics:    {name: Estimate(value, method: analytic|simulated|measured)}
  provenance: {evaluator: the stage that produced it, inputs: {stage: ...}}
```

Every stage is a command printing `name=value` ([D954](decisions.md)); `Records.trial` wraps those
numbers into a `Result` (`flux_store.result`). `method` is what the record's readers separate
predictions from measurements by. Correctness is never a measurement's: it is the gate's.
Rows of the older, wider shape (intervals, validity, domain, bottleneck, escalation) read back
with those keys ignored.

## Running a tool: `run_tool`

`run_tool(cmd, cwd=, timeout_s=, ...) -> ToolRun` (`flux_loop.toolrun`) is the one launcher
(D429): the "not on PATH" refusal, the timeout, the output's ends (`tails`, `TAIL_CHARS`) and a
task saying what ran and how (D709). The loop's step commands and probes launch through it.

## Toolchain fingerprints and the measurement cache

`tool_fingerprint(binary)` names the build of a tool on PATH: `nix:<store hash>` under nix, else
`version:<first --version line>`, else `path:<resolved path>` (D316). `toolchain_fingerprint()`
maps each of `MEASURING_TOOLS` (openroad, yosys, verilator) present to its fingerprint, plus the
flow recipe (D564).

They key **the measurement cache** (`flux_loop.measure_cache.MeasurementCache`): a JSON sidecar
beside the record, always on, keyed by the candidate's source, what the stage runs (its command,
the scripts it names, the params) and the fingerprints ([D340](decisions.md),
[D790](decisions.md)). They also key a stage's evidence identity (`measured_as`, D898) and a
document's judge versions (D778), so a tool upgrade measures again rather than reusing a number
the old build produced.

## One measurement, one implementation

Where a tool is reached by two routes, both call the same measurement code
([D451](decisions.md)); a second implementation lets one design disagree with itself.
`tests/unit/test_one_measurement_two_faces.py` pins it for ChampSim. OpenROAD has one face: an RTL
application's `rtl.py measure` ([D948](decisions.md)).

## Calibration between stages

The loop keeps its own calibration node between stages ([D464](decisions.md)): after every step
up the chain it records how far the costly stage's numbers were from the cheap stage's over the
designs both measured, and never rewrites a measurement. `bias(scored, fast=..., against=...)`
(`flux_loop.calibrate`) returns a `Bias` per metric (the ratio costly/cheap, its spread and its
count); `Bias.apply` gives a tagged estimate, never a measurement. Fewer than two shared designs
yields nothing: one pair cannot separate bias from luck.

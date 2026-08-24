# experiments/ — measured comparisons, not tests

Scripts here answer questions the test suite deliberately does not: controlled comparisons
whose OUTCOME is unknown before running (a test asserts a known invariant; an experiment
measures an open question). Each script prints its per-run records and a summary; the
conclusions live in `flux/docs/*-report.md` with the exact numbers, and the decision log
records what was concluded and what the measurement does NOT establish.

Rules, same spirit as knowledge mining (D243): real tools only, every number from a real run,
arms differ in exactly one variable, and a null result is a result — "no measurable effect on
this family" gets reported with the same care as an effect.

- `knowledge_efficacy.py` — does knowledge feeding measurably help? Two comparisons
  (docs/decisions.md D248): design-guidance chunks in RTL generation prompts (D244) and mined
  facts in agentic campaign proposals (D245), each with-vs-without, N repetitions, real qwen +
  real Verilator/ZigZag. Results: `flux/docs/knowledge-efficacy-report.md`.
- `escalation_speedup.py` — how much does measuring contenders concurrently buy? Two runs of the
  same round over a cold store, differing only in `escalation_parallelism` (docs/decisions.md
  D290); every escalation a real Yosys/OpenROAD/Verilator run, so it is scheduled, not run in CI.
- `thinking_efficacy.py` — does letting a reasoning model think produce better fabric proposals?
  Two arms differing only in qwen3's `/no_think` switch (docs/decisions.md D289), every proposal
  validated and screened by the real machinery.

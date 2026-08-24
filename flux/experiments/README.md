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

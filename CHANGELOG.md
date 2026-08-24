# Changelog

Every change has a numbered entry in [docs/decisions.md](docs/decisions.md) with its reason and
what was checked. This file is the short version, newest first.

## 0.1.0 (unreleased)

The first version meant for people outside the project.

### Added
- SystemC prototypes: `budget.prototype: systemc` proves the algorithm as an `SC_MODULE` against
  libsystemc; ICSC translates it to SystemVerilog when it is on PATH (D635, D636).
- `flux champsim run|build|check`: a generic ChampSim evaluator that measures its own baseline (D637).
- Components in `space:` (`optional: true` lets the search choose the combination), `{point}`,
  `seeds:`, conditional knobs, phase globs and a goal relative to the best (`keep`) (D634, D637).
- `applications/npu_gemm`: an accelerator sized for a workload, ZigZag as the cost model (D625).
- `flux selftest` and a model check before a run starts (D623); `docs/tutorial.md` (D624).
- `applications/gelu_fp16`: an FP16 GELU as a formula; a coding agent writes the prototype (D619).
- A link check over the repository's Markdown, as a unit test (D619).
- A coding agent can write the prototype (`flow.generate: {agent: ...}` with `prototype: true`),
  checking it with the new `flux rtl proto`. The document's knowledge now reaches the prototype
  stage (D618).
- A failing prototype check shows where the failures are, grouped by the input's sign and
  exponent, with one example per range (D617).
- A prototype computes with a formula, not a lookup: the model is asked for a polynomial per
  segment, and a table may hold at most 64 entries by default (`budget.prototype_table_max`,
  D616).
- Nothing oversized is built: a verified prototype over `budget.prototype_cost_max` is made
  cheaper before it is spelled, and never synthesised while over it (D615).
- A verified Python prototype is spelled as SystemVerilog by the loop, bit for bit, with no model
  turn (`flux_loop.py2sv`, D611).
- `flux new --kind tune` (tune your own program's knobs) and `--kind rtl-sweep` (a hardware family
  from knobs), and [docs/cookbook.md](docs/cookbook.md): which recipe for which problem (D608).
- A Python prototype step for any RTL problem with a golden model (`budget.prototype: true`), and a
  golden model written by the model when the document names one that doesn't exist (D604).
- `flux new NAME --kind python|rtl|sweep` writes a working problem to start from (D598).
- `applications/primes`, the first problem that isn't hardware (D598).
- `flux log RECORD` shows every model and coding-agent turn: prompts, replies, tool calls,
  errors (D599).
- `flux task run --json FILE` writes the answer for scripts (D591).
- Installing without Nix: `pip install -e ./flux`, with optional extras (D600).
- Search policies and worlds of your own, in a file beside the document
  (`flow.dse: module:Class`) (D602).
- Guides: [docs/extending.md](docs/extending.md) (the extension points and their stability)
  and [docs/models.md](docs/models.md) (models, coding agents, which model for which problem)
  (D601, D602).
- A Flux skill for outside coding agents, `skills/flux/` (D592).
- `flow:` is the only place a box of the loop is said, with every box of the drawing including
  `extract` and `records`. The `roles:`, `generator:`, `critique:` and `decompose:` keys are gone (D629).
- A problem document says only what is its own: `extension` and `campaign` are gone, and the gate
  count, rtl metrics, units and goal stage are inferred (D628).
- A run ends only when you stop it (`flux stop`, Ctrl-C, `q`) or at `--passes N`. A pass with
  nothing left to try is followed by one that explores for a better design, holding the goal
  (D593).
- `flux rtl test` exits 3 when the design does not compile. The loop counts that as a build
  failure, not a score, so repairs keep the last version that compiled (D594).
- A coding agent's brief names the gate command it is judged by (D595).
- Model replies are read more leniently: JSON in prose, raw newlines in strings, and
  SEARCH/REPLACE patches (D603).
- `flux status`, `flux stop` and `flux attach` find a run from its record, whatever
  `FLUX_TMPDIR` says (D597).

- The hardware-cost estimate is calibrated on ASAP7 synthesis, and the default ceiling is
  2,000, the size that synthesises in about 20 minutes (D619).
- `flux task check` names the prefetcher's missing traces (D619).
- The `flux` command exits with the right code, and every user error is one line (D590).
- The dev shell works when entered from outside the checkout (D598).
- A tool call the server cannot parse ends the turn, not the attempt (D596).
- Output piped to a file or `tee` is written line by line (D597).

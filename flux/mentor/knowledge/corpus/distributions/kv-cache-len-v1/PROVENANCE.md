# Source: ShareGPT_V3_unfiltered_cleaned_split.json (real conversation-length distribution)

Real, ingested distribution data (docs/decisions.md D87, docs/gap-analysis.md G5's own last-named
open piece — every prior reference to `"empirical@corpus/kv-cache-len-v1"` in this repo's own
workload examples was a placeholder URI, never actually resolved). Represents a real, measured
distribution of *decode-time context length* (how many real tokens were already in a real
conversation immediately before a real assistant turn began) — the genuine empirical quantity
`ir/workload/examples/llm-decode-attn-qk0.yaml`'s own dynamic `T` bound (KV-cache length so far)
is a model of.

- **Upstream**: <https://huggingface.co/datasets/anon8231489123/ShareGPT_Vicuna_unfiltered>,
  `ShareGPT_V3_unfiltered_cleaned_split.json`
- **Revision**: dataset repo commit `192ab2185289094fc556ec8ce5ce1e8e587154ca` (checked via
  HuggingFace's own API, `lastModified: 2023-04-12`)
- **License**: Apache-2.0 (declared in the dataset's own `cardData.license` — checked directly via
  `GET https://huggingface.co/api/datasets/anon8231489123/ShareGPT_Vicuna_unfiltered` before
  ingesting anything, the same "check per-source before ingesting" discipline
  `knowledge/corpus/riscv-unpriv/PROVENANCE.md` established for D31).

## What's actually vendored here — a real, computed summary, not the raw dataset

`data.json` is **not** the raw ShareGPT file (672 MB — too large to vendor, and not the shape
anything here actually consumes). It's a real, computed distribution summary:

- **Processing**: the first 20,000 real conversations in the source file's own order (stream-
  parsed with `ijson`, never fully downloaded — the source connection was closed once 20,000
  conversations were read), each message tokenized with `tiktoken`'s real `cl100k_base` encoding
  (a real, standard BPE tokenizer — not a `len(text)//4` heuristic). For each conversation, the
  real cumulative token count immediately *before* every `"from": "gpt"` turn was recorded as one
  real observation (skipping a turn's own leading conversation, i.e. cumulative==0, which isn't a
  real decode-with-context case) — 69,601 real observations total, from 19,864 non-empty real
  conversations (136 of the first 20,000 entries had an empty `conversations` list and were
  skipped).
- **`summary`**: `n_conversations_processed`, `n_observations`, real `min`/`max`/`mean`/`median`/
  `stdev` across all 69,601 real observations (min=1, max=161281, mean≈795.8, median=726,
  stdev≈858.5 — real, heavily right-skewed, matching the real, common-knowledge shape of chat
  conversation lengths: most are short, a real long tail exists).
- **`percentiles`**: real integer percentiles 0–100 (101 values, each a genuine order statistic
  from the real 69,601-observation array, not interpolated or smoothed) — the actual artifact
  `flux_workload_dynamism.distributions` reads to build real quantile-based dynamic-shape sample
  points (see that module's own docstring for how).

Reproducible: the exact processing script (stream-download, tokenize, collect, sort, take
percentiles) is described above in full; it used only real, public inputs (the dataset itself,
`tiktoken`'s real `cl100k_base` encoding) and no randomness — the same first 20,000 conversations
in the same file will always reproduce the same 101 percentile values.

# Source: ASAP7 predictive 7nm PDK (real standard-cell liberty timing/area data)

Real, ingested PDK data (docs/decisions.md D92) — the real, PDK-derived area numbers
docs/gap-analysis.md G15's own status row named as the reason a redaction layer had nothing real
to redact yet.

- **Upstream (canonical, license-verified source)**:
  <https://github.com/The-OpenROAD-Project/asap7sc7p5t_28>, commit
  `875fd1eee58741378d875d9b81c95526a9b8c47c` (`main`, 2026-04-11).
- **License**: BSD 3-Clause (Copyright 2020/2022 Lawrence T. Clark, Vinay Vashishtha, or Arizona
  State University) — checked directly against the upstream repo's own `LICENSE` file and the
  license header embedded in every real `.lib` file itself before ingesting anything, the same
  "check per-source before ingesting" discipline `knowledge/corpus/riscv-unpriv/PROVENANCE.md`
  (D31) and `knowledge/corpus/distributions/kv-cache-len-v1/PROVENANCE.md` (D87) established.
  Real, permissive, no NDA, no export-controlled process node (ASAP7 is an *academic/predictive*
  7nm PDK from Arizona State University — not a real foundry's production PDK, which would need a
  paid NDA this repo doesn't have; explicitly designed and widely used in open EDA research for
  exactly this "real, physically meaningful, but not fab-accurate" purpose).
- **Actually fetched from**: the identical real files, re-gzipped by the same organization
  (The-OpenROAD-Project) for its own flow tooling, at
  <https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts/tree/master/flow/platforms/asap7/lib/NLDM>,
  commit `f9ec54a6de7b2bc69fd586015f6ebdab34eca69c` (`master`, 2026-08-11) — simpler tooling
  (`.gz` vs. upstream's own `.7z`), same real content, same real license (embedded verbatim in
  each file's own header, checked directly after fetching, not assumed to match).

## What's actually vendored here

`asap7sc7p5t_simple_invbuf_seq_rvt_tt.lib.gz` — a real, deterministic merge of three real,
separately-published liberty files, all RVT (regular threshold voltage) flavor, TT (typical-
typical) corner, NLDM (non-linear delay model) timing:

- `asap7sc7p5t_SIMPLE_RVT_TT_nldm_211120.lib` (56 real cells: AND/OR/NAND/NOR/XOR/XNOR/MAJ/full-
  and half-adder gates) — the base combinational logic library.
- `asap7sc7p5t_INVBUF_RVT_TT_nldm_220122.lib` (37 real cells: INV/BUF/clock-inverter variants) —
  real, checked to be a genuinely separate library from SIMPLE (SIMPLE alone has no inverter cell
  at all — confirmed by grepping its own real `cell (...)` entries before assuming it was
  sufficient for `abc -liberty` on its own).
- `asap7sc7p5t_SEQ_RVT_TT_nldm_220123.lib` (33 real cells: DFF/latch/clock-gate variants) — real
  sequential-cell support, needed for `codegen/rtl_harness`'s own real clocked-design scope (D49).

**Deliberately not the full ASAP7 cell library**: no AO/OA compound-gate libraries (larger,
higher-drive-strength compound AND-OR/OR-AND cells) — real, checked-sufficient for `abc`'s own
technology mapping without them (real end-to-end tests below), the same "keep a first ingestion
small and hand-picked, extend when real work needs more" precedent D3/D31 established. No SRAM,
FF/SS (fast/slow) corners, or LVT/SLVT threshold-voltage flavors — TT/RVT is the real, standard
"typical" starting corner every EDA tutorial and this repo's own single-corner scope uses.

**Merge process, fully reproducible, not opaque**: each source file's own real `cell (...) { ... }`
blocks were extracted by brace-depth-matched parsing (never a naive regex/string split, which
would break on nested `pin (...) { timing (...) { ... } }` blocks) and spliced into one combined
`library (...) { ... }` wrapper, reusing the SIMPLE file's own header attributes (units, voltage
map, etc. — identical across all three real source files, checked before assuming this was safe).
126 real cells total (56 + 37 + 33). The exact same real files, re-fetched and re-merged the same
way, reproduce a byte-identical result — no randomness, no manual editing.

## Real, verified end to end before trusting anything downstream

A real 32-bit combinational adder (`Adder2`) and a real 32-bit clocked register (`Reg32`) were
both synthesized against this exact merged library via real Yosys + ABC
(`dfflibmap -liberty ...; abc -liberty ...; stat -liberty ...`) before any code was written against
it — `Adder2`: 12.655440 real µm² (123 real standard cells: AND2/INV/MAJ/NOR2/XNOR2/XOR2 — a real
majority-based carry chain, not a naive ripple-carry guess). `Reg32`: 13.530240 real µm² (32 real
`DFFASRHQNx1` flip-flops + 32 `INVx1` inverters), with a real, honest sequential/combinational
split Yosys's own `stat -liberty` reports directly: 12.130560 µm² (89.66%) of `Reg32`'s own real
area is sequential — a genuinely new signal no generic (PDK-less) synthesis in this repo could
ever report, since without real cell areas there's no way to distinguish "this cell is a
flip-flop" from "this cell is a NAND gate" by area at all.


## Correction (docs/decisions.md D229): three missing table templates

The original merge reused the SIMPLE file's header attributes, which silently dropped three
`lu_table_template`/`power_lut_template` definitions that only the SEQ file's header carries
(`delay_template_7x7`, `power_template_7x7`, `passive_power_template_7x1`). ABC never noticed —
area mapping does not evaluate timing tables — but OpenSTA emitted `template not found` per DFF
timing group and priced every sequential arc as unconstrained: a clocked design reported
worst slack INF (docs/decisions.md D229 found this through the OpenROAD flow, not through any
synthesis result). The three template blocks were extracted from the same
`asap7sc7p5t_SEQ_RVT_TT_nldm_220123.lib` (same ORFS commit, same licence) by the same
brace-depth-matched parsing and spliced into the header; every referenced template is now
defined (checked programmatically), and the D92 area pins are byte-for-byte unchanged
(re-verified: `tests/integration/test_rtl_synth_asap7_live.py`, 9/9).

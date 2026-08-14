# `evaluators/openroad/` — real physical-design PPA on ASAP7

The synthesis-fidelity rung (docs/decisions.md D225, closing docs/roadmap.md's #1 immediate
action): Yosys maps the candidate's derived MAC datapath onto the vendored ASAP7 RVT/TT liberty
(the same D92 cells the gate-count ranking uses), OpenROAD floorplans and places it on the D225
platform files, and the Evaluator ABI gets **measured silicon numbers** — `area_mm2` from placed
cell footprints, `power_w` from liberty power tables at a stated clock, `worst_slack_ps` as an
extra-vocabulary metric. First real numbers: the 8-lane int-workload datapath is 1928 um^2 /
27.9 mW / +293 ps at 2 ns, scaling 2.00x per lane doubling.

- **What it places**: `flux_generation.derive_design_spec`'s combinational dot-product datapath
  (the D223 spec family), built as its canonical LLM-free implementation. NOT `mac_array.sv` —
  that design's operand memories are testbench-loaded, so synthesis constant-folds its datapath
  away (measured: LANES 8 vs 32 differed by ten cells). `evaluators/rtl` still owns
  `latency_cycles` for that design; this rung deliberately reports neither latency nor energy.
- **Flow depth v0.1**: floorplan -> tracks -> global+detailed placement -> placement-based
  parasitics. CTS and routing are the named next step; `flow_depth` in every report says how far
  the flow went. `validity.ok` is the timing verdict at the stated clock — the 32-lane array
  genuinely misses 2 ns (its adder tree is deeper), and says so.
- **Ports are 32-bit carriers** (the derived spec's own D202 sizing), so multipliers are 32x32
  even for an int8 workload — the honest description is "the spec's datapath", not "an int8
  datapath". Tightening spec ports to true workload precision is a known future refinement.
- **Toolchain**: `nix develop .#physical` (yosys + openroad 26Q2; first entry builds
  or-tools/openroad from source — see flake.nix for why upstream doesn't cache them).
  `YOSYS_BIN`/`OPENROAD_BIN` env overrides follow D147's pattern.

Package: `flux-evaluator-openroad`, backend name `openroad` (12th registry entry). Platform
provenance: `src/flux_evaluator_openroad/platform/asap7/PROVENANCE.md`.

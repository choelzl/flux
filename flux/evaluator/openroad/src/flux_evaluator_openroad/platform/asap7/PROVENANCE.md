# Source: ASAP7 place-and-route platform files (docs/decisions.md D225)

The physical-design half of the ASAP7 ingestion `codegen/rtl_harness/asap7_pdk/PROVENANCE.md`
(D92) began: that vendored the liberty timing/area data for synthesis; this vendors what
OpenROAD's own flow needs on top — the technology LEF (routing layers/vias), the RVT
standard-cell LEF (matching the RVT/TT liberty flavor already vendored), and the ASAP7 platform
flow fragments (track definitions, tapcell parameters, PDN strategy, RC extraction values,
example SDC, reference config).

- **Fetched from**: <https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts>
  `flow/platforms/asap7/`, commit `f9ec54a6de7b2bc69fd586015f6ebdab34eca69c` (`master`,
  2026-08-11) — the SAME commit D92's liberty ingestion used, checked then and re-used here for
  one coherent platform snapshot.
- **Licences, verified per file class, not assumed**:
  - `lef/asap7_tech_1x_201209.lef`, `lef/asap7sc7p5t_28_R_1x_220121a.lef(.gz)`: BSD 3-Clause,
    Copyright 2020/2022 Lawrence T. Clark, Vinay Vashishtha, or Arizona State University —
    read from each file's own embedded header after fetching (the same header the vendored
    liberty files carry).
  - `openRoad/*.tcl`, `openRoad/pdn/*.tcl`, `setRC.tcl`, `constraints.sdc`, `config.mk`:
    BSD 3-Clause per the repository's `LICENSE_BUILD_RUN_SCRIPTS`, which states it covers the
    build and run scripts (the platform flow fragments are those scripts); the LEF/liberty
    content it defers to per-component licences, which are the embedded ASU headers above.
- **Deliberately not fetched**: SRAM/fakeram LEFs (no memory macros in scope yet), L/SL
  threshold-voltage LEF flavors (the vendored liberty is RVT-only), GDS (no DRC/LVS signoff in
  scope), KLayout/openlane tool configs (not this flow). Same "small, hand-picked first
  ingestion" precedent as D92.
- `config.mk` and `constraints.sdc` are vendored as REFERENCE — the evaluator derives its own
  floorplan/SDC values and cites these for where its defaults come from (e.g. ASAP7's own
  `PLACE_DENSITY`, track pitches, `ABC_CLOCK_PERIOD_IN_PS`); they are not executed.

The cell LEF is stored gzip-compressed (23.8 KB vs 391 KB); the evaluator decompresses to its
scratch directory at flow time, the same pattern the liberty ingestion uses.

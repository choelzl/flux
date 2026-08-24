# Source: curated design guidance (original text)

A hand-picked design-wisdom corpus (docs/decisions.md D244, D267) covering memory
implementation choices, multi-port memory composition techniques, datapath PPA guidance, and
interconnect fabric selection — the "Knowledge
feeding the Generator" role for RTL/SystemC design quality.

**Provenance class — read this before trusting a chunk.** Unlike `riscv-unpriv/` (verbatim
CC-BY-4.0 spec text) and unlike mined facts (D243, computed from this repo's own stores),
every word in this directory was WRITTEN FOR THIS REPOSITORY: technical content drawn from the
cited sources and from this repo's own measured decisions, expressed in original prose. Each
entry states inline whether it is (a) a repo-measured fact with a decisions.md pointer, (b) a
data point reported by a cited source, or (c) direction-only guidance — and paragraphs are
written to carry their qualifiers themselves, because the BM25 index retrieves paragraphs
alone.

- **Sources consulted** (read, cited inline, NOT reproduced):
  - T. Verbeure, "Building Multiport Memories with Block RAMs" (2019),
    <https://tomverbeure.github.io/2019/08/03/Multiport-Memories.html>. License check
    (2026-08-18): the blog's repository (github.com/tomverbeure/tomverbeure.github.io) carries
    NO license file — all rights reserved by default, so no verbatim text was ingested; the
    techniques it describes are cited and described in this corpus's own words.
  - E. LaForest, Z. Li, T. O'Rourke, M. G. Liu, J. G. Steffan, "Composing Multi-Ported
    Memories on FPGAs" (ACM TRETS, 2014) — the paper the blog post is based on; ACM copyright,
    same treatment (cited, not reproduced). Its FPGA-measured comparisons are referenced only
    as "no technique dominates; measure per design point".
  - This repository's own measured results, cited by decision number (D225/D228/D229/D231/
    D235/D236/D237/D241) — the only quantitative claims in this corpus with exact numbers
    attached, and each names its scope.
- **Deliberately absent**: SRAM bitcell-scaling formulas, technology constants, or any
  area/power/latency magnitude not measured by this repo or explicitly attributed — entries
  instead point to the real quantification paths (`flux_characterize_memory_level` for CACTI,
  the OpenROAD evaluator for placed numbers). A remembered constant that leads a design is
  exactly the "incorrect assumption" failure this corpus is required to avoid.
- **Standards note**: nothing here reproduces IEEE 1800 (not redistributable, D174) or any of
  the closed standards documented in `../riscv-unpriv/PROVENANCE.md` (AMBA/AXI, JEDEC,
  PCI-SIG, I2C) — checked there, still closed.

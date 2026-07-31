# Source: RISC-V Unprivileged ISA Manual

Five hand-picked chapters (not the full manual — docs/00-decisions.md D3: "keep the first corpus
small and hand-picked... rather than attempting broad coverage immediately"), chosen for direct
relevance to this repo's existing RISC-V SoC example
(`ir/architecture/examples/generic-riscv-soc-v1.yaml`): `preface.adoc`, `naming.adoc` (ISA
extension naming — needed to interpret any `arch` string), `zicsr.adoc` (control/status
registers — present on essentially any real RISC-V core), `m-st-ext.adoc` (M/multiply-divide
extension), `zifencei.adoc` (instruction-fetch fence).

- **Upstream**: <https://github.com/riscv/riscv-isa-manual>, `src/unpriv/*.adoc`
- **Commit**: `310a111489a0bad6e60ef4cbfba574417c6f825f` (main, 2026-07-29)
- **License**: Creative Commons Attribution 4.0 International (CC BY 4.0) — see upstream
  `LICENSE`. Verified directly before ingesting anything, per docs/00-decisions.md's still-open
  "check per-standard before ingesting into the corpus" item — this standard specifically is
  clear.
- **Not ingested, deliberately**: AMBA/AXI, JEDEC, and every other standard docs/00-decisions.md
  D3 names as an *example* of what the knowledge layer could hold — none of them have a resolved
  license/redistribution check yet, and nothing in this repo currently generates or evaluates
  interconnects or memory controllers that would use them. Do not add them here without doing
  that check first.

Files are the verbatim upstream AsciiDoc source, unmodified — `flux_knowledge`'s ingestion
connector (`connectors/adoc.py`) parses them at load time; nothing here is pre-processed.

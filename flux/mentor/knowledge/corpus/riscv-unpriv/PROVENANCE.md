# Source: RISC-V Unprivileged ISA Manual

Five hand-picked chapters (not the full manual — docs/decisions.md D3: "keep the first corpus
small and hand-picked... rather than attempting broad coverage immediately"), chosen for direct
relevance to this repo's existing RISC-V SoC example
(`ir/architecture/examples/generic-riscv-soc-v1.yaml`): `preface.adoc`, `naming.adoc` (ISA
extension naming — needed to interpret any `arch` string), `zicsr.adoc` (control/status
registers — present on essentially any real RISC-V core), `m-st-ext.adoc` (M/multiply-divide
extension), `zifencei.adoc` (instruction-fetch fence).

- **Upstream**: <https://github.com/riscv/riscv-isa-manual>, `src/unpriv/*.adoc`
- **Commit**: `310a111489a0bad6e60ef4cbfba574417c6f825f` (main, 2026-07-29)
- **License**: Creative Commons Attribution 4.0 International (CC BY 4.0) — see upstream
  `LICENSE`. Verified directly before ingesting anything, per docs/decisions.md's still-open
  "check per-standard before ingesting into the corpus" item — this standard specifically is
  clear.
- **Not ingested, checked and closed** (docs/decisions.md D31 — checked for real, not deferred):
  **AMBA/AXI** — ARM's own docs: "No part of the document may be reproduced in any form by any
  means without the express prior written permission of Arm." **JEDEC** — requires a license
  agreement, "may not be reproduced without permission." **PCI-SIG (PCIe)** — member-only access,
  "No license... is granted herein." **I2C (NXP UM10204)** — all-rights-reserved, no
  redistribution grant found; treated as closed. None of these four can be ingested without a
  paid license.
- **Not ingested, checked and open, but out of scope**: **WISHBONE B4** (OpenCores) is genuinely
  public domain — verified against the actual primary-source PDF, page 3: "this document is not
  copyrighted, and has been placed into the public domain. It may be freely copied and distributed
  by any means," plus an explicit royalty-free SoC-use grant. Not ingested anyway: nothing in this
  repo generates or evaluates a WISHBONE-style bus, so it fails the same hand-picked-for-relevance
  bar this corpus itself is held to (D3's "keep the first corpus small and hand-picked... rather
  than attempting broad coverage"), not a licensing bar. Ingest it if/when real WISHBONE-modeling
  work needs it, alongside that work — the same pattern this corpus followed relative to
  `ir/architecture/examples/generic-riscv-soc-v1.yaml`.

Files are the verbatim upstream AsciiDoc source, unmodified — `flux_knowledge`'s ingestion
connector (`connectors/adoc.py`) parses them at load time; nothing here is pre-processed.

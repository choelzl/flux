# Vendored interconnect IP

Third-party RTL, redistributed here under its own licence, kept separate from generated code so
the boundary is unambiguous: **nothing in this directory is written by this repository**, and
nothing here is modified. Flux instantiates it, measures it, and reports whose silicon it is.

## cluster_interconnect/

`xbar_varlat.sv`, `addr_dec_resp_mux_varlat.sv` — the PULP logarithmic interconnect
(variable-latency crossbar with per-output round-robin arbitration).

- Upstream: https://github.com/pulp-platform/cluster_interconnect
- Revision: `2967d8d17be0a6139229ca8d3d4956e182aec3de`
- Licence: Apache-2.0 WITH SHL-2.1 (Solderpad Hardware Licence v2.1) — permissive, redistributable
- Path upstream: `rtl/tcdm_interconnect/`

## common_cells/

`rr_arb_tree.sv`, `lzc.sv`, `cf_math_pkg.sv`, `assertions.svh` — the dependencies
`xbar_varlat` needs, chiefly the round-robin arbiter tree.

- Upstream: https://github.com/pulp-platform/common_cells
- Licence: Apache-2.0 WITH SHL-2.1
- `rr_arb_tree` is the piece that matters: a balanced arbiter tree that MUXES THE DATA AS IT
  ARBITRATES, so a switch's depth grows as log2(inputs) rather than with a priority scan
  followed by a separate wide mux.

## obi_pkg.sv, crossbar.sv

The OBI request/response types and the OBI-typed wrapper around `xbar_varlat`, by Cedric Hölzl,
from https://github.com/choelzl/rtl-lab (`projects/tdm/rtl/`), vendored here at the author's
request. `crossbar.sv` is what makes the PULP core usable from an OBI system: the slave index is
an address bit-slice (`addr[SEL_SLICE_START +: SEL_SLICE_LENGTH]`), `gnt` is combinational,
`rvalid` is registered, and no transactions are outstanding.

## Why this is here

This repo's generated switch measured several times slower than this IP at the same topology
(docs/decisions.md D276). Rather than keep tuning a hand-rolled arbiter against a proven one,
the proven one is vendored, measured through the same harness, and used as the reference every
generated fabric is held to.

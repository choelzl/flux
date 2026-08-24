# `protocols/` — structured protocol facts

Machine-readable descriptions of bus and stream protocols — signals with widths and directions,
parameters, and the numbered rules of the source document — for other Flux modules to consume
programmatically. See [docs/decisions.md](../../docs/decisions.md) D174.

## Not a second `knowledge/`

`knowledge/` retrieves **prose** from spec documents (BM25 over AsciiDoc chunks) — the right shape
for "what does the spec say about X", answered to a human or an agent. This module answers "what
signals does an OBI manager drive, how wide is `be`, and which requirement says so" — to **code**.
A codegen backend emitting a bus interface cannot use a paragraph. Both surfaces over the same
standards are deliberate.

## Every fact carries its source, and the schema enforces it

`provenance` is required, and `licence`, `redistributable` and `normative` are required within it.
Two guards follow:

- **A non-redistributable source may not carry quoted rule text or prose descriptions.** D31 checked
  five standards and found AMBA/AXI, JEDEC, PCIe and I2C closed. Structure (names, widths,
  directions) is what a consumer needs and is not the reproduced expression the licence covers.
- **A `normative: false` document must say what it implements.** An implementation-sourced fact that
  doesn't name its standard is an unattributed claim.

Both failures are silent at the point of use: a fabricated signal width and a sourced one look
identical to the code reading them. Only the provenance distinguishes them.

## What ships, and why the set is small

| id | version | source | normative | licence |
|---|---|---|---|---|
| `obi` | `1.6.0` | OBI-v1.6.0.pdf (OpenHW Group / Silicon Labs) | yes | Solderpad-2.0 |
| `wishbone` | `b4` | wbspec_b4.pdf (OpenCores) | yes | public domain |
| `axi4` | `pulp-0.39.10` | pulp-platform/axi `axi_intf.sv`, `axi_pkg.sv` | **no** | SHL-0.51 |
| `axi4-lite` | `pulp-0.39.10` | pulp-platform/axi `axi_intf.sv` | **no** | SHL-0.51 |
| `axi4-stream` | `verilog-axis-48ff7a7e` | alexforencich/verilog-axis `axis_register.v` | **no** | MIT |
| `definitions-clocking` | `1` | compiled from the two normative documents above | no | (both) |

Each licence was read from the primary document, not from a summary — the same discipline D31
applied. The OBI repository has no `LICENSE` file at all; its grant is on page 2 of the PDF.

**AXI is versioned by the implementation it was read from**, not as `4.0`, because that is what its
content is evidence about. Arm's specification cannot be redistributed ("No part of the document may
be reproduced in any form by any means without the express prior written permission of Arm" — D31),
so where the implementation and the standard could differ, the standard governs and this is not
evidence about it.

**AXI4-Stream comes from a different source** (alexforencich/verilog-axis, MIT) because
`pulp-platform/axi` has no AXI-Stream typedefs. Two implementations means two naming conventions —
check each document's `provenance.document` rather than assuming the AXI entries share a source.

A protocol's absence from this table means no verified open source has been ingested for it, never
that it is unimportant.

## Deriving a stream from a bus

A bus and a stream differ structurally: a bus has an address phase, a data phase and a response
phase; a stream is the data phase alone. So the stream form of an AXI-family bus can be
*constructed* from a bus document rather than sourced separately:

```python
from flux_protocols import derive_stream_from_bus, derived_matches_reference

derived = derive_stream_from_bus(registry.get("axi4", "pulp-0.39.10"))
derived.signal("tdata").width        # "AXI_DATA_WIDTH", carried through from w_data
```

The mapping (`w_data`→`tdata`, `w_strb`→`tkeep`, …) is **Flux's reasoning, not a quotation**, which
is why it is a function rather than a file in `specs/` — a file there claims a source states its
contents. It is checked instead of trusted: `derived_matches_reference` compares the result against
`axi4-stream@verilog-axis-*`, an independently written MIT implementation sharing no source with the
pulp-platform documents the derivation reads.

Measured: deriving from `axi4` reaches six of that implementation's eight signals, from `axi4-lite`
four, and **neither produces a signal the implementation lacks**. The derivation is sound and
incomplete — it under-produces rather than inventing. It cannot reach `tid` or `tdest`, which have
no write-data-channel analogue.

## Using it

```python
from flux_protocols import ProtocolRegistry, resolve_ir_reference

obi = ProtocolRegistry().get("obi")
obi.signal("be").width          # "DATA_WIDTH/8"
obi.signal("gnt").driver        # "subordinate"
obi.rule("R-3").text            # the handshake requirement, verbatim

resolve_ir_reference("axi4@2.0")   # unresolved: this build ships pulp-0.39.10
```

Three surfaces, per `docs/agent-surface.md`: the typed functions above, the CHIA nodes
`flux_protocol_lookup` / `flux_list_protocols` / `flux_check_ir_protocols`, and the matching MCP
tools.

## Emitting an interface

```python
from flux_protocols import ProtocolRegistry, emit_sv_module

sv = emit_sv_module(
    ProtocolRegistry().get("obi"), role="subordinate", module_name="obi_sub",
    parameters={"ADDR_WIDTH": 32, "DATA_WIDTH": 32},
)
```

produces a lint-clean SystemVerilog module whose `req` is an input, `gnt` an output, and `be` four
bits (`DATA_WIDTH/8`, evaluated). Every shipped protocol and role is checked by running real
Verilator over the result — `tests/integration/test_protocol_emit_verilator.py`.

The module body is empty on purpose: a protocol document can honestly determine an *interface*, and
a generated body would invent the behavioural semantics these sources don't state. Widths that a
specification leaves to the integrator (WISHBONE's `ADR_O`) must be passed via `widths=`; nothing is
defaulted, because a silently 1-bit data bus lints clean and means nothing.

**What Verilator proves and doesn't.** That the output is well-formed SystemVerilog. Not that the
document is right about the protocol — a reversed direction in the YAML yields a module that lints
perfectly and is wrong. Only provenance speaks to that, which is why the emitted header carries it,
including a warning when the source is an implementation rather than a specification.

## Checking an interface

The reverse of emission, and the direction that matters for RTL Flux didn't write:

```python
from flux_protocols import check_module_conformance

report = check_module_conformance(source, protocol, role="subordinate", prefix="")
report.conforms      # False if any finding is an error
report.findings      # e.g. "'req' is declared output but a subordinate must receive it"
```

This is the missing half of D39/D43's "verification owns structure, LLM owns behaviour": a
model-written bus interface with a reversed `req`/`gnt` pair passes Verilator without complaint.
Checked against real third-party RTL — `alexforencich/verilog-axis`'s `axis_register` conforms as a
sink on its `s_axis_` side and a source on its `m_axis_` side, which is cross-validation rather than
self-consistency, since that module and our `axi4-stream` document come from different files.

`prefix` strips a per-interface naming prefix; globals like a clock are never prefixed. Optional
signals may be absent, but an optional signal that is *present* and points the wrong way is an
error — optionality governs presence, not correctness.

**`conforms=True` means the interface is shaped right**, never that the design speaks the protocol.

For documents that quote their handshake rules, a slice of the *behavioral* question is now
checkable too (docs/decisions.md D212): a `handshakes:` block records the request/accept pairs,
reset obligations and no-retraction rules in machine form — every field must cite a quoted rule id,
so the block cannot claim more than the vendored text states — and
`emit_sv_assertions(protocol)` derives a SystemVerilog checker module from it, each `assert
property` carrying the source rule's own number. Bind it beside a DUT or drop it in a testbench;
Verilator (`--assert`) reports violations by rule. OBI carries the block today; ordering and
payload-stability rules remain unrepresented, and documents without quoted rules (everything
non-redistributable) cannot carry one by construction.

## Adding a protocol

1. Verify the licence **from the primary document**, not a search summary. If it isn't
   redistributable, you may still record structure — but not prose or rules.
2. Write `specs/<id>-<version>.yaml`. The loader will refuse it if provenance is incomplete.
3. Record what you did *not* cover in `coverage_note`. Silence about a gap reads as completeness.

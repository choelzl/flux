"""Unit tests for `flux_protocols` (docs/decisions.md D174).

Most of these are about the two guards rather than the data: a store of protocol facts is only
worth having if an unsourced or licence-violating entry cannot get into it, and both failures are
silent at the point of use — a fabricated signal width and a sourced one look identical to the code
that reads them.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # annotation-only: the name is a forward reference and pyflakes
    from flux_protocols import Protocol   # reports it undefined without this (D334)


from pathlib import Path
import pytest
import yaml
from flux_protocols import (
    LicenceViolationError,
    derive_stream_from_bus,
    derived_matches_reference,
    check_module_conformance,
    emit_sv_module,
    emit_sv_ports,
    parse_module_ports,
    ProtocolRegistry,
    ProtocolSpecError,
    UnsourcedFactError,
    load_all,
    parse_protocol,
    protocol_references_in,
    resolve_ir_reference,
    specs_dir,
)

# From THIS file, not from the package under test: deriving the repo root by walking up
# from a package makes the constant depend on where that package happens to sit, which
# is exactly what broke when the tree was reorganised.
FLUX_ROOT = Path(__file__).resolve().parents[2]


def _minimal(**overrides):
    doc = {
        "schema_version": "0.1.0",
        "id": "test-proto",
        "version": "1",
        "title": "Test protocol",
        "kind": "bus",
        "provenance": {
            "document": "test.pdf",
            "publisher": "Test",
            "licence": "public-domain",
            "redistributable": True,
            "normative": True,
        },
    }
    doc.update(overrides)
    return doc


# --- the provenance guard ------------------------------------------------------------------

def test_a_document_without_provenance_is_refused():
    doc = _minimal()
    del doc["provenance"]
    with pytest.raises(UnsourcedFactError, match="no `provenance` block"):
        parse_protocol(doc)


@pytest.mark.parametrize("field", ["document", "publisher", "licence", "redistributable", "normative"])
def test_every_provenance_field_is_required(field):
    doc = _minimal()
    del doc["provenance"][field]
    with pytest.raises(UnsourcedFactError, match="provenance is missing"):
        parse_protocol(doc)


def test_a_non_normative_document_must_say_what_it_implements():
    """An implementation-sourced fact that doesn't name the standard it implements is an
    unattributed claim — the reader can't tell what it is evidence about."""
    doc = _minimal()
    doc["provenance"]["normative"] = False
    with pytest.raises(UnsourcedFactError, match="doesn't say what it implements"):
        parse_protocol(doc)


def test_a_non_normative_document_with_implements_is_accepted():
    doc = _minimal()
    doc["provenance"]["normative"] = False
    doc["provenance"]["implements"] = {"standard": "Some Standard", "document": "XYZ-123"}

    assert parse_protocol(doc).provenance.implements["standard"] == "Some Standard"


# --- the licence guard ---------------------------------------------------------------------

def test_a_closed_source_may_not_carry_quoted_rules():
    """docs/decisions.md D31: "No part of the document may be reproduced in any form by any means
    without the express prior written permission of Arm." A protocol store is exactly the kind of
    thing that accumulates such text by accident."""
    doc = _minimal()
    doc["provenance"]["redistributable"] = False
    doc["rules"] = [{"id": "R-1", "text": "Some reproduced requirement text."}]

    with pytest.raises(LicenceViolationError, match="quoted rule"):
        parse_protocol(doc)


def test_a_closed_source_may_not_carry_signal_descriptions():
    doc = _minimal()
    doc["provenance"]["redistributable"] = False
    doc["signals"] = [
        {"name": "sig", "driver": "a", "receiver": "b", "width": 1, "description": "Reproduced prose."}
    ]

    with pytest.raises(LicenceViolationError, match="prose descriptions"):
        parse_protocol(doc)


def test_a_closed_source_may_still_carry_structure():
    """Names, widths and directions are what a consumer needs to generate or check an interface,
    and are not the reproduced expression the licence covers. The guard must not over-reach into
    making closed protocols unrepresentable altogether."""
    doc = _minimal()
    doc["provenance"]["redistributable"] = False
    doc["signals"] = [{"name": "sig", "driver": "a", "receiver": "b", "width": 8}]

    protocol = parse_protocol(doc)
    assert protocol.signal("sig").width == 8
    assert protocol.signal("sig").description is None


# --- structural validation -----------------------------------------------------------------

def test_a_width_naming_an_undefined_parameter_is_refused():
    """A dangling parameter reference is a dead end at exactly the moment a consumer tries to size
    a port."""
    doc = _minimal(signals=[{"name": "d", "driver": "a", "receiver": "b", "width": "NOT_DEFINED"}])
    with pytest.raises(ProtocolSpecError, match="references a parameter this document doesn't define"):
        parse_protocol(doc)


def test_a_width_expression_over_defined_parameters_is_accepted():
    doc = _minimal(
        parameters=[{"name": "DATA_WIDTH", "default": 32}],
        signals=[{"name": "be", "driver": "a", "receiver": "b", "width": "DATA_WIDTH/8"}],
    )
    assert parse_protocol(doc).signal("be").width == "DATA_WIDTH/8"


def test_duplicate_signals_are_refused():
    doc = _minimal(signals=[
        {"name": "s", "driver": "a", "receiver": "b", "width": 1},
        {"name": "s", "driver": "a", "receiver": "b", "width": 2},
    ])
    with pytest.raises(ProtocolSpecError, match="declared more than once"):
        parse_protocol(doc)


def test_an_unknown_kind_is_refused():
    with pytest.raises(ProtocolSpecError, match="is not one of"):
        parse_protocol(_minimal(kind="telepathy"))


# --- the shipped documents -----------------------------------------------------------------

def test_every_shipped_document_loads_and_validates():
    """Guards the guard: a syntactically valid but unsourced document added later must fail here,
    and an empty specs directory must not pass vacuously."""
    protocols = load_all()
    assert len(protocols) >= 5


def test_obi_matches_its_source_tables():
    """Spot-checks against OBI-v1.6.0.pdf Table 1 and Table 2 — chosen because each would be easy
    to get subtly wrong: `be` is a derived width, `gnt` reverses direction relative to `req`, and
    DATA_WIDTH has an enumerated legal set rather than a free integer."""
    obi = ProtocolRegistry().get("obi")

    assert obi.provenance.normative is True
    assert obi.provenance.licence == "Solderpad-2.0"
    assert obi.signal("be").width == "DATA_WIDTH/8"
    assert obi.signal("req").driver == "manager"
    assert obi.signal("gnt").driver == "subordinate"
    assert obi.parameter("DATA_WIDTH").allowed == (32, 64)
    assert "two-way control handshake (req+gnt)" in obi.rule("R-3").text


def test_axi_is_marked_non_normative_and_names_the_closed_standard():
    """The whole reason AXI is representable here: the facts come from a permissively-licensed
    implementation, and a reader must be told that rather than discovering it."""
    axi = ProtocolRegistry().get("axi4", "pulp-0.39.10")

    assert axi.provenance.normative is False
    assert axi.provenance.implements["standard"] == "Arm AMBA AXI4"
    assert axi.provenance.implements["redistributable"] is False
    assert not any(s.description for s in axi.signals), (
        "AXI signal descriptions would have to be written from memory of Arm's specification — "
        "the source is RTL and carries none to quote"
    )


def test_axi4_lite_is_a_strict_signal_subset_of_axi4():
    """Checked rather than asserted in prose: both documents come from the same source file, so if
    the Lite entry had been written from memory this is where it would show."""
    registry = ProtocolRegistry()
    full = {s.name for s in registry.get("axi4", "pulp-0.39.10").signals}
    lite = {s.name for s in registry.get("axi4-lite", "pulp-0.39.10").signals}

    assert lite < full
    assert {"aw_len", "aw_burst", "aw_id", "w_last", "r_last"} <= (full - lite)


def test_wishbone_widths_are_absent_rather_than_guessed():
    """WISHBONE derives widths from a core's DATASHEET, not from the specification. A guessed
    number would be worse than an absent one because it would look sourced."""
    wishbone = ProtocolRegistry().get("wishbone")

    assert wishbone.signal("ADR_O").width is None
    assert wishbone.signal("SEL_O").width is None
    assert wishbone.signal("CYC_O").width == 1  # single-bit signals the document does pin down


# --- lookup and IR resolution ---------------------------------------------------------------

def test_a_bare_id_with_several_versions_refuses_to_guess():
    doc_a = _minimal(version="1")
    doc_b = _minimal(version="2")
    registry = ProtocolRegistry.__new__(ProtocolRegistry)
    registry._protocols = [parse_protocol(doc_a), parse_protocol(doc_b)]
    registry._by_ref = {p.ref: p for p in registry._protocols}

    with pytest.raises(Exception, match="name one rather than relying on a default"):
        registry.get("test-proto")


def test_protocol_references_are_found_anywhere_in_the_tree():
    """Path-driven collection would miss the next place someone puts a protocol field; the two IRs
    already disagree (`noc.model` vs an interface's `protocol`)."""
    document = {
        "noc": {"model": "axi4@2.0"},
        "ops": [{"interfaces": [{"protocol": "obi"}]}],
        "unrelated": {"model": 7},  # not a string: not a protocol reference
    }

    assert protocol_references_in(document) == ["axi4@2.0", "obi"]


def test_the_real_architecture_examples_reference_resolves_or_says_why():
    """`generic-riscv-soc-v1.yaml` declares `axi4@2.0`, and this build ships AXI facts read from
    pulp-platform, not from Arm's v2.0 document. Not resolving is the correct answer, and the
    reason must name what IS available rather than just failing."""
    arch = yaml.safe_load((FLUX_ROOT / "core/ir/architecture/examples/generic-riscv-soc-v1.yaml").read_text())
    references = protocol_references_in(arch)
    assert references == ["axi4@2.0"]

    resolution = resolve_ir_reference(references[0])
    assert not resolution.resolved
    assert "pulp-0.39.10" in resolution.reason


def test_an_unknown_protocol_is_reported_not_raised():
    """IR validators walk whole documents: one unknown string should be a finding about that field,
    not an exception that abandons the rest of the check."""
    resolution = resolve_ir_reference("not-a-real-bus")

    assert not resolution.resolved
    assert "not-a-real-bus" in resolution.reason


def test_axi4_stream_is_sourced_from_a_different_implementation_than_axi4():
    """AXI4-Stream comes from alexforencich/verilog-axis (MIT), not pulp-platform, because
    pulp-platform/axi has no AXI-Stream typedefs. Two implementations means two naming conventions,
    and a reader must be able to see which document a fact came from rather than assuming the AXI
    entries share a source (docs/decisions.md D175).
    """
    registry = ProtocolRegistry()
    stream = registry.get("axi4-stream")
    full = registry.get("axi4", "pulp-0.39.10")

    assert stream.kind == "stream"
    assert stream.provenance.licence == "MIT"
    assert stream.provenance.implements["standard"] == "Arm AMBA AXI4-Stream"
    assert stream.provenance.document != full.provenance.document


def test_axi4_stream_optionality_comes_from_the_sources_enable_parameters():
    """Which qualifiers are optional is read from the implementation's own ENABLE parameters, not
    assumed — tdata/tvalid/tready are always present, the rest are gated."""
    stream = ProtocolRegistry().get("axi4-stream")

    required = {s.name for s in stream.signals if s.required and not s.is_global}
    optional = {s.name for s in stream.signals if not s.required}

    assert required == {"tdata", "tvalid", "tready"}
    assert optional == {"tkeep", "tlast", "tid", "tdest", "tuser"}
    for gate in ("KEEP_ENABLE", "LAST_ENABLE", "ID_ENABLE", "DEST_ENABLE", "USER_ENABLE"):
        assert stream.parameter(gate) is not None


def test_a_stream_has_no_address_or_response_channel():
    """The structural difference from the axi4 bus in the same directory, checked rather than
    described: a consumer picking between them should be able to tell from the data."""
    registry = ProtocolRegistry()
    stream_signals = {s.name for s in registry.get("axi4-stream").signals}
    bus_signals = {s.name for s in registry.get("axi4", "pulp-0.39.10").signals}

    assert not any(name.startswith(("aw_", "ar_", "b_", "r_")) for name in stream_signals)
    assert {"aw_addr", "ar_addr", "b_resp"} <= bus_signals


# --- deriving a stream from a bus (docs/decisions.md D176) ---------------------------------

def test_deriving_from_axi4_invents_nothing_the_reference_lacks():
    """The load-bearing assertion. A derived signal the independently-sourced implementation
    doesn't have would mean the subtraction is producing facts rather than restructuring them —
    the exact failure this module exists to prevent, arrived at by a different route.

    The two sides share no source: the derivation reads pulp-platform (Solderpad), the reference is
    alexforencich/verilog-axis (MIT).
    """
    registry = ProtocolRegistry()
    derived = derive_stream_from_bus(registry.get("axi4", "pulp-0.39.10"))

    comparison = derived_matches_reference(derived, registry.get("axi4-stream"))

    assert comparison["invented"] == []
    assert comparison["agreed"] == ["tdata", "tkeep", "tlast", "tready", "tuser", "tvalid"]
    assert comparison["underived"] == ["tdest", "tid"]


def test_deriving_from_axi4_lite_reaches_less_and_still_invents_nothing():
    """Lite has no w_last/w_user, so the subtraction reaches four signals instead of six. Pinned
    because it is the honest limit of "use AXI-Lite and remove things": it gets the handshake and
    the payload, not the packet framing."""
    registry = ProtocolRegistry()
    derived = derive_stream_from_bus(registry.get("axi4-lite", "pulp-0.39.10"))

    comparison = derived_matches_reference(derived, registry.get("axi4-stream"))

    assert comparison["invented"] == []
    assert comparison["agreed"] == ["tdata", "tkeep", "tready", "tvalid"]
    assert comparison["underived"] == ["tdest", "tid", "tlast", "tuser"]


def test_a_derived_stream_carries_widths_and_directions_through():
    """Derivation must restructure, not flatten: a stream whose tdata lost its parameterised width
    would be useless to the codegen this module exists to serve."""
    registry = ProtocolRegistry()
    derived = derive_stream_from_bus(registry.get("axi4", "pulp-0.39.10"))

    assert derived.kind == "stream"
    assert derived.signal("tdata").width == "AXI_DATA_WIDTH"
    assert derived.signal("tkeep").width == "AXI_STRB_WIDTH"
    assert derived.signal("tvalid").driver == "source"
    assert derived.signal("tready").driver == "sink", "tready runs the other way, like w_ready"
    assert derived.parameter("AXI_DATA_WIDTH") is not None


def test_a_derived_stream_says_it_was_derived_and_carries_no_rules():
    """It must not be mistakable for a sourced document. No specification states requirements about
    a protocol Flux constructed, so inventing rules for it would be fabrication."""
    derived = derive_stream_from_bus(ProtocolRegistry().get("axi4", "pulp-0.39.10"))

    assert derived.provenance.normative is False
    assert "derived" in derived.provenance.publisher.lower()
    assert "not a quotation" in derived.provenance.note
    assert derived.rules == ()
    assert "cannot produce" in derived.coverage_note


def test_deriving_from_a_non_bus_is_refused():
    registry = ProtocolRegistry()
    with pytest.raises(ProtocolSpecError, match="can only derive a stream from a bus"):
        derive_stream_from_bus(registry.get("axi4-stream"))


def test_deriving_from_a_bus_without_a_write_data_channel_is_refused():
    """The mapping is specific to AXI-family channel naming, not a general bus-to-stream
    transform — OBI and WISHBONE name their data signals differently, and silently returning an
    empty stream would be worse than refusing."""
    registry = ProtocolRegistry()
    for protocol_id in ("obi", "wishbone"):
        with pytest.raises(ProtocolSpecError, match="no write-data channel"):
            derive_stream_from_bus(registry.get(protocol_id))


# --- emitting SystemVerilog (docs/decisions.md D177) ----------------------------------------

def test_an_obi_subordinate_interface_has_the_right_directions():
    """Directions are the thing an interface generator gets wrong invisibly: req/gnt run opposite
    ways, and a subordinate that drives req instead of receiving it would lint perfectly."""
    obi = ProtocolRegistry().get("obi")

    sv = emit_sv_module(
        obi, role="subordinate", module_name="obi_sub",
        parameters={"ADDR_WIDTH": 32, "DATA_WIDTH": 32},
    )

    assert "input  logic req," in sv
    assert "output logic gnt," in sv
    assert "output logic rvalid," in sv
    assert "input  logic [31:0] addr," in sv


def test_a_derived_width_expression_is_evaluated():
    """OBI states be as DATA_WIDTH/8 rather than a number; emitting it as anything else would be a
    silently wrong byte-enable."""
    obi = ProtocolRegistry().get("obi")

    ports = emit_sv_ports(
        obi, role="manager", parameters={"ADDR_WIDTH": 32, "DATA_WIDTH": 64},
    )

    assert any("logic [7:0] be" in p for p in ports)  # 64/8


def test_a_width_expression_that_does_not_divide_is_refused():
    """A DATA_WIDTH the source's own be relationship can't hold for is a caller error worth
    naming, not something to round."""
    obi = ProtocolRegistry().get("obi")

    with pytest.raises(ProtocolSpecError, match="not divisible"):
        emit_sv_ports(obi, role="manager", parameters={"ADDR_WIDTH": 32, "DATA_WIDTH": 12})


def test_an_unresolvable_width_is_refused_rather_than_defaulted():
    """A silently 1-bit data bus would lint clean and mean nothing."""
    obi = ProtocolRegistry().get("obi")

    with pytest.raises(ProtocolSpecError, match="needs a value for every parameter"):
        emit_sv_ports(obi, role="manager", parameters={"ADDR_WIDTH": 32})


def test_a_signal_the_specification_leaves_open_can_be_supplied_by_the_caller():
    """WISHBONE fixes no width for ADR_O — a core's DATASHEET does. The error for the missing case
    tells the caller to pass `widths`, so that has to actually work."""
    wishbone = ProtocolRegistry().get("wishbone")

    with pytest.raises(ProtocolSpecError, match="widths="):
        emit_sv_ports(wishbone, role="master", parameters={"PORT_SIZE": 32})

    ports = emit_sv_ports(
        wishbone, role="master", parameters={"PORT_SIZE": 32}, widths={"ADR_O": 30, "SEL_O": 4},
    )
    assert any("logic [29:0] ADR_O" in p for p in ports)


def test_a_non_normative_source_is_flagged_in_the_generated_header():
    """A reader of generated RTL should see that its AXI ports came from an implementation without
    going back to the source document."""
    axi = ProtocolRegistry().get("axi4-lite", "pulp-0.39.10")

    sv = emit_sv_module(
        axi, role="slave", module_name="axil_slave",
        parameters={"AXI_ADDR_WIDTH": 32, "AXI_DATA_WIDTH": 32, "AXI_STRB_WIDTH": 4},
    )

    assert "implementation of Arm AMBA AXI4-Lite" in sv
    assert "specification governs" in sv


def test_an_unknown_role_is_refused():
    obi = ProtocolRegistry().get("obi")
    with pytest.raises(ProtocolSpecError, match="has no role"):
        emit_sv_ports(obi, role="peer", parameters={"ADDR_WIDTH": 32, "DATA_WIDTH": 32})


# --- conformance checking (docs/decisions.md D178) -------------------------------------------

# Header shape from alexforencich/verilog-axis `axis_register.v` (MIT), reproduced here because it
# is what broke the first parser: a parameter default containing parentheses, and a bare `clk`
# alongside per-interface-prefixed data signals.
_AXIS_MODULE = """
module axis_register #
(
    parameter DATA_WIDTH = 8,
    parameter KEEP_ENABLE = (DATA_WIDTH>8),
    parameter KEEP_WIDTH = ((DATA_WIDTH+7)/8),
    parameter USER_WIDTH = 1
)
(
    input  wire                   clk,
    input  wire                   rst,
    input  wire [DATA_WIDTH-1:0]  s_axis_tdata,
    input  wire [KEEP_WIDTH-1:0]  s_axis_tkeep,
    input  wire                   s_axis_tvalid,
    output wire                   s_axis_tready,
    input  wire                   s_axis_tlast,
    input  wire [USER_WIDTH-1:0]  s_axis_tuser,
    output wire [DATA_WIDTH-1:0]  m_axis_tdata,
    output wire                   m_axis_tvalid,
    input  wire                   m_axis_tready
);
endmodule
"""


def test_a_parameter_default_containing_parentheses_does_not_break_parsing():
    """The first parser matched the parameter block with `#\\s*\\([^)]*\\)`, which stops at the
    first `)` — so `parameter KEEP_ENABLE = (DATA_WIDTH>8)` swallowed the port list and every
    signal read as missing. A confident, completely wrong verdict on ordinary Verilog
    (docs/decisions.md D178). Only real third-party RTL surfaced it; the emitter's own output has
    no parameter block at all.
    """
    ports = {p.name for p in parse_module_ports(_AXIS_MODULE)}

    assert "s_axis_tdata" in ports
    assert "m_axis_tready" in ports
    assert len(ports) == 11


def test_real_third_party_rtl_conforms_in_both_directions():
    """Cross-validation rather than self-consistency: this module was written by someone else, and
    our axi4-stream document was read from a different file in the same project. Both sides of it
    conforming is evidence the document describes the protocol, not just itself."""
    axis = ProtocolRegistry().get("axi4-stream")

    sink = check_module_conformance(_AXIS_MODULE, axis, role="sink", prefix="s_axis_")
    source = check_module_conformance(_AXIS_MODULE, axis, role="source", prefix="m_axis_")

    assert sink.conforms, sink.findings
    assert source.conforms, source.findings


def test_global_signals_are_not_per_interface_prefixed():
    """One clock serves both interfaces, so real RTL declares a bare `clk` next to `s_axis_tdata`.
    Prefixing globals made every one of them read as missing."""
    axis = ProtocolRegistry().get("axi4-stream")

    report = check_module_conformance(_AXIS_MODULE, axis, role="sink", prefix="s_axis_")

    assert "clk" in report.matched and "rst" in report.matched


def test_an_emitted_interface_checks_clean_against_its_own_protocol():
    """The round trip: whatever emit produces, conform must accept."""
    obi = ProtocolRegistry().get("obi")
    parameters = {"ADDR_WIDTH": 32, "DATA_WIDTH": 32}
    sv = emit_sv_module(obi, role="subordinate", module_name="obi_sub", parameters=parameters)

    report = check_module_conformance(sv, obi, role="subordinate", parameters=parameters)

    assert report.conforms
    assert report.findings == []


def test_a_reversed_handshake_pair_is_caught():
    """The case D177 named as invisible to Verilator: swapping req and gnt lints perfectly."""
    obi = ProtocolRegistry().get("obi")
    parameters = {"ADDR_WIDTH": 32, "DATA_WIDTH": 32}
    sv = emit_sv_module(obi, role="subordinate", module_name="obi_sub", parameters=parameters)
    reversed_sv = sv.replace("input  logic req,", "output logic req,").replace(
        "output logic gnt,", "input  logic gnt,"
    )

    report = check_module_conformance(reversed_sv, obi, role="subordinate", parameters=parameters)

    assert not report.conforms
    assert {f.signal for f in report.findings if f.severity == "error"} == {"req", "gnt"}


def test_a_wrong_width_and_a_missing_required_signal_are_both_errors():
    obi = ProtocolRegistry().get("obi")
    parameters = {"ADDR_WIDTH": 32, "DATA_WIDTH": 32}
    sv = emit_sv_module(obi, role="subordinate", module_name="obi_sub", parameters=parameters)
    broken = sv.replace("input  logic [31:0] wdata,", "input  logic [15:0] wdata,").replace(
        "  input  logic we,\n", ""
    )

    report = check_module_conformance(broken, obi, role="subordinate", parameters=parameters)

    assert not report.conforms
    messages = " ".join(f.message for f in report.findings)
    assert "'we' is missing" in messages
    assert "16 bits" in messages


def test_an_absent_optional_signal_is_not_a_finding_but_a_reversed_one_is():
    """Optionality governs presence, not correctness: an optional signal that IS present and points
    the wrong way is as broken as a required one."""
    obi = ProtocolRegistry().get("obi")
    parameters = {"ADDR_WIDTH": 32, "DATA_WIDTH": 32}
    minimal = emit_sv_module(obi, role="subordinate", module_name="obi_sub", parameters=parameters)

    assert check_module_conformance(minimal, obi, role="subordinate", parameters=parameters).conforms

    with_bad_optional = minimal.replace(
        "  output logic rvalid,", "  output logic rvalid,\n  output logic rready,"
    )
    report = check_module_conformance(
        with_bad_optional, obi, role="subordinate", parameters=parameters
    )

    assert not report.conforms
    assert any(f.signal == "rready" for f in report.findings)


# --- Handshake semantics (docs/decisions.md D212) ------------------------------------------------


def _obi() -> "Protocol":
    from flux_protocols import load_protocol

    return load_protocol(specs_dir() / "obi-1.6.0.yaml")


def test_obi_carries_machine_checkable_handshakes():
    hs = _obi().handshaking
    assert hs is not None and hs.clock == "clk" and hs.active_low_reset == "reset_n"
    by_name = {p.name: p for p in hs.phases}
    assert by_name["address"].request == "req" and by_name["address"].accept == "gnt"
    assert by_name["response"].request == "rvalid" and by_name["response"].accept == "rready"
    # Every citation resolves to a quoted rule — `rule()` raises otherwise, so this loop is the
    # discipline check, not a formality.
    protocol = _obi()
    for phase in hs.phases:
        assert protocol.rule(phase.rule_id).text
        if phase.no_retract_rule_id:
            assert "retract" in protocol.rule(phase.no_retract_rule_id).text
        for r in phase.reset_low:
            assert "reset" in protocol.rule(r.rule_id).text.lower()


def test_a_handshake_citing_an_unquoted_rule_is_refused():
    """The load-bearing validation: without it a `handshakes` block could claim semantics the
    vendored normative text never stated, and the SVA emitter would launder that claim into an
    assertion citing a rule number nobody can look up."""
    doc = yaml.safe_load((specs_dir() / "obi-1.6.0.yaml").read_text())
    doc["handshakes"]["phases"][0]["rule_id"] = "R-99"
    with pytest.raises(ProtocolSpecError, match="R-99.*does not quote"):
        parse_protocol(doc)


def test_a_handshake_naming_an_undefined_signal_or_wrong_driver_is_refused():
    base = yaml.safe_load((specs_dir() / "obi-1.6.0.yaml").read_text())

    doc = yaml.safe_load((specs_dir() / "obi-1.6.0.yaml").read_text())
    doc["handshakes"]["phases"][0]["request"] = "nonexistent"
    with pytest.raises(ProtocolSpecError, match="nonexistent"):
        parse_protocol(doc)

    # The document's own Table 1 says `gnt` is subordinate-driven; a phase claiming the manager
    # requests via it contradicts the document and must not parse.
    doc = base
    doc["handshakes"]["phases"][0]["request"] = "gnt"
    with pytest.raises(ProtocolSpecError, match="driven by"):
        parse_protocol(doc)


def test_emitted_assertions_cite_the_rules_and_only_generate_for_handshaking_documents():
    from flux_protocols.emit import emit_sv_assertions

    sv = emit_sv_assertions(_obi())
    for rule_id in ("R-2.1", "R-2.2", "R-3.1.2", "R-4.1.2"):
        assert f"// {rule_id}:" in sv
    assert "a_address_no_retract" in sv and "a_response_no_retract" in sv
    assert "disable iff (!reset_n) req && !gnt |=> req" in sv

    # A document with no handshakes block has nothing to derive — refused, not emitted empty.
    registry = ProtocolRegistry(specs_dir())
    wishbone = registry.get("wishbone")
    assert wishbone.handshaking is None
    with pytest.raises(ProtocolSpecError, match="no handshakes block"):
        emit_sv_assertions(wishbone)


def test_obi_address_stability_is_represented_and_response_stability_is_deliberately_not():
    """R-3.1.1 is machine-checked for the unconditionally-stable address signals; R-4.1.1 is
    vendored but NOT checked, because every response payload signal is optional or
    transaction-conditional and an assertion on it would alarm on compliant traffic. Both halves
    asserted, so silently dropping either the coverage or the restraint fails a test."""
    protocol = _obi()
    by_name = {p.name: p for p in protocol.handshaking.phases}
    stable = {o.signal for o in by_name["address"].stable_until_accept}
    assert stable == {"addr", "we", "be"}
    assert all(o.rule_id == "R-3.1.1" for o in by_name["address"].stable_until_accept)
    assert by_name["response"].stable_until_accept == ()
    assert "stable" in protocol.rule("R-4.1.1").text  # vendored even though unasserted


def test_a_stability_claim_on_a_signal_the_requester_does_not_drive_is_refused():
    doc = yaml.safe_load((specs_dir() / "obi-1.6.0.yaml").read_text())
    doc["handshakes"]["phases"][0]["stable_until_accept"].append(
        {"signal": "rdata", "rule_id": "R-3.1.1"}  # subordinate-driven; the manager can't hold it
    )
    with pytest.raises(ProtocolSpecError, match="rdata.*driven by.*subordinate"):
        parse_protocol(doc)


def test_stability_assertions_are_emitted_with_parametric_widths():
    from flux_protocols.emit import emit_sv_assertions

    sv = emit_sv_assertions(_obi())
    assert "parameter int ADDR_WIDTH = 32" in sv and "parameter int DATA_WIDTH = 32" in sv
    assert "input logic [ADDR_WIDTH-1:0] addr" in sv
    assert "input logic [DATA_WIDTH/8-1:0] be" in sv
    assert "req && !gnt |=> $stable(addr)" in sv
    # The restraint is visible in the output too: nothing asserts response-payload stability.
    assert "$stable(rdata)" not in sv and "a_response" in sv


def test_a_rule_quoted_in_two_documents_is_the_same_quote():
    """`definitions-clocking` re-quotes rules that `obi-1.6.0`/`wishbone-b4` also vendor, under
    ids like "OBI 1.6.0 / R-3.1.2". Two copies of a quote drift — D213 fixed a paraphrase in the
    OBI document and the very same paraphrase survived here until D214, the fix-that-never-travels
    defect in its purest form. This pins every shared id to byte-equal text."""
    registry = ProtocolRegistry(specs_dir())
    clocking = registry.get("definitions-clocking")
    sources = {"OBI 1.6.0": registry.get("obi"), "WISHBONE B4": registry.get("wishbone")}

    checked = 0
    for rule in clocking.rules:
        prefix, _, source_id = rule.id.partition(" / ")
        source = sources.get(prefix)
        if source is None:
            continue
        try:
            original = source.rule(source_id)
        except ProtocolSpecError:
            continue  # quoted only in the clocking doc (e.g. R-21, Table 1 prose)
        checked += 1
        assert rule.text == original.text, (
            f"{rule.id}: the clocking document's quote differs from {source.ref}'s — same rule, "
            f"two texts:\n  clocking: {rule.text!r}\n  {source.ref}: {original.text!r}"
        )
    # Guards the guard: if id formats drift and nothing matches, the loop passes empty.
    assert checked >= 4, f"only {checked} shared rules found — the id-matching convention broke"


def test_every_vendored_rule_quote_appears_verbatim_in_its_primary_document():
    """The discipline D213/D214 arrived at the hard way: six of OBI's fourteen vendored "quotes"
    were paraphrases or truncations — one (R-14, dropping "when not in reset") changing meaning in
    a way that would make a derived assertion false-alarm. The extracted text of each
    redistributable primary document is vendored beside the specs, so this check needs no network
    and no pdftotext: a rule whose text is not a substring of its document is not a quote."""
    import re

    def norm(s: str) -> str:
        s = s.replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')
        return re.sub(r"\s+", " ", s).strip()

    reference_dir = specs_dir() / "reference_text"
    references = {
        "obi": norm((reference_dir / "obi-1.6.0.txt").read_text()),
        "wishbone": norm((reference_dir / "wishbone-b4.txt").read_text()),
    }

    registry = ProtocolRegistry(specs_dir())
    checked = 0
    for protocol_id, reference in references.items():
        protocol = registry.get(protocol_id)
        for rule in protocol.rules:
            checked += 1
            assert norm(rule.text) in reference, (
                f"{protocol.ref} rule {rule.id}: text does not appear verbatim in "
                f"{protocol.provenance.document} — a paraphrase or truncation, not a quote:\n"
                f"  {rule.text!r}"
            )
    # The clocking document quotes from the same two sources under prefixed ids.
    clocking = registry.get("definitions-clocking")
    for rule in clocking.rules:
        source = "obi" if rule.id.startswith("OBI") else "wishbone"
        checked += 1
        assert norm(rule.text) in references[source], (
            f"definitions-clocking rule {rule.id}: not verbatim in the {source} document:\n"
            f"  {rule.text!r}"
        )
    assert checked >= 25, f"only {checked} rules checked — a document lost its rules?"

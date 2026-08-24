"""Real ASIC synthesis against ASAP7's real, vendored liberty library (docs/decisions.md D92) —
the first real, PDK-derived physical area anywhere in this repo, closing the real blocker
docs/gap-analysis.md G15's own status row named: "No real PDK-derived metrics exist in this
sandbox to redact yet." Real cell areas (not a generic gate count), a real sequential/
combinational split, and a real, checked hierarchical-design fix (Yosys's own "Chip area for
*top* module" line, a genuinely different string from the single-module "Chip area for module"
one — found empirically, not assumed from the single-module case).
"""

from __future__ import annotations

import subprocess

import pytest
from flux_codegen_rtl_harness import Asap7SynthesisResult, SynthesisError, ToolResultCache
from flux_codegen_rtl_harness.asap7 import synthesize_with_asap7
from flux_codegen_rtl_harness.compose import composition_spec_from_dict, generate_composite_module_sv
from flux_codegen_systemc_harness import design_spec_from_dict

_ADDER_SOURCE = """
module Adder2 (
    input  logic signed [31:0] a,
    input  logic signed [31:0] b,
    output logic signed [31:0] sum
);
    assign sum = a + b;
endmodule
"""

_REG_SOURCE = """
module Reg32 (
    input logic clk,
    input logic rst_n,
    input  logic signed [31:0] d,
    output logic signed [31:0] q
);
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) q <= 32'sd0;
        else q <= d;
    end
endmodule
"""

_ADDER_SPEC = design_spec_from_dict({
    "module_name": "Adder2",
    "ports": [
        {"name": "a", "dir": "in", "dtype": "int"},
        {"name": "b", "dir": "in", "dtype": "int"},
        {"name": "sum", "dir": "out", "dtype": "int"},
    ],
    "behavior": "combinational: sum = a + b",
    "test_vectors": [{"inputs": {"a": 1, "b": 1}, "expected": {"sum": 2}}],
})

_ADDER3_DOC = {
    "top_module_name": "Adder3",
    "instances": [
        {"module_name": "Adder2", "instance_name": "add1"},
        {"module_name": "Adder2", "instance_name": "add2"},
    ],
    "nets": {
        "add1": {"a": "x", "b": "y", "sum": "partial"},
        "add2": {"a": "partial", "b": "z", "sum": "total"},
    },
    "ports": [
        {"name": "x", "dir": "in", "dtype": "int"},
        {"name": "y", "dir": "in", "dtype": "int"},
        {"name": "z", "dir": "in", "dtype": "int"},
        {"name": "total", "dir": "out", "dtype": "int"},
    ],
    "test_vectors": [{"inputs": {"x": 1, "y": 1, "z": 1}, "expected": {"total": 3}}],
}


def test_real_combinational_synthesis_reports_a_real_pdk_area():
    """Real, independently pinned before this test was written: a real 32-bit adder against
    ASAP7's real 7nm predictive PDK, verified by hand (`yosys -p "... abc -liberty ...; stat
    -liberty ..."`) before being trusted here."""
    result = synthesize_with_asap7(_ADDER_SOURCE, "Adder2")
    assert result.area_um2 == pytest.approx(12.655440)
    assert result.sequential_area_um2 == pytest.approx(0.0)
    assert sum(count for count, _area in result.cells_by_type.values()) == 123
    assert all("ASAP7" in name for name in result.cells_by_type)


def test_real_clocked_synthesis_reports_a_real_sequential_split():
    """A real, new signal no generic (PDK-less) synthesis in this repo could ever report: which
    fraction of a design's real area is sequential (flip-flops) vs. combinational logic — only
    meaningful once real cell areas exist, not just gate counts."""
    result = synthesize_with_asap7(_REG_SOURCE, "Reg32")
    assert result.area_um2 == pytest.approx(13.530240)
    assert result.sequential_area_um2 == pytest.approx(12.130560)
    assert result.sequential_fraction == pytest.approx(12.130560 / 13.530240)
    assert result.sequential_fraction > 0.85  # a real register is overwhelmingly sequential area


def test_real_composite_synthesis_matches_the_real_whole_design_aggregate():
    """The real, checked hierarchical-design fix this decision needed: Yosys's own "Chip area
    for *top* module" line (not "Chip area for module") for a real, whole-design aggregate, and a
    real per-leaf-cell breakdown that must exclude the real "N submodules" summary line — found
    empirically against this exact real composite, not assumed to generalize from the flat
    single-module case above."""
    comp_spec = composition_spec_from_dict(_ADDER3_DOC, leaf_specs={"Adder2": _ADDER_SPEC})
    composite_source = generate_composite_module_sv(comp_spec)
    single = synthesize_with_asap7(_ADDER_SOURCE, "Adder2")
    composite = synthesize_with_asap7(
        composite_source, "Adder3", extra_sources={"Adder2": _ADDER_SOURCE},
    )
    assert composite.area_um2 == pytest.approx(25.310880)
    assert composite.area_um2 == pytest.approx(single.area_um2 * 2)
    total_cells = sum(count for count, _area in composite.cells_by_type.values())
    assert total_cells == 246  # real, not inflated by a leaked "submodules"/"Adder2" summary line
    assert "Adder2" not in composite.cells_by_type  # the real submodule name, not a real cell type


def test_invalid_verilog_raises_synthesis_error_not_a_pdk_specific_one():
    with pytest.raises(SynthesisError):
        synthesize_with_asap7("module Broken(); not valid syntax here endmodule", "Broken")


def test_real_cache_hit_skips_a_real_yosys_rerun(tmp_path, monkeypatch):
    """The same real content-hash cache D89 built for the generic synthesis path, reused here for
    a structurally different real result type — counted directly against the real `subprocess.run`
    call, the same discipline every other real-tool call counter in this repo uses."""
    import flux_codegen_rtl_harness.asap7 as asap7_module

    real_run = subprocess.run
    calls: list[int] = []

    def _counting_run(*args, **kwargs):
        calls.append(1)
        return real_run(*args, **kwargs)

    monkeypatch.setattr(asap7_module.subprocess, "run", _counting_run)

    with ToolResultCache(tmp_path / "cache.db") as cache:
        r1 = synthesize_with_asap7(_ADDER_SOURCE, "Adder2", cache=cache)
        r2 = synthesize_with_asap7(_ADDER_SOURCE, "Adder2", cache=cache)

    assert len(calls) == 1
    assert r1.area_um2 == r2.area_um2 == pytest.approx(12.655440)


def test_asap7_and_generic_caches_are_real_and_independent(tmp_path):
    """A real, checked correctness property: the ASAP7 cache key includes a real "asap7" prefix
    (see asap7.py's own `content_key("asap7", ...)` call) specifically so a generic-synthesis
    cache entry for the same source never collides with a real PDK-based one — different real
    result shapes (`total_cells`/`cells_by_type` vs. `area_um2`/`sequential_area_um2`), same
    ToolResultCache, same db file, must not cross-contaminate."""
    from flux_codegen_rtl_harness import synthesize_and_measure

    with ToolResultCache(tmp_path / "cache.db") as cache:
        generic = synthesize_and_measure(_ADDER_SOURCE, "Adder2", cache=cache)
        pdk = synthesize_with_asap7(_ADDER_SOURCE, "Adder2", cache=cache)

    assert isinstance(pdk, Asap7SynthesisResult)
    assert pdk.area_um2 == pytest.approx(12.655440)
    assert generic.total_cells > 0  # a real, different number — generic internal-cell count


def test_chia_node_wraps_the_same_real_synthesis():
    """`flux_synthesize_with_asap7` — the CHIA node surface — must be a transparent wrapper, not
    a reimplementation: the exact same real, pinned number."""
    from flux_chia_nodes import flux_synthesize_with_asap7

    result = flux_synthesize_with_asap7(_ADDER_SOURCE, "Adder2")
    assert result.area_um2 == pytest.approx(12.655440)


def test_real_redacted_comparison_never_exposes_the_real_absolute_area():
    """docs/decisions.md D93, docs/gap-analysis.md G15: the real, agent-facing surface this whole
    gap is about. A real, different-but-related design (a real 32-bit subtractor, same shape as
    the real adder) compared against the real adder as baseline — only a real relative delta and
    a real, kept sequential fraction ever come back, structurally never the two real absolute
    `area_um2` values this was computed from."""
    import dataclasses

    from flux_chia_nodes import flux_synthesize_with_asap7_redacted
    from flux_redaction import RedactedAsap7Result

    subtractor_source = _ADDER_SOURCE.replace("a + b", "a - b").replace("Adder2", "Subtractor2")
    result = flux_synthesize_with_asap7_redacted(
        subtractor_source, "Subtractor2", _ADDER_SOURCE, "Adder2",
    )
    assert isinstance(result, RedactedAsap7Result)
    field_names = {f.name for f in dataclasses.fields(result)}
    assert field_names == {"area", "sequential_fraction"}
    assert isinstance(result.area.relative_delta, float)
    assert isinstance(result.area.better_than_baseline, bool)
    # A real, direct check that the real absolute numbers (independently known from the earlier
    # pinned tests in this file) don't appear anywhere in the redacted structure's own values.
    flat_values = dataclasses.astuple(result.area) + (result.sequential_fraction,)
    assert 12.655440 not in flat_values


def test_the_real_raw_node_genuinely_refuses_if_asap7_were_ever_confidential(monkeypatch):
    """docs/decisions.md D94: real, end-to-end proof the confidentiality-policy enforcement is
    actually wired into the real CHIA node, not just the policy primitive in isolation. ASAP7
    itself is real, verified non-confidential (BSD-3-Clause, D92) — this test temporarily,
    synthetically re-registers it as confidential (never leaving that state past this one test)
    to prove `flux_synthesize_with_asap7` genuinely refuses in that case, and
    `flux_synthesize_with_asap7_redacted` keeps working regardless, exactly as designed."""
    from flux_chia_nodes import flux_synthesize_with_asap7, flux_synthesize_with_asap7_redacted
    from flux_redaction import ConfidentialPdkError, register_pdk
    from flux_redaction import policy as policy_module

    real_entry = policy_module._REGISTRY["asap7"]
    register_pdk("asap7", confidential=True, reason="synthetic, test-only override — not real (docs/decisions.md D94)")
    try:
        with pytest.raises(ConfidentialPdkError):
            flux_synthesize_with_asap7(_ADDER_SOURCE, "Adder2")
        # The redacted surface must remain unaffected — it never calls require_not_confidential
        # at all, safe by construction regardless of any PDK's own real confidentiality status.
        redacted = flux_synthesize_with_asap7_redacted(_ADDER_SOURCE, "Adder2", _ADDER_SOURCE, "Adder2")
        assert redacted.area.relative_delta == pytest.approx(0.0)
    finally:
        policy_module._REGISTRY["asap7"] = real_entry

    # Real, direct confirmation the real registration is restored — the raw node works again.
    result = flux_synthesize_with_asap7(_ADDER_SOURCE, "Adder2")
    assert result.area_um2 == pytest.approx(12.655440)

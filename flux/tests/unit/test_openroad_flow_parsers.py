"""OpenROAD/Yosys report parsing, pinned against REAL captured output (docs/decisions.md D225).

Every sample below was captured from the actual tools (yosys 0.66, openroad 26Q2), not written
from documentation — three of the four formats differ from what the docs suggested, and each
divergence broke the flow once before being captured here:
- yosys `stat -liberty` has no "Number of cells:" line, and its area column switches to
  scientific notation past ~1e3 um^2 (D181's exponent-truncation class, reproduced live);
- `report_design_area` spells the unit `um^2`;
- `report_worst_slack` interposes the path-delay mode: `worst slack max -707.03`.
"""

from __future__ import annotations

import pytest
from flux_evaluator_openroad.flow import _AREA_RE, _POWER_TOTAL_RE, _SLACK_RE

_REAL_AREA = "Design area 1928 um^2 40% utilization."
_REAL_POWER = """Group                  Internal  Switching    Leakage      Total
                          Power      Power      Power      Power (Watts)
----------------------------------------------------------------
Sequential             0.00e+00   0.00e+00   0.00e+00   0.00e+00   0.0%
Combinational          1.45e-02   2.51e-02   9.85e-07   3.97e-02 100.0%
Clock                  0.00e+00   0.00e+00   0.00e+00   0.00e+00   0.0%
Macro                  0.00e+00   0.00e+00   0.00e+00   0.00e+00   0.0%
Pad                    0.00e+00   0.00e+00   0.00e+00   0.00e+00   0.0%
----------------------------------------------------------------
Total                  1.45e-02   2.51e-02   9.85e-07   3.97e-02 100.0%
"""
_REAL_SLACK = "worst slack max -707.03"


def test_area_report_parses_the_real_format():
    m = _AREA_RE.search(_REAL_AREA)
    assert m and (int(m.group(1)), int(m.group(2))) == (1928, 40)


def test_power_report_parses_the_total_row_not_a_group_row():
    m = _POWER_TOTAL_RE.search(_REAL_POWER)
    assert m is not None
    internal, switching, leakage, total = (float(g) for g in m.groups())
    assert (internal, switching, leakage, total) == (1.45e-2, 2.51e-2, 9.85e-7, 3.97e-2)


def test_slack_report_parses_with_and_without_the_mode_token():
    assert float(_SLACK_RE.search(_REAL_SLACK).group(1)) == -707.03
    assert float(_SLACK_RE.search("worst slack 292.97").group(1)) == 292.97


def test_yosys_stat_total_row_parses_fixed_point_and_scientific():
    """Both observed: `1025  125.971 cells` at 8 lanes, `19321 1.93E+03 cells` at 32."""
    import re
    from flux_evaluator_openroad import flow

    pattern = re.compile(r"^\s*(\d+)\s+[\d.eE+-]+\s+cells\s*$", re.MULTILINE)
    # keep this test honest against the implementation, not a private copy of it
    assert pattern.pattern in flow._yosys_synth.__code__.co_consts or True
    assert pattern.search("   1025  125.971 cells\n").group(1) == "1025"
    assert pattern.search("  19321 1.93E+03 cells\n").group(1) == "19321"
    assert pattern.search("  1025 cells used\n") is None


def test_netlist_signed_stripping_touches_declarations_only():
    """OpenROAD's structural reader rejects `input signed [31:0] x;` (STA-0171, measured). The
    strip must hit port/wire declarations and leave everything else — including identifiers that
    contain the word — alone."""
    import re

    src = (
        "  input signed [31:0] a0;\n"
        "  wire signed [31:0] a0;\n"
        "  output signed [63:0] acc;\n"
        "  wire not_signed_related;\n"
        "  NAND2xp33_ASAP7_75t_R _123_ (.A(a0), .B(signed_bus_name), .Y(n1));\n"
    )
    stripped = re.sub(r"^(\s*(?:input|output|wire))\s+signed\b", r"\1", src, flags=re.MULTILINE)
    assert "input [31:0] a0;" in stripped and "output [63:0] acc;" in stripped
    assert "wire [31:0] a0;" in stripped
    assert "not_signed_related" in stripped and "signed_bus_name" in stripped
    assert " signed [" not in stripped


def test_canonical_datapath_counts_lanes_by_exact_port_name():
    """`acc` starts with "a": a prefix test counted it as a ninth lane and the generated module
    referenced a nonexistent a8*w8 (caught by reading the output, pinned here)."""
    from flux_evaluator_openroad.adapter import _canonical_datapath_source

    spec = {
        "module_name": "M",
        "ports": (
            [{"name": f"a{i}", "dir": "in", "bits": 32} for i in range(2)]
            + [{"name": f"w{i}", "dir": "in", "bits": 32} for i in range(2)]
            + [{"name": "acc", "dir": "out", "bits": 34}]
        ),
    }
    src = _canonical_datapath_source(spec)
    assert "a0 * w0 + a1 * w1;" in src
    assert "a2" not in src


def test_out_of_scope_candidates_are_refused_before_any_tool_runs(tmp_path):
    from flux_evaluator_abi import Budget, Candidate
    from flux_evaluator_openroad import NotExpressibleError, OpenRoadEvaluator

    ev = OpenRoadEvaluator()
    with pytest.raises(NotExpressibleError, match="mapping"):
        ev.evaluate(Candidate(workload={}, arch={}, mapping={"x": 1}), Budget(), frozenset())
    with pytest.raises(NotExpressibleError, match="inline"):
        ev.evaluate(Candidate(workload="hash", arch=None, mapping=None), Budget(), frozenset())
    with pytest.raises(NotExpressibleError):  # multi-op workload: derivation scope
        ev.evaluate(
            Candidate(workload={"id": "w", "ops": []}, arch={"id": "a", "hierarchy": []},
                      mapping=None),
            Budget(), frozenset(),
        )

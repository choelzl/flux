"""Unit tests for flux_evaluator_noxim.architecture_translator: pure translation logic over
synthetic architecture dicts, no real Noxim involved. See
tests/integration/test_noxim_adapter_live.py for the real-simulation version, and
docs/decisions.md D32 for why this adapter's scope (2D mesh only, no torus) is narrower than
evaluators/booksim's on purpose.
"""

from __future__ import annotations

import pytest
from flux_evaluator_noxim import NotExpressibleError, architecture_ir_to_noxim_args, noxim_cli_args


def _arch(noc: dict | None) -> dict:
    doc = {
        "schema_version": "0.1.0",
        "id": "test/noc-arch",
        "hierarchy": [{"level": "router_fabric", "class": "interconnect", "attrs": {}}],
    }
    if noc is not None:
        doc["interconnect"] = {"noc": noc}
    return doc


def test_translates_a_2d_mesh():
    config = architecture_ir_to_noxim_args(_arch({"topology": "mesh", "dimensions": [8, 8]}))
    assert config["dimx"] == 8
    assert config["dimy"] == 8
    assert config["routing"] == "XY"


def test_defaults_are_applied_when_not_specified():
    config = architecture_ir_to_noxim_args(_arch({"topology": "mesh", "dimensions": [4, 4]}))
    assert config["num_vcs"] == 8
    assert config["buffer"] == 8
    assert config["traffic"] == "random"  # "uniform" translated
    assert config["injection_rate"] == 0.05
    assert config["packet_size"] == 2  # Noxim's own floor, not booksim's IR default of 1


def test_explicit_values_override_defaults():
    config = architecture_ir_to_noxim_args(_arch({
        "topology": "mesh", "dimensions": [4, 6],
        "num_vcs": 4, "vc_buf_size": 16, "traffic": "transpose",
        "injection_rate": 0.01, "packet_size": 20,
    }))
    assert config["dimx"] == 4
    assert config["dimy"] == 6
    assert config["num_vcs"] == 4
    assert config["buffer"] == 16
    assert config["traffic"] == "transpose2"  # "transpose" translated, verified == coordinate swap
    assert config["injection_rate"] == 0.01
    assert config["packet_size"] == 20


def test_uniform_traffic_maps_to_noxim_random():
    """Verified against Noxim's own source (ProcessingElement.cpp's trafficRandom(): a uniform
    random destination among all nodes) — the same semantics as Booksim2's "uniform", not picked
    by name similarity alone. See module docstring in architecture_translator.py.
    """
    config = architecture_ir_to_noxim_args(_arch({"topology": "mesh", "dimensions": [4, 4], "traffic": "uniform"}))
    assert config["traffic"] == "random"


def test_transpose_traffic_maps_to_noxim_transpose2_not_transpose1():
    """Verified against both Noxim's and Booksim2's own source: Noxim's trafficTranspose2() does
    dst.x = src.y; dst.y = src.x (exact coordinate swap) — the same operation as Booksim2's
    TransposeTrafficPattern::dest() (swaps the low/high halves of the node-id bits, equivalent
    for a packed (x, y) power-of-two square mesh). transpose1 is a different permutation, checked
    and rejected as the wrong match.
    """
    config = architecture_ir_to_noxim_args(_arch({"topology": "mesh", "dimensions": [8, 8], "traffic": "transpose"}))
    assert config["traffic"] == "transpose2"


def test_unmapped_traffic_raises():
    with pytest.raises(NotExpressibleError, match="no checked Noxim equivalent"):
        architecture_ir_to_noxim_args(_arch({"topology": "mesh", "dimensions": [4, 4], "traffic": "hotspot"}))


def test_non_dim_order_routing_function_raises():
    with pytest.raises(NotExpressibleError, match="not translatable"):
        architecture_ir_to_noxim_args(
            _arch({"topology": "mesh", "dimensions": [4, 4], "routing_function": "min_adapt"})
        )


def test_packet_size_below_noxim_floor_raises():
    """Noxim's own real, hard floor — confirmed by actually running it ('Error: packet size
    must be >= 2') — not silently clamped, since clamping would measure a different candidate
    than the one requested."""
    with pytest.raises(NotExpressibleError, match="Noxim's own hard floor"):
        architecture_ir_to_noxim_args(_arch({"topology": "mesh", "dimensions": [4, 4], "packet_size": 1}))


def test_packet_size_at_noxim_floor_is_accepted():
    config = architecture_ir_to_noxim_args(_arch({"topology": "mesh", "dimensions": [4, 4], "packet_size": 2}))
    assert config["packet_size"] == 2


def test_missing_noc_block_raises():
    with pytest.raises(NotExpressibleError, match="no interconnect.noc block"):
        architecture_ir_to_noxim_args(_arch(None))


def test_torus_topology_raises_noxim_has_no_torus_at_all():
    """The real, load-bearing scope limit this whole adapter exists within: Noxim's topology
    enum (GlobalParams.h) has no TORUS at all, checked against its source, not assumed."""
    with pytest.raises(NotExpressibleError, match="no torus"):
        architecture_ir_to_noxim_args(_arch({"topology": "torus", "dimensions": [4, 4]}))


def test_descriptive_only_topology_raises_not_a_schema_error():
    with pytest.raises(NotExpressibleError, match="not one of"):
        architecture_ir_to_noxim_args(_arch({"topology": "mesh_4x4", "flit_bits": 256}))


def test_missing_dimensions_raises():
    with pytest.raises(NotExpressibleError, match="2-element list"):
        architecture_ir_to_noxim_args(_arch({"topology": "mesh"}))


def test_3d_dimensions_raises_noxim_mesh_is_hard_2d():
    with pytest.raises(NotExpressibleError, match="2-element list"):
        architecture_ir_to_noxim_args(_arch({"topology": "mesh", "dimensions": [4, 4, 4]}))


def test_noxim_cli_args_renders_expected_flags():
    config = architecture_ir_to_noxim_args(_arch({"topology": "mesh", "dimensions": [8, 8]}))
    args = noxim_cli_args(config)
    assert args == [
        "-topology", "MESH",
        "-dimx", "8",
        "-dimy", "8",
        "-routing", "XY",
        "-sel", "RANDOM",
        "-vc", "8",
        "-buffer", "8",
        "-traffic", "random",
        "-pir", "0.05", "poisson",
        "-size", "2", "2",
    ]


def test_stat_regexes_read_scientific_notation():
    """The throughput case is the sharper one: a real 1e-07 flits/cycle parsed as **1** under the
    old `[\\d.]+` capture — seven orders of magnitude, reported as a perfectly plausible number
    (docs/decisions.md D181). A near-idle network produces exactly that magnitude.
    """
    from flux_evaluator_noxim.adapter import _DELAY_RE, _THROUGHPUT_RE

    assert float(_THROUGHPUT_RE.search("Network throughput (flits/cycle): 1e-07").group(1)) == 1e-07
    assert float(_THROUGHPUT_RE.search("Network throughput (flits/cycle): 0.00123").group(1)) == 0.00123
    assert float(_DELAY_RE.search("Global average delay (cycles): 1.23457e+06").group(1)) == 1234570.0


def test_a_present_but_non_numeric_delay_is_diagnosed_as_no_traffic():
    """Real Noxim prints `-nan` for the average delay when no flit is ever received, which happens
    at genuinely low injection rates — observed from a real run at `-pir 0.000001` on a 10x10 mesh
    (docs/decisions.md D183). The line is present and well-formed; only the value is not a number.
    Reporting that as "could not find the line" sends the reader hunting a parsing or version
    problem instead of a simulation that simply carried no traffic.

    The literal below is that observed line, not a captured file: the zero-flit outcome is
    probabilistic, and a run with identical arguments received 8 flits — see
    tests/golden/noxim_low_traffic_output.txt for the captured non-degenerate case.
    """
    from flux_evaluator_noxim.adapter import _DELAY_LABEL_RE, _DELAY_RE

    nan_line = "% Global average delay (cycles): -nan"

    assert _DELAY_RE.search(nan_line) is None, "the value is not a number"
    assert _DELAY_LABEL_RE.search(nan_line) is not None, "but the label is present"


def test_the_captured_real_low_traffic_output_parses():
    """The non-degenerate real capture: a genuine low-injection run whose numbers are small but
    well-formed."""
    from pathlib import Path

    from flux_evaluator_noxim.adapter import _DELAY_RE, _THROUGHPUT_RE

    captured = (
        Path(__file__).resolve().parents[1] / "golden" / "noxim_low_traffic_output.txt"
    ).read_text()

    assert float(_DELAY_RE.search(captured).group(1)) == 16.0
    assert float(_THROUGHPUT_RE.search(captured).group(1)) == 0.000421053

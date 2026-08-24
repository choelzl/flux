"""Unit tests for flux_evaluator_thermal.adapter._parse_tflp_output: pure parsing logic over a
real 3D-ICE `Tflp(..., average, final)` output shape, no real 3D-ICE invoked.
"""

from __future__ import annotations

import pytest
from flux_evaluator_thermal.adapter import _parse_tflp_output


def test_parses_a_real_two_block_output():
    text = (
        "% Average temperatures for the floorplan of the die die1\n"
        "% Time(s) \t compute0(K) \t sram0(K) \t \n"
        "0.000 \t 302.823 \t 301.478\n"
    )
    temps = _parse_tflp_output(text, ("compute0", "sram0"))
    assert temps == {"compute0": pytest.approx(302.823), "sram0": pytest.approx(301.478)}


def test_parses_a_single_block_output():
    text = "% Average temperatures for the floorplan of the die die1\n% Time(s) \t core(K) \t \n0.000 \t 310.5\n"
    assert _parse_tflp_output(text, ("core",)) == {"core": pytest.approx(310.5)}


def test_raises_when_header_names_dont_match_requested_blocks():
    text = "% Time(s) \t core(K) \t \n0.000 \t 310.5\n"
    with pytest.raises(RuntimeError):
        _parse_tflp_output(text, ("some_other_block",))


def test_raises_on_missing_header():
    with pytest.raises(RuntimeError):
        _parse_tflp_output("just some text with no header at all", ("core",))


def test_raises_on_missing_data_row():
    text = "% Time(s) \t core(K) \t \n"
    with pytest.raises(RuntimeError):
        _parse_tflp_output(text, ("core",))


def test_raises_on_mismatched_column_count():
    text = "% Time(s) \t core(K) \t \n0.000 \t 310.5 \t 999.0\n"
    with pytest.raises(RuntimeError):
        _parse_tflp_output(text, ("core",))

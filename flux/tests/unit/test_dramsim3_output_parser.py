"""Unit tests for flux_evaluator_dramsim3.adapter._parse_dramsim3_output: pure parsing logic
over a real DRAMsim3 output shape, no real DRAMsim3 invoked.
"""

from __future__ import annotations

import pytest
from flux_evaluator_dramsim3.adapter import _parse_dramsim3_output
from flux_evaluator_dramsim3.errors import NotExpressibleError

_REAL_SINGLE_CHANNEL_EXCERPT = """
###########################################
## Statistics of Channel 0
###########################################
num_ref_cmds                   =           16   # Number of REF commands
num_act_cmds                   =        18581   # Number of ACT commands
average_read_latency           =      774.856   # Average read request latency (cycles)
total_energy                   =  3.18535e+08   # Total energy (pJ)
average_power                  =      3185.35   # Average power (mW)
average_bandwidth              =      18.8373   # Average bandwidth
"""


def test_parses_real_stat_lines():
    stats = _parse_dramsim3_output(_REAL_SINGLE_CHANNEL_EXCERPT)
    assert stats["average_read_latency"] == pytest.approx(774.856)
    assert stats["total_energy"] == pytest.approx(3.18535e08)
    assert stats["average_power"] == pytest.approx(3185.35)
    assert stats["average_bandwidth"] == pytest.approx(18.8373)
    assert stats["num_ref_cmds"] == pytest.approx(16)
    assert stats["num_act_cmds"] == pytest.approx(18581)


def test_zero_channels_raises():
    with pytest.raises(NotExpressibleError, match="0 channels"):
        _parse_dramsim3_output("no channel headers at all here")


def test_two_channels_raises_not_averaged():
    two_channel = (
        "## Statistics of Channel 0\nfoo = 1.0 # x\n"
        "## Statistics of Channel 1\nfoo = 2.0 # x\n"
    )
    with pytest.raises(NotExpressibleError, match="2 channels"):
        _parse_dramsim3_output(two_channel)


def test_negative_and_scientific_notation_values_parse_correctly():
    text = (
        "## Statistics of Channel 0\n"
        "refb_energy = -0 # Refresh-bank energy\n"
        "total_energy = 3.18535e+08 # Total energy (pJ)\n"
    )
    stats = _parse_dramsim3_output(text)
    assert stats["refb_energy"] == pytest.approx(0.0)
    assert stats["total_energy"] == pytest.approx(3.18535e08)

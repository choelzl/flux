"""Unit tests for flux_evaluator_dramsim3.architecture_translator: pure translation logic over
synthetic architecture dicts, no real DRAMsim3 involved. See
tests/integration/test_dramsim3_adapter_live.py for the real-simulation version.
"""

from __future__ import annotations

import pytest
from flux_evaluator_dramsim3 import NotExpressibleError, architecture_ir_to_dramsim3_params


def _arch(hierarchy: list[dict]) -> dict:
    return {"schema_version": "0.1.0", "id": "test/dram-arch", "hierarchy": hierarchy}


def _dram_entry(config=None, cycles=None, stream=None, level="dram"):
    attrs = {"size_kb": 1048576}
    if config is not None:
        attrs["dramsim3_config"] = config
    if cycles is not None:
        attrs["dramsim3_cycles"] = cycles
    if stream is not None:
        attrs["dramsim3_stream"] = stream
    return {"level": level, "class": "memory", "attrs": attrs}


def test_missing_dramsim3_config_raises():
    with pytest.raises(NotExpressibleError, match="no hierarchy entry"):
        architecture_ir_to_dramsim3_params(_arch([_dram_entry()]))


def test_empty_hierarchy_raises():
    with pytest.raises(NotExpressibleError):
        architecture_ir_to_dramsim3_params(_arch([]))


def test_non_memory_entries_are_ignored_not_matched():
    compute = {"level": "pe_array", "class": "compute", "attrs": {"dramsim3_config": "ignored"}}
    with pytest.raises(NotExpressibleError):
        architecture_ir_to_dramsim3_params(_arch([compute]))


def test_extracts_the_real_config_name():
    params = architecture_ir_to_dramsim3_params(_arch([_dram_entry(config="DDR4_8Gb_x8_3200")]))
    assert params.config_name == "DDR4_8Gb_x8_3200"


def test_defaults_cycles_and_stream_when_not_specified():
    params = architecture_ir_to_dramsim3_params(_arch([_dram_entry(config="DDR4_8Gb_x8_3200")]))
    assert params.cycles == 100_000
    assert params.stream == "random"


def test_explicit_cycles_and_stream_override_defaults():
    params = architecture_ir_to_dramsim3_params(
        _arch([_dram_entry(config="DDR4_8Gb_x8_3200", cycles=5000, stream="random")])
    )
    assert params.cycles == 5000
    assert params.stream == "random"


def test_first_memory_entry_with_config_wins_when_multiple_memory_levels_exist():
    gbuf = {"level": "gbuf", "class": "memory", "attrs": {"size_kb": 512}}  # no dramsim3_config
    dram = _dram_entry(config="DDR3_8Gb_x8_1866")
    params = architecture_ir_to_dramsim3_params(_arch([gbuf, dram]))
    assert params.config_name == "DDR3_8Gb_x8_1866"

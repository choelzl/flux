"""Unit tests for flux_evaluator_thermal.architecture_translator: pure translation logic over
synthetic architecture dicts, no real 3D-ICE involved. See
tests/integration/test_thermal_adapter_live.py for the real-simulation version.
"""

from __future__ import annotations

import pytest
from flux_evaluator_thermal import NotExpressibleError, architecture_ir_to_3dice_stack
from flux_evaluator_thermal.architecture_translator import _sanitize_identifier


def _arch(hierarchy: list[dict]) -> dict:
    return {
        "schema_version": "0.1.0",
        "id": "test/thermal-arch",
        "hierarchy": hierarchy,
    }


def _block(level="pe_array", power_w=2.5, x_um=0, y_um=0, width_um=3000, height_um=3000, die=0, **extra_attrs):
    attrs = {"power_w": power_w, **extra_attrs}
    return {
        "level": level,
        "class": "compute",
        "attrs": attrs,
        "floorplan": {"x_um": x_um, "y_um": y_um, "width_um": width_um, "height_um": height_um, "die": die},
    }


def _no_floorplan(level="dram"):
    return {"level": level, "class": "memory", "attrs": {"size_kb": 1048576}}


def test_raises_when_no_hierarchy_entry_has_both_floorplan_and_power():
    with pytest.raises(NotExpressibleError):
        architecture_ir_to_3dice_stack(_arch([_no_floorplan()]))


def test_raises_on_empty_hierarchy():
    with pytest.raises(NotExpressibleError):
        architecture_ir_to_3dice_stack(_arch([]))


def test_entry_with_floorplan_but_no_power_is_excluded():
    entry = _block()
    del entry["attrs"]["power_w"]
    with pytest.raises(NotExpressibleError):
        architecture_ir_to_3dice_stack(_arch([entry]))


def test_entry_with_power_but_no_floorplan_is_excluded():
    entry = _no_floorplan()
    entry["attrs"]["power_w"] = 1.0
    with pytest.raises(NotExpressibleError):
        architecture_ir_to_3dice_stack(_arch([entry]))


def test_off_die_entries_are_excluded_not_erroring_when_something_else_qualifies():
    stack = architecture_ir_to_3dice_stack(_arch([_no_floorplan(), _block()]))
    assert [b.name for b in stack.blocks] == ["pe_array"]


def test_single_block_chip_dimensions_are_its_own_bounding_box():
    stack = architecture_ir_to_3dice_stack(_arch([_block(x_um=0, y_um=0, width_um=3000, height_um=3000)]))
    assert stack.chip_length_um == 3000
    assert stack.chip_width_um == 3000


def test_two_block_chip_dimensions_are_the_real_bounding_box():
    stack = architecture_ir_to_3dice_stack(
        _arch(
            [
                _block(level="pe_array", x_um=0, y_um=0, width_um=3000, height_um=3000),
                _block(level="gbuf", x_um=3000, y_um=0, width_um=2000, height_um=3000),
            ]
        )
    )
    assert stack.chip_length_um == 5000  # 3000 + 2000
    assert stack.chip_width_um == 3000


def test_flp_content_carries_position_dimension_and_power_per_block():
    stack = architecture_ir_to_3dice_stack(_arch([_block(power_w=2.5)]))
    assert len(stack.dies) == 1
    flp = stack.dies[0].flp_content
    assert "pe_array:" in flp
    assert "position 0, 0 ;" in flp
    assert "dimension 3000, 3000 ;" in flp
    assert "power values 2.5 ;" in flp


def test_stk_content_references_the_flp_file_and_the_real_chip_dimensions():
    stack = architecture_ir_to_3dice_stack(_arch([_block()]))
    assert 'floorplan "die0.flp"' in stack.stk_content
    assert "chip length 3000 , width 3000 ;" in stack.stk_content
    assert "steady ;" in stack.stk_content


def test_single_block_defaults_to_die_zero():
    stack = architecture_ir_to_3dice_stack(_arch([_block()]))
    assert len(stack.dies) == 1
    assert stack.dies[0].index == 0


def test_blocks_sharing_a_die_index_land_on_the_same_layer():
    stack = architecture_ir_to_3dice_stack(
        _arch(
            [
                _block(level="pe_array", x_um=0, y_um=0, width_um=3000, height_um=3000),
                _block(level="gbuf", x_um=3000, y_um=0, width_um=2000, height_um=3000),
            ]
        )
    )
    assert len(stack.dies) == 1
    assert {b.name for b in stack.dies[0].blocks} == {"pe_array", "gbuf"}


def test_blocks_on_different_die_indices_become_real_separate_layers():
    stack = architecture_ir_to_3dice_stack(
        _arch(
            [
                _block(level="compute_die", x_um=0, y_um=0, width_um=3000, height_um=3000, die=1),
                _block(level="memory_die", x_um=0, y_um=0, width_um=3000, height_um=3000, die=0),
            ]
        )
    )
    assert len(stack.dies) == 2
    assert [d.index for d in stack.dies] == [1, 0]  # highest (closest to heat sink) first
    assert [b.name for b in stack.dies[0].blocks] == ["compute_die"]
    assert [b.name for b in stack.dies[1].blocks] == ["memory_die"]


def test_multi_die_stack_declares_every_dies_own_die_and_output_line():
    stack = architecture_ir_to_3dice_stack(
        _arch(
            [
                _block(level="compute_die", x_um=0, y_um=0, width_um=3000, height_um=3000, die=1),
                _block(level="memory_die", x_um=0, y_um=0, width_um=3000, height_um=3000, die=0),
            ]
        )
    )
    assert stack.stk_content.count("die die_mat_") == 2  # two real material compositions
    assert 'floorplan "die1.flp"' in stack.stk_content
    assert 'floorplan "die0.flp"' in stack.stk_content
    assert stack.stk_content.count("Tflp (") == 2  # one real output request per die
    # Chip dimensions are the union bounding box across *every* die, not per-die.
    assert stack.chip_length_um == 3000
    assert stack.chip_width_um == 3000


def test_duplicate_level_names_get_disambiguated():
    stack = architecture_ir_to_3dice_stack(
        _arch(
            [
                _block(level="core", x_um=0, y_um=0, width_um=1000, height_um=1000),
                _block(level="core", x_um=1000, y_um=0, width_um=1000, height_um=1000),
            ]
        )
    )
    names = [b.name for b in stack.blocks]
    assert len(names) == len(set(names)), f"expected unique names, got {names}"


def test_cell_um_is_configurable():
    stack = architecture_ir_to_3dice_stack(_arch([_block()]), cell_um=50.0)
    assert stack.cell_um == 50.0
    assert "cell length 50 , width 50 ;" in stack.stk_content


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("pe_array", "pe_array"),
        ("gbuf", "gbuf"),
        ("core-0", "core_0"),
        ("0core", "blk_0core"),
        ("mem.l2", "mem_l2"),
    ],
)
def test_sanitize_identifier_produces_a_valid_3dice_identifier(raw, expected):
    assert _sanitize_identifier(raw) == expected

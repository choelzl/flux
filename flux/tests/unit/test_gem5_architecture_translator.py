"""Unit tests for flux_evaluator_gem5.architecture_translator: pure translation logic over
synthetic architecture dicts, no real gem5 involved. See
tests/integration/test_gem5_adapter_live.py for the real-simulation version.
"""

from __future__ import annotations

import pytest
from flux_evaluator_gem5 import NotExpressibleError, architecture_ir_to_gem5_config_args


def _arch(hierarchy: list[dict] | None) -> dict:
    doc = {"schema_version": "0.1.0", "id": "test/cpu-arch"}
    if hierarchy is not None:
        doc["hierarchy"] = hierarchy
    return doc


def _compute_node(isa="rv64gc", cores=None, freq_ghz=1.2, gem5_cpu_type=None, level="cpu0"):
    attrs = {"isa": isa, "freq_ghz": freq_ghz}
    if cores is not None:
        attrs["cores"] = cores
    if gem5_cpu_type is not None:
        attrs["gem5_cpu_type"] = gem5_cpu_type
    return {"level": level, "class": "compute", "attrs": attrs}


def test_translates_a_single_riscv_compute_node():
    args = architecture_ir_to_gem5_config_args(_arch([_compute_node()]))
    assert args == [
        "--cpu-type", "RiscvTimingSimpleCPU",
        "--num-cpus", "1",
        "--cpu-clock", "1.2GHz",
    ]


def test_cores_defaults_to_one():
    args = architecture_ir_to_gem5_config_args(_arch([_compute_node(cores=None)]))
    assert "--num-cpus" in args
    assert args[args.index("--num-cpus") + 1] == "1"


def test_cores_other_than_one_is_rejected():
    """A real, verified finding (docs/decisions.md D38), not an arbitrary restriction: gem5's own
    multi-core stats naming (system.cpu0.numCycles, system.cpu1.numCycles, ...) isn't covered by
    CHIA's DEFAULT_STATS_KEYS — see module docstring."""
    with pytest.raises(NotExpressibleError, match="cores=4"):
        architecture_ir_to_gem5_config_args(_arch([_compute_node(cores=4)]))


def test_default_cpu_type_is_timing_not_atomic():
    """gem5's own CLI default is AtomicSimpleCPU, which doesn't model timing at all — this
    adapter's own default is a real timing model instead (see architecture_translator.py's
    module docstring)."""
    args = architecture_ir_to_gem5_config_args(_arch([_compute_node()]))
    assert args[args.index("--cpu-type") + 1] == "RiscvTimingSimpleCPU"


def test_explicit_gem5_cpu_type_gets_riscv_prefix():
    args = architecture_ir_to_gem5_config_args(_arch([_compute_node(gem5_cpu_type="MinorCPU")]))
    assert args[args.index("--cpu-type") + 1] == "RiscvMinorCPU"


def test_freq_ghz_formats_as_ghz_suffix():
    args = architecture_ir_to_gem5_config_args(_arch([_compute_node(freq_ghz=2.5)]))
    assert args[args.index("--cpu-clock") + 1] == "2.5GHz"


def test_non_riscv_isa_raises():
    with pytest.raises(NotExpressibleError, match="isn't RISC-V"):
        architecture_ir_to_gem5_config_args(_arch([_compute_node(isa="x86_64")]))


def test_missing_isa_raises():
    node = {"level": "cpu0", "class": "compute", "attrs": {"freq_ghz": 1.2}}
    with pytest.raises(NotExpressibleError, match="isn't RISC-V"):
        architecture_ir_to_gem5_config_args(_arch([node]))


def test_missing_freq_ghz_raises():
    node = {"level": "cpu0", "class": "compute", "attrs": {"isa": "rv64gc"}}
    with pytest.raises(NotExpressibleError, match="freq_ghz"):
        architecture_ir_to_gem5_config_args(_arch([node]))


def test_zero_compute_nodes_raises():
    with pytest.raises(NotExpressibleError, match="exactly one"):
        architecture_ir_to_gem5_config_args(_arch([{"level": "gbuf", "class": "memory", "attrs": {}}]))


def test_two_compute_nodes_raises():
    with pytest.raises(NotExpressibleError, match="exactly one"):
        architecture_ir_to_gem5_config_args(
            _arch([_compute_node(level="cpu0"), _compute_node(level="cpu1")])
        )


def test_missing_hierarchy_raises():
    with pytest.raises(NotExpressibleError, match="exactly one"):
        architecture_ir_to_gem5_config_args(_arch(None))


def test_real_generic_riscv_soc_v1_shaped_compute_node_is_rejected_for_multicore():
    """generic-riscv-soc-v1.yaml's real cpu0 node: {isa: rv64gc, cores: 4, freq_ghz: 1.2} — every
    field this translator needs is present, but cores=4 hits the real cores==1 restriction (see
    module docstring, docs/decisions.md D38); a caller must characterize a single-core variant
    instead, the same way tests/integration/test_gem5_adapter_live.py's `_cpu_only_arch` helper
    does."""
    node = {"level": "cpu0", "class": "compute", "attrs": {"isa": "rv64gc", "cores": 4, "freq_ghz": 1.2}}
    with pytest.raises(NotExpressibleError, match="cores=4"):
        architecture_ir_to_gem5_config_args(_arch([node]))

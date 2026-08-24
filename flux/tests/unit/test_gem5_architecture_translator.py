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
    """D446: the args are the adapter's own standard-library config's (`gem5_config.py`), so the
    CPU is named the way `gem5.components.processors.cpu_types.CPUTypes` names it."""
    args = architecture_ir_to_gem5_config_args(_arch([_compute_node()]))
    assert args == [
        "--cpu-type", "timing",
        "--num-cpus", "1",
        "--cpu-clock", "1.2GHz",
    ]


def test_cores_defaults_to_one():
    args = architecture_ir_to_gem5_config_args(_arch([_compute_node(cores=None)]))
    assert "--num-cpus" in args
    assert args[args.index("--num-cpus") + 1] == "1"


def test_cores_other_than_one_is_rejected():
    """A real, verified finding (docs/decisions.md D38), not an arbitrary restriction: gem5 names
    the cycle counter per core once there is more than one (`board.processor.cores0.core...`,
    and `system.cpu0.numCycles` under the deprecated `se.py` this adapter drove until D446), so
    no single counter stands for the board and CHIA's DEFAULT_STATS_KEYS finds none — see module
    docstring."""
    with pytest.raises(NotExpressibleError, match="cores=4"):
        architecture_ir_to_gem5_config_args(_arch([_compute_node(cores=4)]))


def test_default_cpu_type_is_timing_not_atomic():
    """gem5's own CLI default is AtomicSimpleCPU, which doesn't model timing at all — this
    adapter's own default is a real timing model instead (see architecture_translator.py's
    module docstring)."""
    args = architecture_ir_to_gem5_config_args(_arch([_compute_node()]))
    assert args[args.index("--cpu-type") + 1] == "timing"


def test_both_cpu_vocabularies_are_accepted():
    """A document written against `se.py`'s `--cpu-type` carries a SimObject name; one written
    since D446 carries the standard library's own value. A stored document must not stop meaning
    what it meant, so both map to the same CPU."""
    for named, expected in (("MinorCPU", "minor"), ("minor", "minor"),
                            ("DerivO3CPU", "o3"), ("o3", "o3"),
                            ("AtomicSimpleCPU", "atomic")):
        args = architecture_ir_to_gem5_config_args(_arch([_compute_node(gem5_cpu_type=named)]))
        assert args[args.index("--cpu-type") + 1] == expected


def test_a_cpu_gem5_does_not_build_is_refused_not_passed_through():
    """The prefix this replaced could not be wrong: any name became `Riscv<name>` and gem5 found
    out. A standard-library value is a closed set, so a typo is refused here instead of dying
    minutes into a run."""
    with pytest.raises(NotExpressibleError, match="MagicCPU"):
        architecture_ir_to_gem5_config_args(_arch([_compute_node(gem5_cpu_type="MagicCPU")]))


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

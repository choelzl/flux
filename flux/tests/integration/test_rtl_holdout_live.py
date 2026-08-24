"""Held-out verification catches an overfitted repair (D223), with real Verilator.

The repair loop discloses the failing vectors to the model, so a module could memorize them. A
lookup table passes the shown vectors and fails the holdout; an honest dot product passes both.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import _helpers
import yaml
from flux_codegen_rtl_harness import compile_and_run, design_spec_from_dict
from flux_evaluator_openroad.derive import derive_design_spec

FLUX_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def specs():
    wl = yaml.safe_load((FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml").read_text())
    arch = yaml.safe_load(
        (FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml").read_text())
    shown = derive_design_spec(wl, arch)
    holdout = derive_design_spec(wl, arch, n_vectors=8, vector_seed_salt="holdout")
    return wl, arch, shown, holdout


def _ports_sv(spec: dict) -> str:
    return ",\n".join(
        f"  {'input' if p['dir'] == 'in' else 'output'} logic signed [{p['bits']-1}:0] {p['name']}"
        for p in spec["ports"]
    )


def test_a_memorizing_module_passes_shown_vectors_and_fails_holdout(specs):
    _, _, shown, holdout = specs
    spec, lanes = shown.spec, shown.lanes

    conds = []
    for v in spec["test_vectors"]:
        match = " && ".join(
            f"(a{i} == {v['inputs'][f'a{i}']}) && (w{i} == {v['inputs'][f'w{i}']})"
            for i in range(lanes)
        )
        conds.append(f"({match}) ? {v['expected']['acc']} :")
    cheat = (
        f"module {spec['module_name']} (\n{_ports_sv(spec)}\n);\n"
        f"  assign acc =\n    " + "\n    ".join(conds) + "\n    '0;\nendmodule\n"
    )

    r_shown = compile_and_run(cheat, design_spec_from_dict(shown.spec))
    r_holdout = compile_and_run(cheat, design_spec_from_dict(holdout.spec))
    assert r_shown.all_passed, "the cheat must be a perfect fit to the disclosed vectors"
    assert r_holdout.passed_vectors == 0, (
        f"the memorizer somehow passed {r_holdout.passed_vectors} held-out vectors"
    )


def test_an_honest_implementation_passes_both_vector_sets(specs):
    """A correct design passes the holdout too."""
    _, _, shown, holdout = specs
    spec, lanes = shown.spec, shown.lanes
    honest = (
        f"module {spec['module_name']} (\n{_ports_sv(spec)}\n);\n"
        "  assign acc = " + " + ".join(f"a{i} * w{i}" for i in range(lanes)) + ";\nendmodule\n"
    )
    assert compile_and_run(honest, design_spec_from_dict(shown.spec)).all_passed
    assert compile_and_run(honest, design_spec_from_dict(holdout.spec)).all_passed





@pytest.mark.parametrize("derive_name", ["derive_sequential_design", "derive_gemm_design"])
def test_composition_is_already_the_holdout_on_the_wrapped_paths(specs, derive_name):
    """A leaf that memorizes its shown vectors passes standalone and fails the composed
    verification, whose wrapper drives seeded-random data (D224). Fails if the wrapper's data is
    ever shown to the model."""
    import flux_evaluator_openroad.derive as derive
    from flux_codegen_rtl_harness import CompileError

    wl, arch, _, _ = specs
    derived = getattr(derive, derive_name)(wl, arch)
    leaf = derived.leaf_spec
    ports = leaf["ports"]
    in_ports = [p["name"] for p in ports if p["dir"] == "in"]
    out_ports = [p["name"] for p in ports if p["dir"] == "out"]

    ports_sv = ",\n".join(
        f"  {'input' if p['dir'] == 'in' else 'output'} logic signed "
        f"[{p.get('bits', 32) - 1}:0] {p['name']}"
        for p in ports
    )
    assigns = []
    for out in out_ports:
        conds = []
        for v in leaf["test_vectors"]:
            match = " && ".join(f"({n} == {v['inputs'][n]})" for n in in_ports)
            conds.append(f"({match}) ? {v['expected'][out]} :")
        assigns.append("  assign " + out + " =\n    " + "\n    ".join(conds) + "\n    '0;")
    cheat = f"module {leaf['module_name']} (\n{ports_sv}\n);\n" + "\n".join(assigns) + "\nendmodule\n"

    shown = compile_and_run(cheat, design_spec_from_dict(leaf))
    assert shown.all_passed, "the memorizer must fit the disclosed vectors perfectly"

    try:
        composed = compile_and_run(
            derived.wrapper_source, design_spec_from_dict(derived.top_spec),
            extra_sources={derived.leaf_module_name: cheat}, timeout_s=300.0,
        )
        caught = not composed.all_passed
    except CompileError:
        caught = True  # rejected at composition is caught too
    assert caught, f"{derive_name}: a memorizing leaf survived the composed verification"



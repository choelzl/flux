"""Held-out verification catches an overfitted repair (docs/decisions.md D223) — REAL Verilator,
no LLM needed for the mechanism claim.

The repair loop feeds failing vectors back to the LLM, so the graded vectors are disclosed. The
attack this guards against is a module that memorizes them: an exact-match lookup passes every
shown vector and implements nothing. Both directions proven here: the cheat passes shown and
fails holdout; an honest hand-written dot product passes both. (The full LLM path is exercised
by the Ollama-gated node test below.)
"""

from __future__ import annotations

from pathlib import Path

import pytest

import _helpers
import yaml
from flux_codegen_rtl_harness import compile_and_run, design_spec_from_dict
from flux_generation import derive_design_spec

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
    """The other direction — the guard must not punish a correct design."""
    _, _, shown, holdout = specs
    spec, lanes = shown.spec, shown.lanes
    honest = (
        f"module {spec['module_name']} (\n{_ports_sv(spec)}\n);\n"
        "  assign acc = " + " + ".join(f"a{i} * w{i}" for i in range(lanes)) + ";\nendmodule\n"
    )
    assert compile_and_run(honest, design_spec_from_dict(shown.spec)).all_passed
    assert compile_and_run(honest, design_spec_from_dict(holdout.spec)).all_passed




@_helpers.requires_ollama
def test_the_full_node_reports_holdout_alongside_generation(specs):
    """The node path with a real LLM: whatever the generation outcome, the report must carry
    the holdout verdict separately, and `success` must require both."""
    from flux_chia_nodes import flux_generate_rtl_for_architecture

    wl, arch, _, _ = specs
    report = flux_generate_rtl_for_architecture(wl, arch)
    d = report.to_dict()
    assert "holdout" in d and "overfitted_repair" in d
    if report.generation.success:
        assert report.holdout is not None and report.holdout.total_vectors >= 8
        assert report.success == report.holdout.all_passed
        assert report.overfitted_repair == (not report.holdout.all_passed)
    else:
        assert report.holdout is None and not report.success


@pytest.mark.parametrize("derive_name", ["derive_sequential_design", "derive_gemm_design"])
def test_composition_is_already_the_holdout_on_the_wrapped_paths(specs, derive_name):
    """The sequential and GEMM paths need no separate holdout wiring, and this is measured, not
    argued (docs/decisions.md D224): the leaf's shown vectors are fixed hand-picked values while
    the wrapper drives seeded-random data, so a leaf that memorizes its disclosed vectors passes
    standalone (3/3) and fails the composed verification. If either derivation ever starts
    showing the wrapper's own data to the LLM, this test starts failing — which is the alarm
    working."""
    import flux_generation
    from flux_codegen_rtl_harness import CompileError

    wl, arch, _, _ = specs
    derived = getattr(flux_generation, derive_name)(wl, arch)
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


def test_a_holdout_failure_drives_regeneration_without_disclosing_the_vectors(specs):
    """docs/decisions.md D234: the feedback rail made live. A scripted proposer (real Verilator
    throughout — the script replaces only the LLM, exactly the injection pattern the campaign
    runner has for evaluators) first emits a memorizing module that passes every shown vector
    and fails the holdout; the holdout failure triggers ONE regeneration round whose prompt
    carries only the failure count; the second proposal is honest and passes both sets.

    Two invariants pinned: the regeneration happened (holdout_regens == 1, final success), and
    NO held-out input value ever appeared in any prompt — disclosure would convert the holdout
    into more shown vectors and revive exactly the overfit D223 exists to catch."""
    from flux_chia_nodes import flux_generate_rtl_for_architecture

    wl, arch, shown, holdout_spec = specs
    spec, lanes = shown.spec, shown.lanes

    conds = []
    for v in spec["test_vectors"]:
        match = " && ".join(
            f"(a{i} == {v['inputs'][f'a{i}']}) && (w{i} == {v['inputs'][f'w{i}']})"
            for i in range(lanes)
        )
        conds.append(f"({match}) ? {v['expected']['acc']} :")
    memorizer = (
        f"module {spec['module_name']} (\n{_ports_sv(spec)}\n);\n"
        f"  assign acc =\n    " + "\n    ".join(conds) + "\n    '0;\nendmodule\n"
    )
    honest = (
        f"module {spec['module_name']} (\n{_ports_sv(spec)}\n);\n"
        "  assign acc = " + " + ".join(f"a{i} * w{i}" for i in range(lanes)) + ";\nendmodule\n"
    )

    class _Scripted:
        """Duck-types OllamaLLM's .prompt(str).result surface."""

        def __init__(self) -> None:
            self.responses = [memorizer, honest]
            self.prompts: list[str] = []

        def prompt(self, text: str):
            self.prompts.append(text)

            class _R:
                result = self.responses.pop(0)

            return _R()

    scripted = _Scripted()
    report = flux_generate_rtl_for_architecture(wl, arch, llm=scripted)

    assert report.holdout_regens == 1
    assert report.generation.success and report.holdout.all_passed
    assert report.success and not report.overfitted_repair

    # the no-disclosure invariant: not one held-out input value string in any prompt
    all_prompts = "\n".join(scripted.prompts)
    for v in holdout_spec.spec["test_vectors"]:
        for value in v["inputs"].values():
            if value in (0, 1, -1):  # too common to be evidence either way
                continue
            assert f"= {value};" not in all_prompts and f"== {value})" not in all_prompts
    # but the failure COUNT did travel — that is the entire feedback signal
    assert "additional unseen vectors" in all_prompts

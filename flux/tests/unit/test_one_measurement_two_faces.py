"""One measurement, two faces (D451).

A tool is reachable through the Evaluator ABI by name and as a plain function for a study whose
candidate is its own artifact; both must run the same implementation or they can disagree. A spy
in the shared function checks which code path is taken, so no tools are needed.
"""

from __future__ import annotations

from pathlib import Path


def test_openroads_two_faces_reach_the_same_flow(monkeypatch):
    """`run_ppa_flow`/`run_synthesis_flow` is the measurement; the ABI adapter and the three
    studies that place their own RTL all go through it."""
    import flux_evaluator_openroad
    import flux_evaluator_openroad.adapter as adapter
    import flux_evaluator_openroad.flow as flow

    seen: list[tuple[str, str]] = []

    class Report:
        """What both faces read: the wrapper's own report object (D440's `PpaReport`)."""

        flow_depth = "placement"
        area_um2 = 100.0
        area_mm2 = 0.0001
        power_total_w = 0.01
        worst_slack_ps = 50.0
        clock_period_ps = 1000.0
        cell_count = 10
        openroad_log_tail = ""
        critical_path = None                 # D526: the worst path as data; none in this stub
        routed = False

        def metrics(self) -> dict[str, float]:
            return {"area_um2": 100.0, "area_mm2": 0.0001, "power_w": 0.01,
                    "worst_slack_ps": 50.0, "clock_period_ps": 1000.0, "cell_count": 10,
                    "fmax_mhz": 1052.6}

    def spy_ppa(source, module_name, **kw):
        seen.append(("ppa", module_name))
        return Report()

    def spy_synth(source, module_name, **kw):
        seen.append(("synthesis", module_name))
        return Report()

    for module in (flow, adapter, flux_evaluator_openroad):
        for name, spy in (("run_ppa_flow", spy_ppa), ("run_synthesis_flow", spy_synth)):
            if hasattr(module, name):
                monkeypatch.setattr(module, name, spy, raising=False)

    # face 1: the ABI, by registry name, over inline Workload + Architecture IR
    from flux_evaluator_abi import Budget, Candidate, make_evaluator

    workload = {
        "schema_version": "0.1.0", "id": "test/gemm0",
        "ops": [{"id": "gemm0", "kind": "einsum", "expr": "B C, C K -> B K",
                 "bounds": {"B": 4, "C": 32, "K": 32},
                 "precision": {"I": 8, "W": 8, "O": 16, "O_final": 8}}]}
    arch = {
        "schema_version": "0.1.0", "id": "test/arch8",
        "hierarchy": [{"level": "gbuf", "class": "memory", "attrs": {"size_kb": 512}},
                      {"level": "pe_array", "class": "compute", "attrs": {"dims": {"X": 8}}}]}
    evaluator = make_evaluator("openroad")
    result = evaluator.evaluate(Candidate(workload=workload, arch=arch, mapping=None),
                                Budget(), frozenset({"area_mm2"}))
    assert result.metrics and seen and seen[0][0] == "ppa", (
        "the adapter measures through the shared flow, not its own tool call")
    assert result.provenance.evaluator.startswith("openroad")

    # face 2: macarray, whose candidate is the SystemVerilog it generated
    import flux_ir
    from flux_macarray.config import DEFAULT
    from flux_macarray.measure import measure_one
    from flux_macarray.rtl import generate
    from flux_macarray.verify import DEFAULT_WORKLOAD, shape_from_workload

    shape = shape_from_workload(flux_ir.load_document(DEFAULT_WORKLOAD), 2, accumulate=True)
    design = generate(DEFAULT, shape)
    before = len(seen)
    got = measure_one(design, stage="synthesis", clock_period_ps=1000.0)
    assert "error" not in got and len(seen) == before + 1 and seen[-1][0] == "synthesis"

    # face 2 again: the NLU study and the mapping study's block screen, same function
    from flux_imapping.synth import screen_block

    before = len(seen)
    report = screen_block("module m(); endmodule", "m", "a-block")
    assert report.area_um2 == 100.0 and len(seen) == before + 1


def test_champsims_two_faces_reach_the_same_runner():
    """`simulate` is the measurement; the ABI adapter, `flux champsim run` (`study.measure`) and
    the no-prefetcher baseline all call it."""
    import inspect

    import flux_evaluator_champsim.adapter as adapter
    import flux_evaluator_champsim.baseline as baseline
    import flux_evaluator_champsim.run as run
    import flux_evaluator_champsim.study as study

    assert adapter.simulate is run.simulate and study.simulate is run.simulate and baseline.simulate is run.simulate
    assert "simulate(" in inspect.getsource(adapter.ChampSimEvaluator.evaluate)
    assert "simulate," in inspect.getsource(study.measure)
    assert "simulate(" in inspect.getsource(baseline.baseline_ipc)

def test_the_studies_do_not_reimplement_a_tool_call():
    """The structural half: no application may invoke a measuring tool itself. Where a study
    needs a tool, it goes through that tool's wrapper package (which the ABI adapter shares),
    so there is one place that knows how to run it and one place its output is parsed."""
    import re

    root = Path(__file__).resolve().parents[1] / "applications"
    tools = re.compile(r'(subprocess\.(run|Popen|check_output))\s*\(\s*\[?\s*["\']'
                       r'(yosys|openroad|verilator|sta|champsim|gem5)')
    offenders = []
    for path in root.rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        if tools.search(path.read_text()):
            offenders.append(str(path.relative_to(root)))
    assert not offenders, f"applications invoking a measuring tool directly: {offenders}"

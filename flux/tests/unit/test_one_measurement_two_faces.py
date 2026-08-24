"""One measurement, two faces (docs/evaluator-abi.md, docs/decisions.md D451).

A tool here is reachable two ways: through the Evaluator ABI by name, for a candidate the IR
can express, and as a plain function, for a study whose candidate is its own artifact (generated
SystemVerilog, a `bingo.ini` against three traces, a whole fabric). That is two interfaces, and
it is only safe while it is ONE implementation: the moment an adapter measures a design its own
way, the same design measured through the two faces can disagree and nothing on either number
says which one is the silicon.

These tests put a spy in the shared measurement function and drive both faces at it. They need
no tools installed, because what is being checked is which code path is taken, not what the
tool returns.
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
    from flux_imapping.phys import screen_block

    before = len(seen)
    report = screen_block("module m(); endmodule", "m", "a-block")
    assert report.area_um2 == 100.0 and len(seen) == before + 1


def test_champsims_two_faces_reach_the_same_runner(monkeypatch):
    """`run_champsim` is the measurement; the ABI adapter and the prefetcher's own batch
    backend both go through it."""
    import flux_evaluator_champsim_bingo as champsim
    import flux_evaluator_champsim_bingo.adapter as adapter

    seen: list[str] = []

    def spy(config, trace, **kw):
        seen.append(str(trace))
        return {"ipc": 1.5, "cycles": 10.0, "instructions": 15.0, "wall_clock_s": 0.1}

    monkeypatch.setattr(adapter, "run_champsim", spy, raising=False)
    monkeypatch.setattr(champsim, "run_champsim", spy, raising=False)

    from flux_prefetcher.measure import local_measure_batch
    from flux_prefetcher.config import DEFAULT

    got = local_measure_batch([{"config": DEFAULT, "trace": "/traces/a.gz", "types": ["bingo"],
                                "warmup": 1, "simulation": 2, "partner_knobs": {},
                                "binary": "/bin/champsim"}], parallelism=1)
    assert got[0]["ipc"] == 1.5 and seen == ["/traces/a.gz"], (
        "the study's batch backend measures through the shared runner")

    # and the ABI face, over the same runner
    import inspect

    source = inspect.getsource(adapter.ChampSimBingoEvaluator.evaluate)
    assert "run_champsim(" in source, (
        "the adapter must call the shared runner rather than its own subprocess")


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

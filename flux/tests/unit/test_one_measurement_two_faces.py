"""One measurement, two faces (D451).

A tool is reachable through the Evaluator ABI by name and as a plain function for a study whose
candidate is its own artifact; both must run the same implementation or they can disagree. A spy
in the shared function checks which code path is taken, so no tools are needed.
"""

from __future__ import annotations

from pathlib import Path


def test_champsims_two_faces_reach_the_same_runner():
    """`simulate` is the measurement; `champsim.py run` (`study.measure`) and the no-prefetcher
    baseline both call it (the ABI adapter went in D957: no script called it)."""
    import inspect

    import champsim_tools.baseline as baseline
    import champsim_tools.run as run
    import champsim_tools.study as study

    assert study.simulate is run.simulate and baseline.simulate is run.simulate
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

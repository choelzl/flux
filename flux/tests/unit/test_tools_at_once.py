"""D1004: `budget.tools_at_once` -- at most N checks and measurements at once across the loop:
threads (passes and parts at once) and processes (an agent's `flux probe`) wait for one slot."""

from __future__ import annotations

import subprocess
import sys
import threading
import time

import pytest

from flux_loop import TaskError, TaskSpec
from flux_loop.toollock import tool_slot


def _overlap(tmp_path, n: int, what: str = "stage screen", runs: int = 4) -> int:
    busy, most, lock = [0], [0], threading.Lock()

    def one():
        with tool_slot(tmp_path, n, what):
            with lock:
                busy[0] += 1
                most[0] = max(most[0], busy[0])
            time.sleep(0.15)
            with lock:
                busy[0] -= 1

    ts = [threading.Thread(target=one) for _ in range(runs)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return most[0]


def test_at_most_n_run_at_once_and_writing_a_design_is_not_counted(tmp_path):
    assert _overlap(tmp_path, 1) == 1
    assert _overlap(tmp_path, 2) == 2
    assert _overlap(tmp_path, 0) == 4, "0: no limit"
    assert _overlap(tmp_path, 1, "generate: compose") == 4, "writing a design is not a check"


def test_a_process_of_its_own_waits_for_the_same_slot(tmp_path):
    code = ("import sys, time; from flux_loop.toollock import tool_slot\n"
            "with tool_slot(sys.argv[1], 1, 'probe screen'):\n    print('in', time.monotonic(), flush=True); time.sleep(0.5)\n")
    with tool_slot(tmp_path, 1, "test golden"):
        t0 = time.monotonic()
        proc = subprocess.Popen([sys.executable, "-c", code, str(tmp_path)], stdout=subprocess.PIPE, text=True)
        time.sleep(0.6)
        held = time.monotonic()
    out = proc.communicate(timeout=30)[0]
    assert float(out.split()[1]) >= held - 0.05 > t0, out


def test_the_budget_takes_a_count(tmp_path):
    doc = {"id": "t", "statement": "s", "flow": {"test": {"check": "true"}}, "objectives": [{"metric": "m", "direction": "minimize"}]}
    assert TaskSpec.from_dict({**doc, "budget": {"tools_at_once": 1}}, base=tmp_path).budget["tools_at_once"] == 1
    with pytest.raises(TaskError, match="tools_at_once"):
        TaskSpec.from_dict({**doc, "budget": {"tools_at_once": -1}}, base=tmp_path)

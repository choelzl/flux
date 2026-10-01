"""D732: a run's log with the time of each line -- `flux_web.stamp` between the run and the log."""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import time

from flux_web.stamp import STAMP_RE, main


def test_each_line_is_stamped_in_order_and_the_runs_code_comes_back(tmp_path):
    log = tmp_path / "loop.log"
    log.write_text("── started by bob ──\n")
    code = "import sys; print('one'); print('two', file=sys.stderr, flush=True); sys.stdout.write('no end'); sys.exit(3)"
    assert main([str(log), "--", sys.executable, "-c", code]) == 3
    lines = log.read_text().splitlines()
    assert lines[0] == "── started by bob ──", "what was there stays as it was"
    texts = [re.sub(STAMP_RE, "", ln) for ln in lines[1:]]
    assert sorted(texts) == ["no end", "one", "two"] and all(re.match(STAMP_RE, ln) for ln in lines[1:])


def test_a_stop_now_reaches_the_run_and_its_last_words_are_written(tmp_path):
    log = tmp_path / "loop.log"
    code = ("import signal, sys, time\n"
            "def bye(*_): print('stopping: the pass ends', flush=True); sys.exit(130)\n"
            "signal.signal(signal.SIGINT, bye)\nprint('working', flush=True)\ntime.sleep(60)\n")
    proc = subprocess.Popen([sys.executable, "-m", "flux_web.stamp", str(log), "--", sys.executable, "-c", code],
                            start_new_session=True, env={**os.environ})
    for _ in range(200):
        if log.exists() and "working" in log.read_text():
            break
        time.sleep(0.05)
    os.killpg(proc.pid, signal.SIGINT)                      # as the web's Stop now does
    assert proc.wait(timeout=20) == 130
    assert [re.sub(STAMP_RE, "", ln) for ln in log.read_text().splitlines()] == ["working", "stopping: the pass ends"]

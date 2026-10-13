"""D1027: at most N calls at once on one endpoint -- its host and port -- across every loop and user:
an agent's turn and a model's request each hold a slot (a file lock in FLUX_SLOTS_DIR) while they run;
N is the admin's FLUX_<NAME>_AT_ONCE or FLUX_REMOTE_AT_ONCE, 0 no limit."""

from __future__ import annotations

import sys
import threading
import time

from flux_llm.slots import at_once, endpoint_key, endpoint_slot


def test_an_endpoint_is_its_host_and_port():
    assert endpoint_key("http://10.0.0.5:8080/v1", "x") == "10.0.0.5:8080"
    assert endpoint_key("https://API.example.com/v1", "x") == "api.example.com:443"
    assert endpoint_key("http://box/v1", "x") == "box:80"
    assert endpoint_key("", "agent-claude") == "agent-claude", "no endpoint said: its provider's own, by name"


def test_calls_beyond_the_limit_wait_for_a_slot(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUX_SLOTS_DIR", str(tmp_path))
    running, most, lock = [0], [0], threading.Lock()

    def call():
        with endpoint_slot("box:8080", 2, "a test"):
            with lock:
                running[0] += 1
                most[0] = max(most[0], running[0])
            time.sleep(0.3)
            with lock:
                running[0] -= 1

    threads = [threading.Thread(target=call) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert most[0] == 2
    assert sorted(p.name for p in (tmp_path / "box_8080").iterdir()) == ["slot-0.lock", "slot-1.lock"]


def test_no_limit_takes_no_slot(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUX_SLOTS_DIR", str(tmp_path))
    monkeypatch.setenv("FLUX_CODEX_AT_ONCE", "lots")
    assert at_once("codex") == 0
    with endpoint_slot("box:8080", at_once("codex")):
        pass
    assert not (tmp_path / "box_8080").exists()


def test_an_agents_turn_holds_a_slot_of_its_endpoint(tmp_path, monkeypatch):
    """Two turns of an agent limited to one at once on its endpoint run one after the other."""
    from flux_loop.agent import AgentSpec, run_turn

    monkeypatch.setenv("FLUX_SLOTS_DIR", str(tmp_path / "slots"))
    monkeypatch.setenv("FLUX_SLOW_AT_ONCE", "1")
    script = tmp_path / "slow.py"
    script.write_text("import time, sys\nprint(time.time()); time.sleep(0.5); print(time.time())\n")
    spec = AgentSpec("slow", (sys.executable, str(script)), endpoint="http://gpu-box:9000/v1", timeout_s=30)
    got = []
    threads = [threading.Thread(target=lambda: got.append(run_turn(spec, spec.argv, {}, workdir=tmp_path))) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    spans = sorted([float(x) for x in t.stdout.split()] for t in got)
    assert spans[1][0] >= spans[0][1] - 0.05, f"the second turn waited for the first: {spans}"
    assert (tmp_path / "slots" / "gpu-box_9000").is_dir()

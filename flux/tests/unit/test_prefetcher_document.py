"""The prefetcher as a worldless document: `prefetcher.problem.yaml` on `bingo.py`, and
`applications/prefetcher/invent.problem.yaml` on `flux champsim`. The loop runs against the FAKE ChampSim of
test_champsim_generic.py, so nothing here needs the traces or the simulator."""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

from test_champsim_generic import fake  # noqa: F401 -- the fixture

from flux_loop import PromptProblem, load_task, request_for, run_loop

APP = Path(__file__).resolve().parents[2] / "applications" / "prefetcher"


def _bingo():
    spec = importlib.util.spec_from_file_location("bingo_doc", APP / "bingo.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_both_documents_load():
    task = load_task(APP / "problem.yaml")
    assert task.extension == ".ini" and not task.space and task.gate.named("test")
    assert "bingo_pht_size" in task.knowledge and "l2c_prefetcher_types = bingo" in task.knowledge
    assert load_task(APP / "invent.problem.yaml").gate.named("build").builds


def test_a_file_that_leaves_knobs_out_takes_the_shipped_ones(tmp_path, capsys):
    bingo = _bingo()
    (tmp_path / "d.ini").write_text("l2c_prefetcher_types = bingo,sms\nsms_pref_degree = 8\n")
    k = bingo.full(tmp_path / "d.ini")
    assert k["l2c_prefetcher_types"] == "bingo,sms" and k["bingo_pht_size"] == "4096" and k["sms_pref_degree"] == "8"
    assert bingo.storage_bytes({n: int(k[n]) for n in bingo.RANGES}) == 35_096
    (tmp_path / "r.ini").write_text("bingo_region_size = 1024\n")
    assert bingo.full(tmp_path / "r.ini")["bingo_pattern_len"] == "16", "pattern_len follows region_size"
    (tmp_path / "bad.ini").write_text("bingo_pc_width = 0\nbingo_min_addr_width = 0\n")
    for argv in (["check", str(tmp_path / "d.ini")], ["check", str(tmp_path / "d.ini"), "--max-storage", "30000"],
                 ["check", str(tmp_path / "r.ini")], ["check", str(tmp_path / "bad.ini")]):
        assert bingo.main(argv) == 0
    assert capsys.readouterr().out.splitlines() == [
        "0 failing", "1 failing: 35096 B is over the 30000 B budget", "0 failing",
        "1 failing: pc_width + min_addr_width must exceed 0 (the PHT would have no key)"]


def test_the_model_writes_the_file_and_it_is_measured_on_a_fake_champsim(fake, tmp_path, monkeypatch):  # noqa: F811
    from flux_llm import ScriptedProposer

    home = tmp_path / "app"
    home.mkdir()
    for f in ("problem.yaml", "bingo.py", "knobs.md", "bingo_default.ini"):
        shutil.copy(APP / f, home / f)
    shutil.copytree(fake["traces"], home / "traces")
    monkeypatch.setenv("PATH", f"{fake['exe'].parent}:{__import__('os').environ['PATH']}")   # `needs: [pythia]`
    task = load_task(home / "problem.yaml")
    model = ScriptedProposer(['{"artifact": "l2c_prefetcher_types = bingo\\nbingo_l2c_thresh = 0.6\\n", "why": "-"}'])
    out = run_loop(PromptProblem(task), request_for(task, db=str(tmp_path / "r.db"), steps=1, passes=1,
                                                   screen_only=True, workers=1), proposer=model, log=lambda _m: None)
    assert "knobs.md" in model.prompts[0] or "bingo_pht_size" in model.prompts[0], "the model reads the knobs"
    got = [s for s in out.scored if s.metrics.get("geomean_speedup")]
    assert got, "the model's file was not measured"
    m = got[0].metrics
    assert m["storage_bytes"] == 35_096 and m["geomean_speedup"] > 1.0
    assert "bingo|alpha.champsim.gz" in fake["log"].read_text().splitlines()

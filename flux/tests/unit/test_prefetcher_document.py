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
    assert bingo.storage_bytes({n: int(k[n]) for n in bingo.RANGES}) == 35_096, "Bingo's own tables"
    assert bingo.design_storage_bytes(k) > 35_096, "sms beside it costs its tables (D873)"
    (tmp_path / "r.ini").write_text("bingo_region_size = 1024\n")
    assert bingo.full(tmp_path / "r.ini")["bingo_pattern_len"] == "16", "pattern_len follows region_size"
    (tmp_path / "bad.ini").write_text("bingo_pc_width = 0\nbingo_min_addr_width = 0\n")
    for argv in (["check", str(tmp_path / "d.ini")], ["check", str(tmp_path / "d.ini"), "--max-storage", "30000"],
                 ["check", str(tmp_path / "r.ini")], ["check", str(tmp_path / "bad.ini")]):
        assert bingo.main(argv) == 0
    assert capsys.readouterr().out.splitlines() == [
        "0 failing", "1 failing: 67552 B is over the 30000 B budget", "0 failing",
        "1 failing: pc_width + min_addr_width must exceed 0 (the PHT would have no key)"]


def test_the_check_refuses_what_the_contract_forbids(tmp_path, capsys):
    """D872: these all passed `0 failing`; `dram_io_freq = 9600` scored 1.04418 against 1.03685, the
    prefetcher unchanged, since the no-prefetcher baseline runs without the file."""
    bingo = _bingo()
    cheats = {
        "l2c_prefetcher_types = sms\n": "bingo must come first",
        "l2c_prefetcher_types = sms,bingo\n": "bingo must come first",
        "l2c_prefetcher_types = bingo,scooby\n": "scooby crashes beside bingo",
        "l2c_prefetcher_types = bingo,mlop\n": "mlop crashes beside bingo",
        "l2c_prefetcher_types = bingo,next_line\n": "next_line crashes beside bingo",
        "l2c_prefetcher_types = bingo,bop\n": "bop is not a partner",
        "l2c_prefetcher_types = bingo,sms,sms\n": "names a prefetcher twice",
        "dram_io_freq = 9600\n": "dram_io_freq is not a knob knobs.md lists",
        "simulation_instructions = 1000\n": "simulation_instructions is not a knob",
        "warmup_instructions = 0\n": "warmup_instructions is not a knob",
        "l1d_prefetcher_types = stride\n": "l1d_prefetcher_types is not a knob",
        "sms_pht_size = 4096\n": "sms_pht_size is read only with sms in l2c_prefetcher_types",
        "bingo_debug_level = 1\n": "knobs.md keeps it at 0",
        "bingo_l1d_thresh = 0.5\n": "knobs.md keeps it at 1.01",
        "bingo_llc_thresh = 0.9\n": "knobs.md keeps it at 0.05",
        "bingo_pc_address_fill_level = LLC\n": "knobs.md keeps it at L2",
        "l2c_prefetcher_types = bingo,sms\nsms_region_size = 3000\n": "not a power of two",
        "l2c_prefetcher_types = bingo,stride\nstride_pref_degree = 1000\n": "outside 0..64",
        "l2c_prefetcher_types = bingo,sandbox\nsandbox_bloom_filter_size = 16\n": "no hash function",
    }
    for i, text in enumerate(cheats):
        (tmp_path / f"c{i}.ini").write_text(text)
        assert bingo.main(["check", str(tmp_path / f"c{i}.ini")]) == 0
    got = capsys.readouterr().out.splitlines()
    for (text, why), line in zip(cheats.items(), got):
        assert line.startswith("1 failing:") and why in line, (text, line)
    fine = ["l2c_prefetcher_types = bingo,sms,stride\nsms_pht_size = 1024\nstride_num_trackers = 32\n",
            "l2c_prefetcher_types = bingo,power7\nstride_num_trackers = 32\nstreamer_pref_degree = 3\n",
            "bingo_l1d_thresh = 1.010\nbingo_debug_level = 0\nbingo_pc_address_fill_level = L2\n",
            "l2c_prefetcher_types = bingo,spp_ppf_dev,ipcp,spp_dev2\nppf_perc_threshold_lo = -20\n"]
    for i, text in enumerate(fine):
        (tmp_path / f"f{i}.ini").write_text(text)
        assert bingo.main(["check", str(tmp_path / f"f{i}.ini")]) == 0
    assert capsys.readouterr().out.splitlines() == ["0 failing"] * len(fine)
    k = bingo.full(tmp_path / "f0.ini")
    assert k["sms_pht_size"] == "1024" and k["sms_region_size"] == "4096" and k["stride_pref_degree"] == "2", \
        "a partner's knob left out takes knobs.md's shipped value, not ChampSim's compiled one"


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

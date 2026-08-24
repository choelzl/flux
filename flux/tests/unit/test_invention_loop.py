"""The invention loop's reference and its repair of designs that run but do nothing (D361).

Two defects the `--invent 2` run exposed (D360). The loop asked every new design to beat the
stock `bingo+sms+stride` while the study's best stack was `bingo+sms+invented2`, so a design
that would complement `invented2` was scored beside `stride`. And a design that compiled but
issued zero prefetches had its diagnosis printed and was then abandoned, though the counters
name the bug precisely enough for a repair.

The loop is exercised end to end with a fake model, compiler and simulator: what is asserted is
the ORDER of what it asks for and what it measures, not any number.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FLUX_ROOT = Path(__file__).resolve().parents[2]

pytest.importorskip("chia.base.ChiaFunction")

from flux_chia_nodes import invent_prefetcher as node  # noqa: E402
from flux_codegen_champsim_prefetcher import PrefetcherProposal, inert_repair_prompt  # noqa: E402
from flux_prefetcher.invented import Invention  # noqa: E402
from flux_prefetcher.objective import BENCHMARKS  # noqa: E402

KEPT = Invention(name="invented2", header="// kept", knobs={"invented2_conf": 4}, idea="",
                 geomean_alone=1.0045, geomean_with_stack=1.0567,
                 reference_stack=("bingo", "sms", "stride"), reference_geomean=1.0554)


# ---- the reference ----------------------------------------------------------------------------

def test_the_reference_candidates_are_the_stock_stack_and_every_kept_claim():
    stacks = node._reference_candidates([KEPT], None)
    assert stacks == [["bingo", "sms", "stride"], ["bingo", "sms", "stride", "invented2"]]


def test_an_explicit_reference_stack_is_measured_alone():
    assert node._reference_candidates([KEPT], ["bingo", "invented2"]) == [["bingo", "invented2"]]


def test_a_kept_design_with_no_recorded_stack_is_tried_beside_the_stock_one():
    old = Invention(name="invented4", header="", knobs={}, idea="", geomean_alone=1.0,
                    geomean_with_stack=1.06)
    assert node._reference_candidates([old], None)[1] == ["bingo", "sms", "stride", "invented4"]


# ---- the repair prompt ------------------------------------------------------------------------

def test_the_inert_repair_prompt_carries_the_counters_diagnosis_and_the_header():
    p = PrefetcherProposal(name="invented9", header="#ifndef X\nclass X {};\n#endif\n")
    text = inert_repair_prompt(p, "issued ZERO prefetches -- the emit path never executes")
    assert "issued ZERO prefetches" in text
    assert "class X {};" in text
    assert "LOGIC bug" in text and "not a redesign" in text


# ---- the loop, end to end with fakes ----------------------------------------------------------

HEADER_INERT = "#ifndef INVENTED7_H\nclass Invented7 : public Prefetcher {};\n#endif\n"
HEADER_FIXED = "#ifndef INVENTED7_H\n// FIXED\nclass Invented7 : public Prefetcher {};\n#endif\n"


class FakeWorld:
    """A model, a compiler and a simulator that agree on one story.

    The model's first design is inert; its repair (recognisable by the word FIXED) emits and
    beats the reference. Stacks containing the kept `invented2` run faster than the stock stack,
    so the loop must pick the taller reference. Every measurement records what it was asked.
    """

    def __init__(self):
        self.asked: list[str] = []
        self.measured: list[tuple[str, tuple[str, ...], str]] = []   # (binary, types, rung)

    def ask(self, prompt: str) -> str:
        self.asked.append(prompt)
        if "compiled and ran" in prompt:
            return "```cpp\n" + HEADER_FIXED + "```\nKNOBS: invented7_deg=2\n"
        return "```cpp\n" + HEADER_INERT + "```\nKNOBS: invented7_deg=2\n"

    def parse(self, name: str, reply: str):
        header = reply.split("```cpp\n")[1].split("```")[0]
        return PrefetcherProposal(name=name, header=header, knobs={"invented7_deg": 2},
                                  rationale="a fake idea")

    def install(self, name, header, knobs, tree):
        tree.mkdir(parents=True, exist_ok=True)
        (tree / f"{name}.h").write_text(header)

    def build(self, tree, **_):
        from flux_codegen_champsim_prefetcher.harness import BuildResult
        return BuildResult(ok=True, binary=tree / "bin" / "sim", errors="", elapsed_s=0.0, tree=tree)

    def measure(self, jobs, parallelism=1, binary=None):
        out = []
        for job in jobs:
            types = tuple(job["types"])
            rung = "decide" if job["simulation"] >= 100_000_000 else "screen"
            self.measured.append((Path(binary).parent.parent.name, types, rung))
            ipc = 1.0
            if types:
                ipc = 1.06 if "invented2" in types else 1.05
                if "invented7" in types:
                    fixed = "FIXED" in (Path(binary).parent.parent / "invented7.h").read_text()
                    ipc = (1.08 if len(types) > 1 else 1.02) if fixed else (1.0 if len(types) == 1 else 1.06)
            issued = 0.0 if types == ("invented7",) and ipc == 1.0 else 10.0
            out.append({"ipc": ipc, "cycles": 1.0, "instructions": 1.0, "wall_clock_s": 0.0,
                        "stats": {"L2C_prefetch_issued": issued, "L2C_prefetch_useful": issued}})
        return out


@pytest.fixture
def world(tmp_path, monkeypatch):
    w = FakeWorld()
    import flux_codegen_champsim_prefetcher as gen
    import flux_evaluator_champsim_bingo as ev
    import flux_llm
    from flux_prefetcher import invented, measure, staging

    monkeypatch.setattr(gen, "parse_proposal", w.parse)
    monkeypatch.setattr(gen, "unbuildable_reason", lambda p: None)
    monkeypatch.setattr(gen, "install", w.install)
    monkeypatch.setattr(gen, "build", w.build)
    monkeypatch.setattr(gen, "stage_tree", lambda src, into, **k: into)
    monkeypatch.setattr(ev, "resolve_binary", lambda *a, **k: tmp_path / "stock" / "bin" / "pythia")
    monkeypatch.setattr(ev, "resolve_source_tree", lambda *a, **k: tmp_path / "src")
    monkeypatch.setattr(flux_llm, "local_proposer", lambda **k: w.ask)
    monkeypatch.setattr(measure, "local_measure_batch", w.measure)
    monkeypatch.setattr(staging, "stage_traces", lambda traces, log=None: traces)
    monkeypatch.setattr(invented, "library", lambda root=None, **k: [KEPT])
    monkeypatch.setattr(invented, "register", lambda found: [i.name for i in found])

    def fake_build_binary(inventions, *, source_tree, cache_dir, log):
        out = cache_dir / "pythia-invented-lib"
        for inv in inventions:
            w.install(inv.name, inv.header, inv.knobs, out)
        return out / "bin" / "sim"
    monkeypatch.setattr(invented, "build_binary", fake_build_binary)
    (tmp_path / "traces").mkdir()
    for b in BENCHMARKS:
        (tmp_path / "traces" / f"{b}.simout_champsim.gz").write_bytes(b"")
    (tmp_path / "kept").mkdir()
    for i in (1, 2, 6):
        (tmp_path / "kept" / f"invented{i}.json").write_text("{}")
    return w


def test_the_loop_invents_against_the_tallest_stack_and_repairs_an_inert_design(world, tmp_path):
    report = node.flux_invent_prefetcher(
        rounds=1, traces_dir=str(tmp_path / "traces"), keep_dir=str(tmp_path / "kept"),
        scratch_root=str(tmp_path), parallelism=2, confirm_best=True)

    # The reference: both claims measured in one wave, the taller one chosen.
    assert report["reference"]["name"] == "bingo+sms+stride+invented2"
    assert report["reference"]["candidates"] == {"bingo+sms+stride": 1.05,
                                                 "bingo+sms+stride+invented2": 1.06}
    first_wave = [t for b, t, r in world.measured if b == "pythia-invented-lib"]
    assert ("bingo", "sms", "stride", "invented2") in first_wave, "measured on the library build"
    assert "beat `bingo+sms+stride+invented2`, which reaches geomean 1.0600" in world.asked[0]

    # Names continue past the highest kept index.
    assert report["attempts"][0]["name"] == "invented7"

    # The inert design was handed the counters' diagnosis, once, and the fix was measured.
    fixes = [p for p in world.asked if "compiled and ran" in p]
    assert len(fixes) == 1 and "issued ZERO prefetches" in fixes[0]
    attempt = report["attempts"][0]
    assert attempt["logic_repairs"] == 1 and not attempt["inert"]
    assert attempt["geomean_alone"] == pytest.approx(1.02)
    assert attempt["geomean_with_stack"] == pytest.approx(1.08)
    assert attempt["reference_stack"] == ["bingo", "sms", "stride", "invented2"]
    kept = json.loads((tmp_path / "kept" / "invented7.json").read_text())
    assert kept["logic_repairs"] == 1 and "FIXED" in (tmp_path / "kept" / "invented7.h").read_text()

    # A contender that beat the reference on the screen was confirmed, beside the same stack.
    assert report["confirmation"]["reference_stack"] == ["bingo", "sms", "stride", "invented2"]
    assert report["confirmation"]["beats_reference"]
    decide = [t for b, t, r in world.measured if r == "decide"]
    assert ("bingo", "sms", "stride", "invented2", "invented7") in decide


def test_an_inert_design_is_not_repaired_when_repairs_are_off(world, tmp_path):
    report = node.flux_invent_prefetcher(
        rounds=1, inert_repairs=0, traces_dir=str(tmp_path / "traces"),
        keep_dir=str(tmp_path / "kept"), scratch_root=str(tmp_path), parallelism=2)
    assert not [p for p in world.asked if "compiled and ran" in p]
    assert report["attempts"][0]["inert"] and report["attempts"][0]["logic_repairs"] == 0
    assert report["confirmation"] is None, "an inert design is not confirmed"

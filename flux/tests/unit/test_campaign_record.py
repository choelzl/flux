"""`--db PATH` must create PATH.

It did not. Measurements were cached in a sidecar BESIDE the named file, which made resume work
and made the flag a lie: `--db /tmp/run.db` produced `/tmp/run.champsim.json` and no `/tmp/run.db`,
so a reader who went looking for the campaign found nothing. What was tried, what was refused and
why, and which rung produced each number all vanished when the process exited.

The recorder is deliberately failure-tolerant — a study must not die because its logbook is
unwritable — which is exactly why it needs tests: a silent no-op looks identical to success.
"""

from __future__ import annotations

from pathlib import Path

import pytest

FLUX_ROOT = Path(__file__).resolve().parents[2]

from flux_prefetcher.config import DEFAULT  # noqa: E402
from flux_prefetcher.flow import Recorder  # noqa: E402

OBJECTIVE = {"study": "bingo-l2-prefetcher", "stage": 2}


def test_it_creates_the_database_the_flag_names(tmp_path):
    db = tmp_path / "run.db"
    rec = Recorder(str(db), OBJECTIVE, lambda _m: None)
    rec.close("completed")
    assert db.is_file(), "--db named a file that was never created"
    assert db.stat().st_size > 0


def test_trials_are_recorded_with_their_phase_and_rung(tmp_path):
    """The rung is the point: a number's fidelity is what D351 is about."""
    from flux_store import CampaignStore

    db = tmp_path / "run.db"
    rec = Recorder(str(db), OBJECTIVE, lambda _m: None)
    rec.phase("stage1")
    rec.trial(DEFAULT, ("bingo",), rung="screen", strategy="incumbent",
              metrics={"geomean_speedup": 1.0607, "storage_bytes": 35096.0},
              error=None, wall_s=7.0)
    rec.phase("confirm")
    rec.trial(DEFAULT, ("bingo",), rung="decide", strategy="confirmed",
              metrics={"geomean_speedup": 1.0440, "storage_bytes": 35096.0},
              error=None, wall_s=360.0)
    rec.close("completed")

    store = CampaignStore(str(db))
    trials = store.trials(store.list_campaigns()[0]["campaign_id"])
    store.close()
    assert len(trials) == 2
    assert {t.phase for t in trials} == {"stage1", "confirm"}
    assert {t.rung for t in trials} == {"screen", "decide"}


def test_a_refusal_is_recorded_with_its_reason(tmp_path):
    """Refusals are the half of a run that explains the other half."""
    from flux_store import CampaignStore

    db = tmp_path / "run.db"
    rec = Recorder(str(db), OBJECTIVE, lambda _m: None)
    rec.phase("stage2")
    rec.trial(DEFAULT, ("bingo",), rung="screen", strategy="shrink", metrics=None,
              error="below the retention floor: geomean 1.0000 < 1.0558", wall_s=7.0)
    rec.close("completed")

    store = CampaignStore(str(db))
    trials = store.trials(store.list_campaigns()[0]["campaign_id"])
    store.close()
    assert len(trials) == 1
    assert trials[0].status == "refused"
    assert "retention floor" in (trials[0].error or "")


def test_the_stack_is_part_of_the_recorded_identity(tmp_path):
    """`bingo` and `bingo+sms` on identical knobs are two designs, not one row."""
    from flux_store import CampaignStore

    db = tmp_path / "run.db"
    rec = Recorder(str(db), OBJECTIVE, lambda _m: None)
    rec.phase("compose")
    for types in (("bingo",), ("bingo", "sms")):
        rec.trial(DEFAULT, types, rung="screen", strategy="compose",
                  metrics={"geomean_speedup": 1.06}, error=None, wall_s=7.0)
    rec.close("completed")

    store = CampaignStore(str(db))
    trials = store.trials(store.list_campaigns()[0]["campaign_id"])
    store.close()
    assert len({t.candidate_key for t in trials}) == 2, "the stack must distinguish them"
    assert any("sms" in t.candidate_key for t in trials)


def test_an_unwritable_database_costs_the_record_not_the_run(tmp_path):
    """A study must not die because its logbook is unwritable."""
    unwritable = tmp_path / "nope" / "deeper" / "run.db"      # parent does not exist
    said = []
    rec = Recorder(str(unwritable), OBJECTIVE, said.append)
    rec.phase("stage1")
    rec.trial(DEFAULT, ("bingo",), rung="screen", strategy="incumbent",
              metrics={"geomean_speedup": 1.0}, error=None, wall_s=1.0)
    rec.close("completed")
    assert rec.store is None
    assert any("no campaign record" in m for m in said), "the failure must be reported, not hidden"


def test_the_same_objective_resumes_rather_than_forking(tmp_path):
    """Campaign id is the objective's hash (D220): re-running continues the same campaign."""
    from flux_store import CampaignStore

    db = tmp_path / "run.db"
    first = Recorder(str(db), OBJECTIVE, lambda _m: None)
    first_id = first.campaign_id
    first.close("completed")
    second = Recorder(str(db), OBJECTIVE, lambda _m: None)
    assert second.campaign_id == first_id
    second.close("completed")

    store = CampaignStore(str(db))
    assert len(store.list_campaigns()) == 1, "the same objective forked a sibling campaign"
    store.close()


def test_invention_runs_before_the_simulator_is_built(tmp_path, monkeypatch):
    """A design invented THIS run must be on THIS run's compose menu.

    The invention hook is injected (the library must not import the interfaces layer) and is
    called from `_setup` before the invented-partner binary is built, so its survivors are
    installed in the simulator the rest of the run measures with.
    """
    import random

    from flux_prefetcher import flow
    from flux_prefetcher.study import PrefetcherRequest

    order = []
    monkeypatch.setattr(flow, "resolve_binary", lambda b=None: tmp_path / "pythia", raising=False)

    def fake_invent(**kw):
        order.append(("invent", kw.get("rounds")))
        return {"attempts": [{"name": "inventedX", "outcome": "measured"}], "confirmation": None}

    class FakeLibrary:
        pass

    s = flow.Study(request=PrefetcherRequest(db=str(tmp_path / "x.db"), invent_rounds=2,
                                             include_invented=False, screen_only=True),
                   say=lambda m: order.append(("say", m[:40])), started=0.0,
                   rng=random.Random(0))
    import flux_evaluator_champsim_bingo.binary as binmod
    monkeypatch.setattr(binmod, "resolve_binary", lambda b=None: tmp_path / "pythia")
    (tmp_path / "pythia").write_text("")
    monkeypatch.setattr(flow, "_resolve_traces", lambda r: {}, raising=False)
    monkeypatch.setattr(flow, "stage_traces", lambda t, log=None: t, raising=False)
    monkeypatch.setattr(flow, "_fingerprint", lambda b: {"champsim": "test"}, raising=False)
    flow._setup(s, None, fake_invent)
    assert ("invent", 2) in order
    assert any("1 new design(s)" in m for k, m in order if k == "say")


def test_the_cache_is_keyed_on_the_stock_binary_even_when_a_rebuild_runs(tmp_path, monkeypatch):
    """The rebuilt binary is what runs (provenance says so); the STOCK binary is what keys the
    cache, and the inventions it carries key each measurement that uses one (D361)."""
    import random

    import flux_evaluator_champsim_bingo.binary as binmod
    import flux_cache
    from flux_prefetcher import flow
    from flux_prefetcher import invented as inv
    from flux_prefetcher.study import PrefetcherRequest

    stock, built = tmp_path / "pythia", tmp_path / "rebuilt"
    stock.write_text("stock"); built.write_text("rebuilt")
    monkeypatch.setattr(binmod, "resolve_binary", lambda b=None: stock)
    monkeypatch.setattr(flow, "_resolve_traces", lambda r: {}, raising=False)
    monkeypatch.setattr(flow, "stage_traces", lambda t, log=None: t, raising=False)
    monkeypatch.setattr(flow, "_fingerprint", lambda b: {"champsim": f"{b.name}@x"}, raising=False)
    design = inv.Invention(name="invented2", header="// h", knobs={}, idea="", geomean_alone=1.0,
                           geomean_with_stack=1.06)
    monkeypatch.setattr(inv, "library", lambda *a, **k: [design])
    monkeypatch.setattr(inv, "build_binary", lambda *a, **k: built)
    monkeypatch.setattr(inv, "register", lambda found: [i.name for i in found])
    monkeypatch.setattr("flux_evaluator_champsim_bingo.resolve_source_tree", lambda *a: tmp_path,
                        raising=False)
    namespaces = []
    real = flux_cache.MeasurementCache

    def spy(db, fingerprint, **kw):
        namespaces.append(dict(fingerprint))
        return real(db, fingerprint, **kw)
    monkeypatch.setattr(flux_cache, "MeasurementCache", spy)

    s = flow.Study(request=PrefetcherRequest(db=str(tmp_path / "x.db"), include_invented=True,
                                             screen_only=True),
                   say=lambda m: None, started=0.0, rng=random.Random(0))
    flow._setup(s, None, None)
    assert s.binary == built, "the rebuild is what runs"
    assert s.fingerprint == {"champsim": "rebuilt@x"}, "provenance names what ran"
    assert namespaces == [{"champsim": "pythia@x"}], "the cache is keyed on the stock binary"
    assert s.screen.sources == {"invented2": design.digest}


def test_a_resumed_campaign_reads_its_own_record_back(tmp_path):
    """"Resumed" used to mean cache hits only: the seed pool started from the shipped default
    and the first proposer call was blind. The record is read back instead (D367)."""
    from flux_prefetcher.config import BingoConfig

    objective = {"study": "prefetcher", "traces": ["a", "b", "c"]}
    first = Recorder(str(tmp_path / "x.db"), objective, log=lambda m: None)
    assert not first.resumed
    good = DEFAULT.replace(pht_size=65536, pht_ways=32)
    first.phase("stage1")
    first.trial(DEFAULT, ("bingo",), rung="screen", strategy="incumbent",
                metrics={"geomean_speedup": 1.0485, "storage_bytes": 35096.0}, error=None,
                wall_s=1.0)
    first.trial(good, ("bingo",), rung="screen", strategy="llm",
                metrics={"geomean_speedup": 1.0632, "storage_bytes": 799840.0}, error=None,
                wall_s=1.0)
    first.trial(good, ("bingo", "sms"), rung="screen", strategy="compose",
                metrics={"geomean_speedup": 1.0681, "storage_bytes": 799840.0}, error=None,
                wall_s=1.0)
    first.trial(good, ("bingo",), rung="decide", strategy="confirmed",
                metrics={"geomean_speedup": 1.0570, "storage_bytes": 799840.0}, error=None,
                wall_s=1.0)
    first.trial(DEFAULT.replace(pc_width=2), ("bingo",), rung="screen", strategy="llm",
                metrics=None, error="simulation failed", wall_s=0.0)
    first.close("completed")

    second = Recorder(str(tmp_path / "x.db"), objective, log=lambda m: None)
    assert second.resumed
    known = second.known(rung="screen")
    assert [(c, round(g, 4)) for c, g in known] == [(good, 1.0632), (DEFAULT, 1.0485)], (
        "bingo-only, screen rung, best first; the composed stack, the confirmed number and "
        "the refused trial are not seeds")
    assert BingoConfig.from_knobs({**good.knobs(), "bingo_l2c_thresh": good.l2c_thresh}) == good
    assert Recorder(str(tmp_path / "y.db"), objective, log=lambda m: None).known() == []


def test_the_records_one_knob_pairs_become_typed_directions(tmp_path):
    """D369: every one-knob pair the campaign measured is a controlled experiment already paid
    for; its direction reaches the proposer as arithmetic, never as stored prose."""
    from flux_prefetcher.propose import build_prompt
    from flux_prefetcher.reflect import insights_text, pairwise_insights

    known = [
        (DEFAULT, 1.040),
        (DEFAULT.replace(pht_size=8192), 1.050),        # pht up: +0.010
        (DEFAULT.replace(pht_size=8192, ft_size=128), 1.052),
        (DEFAULT.replace(ft_size=128), 1.043),           # ft up: +0.003, twice
        (DEFAULT.replace(pc_width=8), 1.030),            # pc down from 16: one pair only
        (DEFAULT.replace(region_size=4096, pattern_len=64), 1.041),
    ]
    insights = pairwise_insights(known)
    by_knob = {k.knob: k for k in insights}
    assert by_knob["bingo_pht_size"].direction == "up"
    assert by_knob["bingo_pht_size"].mean_delta == pytest.approx(0.0095, abs=1e-4)
    assert by_knob["bingo_pht_size"].pairs == 2
    assert by_knob["bingo_ft_size"].direction == "up"
    assert "bingo_pc_width" not in by_knob, "one pair is an anecdote, not a direction"
    assert "bingo_region_size" not in by_knob, "one region pair: withheld too"
    assert insights[0].knob == "bingo_pht_size", "strongest effect first"

    text = insights_text(insights)
    assert "bingo_pht_size up: +0.0095" in text and "directions, not instructions" in text
    prompt = build_prompt({"a": 1.0}, 4, learned=text)
    assert "WHAT THE RECORD SHOWS" in prompt
    assert "WHAT THE RECORD SHOWS" not in build_prompt({"a": 1.0}, 4)

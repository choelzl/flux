"""mentor/records and mentor/extract (D397): the record layer's semantics -- results
distinct from conclusions -- and the generalized pairwise laws, including the
prefetcher-equivalence guarantee (its D369 adapter must produce identical facts)."""

from __future__ import annotations

from flux_extract import Law, laws_text, pairwise_laws
from flux_records import Records


def test_pairwise_laws_generalize_the_d369_arithmetic():
    known = [
        ({"a": 1.0, "b": 4.0}, 1.00),
        ({"a": 2.0, "b": 4.0}, 1.10),   # a up -> +0.10
        ({"a": 1.0, "b": 8.0}, 0.95),   # b up -> -0.05
        ({"a": 2.0, "b": 8.0}, 1.05),   # a up (at b=8) -> +0.10; b up (at a=2) -> -0.05
    ]
    laws = pairwise_laws(known, min_pairs=2, metric="geomean")
    by = {l.knob: l for l in laws}
    assert by["a"].direction == "up" and abs(by["a"].mean_delta - 0.10) < 1e-9
    assert by["b"].direction == "down" and abs(by["b"].mean_delta - 0.05) < 1e-9
    assert by["a"].pairs == 2 and by["b"].pairs == 2
    text = laws_text(laws)
    assert "directions, not instructions" in text and "a up" in text


def test_coupled_knobs_count_as_one():
    known = [
        ({"r": 1.0, "p": 1.0, "x": 0.0}, 1.0),
        ({"r": 2.0, "p": 2.0, "x": 0.0}, 1.2),   # r+p move together = one knob
        ({"r": 1.0, "p": 1.0, "x": 1.0}, 1.1),
        ({"r": 2.0, "p": 2.0, "x": 1.0}, 1.3),
    ]
    laws = pairwise_laws(known, min_pairs=2, coupled=[frozenset({"r", "p"})])
    assert any(l.knob == "p" and l.pairs == 2 for l in laws)  # sorted-first name


def test_single_pair_is_an_anecdote_and_withheld():
    known = [({"a": 1.0}, 1.0), ({"a": 2.0}, 2.0)]
    assert pairwise_laws(known, min_pairs=2) == []
    assert len(pairwise_laws(known, min_pairs=1)) == 1


def test_records_round_trip_results_and_conclusions(tmp_path):
    db = str(tmp_path / "r.db")
    r = Records(db, objective={"study": "t", "seed": 1})
    assert not r.resumed
    r.phase("scoring")
    r.trial({"policy": "S1", "fabric": "hier"}, "S1+hier", rung="analytic",
            strategy="cross", metrics={"thr": 12.5, "lat": 15.0})
    r.trial({"policy": "S0", "fabric": "hier"}, "S0+hier", rung="analytic",
            strategy="cross", metrics={"thr": 10.0, "lat": 18.0})
    r.trial({"refused": "bad taps"}, "refused:1", rung="gate", strategy="llm",
            metrics=None, error="not injective")
    r.conclude({"conclusion": {"balanced_pick": {"pair": "S1 + hier"}}})
    r.note("prefer smaller fabrics")

    r2 = Records(db, objective={"study": "t", "seed": 1})   # same objective resumes
    assert r2.resumed
    known = r2.known(rung="analytic", metric="thr")
    assert [k[0]["policy"] for k in known] == ["S1", "S0"]   # best first
    cs = r2.conclusions()
    assert cs and cs[0]["label"] == "INFERENCE"
    assert cs[0]["conclusion"]["balanced_pick"]["pair"] == "S1 + hier"


def test_records_swallow_unwritable_logbooks(tmp_path):
    r = Records(str(tmp_path / "nodir" / "x" / "r.db"), objective={"s": 1})
    r.trial({"a": 1}, "k", rung="analytic", strategy="s", metrics={"m": 1.0})
    r.conclude({"c": 1})
    assert r.known(rung="analytic", metric="m") == []        # no record, no crash


def test_imapping_run_records_and_reads_back(tmp_path):
    from flux_imapping import run_study
    from flux_imapping.flow import _record_context
    from flux_records import Records

    db = str(tmp_path / "im.db")
    run_study(seed=2, ops=2, climb_rounds=0, coordination_rounds=0, db_path=db)
    r = Records(db, objective={"study": "interconnect_mapping", "seed": 2, "ops": 2,
                               "vu": 0.7, "dma": 0.6})
    assert r.resumed
    known = r.known(rung="analytic", metric="holdout_throughput")
    assert len(known) >= 24 and "fabric" in known[0][0]
    ctx = _record_context(r)
    assert "WHAT THE RECORD SHOWS" in ctx and "rows/cy" in ctx
    assert "balanced pick" in ctx

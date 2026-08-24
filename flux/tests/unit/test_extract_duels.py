"""D400: head-to-head extraction for categorical knobs, and macarray's read-back.

Same posture as D369's laws: two configs differing in one knob are a controlled
experiment already paid for; where the knob's values are names, the extraction is a
duel with a winner, pooled across A-vs-B and B-vs-A orderings, anecdotes withheld.
"""

from __future__ import annotations

from flux_extract import duels_text, head_to_head


def test_head_to_head_pools_orderings_and_orients_the_winner():
    known = [
        ({"mult": "booth4", "red": "tree"}, 1200.0),
        ({"mult": "wallace", "red": "tree"}, 1100.0),
        ({"mult": "wallace", "red": "chain"}, 900.0),
        ({"mult": "booth4", "red": "chain"}, 1050.0),
    ]
    duels = head_to_head(known, metric="MHz")
    by_knob = {d.knob: d for d in duels}
    m = by_knob["mult"]
    assert (m.winner, m.loser, m.pairs) == ("booth4", "wallace", 2)
    assert m.mean_delta == 125.0                    # (100 + 150) / 2, both orderings pooled
    r = by_knob["red"]
    assert (r.winner, r.loser) == ("tree", "chain") and r.mean_delta == 175.0
    assert "booth4 beats wallace" in duels_text(duels)


def test_head_to_head_withholds_anecdotes_and_ignores_multiknob_pairs():
    known = [
        ({"mult": "booth4", "red": "tree"}, 1200.0),
        ({"mult": "wallace", "red": "chain"}, 900.0),   # two knobs differ: not controlled
        ({"mult": "array", "red": "tree"}, 1000.0),     # one pair only: an anecdote
    ]
    assert head_to_head(known) == []
    assert head_to_head(known, min_pairs=1)[0].winner == "booth4"


def test_macarray_record_context_reads_duels_back(tmp_path):
    from flux_macarray.flow import _record_context
    from flux_records import Records

    db = str(tmp_path / "mac.db")
    objective = {"study": "macarray", "shape": "test", "target_mhz": 1000.0,
                 "preserve_fmax": False}
    r1 = Records(db, objective)
    for knobs, fmax in [
        ({"multiplier": "booth4", "reducer": "tree", "pipeline": 1, "mapping": "delay"}, 1285.0),
        ({"multiplier": "wallace", "reducer": "tree", "pipeline": 1, "mapping": "delay"}, 1150.0),
        ({"multiplier": "booth4", "reducer": "tree", "pipeline": 0, "mapping": "delay"}, 1100.0),
        ({"multiplier": "wallace", "reducer": "tree", "pipeline": 0, "mapping": "delay"}, 980.0),
    ]:
        r1.trial(knobs, f"k{fmax}", rung="confirm", strategy="enumerate",
                 metrics={"fmax_mhz": fmax, "area_um2": 400.0}, analytic=False)
    assert _record_context(r1) == ""                # first run: nothing to read back yet
    r2 = Records(db, objective)
    assert r2.resumed
    ctx = _record_context(r2)
    assert "WHAT THE RECORD SHOWS" in ctx and "booth4 beats wallace" in ctx
    assert "pipeline" in ctx and "verdicts, not instructions" in ctx

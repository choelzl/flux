"""Head-to-head extraction for categorical knobs, and macarray's read-back (D400).

Two configs differing in one named knob are a duel with a winner, pooled across both orderings,
anecdotes (a single pair) withheld.
"""

from __future__ import annotations

from flux_records.extract import duels_text, head_to_head


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

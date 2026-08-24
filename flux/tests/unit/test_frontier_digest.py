"""What the orchestrator is shown of its own study (docs/decisions.md D330).

The digest is the model's entire picture of what has been measured, on every decision. The shared
renderer sorted by the first metric and took the top few, which for this application meant the six
CHEAPEST fabrics — and the cheapest were the unroutable ones, because a fabric missing connections
is missing the muxes that would have cost area. The model opened every decision looking at three
designs that cannot deliver a word, six rows out of fifty-seven, with optimistic frequencies.
"""

from __future__ import annotations

import json
import sqlite3


CLIENTS, BANKS, WIDTH = 28, 32, 128
ROUTABLE = [{"switches": 7, "in": 4, "out": 4}, {"switches": 4, "in": 7, "out": 8}]
# Three DIFFERENT routable fabrics. Reusing one shape three times makes them one fabric, which the
# digest correctly collapses — a fixture that cannot tell the two behaviours apart tests neither.
ROUTABLE_B = [{"switches": 8, "in": 4, "out": 4}, {"switches": 4, "in": 8, "out": 8}]
ROUTABLE_C = [{"switches": 7, "in": 4, "out": 8}, {"switches": 8, "in": 7, "out": 4}]
UNROUTABLE = [{"switches": 7, "in": 4, "out": 4}, {"switches": 7, "in": 4, "out": 4},
              {"switches": 28, "in": 1, "out": 2}]


def _result(area, fmax, served, cap):
    """A real `Result`, built through the ABI and serialised by it.

    Hand-writing the JSON was tried and produced a shape the ABI rejects — no `ci_low`, no
    `validity`. A fixture that fakes the serialisation of the type under test is testing its own
    guess at the format.
    """
    from flux_evaluator_abi import (
        Bottleneck, Domain, Escalation, Estimate, Limiter, Method, Provenance, Result, Validity,
    )

    def est(value, unit):
        return Estimate(value=float(value), ci_low=float(value), ci_high=float(value),
                        unit=unit, method=Method.MEASURED)

    from flux_evaluator_interconnect_phys.adapter import EVALUATOR_ID

    return Result(
        metrics={"area_mm2": est(area, "mm2"), "fmax_mhz": est(fmax, "MHz"),
                 "throughput_words_per_cycle": est(served, "words/cycle"),
                 "max_throughput_words_per_cycle": est(cap, "words/cycle")},
        validity=Validity(ok=True, checker_version="test"),
        domain=Domain(in_domain=True),
        bottleneck=Bottleneck(limiter=Limiter.NOC),
        provenance=Provenance(evaluator=EVALUATOR_ID, inputs={}),
        escalation=Escalation(recommended=False))


def _store(tmp_path, rows):
    """rows: (label, stages, area, fmax, served, max_served).

    The REAL `CampaignStore` creates the schema. An earlier version of this fixture hand-wrote a
    CREATE TABLE and the columns did not match, which is the same mistake D323 recorded: a test
    that builds its own version of the thing under test agrees with itself and with nothing else.
    """
    from flux_evaluator_interconnect_phys.adapter import EVALUATOR_ID
    from flux_store import CampaignStore

    db = str(tmp_path / "s.db")
    CampaignStore(db)                     # schema, exactly as the application makes it
    con = sqlite3.connect(db)
    con.execute("INSERT INTO campaigns (campaign_id, objective_json, objective_hash, status,"
                " phase, created_at) VALUES ('c','{}','h','done','done','now')")
    for i, (label, stages, area, fmax, served, cap) in enumerate(rows):
        spec = {"kind": "xbar_staged", "clients": CLIENTS, "banks": BANKS,
                "width_bits": WIDTH, "stages": stages}
        con.execute(
            "INSERT INTO results (id, workload_hash, arch_hash, mapping_hash, evaluator,"
            " result_json, created_at) VALUES (?,?,?,?,?,?,?)",
            (i, "w", f"a{i}", None, EVALUATOR_ID,
             json.dumps(_result(area, fmax, served, cap).to_dict()), "now"))
        con.execute(
            "INSERT INTO trials (campaign_id, seq, phase, candidate_json, candidate_key,"
            " workload_hash, arch_hash, result_id, status, strategy_kind, deterministic,"
            " created_at) VALUES ('c',?,'escalate',?,?,?,?,?,'ok','grid',1,'now')",
            (i, json.dumps({"label": label, "variant": spec}), label, "w", f"a{i}", i))
    con.commit()
    con.close()
    return db


def test_an_unroutable_fabric_is_not_part_of_the_frontier(tmp_path):
    """It is the CHEAPEST thing in the store, and it delivers nothing. Sorting by area put it
    first in the model's view of the study."""
    import flux_interconnect.flow as demo

    db = _store(tmp_path, [
        ("broken", UNROUTABLE, 0.0138, 1162.0, 8.4, 28.0),
        ("good", ROUTABLE, 0.0153, 879.0, 14.9, 28.0)])
    digest = demo.frontier_digest(db)
    assert "broken" not in digest
    assert "good" in digest


def test_a_narrow_waist_is_not_part_of_the_frontier(tmp_path):
    import flux_interconnect.flow as demo

    db = _store(tmp_path, [("narrow", ROUTABLE, 0.010, 900.0, 4.0, 4.0),
                           ("good", ROUTABLE, 0.0153, 879.0, 14.9, 28.0)])
    assert "narrow" not in demo.frontier_digest(db)


def test_the_digest_says_how_much_it_is_not_showing(tmp_path):
    """Six rows out of fifty-seven, with nothing saying so, reads as the whole study."""
    import flux_interconnect.flow as demo

    shapes = [ROUTABLE, ROUTABLE_B, ROUTABLE_C]
    db = _store(tmp_path, [(f"f{i}", shapes[i % 3], 0.015 + i / 1000, 900.0, 14.0 + i, 28.0)
                           for i in range(9)])
    assert "of 9 measured fabrics" in demo.frontier_digest(db)


def test_both_ends_of_the_trade_off_are_shown(tmp_path):
    """A search told only about the cheapest corner cannot see the trade-off it is exploring."""
    import flux_interconnect.flow as demo

    db = _store(tmp_path, [
        ("smallest", ROUTABLE, 0.0100, 900.0, 9.0, 28.0),
        ("fastest", ROUTABLE_C, 0.0400, 900.0, 18.0, 28.0),
        ("middle", ROUTABLE_B, 0.0200, 900.0, 13.0, 28.0)])
    digest = demo.frontier_digest(db)
    assert "smallest" in digest and "fastest" in digest


def test_one_row_per_fabric_not_per_label(tmp_path):
    """`hybrid-radixradix4-xbarswitches4` and `xbar_staged-7x4x4-4x7x8-first` ARE
    `xbar_staged-7x4x4-4x7x8`. Three of six rows were the same silicon under three names."""
    import flux_interconnect.flow as demo

    db = _store(tmp_path, [
        ("xbar_staged-7x4x4-4x7x8", ROUTABLE, 0.0153, 879.0, 14.9, 28.0),
        ("hybrid-radixradix4-xbarswitches4", ROUTABLE, 0.0153, 879.0, 14.9, 28.0),
        ("xbar_staged-7x4x4-4x7x8-first", ROUTABLE, 0.0153, 879.0, 14.9, 28.0)])
    digest = demo.frontier_digest(db)
    assert sum(line.strip().startswith("xbar_staged") or line.strip().startswith("hybrid")
               for line in digest.splitlines()) == 1


def test_an_empty_store_says_so(tmp_path):
    import flux_interconnect.flow as demo

    assert demo.frontier_digest(str(tmp_path / "none.db")) == "(nothing measured yet)"

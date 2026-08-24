"""The small factual fixes of the trimmed review (D440): one interconnect Result builder, the
store's metric maxima instead of raw SQL in the lessons, no fabricated bottleneck in the record
layer, one PpaReport projection, and adapters declaring what they translate."""

from __future__ import annotations


def test_the_store_answers_metric_maxima_and_the_lessons_read_it(tmp_path):
    from flux_records.mining.lessons import achieved_maxima
    from flux_records import Records
    from flux_store import CampaignStore

    db = str(tmp_path / "m.db")
    r = Records(db, objective={"s": 1})
    r.trial({"a": 1}, "k1", stage="screen", strategy="s", metrics={"thr": 12.0, "lat": 3.0})
    r.trial({"a": 2}, "k2", stage="screen", strategy="s", metrics={"thr": 7.5, "lat": 9.0})
    with CampaignStore(db) as store:
        assert store.metric_maxima() == {"thr": 12.0, "lat": 9.0}
    assert achieved_maxima(db) == {"thr": 12.0, "lat": 9.0}
    assert achieved_maxima(tmp_path / "absent.db") == {}


def test_a_record_written_without_a_cost_model_claims_no_bottleneck(tmp_path):
    from flux_evaluator_abi import Limiter
    from flux_records import Records

    db = str(tmp_path / "r.db")
    r = Records(db, objective={"s": 2})
    r.trial({"a": 1}, "k", stage="screen", strategy="s", metrics={"m": 1.0})
    (t,) = r.store.trials(r.campaign_id, status="ok")
    assert t.result.bottleneck.limiter is Limiter.NONE
    assert Limiter("none") is Limiter.NONE


def test_ppa_report_projects_its_metrics_once():
    from flux_evaluator_openroad.flow import PpaReport

    rep = PpaReport(area_um2=431.0, utilization_pct=55.0, power_total_w=0.012, power_breakdown_w={},
                    worst_slack_ps=200.0, clock_period_ps=1000.0, cell_count=321, flow_depth="placement",
                    yosys_log_tail="", openroad_log_tail="")
    m = rep.metrics()
    assert m["fmax_mhz"] == 1e6 / 800 and m["area_um2"] == 431.0 and m["power_w"] == 0.012
    assert m["cell_count"] == 321 and m["clock_period_ps"] == 1000.0 and m["worst_slack_ps"] == 200.0
    import dataclasses
    assert dataclasses.replace(rep, worst_slack_ps=1000.0).fmax_mhz == float("inf")


def test_every_adapter_declares_the_abi_batch_base():
    """D441: the sequential batch body lives once, in `SequentialBatch`; every registered
    adapter declares it as a base and defines no `evaluate_batch` of its own."""

    from flux_evaluator_abi import SequentialBatch, evaluator_class
    from flux_evaluator_abi.registry import _DEFAULTS

    for name in _DEFAULTS:
        cls = evaluator_class(name)
        assert issubclass(cls, SequentialBatch), name
        assert "evaluate_batch" not in cls.__dict__, name


def test_the_loops_log_words_are_role_words():
    from flux_profile import role_of
    from flux_tui.panels import _log_role

    assert _log_role("patched recip: 2 edit(s) -- fix") == "generator"
    assert _log_role("ADMITTED recip: lut#3") == "evaluator"
    assert _log_role("resuming exp from its best design") == "mentor"
    assert _log_role("decision lut#3: ...") == "orchestrator"
    assert _log_role("[tui] hello") == "dim" and role_of("brief: exp") == "mentor"


def test_the_mentor_guards_use_every_studys_nouns():
    from flux_records.mining.conclusions import _ABSOLUTE
    from flux_records.mining.lessons import _UNIVERSAL_MISS

    for text in ("no mapping reaches 8 rows/cy", "every prefetcher fails on 429.mcf", "no fabric clears 1 GHz"):
        assert _UNIVERSAL_MISS.search(text) or _ABSOLUTE.search(text), text
    assert _ABSOLUTE.search("every operator misses by 12 ULP") and _UNIVERSAL_MISS.search("no policy is conflict-free")


def test_library_context_is_one_budgeted_cited_renderer():
    from flux_knowledge import BM25Index, Chunk, library_context

    idx = BM25Index([Chunk(id="lib/a#0", standard_id="library", source_path="mentor/knowledge/library/a.md",
                           heading=None, text="xor swizzle beats modulo bank mapping " * 20),
                     Chunk(id="lib/b#0", standard_id="library", source_path="x/b.md", heading=None,
                           text="cordic hardware for transcendental functions")])
    out = library_context(["xor swizzle", "cordic hardware", "xor swizzle"], index=idx)
    assert out.startswith("FROM THE OPERATOR'S LIBRARY") and out.count("[a.md]") == 1 and "[b.md]" in out
    assert "..." not in out and ("xor swizzle beats modulo bank mapping " * 20).strip() in out   # D548: whole
    assert library_context(["nothing here zzz"], index=idx) == ""
    plain = library_context(["cordic"], index=idx, header=None, prefix="- ", cite=False)
    assert plain == "- cordic hardware for transcendental functions"


def test_the_connectors_share_one_paragraph_splitter():
    from flux_knowledge.connectors.adoc import parse_adoc
    from flux_knowledge.connectors.text import parse_text
    from flux_knowledge.document import chunks_from, paragraphs

    md = "# Title\n\nfirst line\nsecond line\n\n## Sub ##\n\nthird\n"
    assert parse_text(md) == [("Title", "first line second line"), ("Sub", "third")]
    adoc = "= Doc\n\npara one\ncontinues\n\n----\nlisting dropped?\n----\n\n== Section\n\nlast\n"
    got = parse_adoc(adoc)
    assert got[0] == ("Doc", "para one continues") and got[-1] == ("Section", "last")
    assert paragraphs("a\nb\n\nc", heading=lambda raw: False) == [(None, "a b"), (None, "c")]
    ch = chunks_from([("h", "t")], standard_id="s", stem="doc", source_path="p/doc.md")
    assert ch[0].id == "s/doc#0" and ch[0].heading == "h" and ch[0].source_path == "p/doc.md"


def test_the_standings_words_are_shared_by_the_loop_and_the_tui():
    from flux_profile import BEST_SO_FAR, NOT_YET_TRIED, PROVEN, STANDINGS
    from flux_tui.panels import standings_lines

    lines, roles = standings_lines({"parts": [{"part": "a", "state": PROVEN, "name": "x"},
                                              {"part": "b", "state": BEST_SO_FAR, "score": 3, "name": "y"},
                                              {"part": "c", "state": NOT_YET_TRIED}]})
    assert roles[1:] == [STANDINGS[PROVEN], STANDINGS[BEST_SO_FAR], STANDINGS[NOT_YET_TRIED]]
    assert "proven" in lines[1] and "best so far" in lines[2] and "not yet tried" in lines[3]


def test_controlled_pairs_are_one_scan_for_laws_duels_and_mined_ratios():
    from flux_records.extract import controlled_pairs, head_to_head, numeric_knobs, pairwise_laws
    from flux_records.mining import mining

    known = [({"w": 8, "d": 2}, 1.0), ({"w": 16, "d": 2}, 2.0), ({"w": 16, "d": 4}, 3.0), ({"w": 8, "d": 4}, 1.5)]
    pairs = controlled_pairs(known, numeric=True)
    assert sorted(k for k, _a, _b in pairs) == ["d", "d", "w", "w"]
    laws = pairwise_laws(known, metric="m")
    assert {(l.knob, l.direction) for l in laws} == {("w", "up"), ("d", "up")}
    duels = head_to_head([({"mul": "booth"}, 3.0), ({"mul": "wallace"}, 2.0), ({"mul": "booth"}, 3.5),
                          ({"mul": "wallace"}, 2.5)], metric="MHz")
    assert duels and duels[0].winner == "booth"
    assert numeric_knobs({"w": 8, "assign": {"a": 1, "b": "x"}, "flag": True, "arch": {"z": 1}}) == {"w": 8.0, "assign.a": 1.0}
    assert mining._numeric_knobs({"w": 2}) == {"w": 2.0}


def test_records_offer_typed_rows_and_discovery(tmp_path):
    from flux_records import ConclusionRow, Records, RefusalRow, TrialRow

    r = Records(str(tmp_path / "t.db"), objective={"s": 3})
    r.trial({"style": "lut", "name": "a"}, "a", stage="screen", strategy="s", metrics={"fmax_mhz": 900.0, "area_um2": 5.0})
    r.trial({"style": "poly", "name": "b"}, "b", stage="confirm", strategy="s", metrics={"fmax_mhz": 800.0})
    r.trial({"name": "c"}, "c", stage="gate", strategy="s", metrics=None, error="2 over budget")
    r.conclude({"decision": "a", "decided_by": "knee"})
    rows = r.known_rows()
    assert [type(x) for x in rows] == [TrialRow, TrialRow] and rows[0].metrics == {"fmax_mhz": 900.0, "area_um2": 5.0}
    assert r.known_rows(stage="confirm")[0].candidate["style"] == "poly"
    assert r.stages() == ["screen", "confirm"] and r.metrics() == ["fmax_mhz", "area_um2"]
    assert r.metrics(stage="confirm") == ["fmax_mhz"]
    (ref,) = r.refusal_rows(stage="gate")
    assert isinstance(ref, RefusalRow) and ref.reason == "2 over budget" and ref.candidate["name"] == "c"
    (con,) = r.conclusion_rows()
    assert isinstance(con, ConclusionRow) and con.detail == {"decision": "a", "decided_by": "knee"} and con.label == "INFERENCE"
    assert r.known(stage="screen", metric="fmax_mhz")[0][1] == 900.0             # the tuple API stays


def test_record_read_back_is_the_one_skeleton(tmp_path):
    from flux_records.extract import record_read_back
    from flux_records import Records

    objective = {"s": 4}
    r = Records(str(tmp_path / "rb.db"), objective=objective)
    for style, method, v in [("lut", "m1", 900.0), ("poly", "m1", 700.0), ("lut", "m2", 950.0), ("poly", "m2", 760.0)]:
        r.trial({"style": style, "method": method, "latency": 2}, f"{style}-{method}", stage="screen", strategy="s",
                metrics={"fmax_mhz": v})
    r.conclude({"decision": "lut-m2", "decided_by": "knee"})
    assert record_read_back(r, stage="screen", metric="fmax_mhz") == ""                 # a fresh run reads nothing
    again = Records(str(tmp_path / "rb.db"), objective=objective)
    text = record_read_back(again, stage="screen", metric="fmax_mhz", knobs=("style", "method"),
                            metric_label="MHz", conclusion=lambda c: f"decided {c['decision']}",
                            extra=lambda rec: [f"{len(rec.known_rows())} measured"])
    assert text.startswith("WHAT THE RECORD SHOWS (this campaign's earlier runs")
    assert "decided lut-m2" in text and "style: lut beats poly" in text and "4 measured" in text
    assert record_read_back(None, stage="screen", metric="x") == ""

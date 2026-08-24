"""The loop's memory across runs, and the menu it offers (docs/decisions.md D317).

`DirectedSearch._done` started empty on every run, so a warm store was re-enumerated from scratch
each time. The campaign cache made the EVALUATIONS free — 78% of this repo's screen trials are
cache hits, 94% over recent runs — but the enumeration itself is Python, and one such round spent
2,448 seconds, 38% of a whole run, re-deriving 3,307 topologies to find nothing.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from flux_directed_search import Action, DirectedSearch, Outcome



def _store(tmp_path, ids):
    db = tmp_path / "s.db"
    con = sqlite3.connect(db)
    con.execute("create table campaigns (objective_json text)")
    con.executemany("insert into campaigns values (?)",
                    [(json.dumps({"id": i}),) for i in ids])
    con.commit(); con.close()
    return str(db)


def _id(round_part, family):
    return f"demo/interconnect-28c-32b-128bit/round{round_part}-{family}/v1"


def test_a_completed_family_is_remembered(tmp_path):
    import flux_interconnect.flow as demo

    db = _store(tmp_path, [_id("3", "clos"), _id("7", "hybrid")])
    assert demo.already_enumerated(db) == ["clos", "hybrid"]


def test_only_numeric_rounds_count_as_enumerations(tmp_path):
    """`rounda4-staged` is an ANNEAL and `roundp5-staged` a perturbation; both merely inherited a
    family tag from the objective document. Reading them as enumerations would retire families
    nobody enumerated -- worse than the repetition being fixed."""
    import flux_interconnect.flow as demo

    db = _store(tmp_path, [_id("a4", "staged"), _id("p5", "staged"), _id("2s1", "hybrid")])
    assert demo.already_enumerated(db) == []


def test_another_problem_does_not_count(tmp_path):
    """A store reused for a different client or bank count has genuinely enumerated nothing."""
    import flux_interconnect.flow as demo

    db = _store(tmp_path, ["demo/interconnect-16c-8b-64bit/round1-clos/v1"])
    assert demo.already_enumerated(db) == []


def test_an_unknown_family_is_ignored(tmp_path):
    import flux_interconnect.flow as demo

    db = _store(tmp_path, [_id("1", "not-a-family")])
    assert demo.already_enumerated(db) == []


def test_an_unreadable_store_has_simply_done_nothing(tmp_path):
    import flux_interconnect.flow as demo

    assert demo.already_enumerated(str(tmp_path / "does-not-exist.db")) == []
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"not a database")
    assert demo.already_enumerated(str(broken)) == []


# -- the loop honours it --------------------------------------------------------------------


def _search(done=None):
    def noop(_p):
        return Outcome(gained=0)

    return DirectedSearch(
        [Action("enumerate", "m", noop, variants=({"family": "clos"}, {"family": "hybrid"}),
                variant_key=lambda p: p["family"]),
         Action("anneal", "m", noop, variant_key=lambda p: "annealed")],
        ask=None, problem="t", done=done)


def test_prior_work_is_not_offered_again():
    note = _search(done=["clos"])._remaining_note()
    assert "clos" not in note
    assert "hybrid" in note


def test_without_prior_work_everything_is_outstanding():
    note = _search()._remaining_note()
    assert "clos" in note and "hybrid" in note


def test_a_fully_enumerated_store_says_enumerating_again_finds_nothing():
    note = _search(done=["clos", "hybrid"])._remaining_note()
    assert "nothing new" in note
    assert "anneal" in note, "the actions that CAN still find something must be named"


def test_the_caller_is_not_aliased_into_the_loop():
    """`done` is the caller's list; the loop appends to its own copy as it works."""
    mine = ["clos"]
    search = _search(done=mine)
    search._done.append("annealed")
    assert mine == ["clos"]


# -- the pruned menu ------------------------------------------------------------------------


def test_perturb_is_gone_and_anneal_took_its_job():
    """`perturb` looked at one neighbourhood and stopped -- no acceptance test, no walk, and a
    key that made a second look impossible (D313). `anneal` does the same thing by walking, so
    two actions covered one capability and the weaker one drew 5 of 14 steps in a real run, two
    of them wasted on repeats it could not see."""
    import flux_interconnect.flow as demo

    src = Path(demo.__file__).read_text()
    assert 'Action("perturb"' not in src
    assert 'Action("anneal"' in src
    assert '"from"' in src, "anneal must still accept a named design to walk outward from"


def test_every_action_the_menu_wires_up_actually_exists():
    """A NameError inside `main()` passed all 1,538 tests.

    The actions are nested functions inside `main`, so nothing imports them and no test calls
    them: removing `perturb` also removed `_anneal` and `_population`, and the suite stayed
    green while the demo died on its first line of real work. Running `main` here is not an
    option -- it places silicon -- so the wiring is checked statically instead, which is exactly
    the check the interpreter would have done at the point the suite never reaches.
    """
    import ast

    import flux_interconnect.flow as demo

    tree = ast.parse(Path(demo.__file__).read_text())
    # `run_study`, not `main`: the study became a callable sub-flow and the command line moved
    # out from around it (D345). The registry lives with the study, which is the half a calling
    # orchestrator reaches.
    main = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "run_study")
    defined = {n.name for n in ast.walk(main) if isinstance(n, ast.FunctionDef)}
    defined |= {n.id for n in ast.walk(main)
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
    # Lambda and function parameters bind names: `variant_key=lambda p: p["family"]` reads `p`.
    for node in ast.walk(main):
        if isinstance(node, (ast.Lambda, ast.FunctionDef, ast.AsyncFunctionDef)):
            defined |= {a.arg for a in node.args.args + node.args.kwonlyargs}
            for extra in (node.args.vararg, node.args.kwarg):
                if extra:
                    defined.add(extra.arg)
    # Everything the module itself binds: defs, imports, and module-level assignments.
    module_level: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            module_level.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            module_level |= {(a.asname or a.name).split(".")[0] for a in node.names}
    module_level |= {n.id for n in ast.walk(tree)
                     if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}

    registry = next(n.value for n in ast.walk(main)
                    if isinstance(n, ast.Assign)
                    and any(getattr(t, "id", "") == "actions" for t in n.targets))
    referenced = {n.id for n in ast.walk(registry) if isinstance(n, ast.Name)}
    import builtins

    missing = sorted(referenced - defined - module_level - set(dir(builtins)))
    assert not missing, f"the action registry references names that do not exist: {missing}"


def test_the_menu_is_the_five_actions_we_think_it_is():
    import ast

    import flux_interconnect.flow as demo

    tree = ast.parse(Path(demo.__file__).read_text())
    names = {c.args[0].value for c in ast.walk(tree)
             if isinstance(c, ast.Call) and getattr(c.func, "id", "") == "Action"
             and c.args and isinstance(c.args[0], ast.Constant)}
    assert names == {"anneal", "enumerate", "propose", "measure", "repair"}, (
        "the menu changed; if that is deliberate, say why here and update this list")


# -- the fail edge --------------------------------------------------------------------------


def _escalate_store(tmp_path, rows):
    """A store with escalate trials: rows are (label, fmax, capacity)."""
    db = tmp_path / "e.db"
    con = sqlite3.connect(db)
    con.execute("create table trials (candidate_json text, result_id int, phase text)")
    con.execute("create table results (id int, result_json text)")
    for i, (label, fmax, cap) in enumerate(rows):
        con.execute("insert into trials values (?,?,?)", (json.dumps(
            {"label": label, "variant": {"kind": "xbar_staged", "stages": [{"switches": 1}]}}),
            i, "escalate"))
        con.execute("insert into results values (?,?)", (i, json.dumps({
            "metrics": {"fmax_mhz": {"value": fmax},
                        "max_throughput_words_per_cycle": {"value": cap}},
            "provenance": {"inputs": {"blocks": "selector_28x128b: 32 x 417.0 um2, 1773 ps"}}})))
    con.commit(); con.close()
    return str(db)


def test_a_near_miss_is_found_and_carries_its_critical_path(tmp_path):
    """The per-arity delays are the whole point: 1/1773 ps is 564 MHz, so the record already
    says which rank set the clock. A repair prompt without them is guesswork."""
    import flux_interconnect.flow as demo

    db = _escalate_store(tmp_path, [("close", 595.0, 28)])
    (found,) = demo.near_misses(db)
    assert found["label"] == "close" and found["fmax_mhz"] == 595.0
    assert "1773 ps" in found["blocks"]


def test_a_fabric_that_passes_is_not_a_near_miss(tmp_path):
    import flux_interconnect.flow as demo

    db = _escalate_store(tmp_path, [("fine", 700.0, 28)])
    assert demo.near_misses(db) == []


def test_a_narrow_waist_is_a_different_problem(tmp_path):
    """A fabric that cannot carry every client is not a timing near-miss, and asking a model to
    'make it faster' would invite it to buy frequency by shedding concurrency -- which the study
    refuses outright rather than ranking."""
    import flux_interconnect.flow as demo

    db = _escalate_store(tmp_path, [("narrow", 595.0, 4)])
    assert demo.near_misses(db) == []


def test_a_hopeless_design_is_out_of_band(tmp_path):
    """539 MHz against a 600 MHz floor is not one rank too wide; repairing it is a new design."""
    import flux_interconnect.flow as demo

    db = _escalate_store(tmp_path, [("hopeless", 400.0, 28)])
    assert demo.near_misses(db) == []


def test_near_misses_come_closest_first(tmp_path):
    import flux_interconnect.flow as demo

    db = _escalate_store(tmp_path, [("far", 560.0, 28), ("close", 597.0, 28)])
    assert [r["label"] for r in demo.near_misses(db)] == ["close", "far"]


def test_an_unreadable_store_yields_no_repairs(tmp_path):
    import flux_interconnect.flow as demo

    assert demo.near_misses(str(tmp_path / "nope.db")) == []


def test_every_function_the_demo_calls_actually_exists():
    """Twice now, an edit that replaced a span of the file by index deleted functions that were
    still being called, and the suite stayed green both times.

    The first time it took `_anneal` and `_population` with `perturb`; the check added then only
    covered the action registry. The second took `_calibration_path`, `calibration_bucket`,
    `check_toolchain_drift` and `_toolchain_path` — four module-level functions, three of them
    still referenced, `_calibration_path` from four call sites. `import demo` succeeds with any
    of these missing, so an import smoke test proves nothing; the demo simply dies on its first
    real round.

    There is no linter in this repo and none in CI, so nothing else catches an undefined name.
    This is narrow on purpose — direct calls to a bare name, which is the shape that broke — and
    it costs nothing.
    """
    import ast
    import builtins

    import flux_interconnect.flow as demo

    tree = ast.parse(Path(demo.__file__).read_text())

    bound: set[str] = set(dir(builtins))
    for node in ast.walk(tree):
        if isinstance(node, ast.Lambda):
            # Lambda parameters bind names too — `variant_key=lambda p: p["family"]` made `p`
            # look undefined to an earlier version of this check.
            bound |= {a.arg for a in node.args.args + node.args.kwonlyargs}
        elif isinstance(node, ast.ClassDef):
            # A class binds its name and nothing else here; it has no `args`. The module had no
            # class at all until the orchestrator's actions got an explicit context (D355).
            bound.add(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            bound.add(node.name)
            bound |= {a.arg for a in node.args.args + node.args.kwonlyargs}
            if node.args.vararg:
                bound.add(node.args.vararg.arg)
            if node.args.kwarg:
                bound.add(node.args.kwarg.arg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            bound |= {(a.asname or a.name).split(".")[0] for a in node.names}
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)

    called = {(n.func.id, n.lineno) for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    missing = sorted(f"{name} (line {line})" for name, line in called if name not in bound)
    assert not missing, f"demo.py calls names that are never defined: {missing}"


# NO ORDERING CHECK HERE, and it is not an oversight (docs/decisions.md D333).
#
# Two bugs this session were use-before-assignment in `main`: `repairable` computed after the
# action registry that read it, `ask` initialised after the `--problem` block that needed it. Both
# crashed the demo on startup with every test green, because nothing runs `main` — it places
# silicon.
#
# A static check for it was written twice and was wrong both times. The first only flagged names
# already seen, so a name assigned LATER was simply absent and the bug walked through when it was
# re-introduced to verify. The second collected every binding first and then compared against the
# enclosing statement's line, which flags every read inside an `if` block of a name assigned
# inside that same block — fifty false positives on correct code.
#
# Getting it right needs real control-flow analysis, which is a linter, and this repo has none:
# pyflakes, flake8, pylint and mypy are all absent from the dev shell. `pyflakes` on `demo.py`
# would catch exactly this class in one line. That is the fix; a hand-rolled approximation in a
# test file is not, and two attempts at one is enough evidence.


# -- one repair per critical path -------------------------------------------------------------


def _escalate_store_arity(tmp_path, rows):
    """rows: (label, stages, fmax). Capacity is always 28 so every row is a timing near-miss."""
    from flux_evaluator_interconnect_phys.adapter import EVALUATOR_ID
    from flux_evaluator_abi import (
        Bottleneck, Domain, Escalation, Estimate, Limiter, Method, Provenance, Result, Validity,
    )
    from flux_store import CampaignStore

    db = str(tmp_path / "a.db")
    CampaignStore(db)
    con = sqlite3.connect(db)
    con.execute("INSERT INTO campaigns (campaign_id, objective_json, objective_hash, status,"
                " phase, created_at) VALUES ('c','{}','h','done','done','now')")
    for i, (label, stages, fmax) in enumerate(rows):
        spec = {"kind": "xbar_staged", "clients": 28, "banks": 32, "width_bits": 128,
                "stages": stages}

        def est(v, unit):
            return Estimate(value=float(v), ci_low=float(v), ci_high=float(v), unit=unit,
                            method=Method.MEASURED)

        result = Result(
            metrics={"fmax_mhz": est(fmax, "MHz"),
                     "max_throughput_words_per_cycle": est(28, "words/cycle")},
            validity=Validity(ok=True, checker_version="t"), domain=Domain(in_domain=True),
            bottleneck=Bottleneck(limiter=Limiter.NOC),
            provenance=Provenance(evaluator=EVALUATOR_ID, inputs={"blocks": "x"}),
            escalation=Escalation(recommended=False))
        con.execute("INSERT INTO results (id, workload_hash, arch_hash, evaluator, result_json,"
                    " created_at) VALUES (?,?,?,?,?,'now')",
                    (i, "w", f"a{i}", EVALUATOR_ID, json.dumps(result.to_dict())))
        con.execute("INSERT INTO trials (campaign_id, seq, phase, candidate_json, candidate_key,"
                    " workload_hash, arch_hash, result_id, status, strategy_kind, deterministic,"
                    " created_at) VALUES ('c',?,'escalate',?,?,?,?,?,'ok','grid',1,'now')",
                    (i, json.dumps({"label": label, "variant": spec}), label, "w", f"a{i}", i))
    con.commit(); con.close()
    return db


# Two fabrics whose clock is set by the same 28:1 selector, spelled differently, plus one whose
# critical path is a 21:1 — the shape observed in a real run, where four of six repairs attacked
# the same bottleneck and all four chose the same fix.
_SAME_A = [{"switches": 28, "in": 1, "out": 2}, {"switches": 2, "in": 28, "out": 16}]
_SAME_B = [{"switches": 28, "in": 1, "out": 4}, {"switches": 4, "in": 28, "out": 8}]
_OTHER = [{"switches": 14, "in": 2, "out": 3}, {"switches": 2, "in": 21, "out": 16}]


def test_two_fabrics_with_one_bottleneck_are_one_repair(tmp_path):
    import flux_interconnect.flow as demo

    db = _escalate_store_arity(tmp_path, [("a", _SAME_A, 564.0), ("b", _SAME_B, 564.0)])
    assert len(demo.near_misses(db)) == 2
    assert len(demo.near_misses(db, distinct=True)) == 1


def test_different_bottlenecks_are_different_repairs(tmp_path):
    import flux_interconnect.flow as demo

    db = _escalate_store_arity(tmp_path, [("a", _SAME_A, 564.0), ("c", _OTHER, 595.0)])
    assert len(demo.near_misses(db, distinct=True)) == 2


def test_the_closest_miss_represents_its_bottleneck(tmp_path):
    """The nearest one is likeliest to be fixable, and whatever fixes it is the change its
    siblings need too."""
    import flux_interconnect.flow as demo

    db = _escalate_store_arity(tmp_path, [("far", _SAME_A, 520.0), ("near", _SAME_B, 590.0)])
    (kept,) = demo.near_misses(db, distinct=True)
    assert kept["label"] == "near"


def test_the_full_list_is_unchanged_by_default(tmp_path):
    """Validation accepts any genuine near-miss; only what is OFFERED is deduplicated, so a model
    naming a sibling is not refused for it."""
    import flux_interconnect.flow as demo

    db = _escalate_store_arity(tmp_path, [("a", _SAME_A, 564.0), ("b", _SAME_B, 564.0)])
    assert {r["label"] for r in demo.near_misses(db)} == {"a", "b"}


def test_a_fabric_that_will_not_build_has_no_critical_path():
    import flux_interconnect.flow as demo

    assert demo.critical_arity({"kind": "xbar_staged", "stages": []}) == 0
    assert demo.critical_arity(None) == 0

"""D421: the problem-agnostic loop, exercised by a toy problem with NO model and NO
tools. The toy is deliberately unlike hardware -- match a hidden byte pattern, one
half at a time -- so every mechanism proven here is proven problem-agnostic: the
planner and its cooldown, the generation<->test inner loop (design, fast check,
patch, revert, best-at-budget), the gate, admit-and-freeze, compose, the cached
chain, the frontier, the decision, the record, and resuming from proven and
best-so-far parts."""

from __future__ import annotations

import json
import re

import pytest

from flux_loop import (BuildError, Candidate, LoopRequest, Problem, Scored, Verdict,
                       apply_patch, run_loop)
from flux_llm import Reply
from flux_loop import Prototype

TARGET = {"lo": [3, 1, 4, 1], "hi": [5, 9, 2, 6]}


class Bytes(Problem):
    """Artifact: a JSON list of four ints. Parts: lo, hi. Gate: exact match."""

    name = "bytes"

    def subgoals(self):
        return ["lo", "hi"]

    def design_prompt(self, subgoal, method, state, human, prior, prior_why):
        return f"design {subgoal}" + (f" from {prior.artifact}" if prior else ""), None

    def parse_design(self, reply, subgoal):
        try:
            vals = json.loads(reply)
        except Exception:
            return None, "not json"
        return Candidate(f"{subgoal}-v", json.dumps(vals), knobs={"len": len(vals)},
                         subgoal=subgoal), ""

    def build(self, cand, subgoal, state):
        vals = json.loads(cand.artifact)
        if not (isinstance(vals, list) and len(vals) == 4):
            raise BuildError("need exactly four ints")
        return vals

    def fast_check(self, built, cand, subgoal, state):
        # the "unit vectors": the first two positions only
        bad = sum(1 for i in range(2) if built[i] != TARGET[subgoal][i])
        return bad, f"{bad} of the first two positions wrong"

    def judge(self, built, cand, subgoal, state):
        bad = sum(1 for i in range(4) if built[i] != TARGET[subgoal][i])
        return Verdict(bad == 0, float(bad), f"{bad} of 4 positions wrong")

    def compose(self, admitted, state):
        if set(admitted) != {"lo", "hi"}:
            return None
        return Candidate("composed", json.dumps(json.loads(admitted["lo"].artifact)
                                                + json.loads(admitted["hi"].artifact)),
                         knobs={"parts": 2})

    def stages(self):
        return ["screen", "confirm"]

    def analytic_stages(self):
        # D463: which stages are MODELLED is declared, not inferred from their position -- the
        # screen here is arithmetic, the confirm stands in for a measurement.
        return frozenset({"screen"})

    def measure(self, cand, stage, state):
        vals = json.loads(cand.artifact)
        return {"cost": float(sum(vals)), "value": float(len(vals)),
                "stage_confirm": 1.0 if stage == "confirm" else 0.0}

    def frontier_axes(self):
        return (lambda s: s.metrics["value"], lambda s: s.metrics["cost"])


class Scripted:
    """A proposer that answers design prompts with progressively better lists and
    patch prompts with an edit fixing one position -- no model, fully deterministic."""

    def __init__(self):
        self.calls = []

    def propose(self, prompt, *, schema=None, tools=None, budget=None):
        self.calls.append(prompt[:40])
        if prompt.startswith("design lo"):
            return Reply.of(json.dumps([3, 1, 0, 0]))            # first two right, last two wrong
        if prompt.startswith("design hi"):
            return Reply.of(json.dumps([5, 9, 2, 6]))            # exactly right
        if "`lo-v` was refused" in prompt or "was refused" in prompt:
            # a patch: fix the third position; then the fourth on the next round
            if '"0, 0]' in prompt or "0, 0]" in prompt:
                return Reply.of(json.dumps({"edits": [{"find": "0, 0]", "replace": "4, 0]"}],
                                   "why": "fix 3rd"}))
            return Reply.of(json.dumps({"edits": [{"find": "4, 0]", "replace": "4, 1]"}],
                               "why": "fix 4th"}))
        if '"next"' in prompt or "Choose the next part" in prompt:
            return Reply.of(json.dumps({"next": "hi", "method": "direct"}))
        return Reply.of("{}")


def test_the_loop_runs_a_problem_end_to_end_without_a_model_backend(tmp_path):
    prob = Bytes()
    req = LoopRequest(db=str(tmp_path / "bytes.db"), steps=6, repair_attempts=4)
    said = []
    out = run_loop(prob, req, proposer=Scripted(), log=said.append)
    assert set(out.admitted) == {"lo", "hi"}, out.refused
    assert out.decision is not None and out.decision.stage == "confirm"
    assert out.decision.name == "composed"
    assert any("ADMITTED hi" in m for m in said) and any("ADMITTED lo" in m for m in said)
    # the fast check steered: lo needed patches before it passed the gate
    assert any("patched lo" in m for m in said)


def test_a_resumed_campaign_reloads_proven_parts_and_best_attempts(tmp_path):
    db = str(tmp_path / "resume.db")
    prob = Bytes()

    class OnlyHi(Scripted):
        def propose(self, prompt, *, schema=None, tools=None, budget=None):        # lo never gets fixed this run
            if prompt.startswith("design lo"):
                return Reply.of(json.dumps([3, 1, 0, 0]))
            if "was refused" in prompt:
                return Reply.of(json.dumps({"edits": []}))       # unusable -> rewrite -> same
            return Reply.of(super().propose(prompt, schema=schema))

    first = run_loop(prob, LoopRequest(db=db, steps=4, repair_attempts=2),
                     proposer=OnlyHi(), log=lambda _m: None)
    assert "hi" in first.admitted and "lo" not in first.admitted
    assert first.provenance["best"].get("lo") == 2.0        # two positions wrong

    said = []
    second = run_loop(prob, LoopRequest(db=db, steps=4, repair_attempts=4),
                      proposer=Scripted(), log=said.append)
    assert any("reload: hi re-verified" in m for m in said)
    assert any("reload: lo resumes from its best design" in m for m in said)
    assert set(second.admitted) == {"lo", "hi"}


def test_a_resume_regenerates_the_parts_it_is_told_to(tmp_path):
    """D476: `LoopRequest.regenerate` names parts whose record is history -- their admitted
    design, best attempt and prototype are not reloaded, so today's generator drafts them
    again; every other part resumes as before, and the record keeps every row."""
    db = str(tmp_path / "regen.db")
    prob = Bytes()
    first = run_loop(prob, LoopRequest(db=db, steps=4, repair_attempts=2),
                     proposer=Scripted(), log=lambda _m: None)
    assert set(first.admitted) == {"lo", "hi"}
    said = []
    second = run_loop(prob, LoopRequest(db=db, steps=4, repair_attempts=2, regenerate=("lo",)),
                      proposer=Scripted(), log=said.append)
    assert any("reload: lo is being regenerated; its record is history" in m for m in said)
    assert any("reload: hi re-verified" in m for m in said)
    assert not any("reload: lo re-verified" in m for m in said)
    assert set(second.admitted) == {"lo", "hi"}                      # drafted again, admitted again
    said.clear()
    third = run_loop(prob, LoopRequest(db=db, steps=4, repair_attempts=2, regenerate=("*",)),
                     proposer=Scripted(), log=said.append)
    assert not any("re-verified" in m for m in said) and set(third.admitted) == {"lo", "hi"}


def test_the_prototype_stage_resumes_from_the_best_on_record_and_stops_when_nothing_measures(tmp_path):
    """D480: the best refused prototype on record seeds the next pass (its score is the
    gradient's first mark, its failure text the first prompt), and a pass whose attempts
    are refused before their test N times in a row ends there instead of spending the
    whole budget."""
    from flux_loop import LoopRequest, LoopState, Candidate, Verdict, _generate_with_model, _reload
    from flux_records import Records

    class P(Problem):
        name = "p"

        def prototype(self):
            return Prototype(check=self.prototype_check)

        def prototype_check(self, code, subgoal, state):
            if "float" in code:
                return Verdict(False, float("inf"), "float on the data path")
            n = code.count("x")
            return Verdict(n >= 3, 0.0 if n >= 3 else float(3 - n), "" if n >= 3 else f"{3 - n} short")

        def design_prompt(self, subgoal, method, state, human, prior, prior_why):
            return "now the rtl", None

        def parse_design(self, reply, subgoal):
            return Candidate("c", "rtl", subgoal=subgoal), ""

        def build(self, cand, subgoal, state):
            return cand.artifact

    db = str(tmp_path / "r.db")
    seen: list[str] = []

    class Refuses:                                   # every attempt breaks the rule
        def propose(self, prompt, *, schema=None, tools=None, budget=None):
            seen.append(prompt)
            return Reply.of(json.dumps({"prototype": "def design(x):\n    return float(x)"}))

    rec = Records(db, objective={"score": 1})
    st = LoopState(request=LoopRequest(db=db, repair_attempts=1, prototype_attempts=30,
                                       prototype_unmeasured_stop=3),
                   say=lambda _m: None, proposer=Refuses(), feedback=None, workdir=str(tmp_path),
                   records=rec)
    cand, built, reason = _generate_with_model(P(), "a", "", st, None)
    assert cand is None and "3 attempts in a row refused before their test" in reason
    assert len(seen) == 3                            # not 30
    # a measured-but-refused prototype goes on record ...
    st.proto_best.clear()
    class Short:
        def propose(self, prompt, *, schema=None, tools=None, budget=None):
            return Reply.of(json.dumps({"prototype": "def design(x):\n    return x"}))   # 2 x's: 1 short
    st.proposer = Short(); st.request = LoopRequest(db=db, repair_attempts=1, prototype_attempts=2)
    cand, built, reason = _generate_with_model(P(), "a", "", st, None)
    assert cand is None and "best reached score 1" in reason
    # ... the moment it is the best (D491), not only when the pass ends: a run stopped
    # mid-pass resumes from THIS best, not the older seed
    rows = [t for t in rec.store.trials(rec.campaign_id)
            if t.stage == "prototype" and (t.candidate or {}).get("meta", {}).get("kind") == "prototype"]
    assert [t.candidate["score"] for t in rows][-2:] == [1.0, 1.0]     # at once, and at the pass end
    rec.close("paused")
    # ... and the next pass resumes from it: the gradient's first mark is 1, the prompt is a repair
    rec2 = Records(db, objective={"score": 1})
    said: list[str] = []
    prompts: list[str] = []

    class Fixes:
        def propose(self, prompt, *, schema=None, tools=None, budget=None):
            prompts.append(prompt)
            return Reply.of(json.dumps({"prototype": "def design(x):\n    return x + x"}))       # 3 x's
    st2 = LoopState(request=LoopRequest(db=db, repair_attempts=1, prototype_attempts=2),
                    say=said.append, proposer=Fixes(), feedback=None, workdir=str(tmp_path),
                    records=rec2)
    _reload(P(), st2)
    assert st2.proto_best["a"][0] == 1.0 and "return x" in st2.proto_best["a"][1]
    assert any("resumes its prototype from the best on record (1 over)" in m for m in said)
    import flux_profile

    ended: list[tuple[str, dict]] = []

    class Listener:                                  # the re-describe phase's details (D489)
        def phase_start(self, name, why, params):
            return name

        def phase_end(self, token, name, secs, failed, output):
            ended.append((name, dict(output or {})))

    flux_profile.set_listener(Listener())
    try:
        cand, built, reason = _generate_with_model(P(), "a", "", st2, None)
    finally:
        flux_profile.clear_listener()
    assert reason == "" and "a" in st2.prototypes
    assert "was refused" in prompts[0] and "1 short" in prompts[0]      # a repair prompt, not a fresh one
    # Cedric: the re-describe row's details were "capped at 310 chars leading to no real
    # information inside" -- it now carries the seed's text and the WHOLE report
    redesc = [o for n, o in ended if n == "records: re-describe prototype a"]
    assert redesc and redesc[0]["prototype"] == "def design(x):\n    return x"
    assert redesc[0]["verdict"] == "1 over (the record said 1)" and redesc[0]["why"] == "1 short"
    # a seed that TODAY's rules refuse (D491: tanh's 2,680 under the new table cap) keeps
    # its text as the start, loses its score as the mark, and the prompt says what to fix
    class Q(P):
        def prototype_check(self, code, subgoal, state):
            if code.rstrip().endswith("return x"):
                return Verdict(False, float("inf"), "a rule written since: no bare return")
            return super().prototype_check(code, subgoal, state)
    st3 = LoopState(request=LoopRequest(db=db, repair_attempts=1, prototype_attempts=2),
                    say=lambda _m: None, proposer=Fixes(), feedback=None, workdir=str(tmp_path),
                    records=rec2)
    st3.proto_best["a"] = (1.0, "def design(x):\n    return x", "1 short")
    prompts.clear()
    cand, built, reason = _generate_with_model(Q(), "a", "", st3, None)
    assert "is REFUSED under today's rules; make it pass them first" in prompts[0]
    assert "a rule written since: no bare return" in prompts[0]
    assert "was refused:\n\nthe best on record (1 over when it was measured) is REFUSED" in prompts[0]   # today's words, not "1 short" (which the HISTORY block may still cite from the record)


def test_a_resume_does_not_take_a_prototype_for_the_best_design(tmp_path):
    """D471: a passing prototype on record (score 0, Python) is not the part's best
    TARGET design. Reloaded as one, the results tab said "score 0" for a part whose
    best RTL was thousands over, and the RTL turns resumed by patching Python."""
    from flux_loop import LoopRequest, LoopState, Candidate, StageNames, _reload
    from flux_records import Records

    class P(Problem):
        name = "p"

        def subgoals(self):
            return ["exp"]

        def prototype_check(self, code, subgoal, state):
            return Verdict("return 42" in code, 0.0, "")

        def prototype(self):
            return Prototype(check=self.prototype_check)

    db = str(tmp_path / "r.db")
    rec = Records(db, objective={"score": 1})
    rec.trial({"name": "prototype:exp", "artifact": "def design(x):\n    return 42", "knobs": {},
               "meta": {"kind": "prototype"}, "subgoal": "exp", "score": 0.0, "why": ""},
              "exp:prototype", stage=StageNames.PROTOTYPE, strategy="loop",
              metrics={"score": 0.0}, error=None, analytic=True, evaluator="prototype@python")
    rec.trial({"name": "exp_poly", "artifact": "module exp ...", "knobs": {}, "meta": {},
               "subgoal": "exp", "score": 27291.0, "why": "27291 of 65536 over"},
              "exp:exp_poly", stage=StageNames.GATE, strategy="loop",
              metrics={"score": 27291.0}, error="27291 over", analytic=False)
    rec.close("paused")
    said = []
    rec2 = Records(db, objective={"score": 1})
    assert rec2.resumed
    st = LoopState(request=LoopRequest(db=db), say=said.append, proposer=None, feedback=None,
                   workdir=".", records=rec2)
    _reload(P(), st)
    assert st.best["exp"][0] == 27291.0 and st.best["exp"][1].name == "exp_poly"
    assert st.prototypes["exp"].startswith("def design")        # reloaded on its own, as a prototype
    assert any("resumes from its best design so far (score 27291)" in m for m in said)


def test_cooldown_moves_on_when_a_part_will_not_build(tmp_path):
    class Stuck(Problem):
        name = "stuck"

        def subgoals(self):
            return ["a", "b"]

        def design_prompt(self, subgoal, method, state, human, prior, prior_why):
            return f"design {subgoal}", None

        def parse_design(self, reply, subgoal):
            return Candidate(subgoal, reply, subgoal=subgoal), ""

        def build(self, cand, subgoal, state):
            if subgoal == "a":
                raise BuildError("never")
            return cand.artifact

        def judge(self, built, cand, subgoal, state):
            return Verdict(True, 0.0)

    class Any_:
        def propose(self, prompt, *, schema=None, tools=None, budget=None):
            return Reply.of("x")

    said = []
    out = run_loop(Stuck(), LoopRequest(steps=6, repair_attempts=0, cooldown_after=2),
                   proposer=Any_(), log=said.append)
    assert "b" in out.admitted
    assert any("plan: attempt b" in m for m in said)


def test_patching_is_text_level_and_problem_agnostic():
    out, err = apply_patch("alpha beta gamma", [{"find": "beta", "replace": "BETA"}])
    assert err is None and out == "alpha BETA gamma"
    _o, err2 = apply_patch("x x", [{"find": "x", "replace": "y"}])
    assert err2 and "must be unique" in err2
    out3, _e = apply_patch("x x", [{"find": "x", "replace": "y", "nth": 2}])
    assert out3 == "x y"


def test_a_single_goal_problem_needs_no_subgoals(tmp_path):
    class One(Problem):
        name = "one"

        def design_prompt(self, subgoal, method, state, human, prior, prior_why):
            return "design", None

        def parse_design(self, reply, subgoal):
            return Candidate("only", reply), ""

        def build(self, cand, subgoal, state):
            return cand.artifact

        def judge(self, built, cand, subgoal, state):
            return Verdict(built == "42", abs(len(built) - 2), "wrong")

        def measure(self, cand, stage, state):
            return {"cost": 1.0}

    class P:
        def propose(self, prompt, *, schema=None, tools=None, budget=None):
            return Reply.of("42")

    out = run_loop(One(), LoopRequest(steps=2), proposer=P(), log=lambda _m: None)
    assert "*" in out.admitted and out.decision is not None
    assert isinstance(out.decision, Scored) and out.decision.metrics == {"cost": 1.0}


def test_the_timing_tree_shows_the_loops_structure(tmp_path):
    """The tree is the drawing's boxes (D546: no "step N" wrapper -- a step is one trip
    round the loop, and the boxes it visits are the tree): propose, then the generation
    box, which contains generate / build / test / repair, then the evaluation stage, which
    contains compose and the stages."""
    import flux_profile

    flux_profile.reset()
    run_loop(Bytes(), LoopRequest(steps=6, repair_attempts=4), proposer=Scripted(),
             log=lambda _m: None)
    paths = set(flux_profile.tree())
    assert not any(k[0].startswith("DSE: step") for k in paths)
    assert ("propose: plan",) in paths and ("propose: brief hi",) in paths
    assert ("generation: hi",) in paths or ("generation: lo",) in paths
    assert any(len(k) == 2 and k[0].startswith("generation") and k[1].startswith("test: build")
               for k in paths)
    assert any(len(k) == 2 and k[0].startswith("generation") and k[1].startswith("repair")
               for k in paths)
    assert ("evaluation",) in paths
    assert ("evaluation", "analytical: screen") in paths
    assert ("evaluation", "simulation: confirm") in paths
    # every phase the loop names is a node of the graph, so the tree IS the drawing
    from flux_loop import node
    named = {k[-1] for k in paths}
    unknown = {n for n in named if node(n) is None and not n.startswith("llm:")}
    assert not unknown, unknown


def test_the_gate_propose_and_critique_rows_carry_their_details():
    """D491 (Cedric: "make in the task tab the critique, propose and gate show more
    details"): the gate rows say what they checked and the request they checked, decompose
    lists the parts with what is known of each, plan says the menu, the pick and the
    method, the brief its content, a critique its verdict and subject."""
    import flux_profile

    ended: list[tuple[str, dict]] = []

    class Listener:
        def phase_start(self, name, why, params):
            return name

        def phase_end(self, token, name, secs, failed, output):
            ended.append((name, dict(output or {})))

        def publish(self, key, payload):
            pass

    flux_profile.set_listener(Listener())
    try:
        run_loop(Bytes(), LoopRequest(steps=4, repair_attempts=2), proposer=Scripted(),
                 log=lambda _m: None)
    finally:
        flux_profile.clear_listener()
    got = dict(ended)                                 # the last output per phase name
    assert got["gate: tools"]["verdict"].startswith("every tool") and "Bytes" in got["gate: tools"]["problem"]
    assert got["gate: the problem"]["verdict"] == "answerable as posed"
    assert "steps = 4" in got["gate: the problem"]["request"] and "repair_attempts = 2" in got["gate: the problem"]["request"]
    assert got["propose: decompose"]["parts"].startswith("lo: to write\nhi: to write")
    plan = got["propose: plan"]
    assert plan["menu"] in ("lo, hi", "lo", "hi") and plan["picked"] in ("lo", "hi") and "method" in plan
    assert got["propose: brief lo"]["brief"] == "(the part statement is the brief)"


def test_the_loop_publishes_live_standings(monkeypatch):
    """D418l: after every step the loop publishes where it stands, through the same
    decoupled channel as its phases -- no loop knows about the TUI."""
    import flux_profile

    seen = []

    class Listener:
        def publish(self, key, payload):
            seen.append((key, payload))

    flux_profile.set_listener(Listener())
    try:
        run_loop(Bytes(), LoopRequest(steps=6, repair_attempts=4), proposer=Scripted(),
                 log=lambda _m: None)
    finally:
        flux_profile.clear_listener()
    keys = [k for k, _p in seen]
    assert keys and set(keys) == {"standings", "mentor"}
    seen = [(k, p) for k, p in seen if k == "standings"]
    where = [p["at"] for _k, p in seen]       # D466: "at" is where the pass is; a STAGE is
    assert where[:2] == ["resumed", "prepared"] and where[-1] == "evaluated"   # an evaluator
    assert any(w.startswith("after step") for w in where)
    last = seen[-1][1]
    assert last["proven"] == 2 and last["parts_total"] == 2 and last["judged"] >= 2
    assert {p["part"] for p in last["parts"]} == {"lo", "hi"}
    assert all(p["state"] == "proven" for p in last["parts"])


def test_the_loop_publishes_mentor_sections_with_the_records_facts(tmp_path):
    import flux_profile

    class Knowing(Bytes):
        def mentor_sections(self, state):
            return [("knowledge: the target's shape", "four ints per half")]

    seen = {}

    class Listener:
        def publish(self, key, payload):
            seen[key] = payload

    flux_profile.set_listener(Listener())
    try:
        run_loop(Knowing(), LoopRequest(db=str(tmp_path / "m.db"), steps=6,
                                        repair_attempts=4),
                 proposer=Scripted(), log=lambda _m: None)
    finally:
        flux_profile.clear_listener()
    titles = [s["title"] for s in seen["mentor"]["sections"]]
    assert titles[0] == "knowledge: the target's shape"
    assert "record: proven and best" in titles
    assert "record: refusals (gate)" in titles          # lo was refused before it passed


# ---------------------------------------------------------------- D422: cycle time + compute
def test_the_compute_sandbox_runs_numpy_and_refuses_the_rest():
    from flux_loop import run_compute

    out = run_compute([
        {"name": "table", "code": "print([round(float(x), 4) for x in np.exp2(np.arange(4) / 4)])"},
        {"name": "bad import", "code": "import os\nprint(os.getcwd())"},
        {"name": "bad name", "code": "print(open('/etc/hostname').read())"},
        {"name": "dunder", "code": "print((1).__class__)"},
        {"name": "slow", "code": "while True: pass"},
        {"name": "long", "code": "print('x' * 5000)"},
        {"name": "stdlib", "code": "import re, itertools\nprint(re.sub(r'\\d', '#', 'a1b2'), list(itertools.pairwise('abc')))"},
        {"name": "exec", "code": "exec('print(1)')"},
    ], timeout_s=2.0, max_chars=100)
    got = dict(out)
    assert got["table"].startswith("[1.0, 1.1892, 1.4142, 1.6818]")
    assert got["bad import"].startswith("refused: import of 'os'") and "do not try another import" in got["bad import"]
    assert got["bad name"].startswith("refused: 'open'")
    assert got["stdlib"].startswith("a#b# [('a', 'b'), ('b', 'c')]")           # D545: the pure stdlib runs
    assert got["exec"].startswith("refused: 'exec'") and "put the code in the snippet itself" in got["exec"]
    assert got["dunder"].startswith("refused: dunder")
    assert got["slow"].startswith("timed out")
    assert "(+" in got["long"] and len(got["long"]) < 200


def test_focus_window_shows_the_fault_and_the_outline():
    from flux_loop import Problem, focus_window

    art = "\n".join([f"  line {i}" if i % 10 else f"module m{i}" for i in range(1, 121)])
    lines = Problem().locate("dut.sv:73:9: error: expected statement\nsee line 5", art)
    assert lines == [5, 73]
    view = focus_window(art, [73], context=3)
    assert "  73 |   line 73" in view and "  70 | module m70" in view
    assert "  20 | module m20" in view          # elsewhere: in the outline
    assert "  21 |   line 21" not in view      # but not the body far away
    assert view.startswith("(window around line(s) 73; 120 lines in all")
    assert focus_window(art, [], context=3).startswith("   1 |   line 1")   # no location: all


def test_prompts_put_the_static_prefix_first_and_carry_compute_results(tmp_path):
    """D422: the static prefix leads every turn's prompt (cache reuse), the changing
    part follows, and a reply's computations come back next turn."""
    from flux_loop import LoopRequest, LoopState, Problem, Candidate, BuildError, run_loop

    prompts = []

    class Prefixed(Bytes):
        def prompt_prefix(self, subgoal, state):
            return "STATIC CONTRACT AND KNOWLEDGE"

    class Proposer(Scripted):
        """Scripted keys on the prompt's START; with a prefix first, key on the
        turn's own text wherever it sits."""

        def propose(self, prompt, *, schema=None, tools=None, budget=None):
            prompts.append((prompt, schema))
            turn = prompt.split("STATIC CONTRACT AND KNOWLEDGE", 1)[-1]
            for marker in ("design lo", "design hi"):
                if marker in turn:
                    return Reply.of(super().propose(marker + turn.split(marker, 1)[1], schema=schema))
            return Reply.of(super().propose(turn.lstrip(), schema=schema))

    run_loop(Prefixed(), LoopRequest(steps=6, repair_attempts=4), proposer=Proposer(),
             log=lambda _m: None)
    design_turns = [p for p, _s in prompts if "design " in p]
    assert design_turns and all(p.startswith("STATIC CONTRACT AND KNOWLEDGE") for p in design_turns)


def test_repair_turns_carry_a_progress_line_and_revert_on_regression():
    """D423/D504: the model is told whether its last edit helped and by how much;
    when the tolerance for worsening edits is spent the text first BACKTRACKS to the
    best of the line it wandered down, and only a second spent line REVERTS it to
    the best attempt overall."""
    from flux_loop import LoopRequest, LoopState, Candidate, _generate_with_model

    seen = []
    # improve; worse, better, worse, worse -> BACKTRACK to the line's best (4); worse, worse
    # -> REVERT to the best overall (3); then on to 0
    counts = iter([5, 3, 6, 4, 7, 8, 9, 10, 2, 0])

    class P(Problem):
        name = "p"

        def design_prompt(self, subgoal, method, state, human, prior, prior_why):
            return "design", None

        def parse_design(self, reply, subgoal):
            return Candidate("c", "v0", subgoal=subgoal), ""

        def build(self, cand, subgoal, state):
            return cand.artifact

        def fast_check(self, built, cand, subgoal, state):
            n = next(counts)
            return n, f"{n} failing"

    class Proposer:
        def __init__(self):
            self.turn = 0

        def propose(self, prompt, *, schema=None, tools=None, budget=None):
            self.turn += 1
            seen.append(prompt)
            if self.turn == 1:
                return Reply.of("x")
            # edit whatever text the prompt shows, so the edits follow a backtrack
            current = re.findall(r"\bv\d+\b", prompt)[-1]
            return Reply.of(json.dumps({"edits": [{"find": current, "replace": f"v{self.turn - 1}"}]}))

    st = LoopState(request=LoopRequest(repair_attempts=12, regress_after=2),
                   say=lambda _m: None, proposer=Proposer(), feedback=None, workdir=".")
    cand, built, reason = _generate_with_model(P(), None, "", st, None)
    assert reason == ""
    assert cand.artifact == "v9"                    # the 0-failing text, reached after both
    joined = "\n".join(seen)
    assert "PROGRESS: your last edit IMPROVED it, 5 -> 3 failing" in joined
    assert "PROGRESS: your last edit made it WORSE, 3 -> 6 failing" in joined
    assert "IMPROVED it, 6 -> 4 failing. Keep going in this direction. The best so far is 3 failing. Above the best, but closing in" in joined
    assert ("7 -> 8 failing. The best so far is 3 failing. The text has been BACKTRACKED "
            "to the best of this line (4 failing; the best overall is 3 failing") in joined
    assert "4 -> 9 failing" in joined              # the backtrack landed on the 4-failing text
    assert "9 -> 10 failing. The best so far is 3 failing. The text has been REVERTED to the best attempt" in joined
    assert "3 -> 2 failing" in joined              # the revert landed on the 3-failing text


def test_prototype_stage_gates_the_transcription(tmp_path):
    """D424: with a prototype spec, the loop proves the algorithm in Python first;
    only a passing prototype reaches the target stage, and it is carried into every
    RTL prompt; a failing prototype refuses the part without spending RTL turns."""
    from flux_loop import LoopRequest, LoopState, Candidate, Verdict, _generate_with_model

    class P(Problem):
        name = "p"

        def prototype(self):
            return Prototype(check=self.prototype_check)

        def prototype_check(self, code, subgoal, state):
            ok = "return 42" in code
            return Verdict(ok, 0.0 if ok else 1.0, "" if ok else "returns the wrong value")

        def design_prompt(self, subgoal, method, state, human, prior, prior_why):
            return "now the rtl", None

        def parse_design(self, reply, subgoal):
            return Candidate("c", "rtl 42", subgoal=subgoal), ""

        def build(self, cand, subgoal, state):
            return cand.artifact

    prompts = []

    class Proposer:
        def __init__(self, good):
            self.good, self.turn = good, 0

        def propose(self, prompt, *, schema=None, tools=None, budget=None):
            self.turn += 1
            prompts.append(prompt)
            if "PROTOTYPE" in prompt or "prototype" in prompt.lower() and "rtl" not in prompt:
                code = "def design(x):\n    return 42" if self.good else "def design(x):\n    return 7"
                return Reply.of(json.dumps({"prototype": code}))
            return Reply.of("rtl")

    st = LoopState(request=LoopRequest(repair_attempts=2, prototype_attempts=3),
                   say=lambda _m: None, proposer=Proposer(good=True), feedback=None, workdir=".")
    cand, built, reason = _generate_with_model(P(), "a", "", st, None)
    assert reason == "" and cand is not None
    assert "a" in st.prototypes and "return 42" in st.prototypes["a"]
    assert any("VERIFIED PROTOTYPE" in p and "return 42" in p and "now the rtl" in p for p in prompts)

    prompts.clear()
    st2 = LoopState(request=LoopRequest(repair_attempts=2, prototype_attempts=3),
                    say=lambda _m: None, proposer=Proposer(good=False), feedback=None, workdir=".")
    cand, built, reason = _generate_with_model(P(), "a", "", st2, None)
    assert cand is None and reason.startswith("prototype did not reach 0 over")
    assert not any("now the rtl" in p for p in prompts)         # no RTL turn was spent
    assert any("PROGRESS" in p for p in prompts)                # the edits got the gradient


# ---- D427: the loop's own shape -- modules by node group, Gradient, roles, Candidate.key
def test_a_rewrite_that_drops_its_tables_gets_them_back_and_a_rewrite_near_a_good_seed_is_refused(tmp_path):
    """D501: two of the day's biggest wastes. A "new prototype" that is design() alone, its
    tables left in the previous text (44 "name X is not defined" attempts) -- the previous
    text's definitions are put back and the attempt runs, with a note. A rewrite where the
    text in hand is nearly right (the regressions) -- refused before it runs when the
    problem says so, unless the reply says REWRITE in its why."""
    from flux_loop import LoopRequest, LoopState, Verdict, _generate_with_model
    from flux_loop.prototype import _restore_dropped

    old = "TAB = [1, 2, 3]\nK = 7\n\ndef helper(v):\n    return v\n\ndef design(x):\n    return TAB[x] + K\n"
    new = "def design(x):\n    return TAB[x] * 2\n"
    fixed = _restore_dropped(old, new, ["TAB"])
    assert fixed.startswith("TAB = [1, 2, 3]\ndef design(x):") and "K = 7" not in fixed

    class P(Problem):
        name = "p"
        seen: list[str] = []

        def prototype(self):
            return Prototype(check=self.prototype_check, domain_size=40)

        def prototype_check(self, code, subgoal, state):
            P.seen.append(code)
            n = code.count("x")
            return Verdict(n >= 3, 0.0 if n >= 3 else float(3 - n), "" if n >= 3 else f"{3 - n} short")

        def design_prompt(self, subgoal, method, state, human, prior, prior_why):
            return "rtl", None

        def parse_design(self, reply, subgoal):
            return Candidate("c", "rtl", subgoal=subgoal), ""

        def build(self, cand, subgoal, state):
            return cand.artifact

    replies = iter([json.dumps({"prototype": "T = 5\ndef design(x):\n    return x + T"}),      # 2 x's: 1 short (best 1)
                    json.dumps({"prototype": "def design(x):\n    return x + x + T", "why": "better"}),   # a rewrite: refused
                    json.dumps({"prototype": "def design(x):\n    return x + x + T", "why": "REWRITE: approach wrong"}),  # allowed, T restored
                    json.dumps({"source": "rtl"})])                                       # then the RTL turn
    prompts: list[str] = []

    class Model:
        def propose(self, prompt, *, schema=None, tools=None, budget=None):
            prompts.append(prompt)
            return Reply.of(next(replies))

    st = LoopState(request=LoopRequest(repair_attempts=1, prototype_attempts=3),
                   say=lambda _m: None, proposer=Model(), feedback=None, workdir=str(tmp_path))
    cand, built, reason = _generate_with_model(P(), "a", "", st, None)
    assert "your NEW prototype was not run: the text in hand fails only 1 of 40 inputs" in prompts[2]
    assert reason == "" and "a" in st.prototypes and st.prototypes["a"].startswith("T = 5\ndef design")   # T put back
    assert len(P.seen) == 2                                                     # the refused rewrite never ran


def test_gradient_carries_the_trend_and_reverts_after_a_streak():
    from flux_loop import Gradient

    g = Gradient(regress_after=2)
    t, best = g.observe(5, "a", key="a")
    assert best and t == "PROGRESS: first measured attempt -- 5 failing."
    t, best = g.observe(3, "b", key="b")
    assert best and "IMPROVED it, 5 -> 3 failing. Keep going" in t
    t, best = g.observe(3, "c", key="c")
    assert not best and "changed NOTHING measurable (3 failing)" in t and "best so far is 3" in t
    assert not g.revert_due("c")                      # a tie is not a regression
    t, _ = g.observe(6, "d", key="d")
    assert "made it WORSE, 3 -> 6 failing" in t and g.worse == 1 and not g.revert_due("d")
    g.observe(7, "e", key="e")
    assert g.worse == 2 and g.revert_due("e") and not g.revert_due("b")
    payload, note = g.revert()
    assert payload == "b" and "REVERTED to the best attempt" in note and "ITS failures" in note
    assert g.worse == 0 and g.prev == 3 and g.best_score == 3
    assert g.history == [5, 3, 3, 6, 7]
    # D484: the revert hands back the BEST attempt's failure text, not the worse edit's
    # (D535: a refused, unmeasured edit spends no tolerance; a measured worse one does)
    g = Gradient(regress_after=1)
    g.observe(3, "b", key="b", failure="x=-Inf got -Inf wanted NaN")
    g.observe(float("inf"), "c", key="c", failure="RSQRT_TABLE is not defined")
    assert not g.revert_due("c") and g.best_failure == "x=-Inf got -Inf wanted NaN"
    g.observe(5, "d", key="d", failure="x=+Inf got NaN wanted +Inf")
    assert g.revert_due("d") and g.best_failure == "x=-Inf got -Inf wanted NaN"


def test_gradient_lets_an_improving_line_above_the_best_continue():
    """D491: tanh's pass resumed from a 9,222 seed; every new design the model wrote
    (28,980 -> 12,596 -> 9,848) was reverted to the seed after its second edit, because
    "worse" was counted against the BEST, not against the last attempt. A line that
    improves is walked to its end; a line that regresses twice is reverted."""
    from flux_loop import Gradient

    g = Gradient(regress_after=2, unit="", fmt=lambda x: f"{x:g}", noun="prototype")
    g.observe(9222.0, "seed", key="seed")
    t, _ = g.observe(28980.0, "a", key="a")
    assert g.worse == 1 and "WORSE" in t
    t, _ = g.observe(12596.0, "b", key="b")
    assert g.worse == 1 and "IMPROVED it, 28980 -> 12596" in t and "closing in" in t
    t, _ = g.observe(9848.0, "c", key="c")
    assert g.worse == 1 and not g.revert_due("c")            # still above 9222, still improving
    t, _ = g.observe(9100.0, "d", key="d")
    assert g.worse == 0 and g.best_score == 9100.0             # the line beat the seed
    t, _ = g.observe(9500.0, "e", key="e")
    t, _ = g.observe(9500.0, "f", key="f")                     # a tie above the best counts
    assert g.worse == 2
    # D504: the two improving edits EARNED tolerance (2 -> 4), so two regressions do not yet
    # backtrack; two more do, and the landing is the best (this line's best IS the best)
    assert not g.revert_due("f")
    g.observe(9600.0, "g", key="g"); g.observe(9700.0, "h", key="h")
    assert g.revert_due("h") and g.revert()[0] == "d"


def test_gradient_backtracks_to_the_lines_best_before_the_global_best():
    """D504 (Cedric: "allow more worsening edits if we have improvements and do some minor
    backtracking instead of reverting to the previous best ... getting out of an optimal
    design sink hole"): a line that found something above the best backtracks to ITS best
    first; only when that line is spent too does it fall back to the global best."""
    from flux_loop import Gradient

    g = Gradient(regress_after=2, unit="", fmt=lambda x: f"{x:g}", noun="prototype")
    g.observe(9222.0, "seed", key="seed", failure="seed's report")
    g.observe(30000.0, "a", key="a")                                  # tol 1
    g.observe(12000.0, "b", key="b", failure="b's report")           # improving: tol 2
    g.observe(10000.0, "c", key="c", failure="c's report")           # improving: tol 3 (the line's best)
    g.observe(15000.0, "d", key="d"); g.observe(16000.0, "e", key="e"); g.observe(17000.0, "f", key="f")
    assert g.revert_due("f")
    payload, note = g.revert()
    assert payload == "c" and "BACKTRACKED to the best of this line (10000" in note and "best overall is 9222" in note
    assert g.revert_failure() == "c's report"
    g.observe(11000.0, "g", key="g"); g.observe(12500.0, "h", key="h")    # spent again
    assert g.revert_due("h")
    payload, note = g.revert()
    assert payload == "seed" and "REVERTED to the best attempt" in note and g.revert_failure() == "seed's report"


def test_gradient_formats_scores_for_the_prototype_stage():
    from flux_loop import Gradient

    g = Gradient(regress_after=1, unit="", fmt=lambda x: f"{x:g}", noun="prototype")
    t, _ = g.observe(27291.0, "p")
    assert t == "PROGRESS: first measured prototype -- 27291."
    t, _ = g.observe(12.5, "q")
    assert "IMPROVED it, 27291 -> 12.5." in t


def test_gradient_treats_a_refused_attempt_as_no_measurement():
    """D470: `inf` is what a prototype check returns when the attempt was refused before
    the test ran (a rule violation, a crash). The live run printed the first one as
    "score inf (best so far)" and would have REVERTED to it. A refusal is not the best,
    not the revert target, not the number the next edit is compared against -- but it is
    never a regression (D535)."""
    from flux_loop import Gradient

    g = Gradient(regress_after=2, unit="", fmt=lambda x: f"{x:g}", noun="prototype")
    t, best = g.observe(float("inf"), "refused", key="refused")
    assert not best and g.best is None and g.prev is None
    assert t == "PROGRESS: your last prototype could not be measured (refused before the test ran)."
    t, best = g.observe(30182.0, "a", key="a")
    assert best and t == "PROGRESS: first measured prototype -- 30182."
    t, best = g.observe(float("inf"), "b", key="b")
    assert not best and "could not be measured" in t and "best so far is 30182" in t
    # D535 (review §1.3.10): an unmeasured attempt is not a regression -- the tolerance is for
    # edits measured worse; a run of them is the prototype stage's own stop (D480/D484)
    assert g.best == (30182.0, "a") and g.worse == 0 and g.prev == 30182.0
    t, best = g.observe(29554.0, "c", key="c")
    assert best and "IMPROVED it, 30182 -> 29554" in t       # compared with the last MEASURED one
    g.observe(float("inf"), "d", key="d")
    g.observe(float("inf"), "e", key="e")
    assert g.worse == 0 and not g.revert_due("e")
    g.observe(31000.0, "f", key="f")
    g.observe(31500.0, "g", key="g")
    assert g.worse == 2 and g.revert_due("g")                # two MEASURED worse: the tolerance is spent
    assert g.revert()[0] == "c"


def test_prototype_stage_records_an_all_refused_pass_without_a_best(tmp_path):
    """Every attempt refused before its test ran: no best, no "score inf" line, the
    refusal is the reason, and the record keeps the last attempt with the rule it broke."""
    from flux_loop import LoopRequest, LoopState, Candidate, Verdict, _generate_with_model
    from flux_records import Records

    class P(Problem):
        name = "p"

        def prototype(self):
            return Prototype(check=self.prototype_check)

        def prototype_check(self, code, subgoal, state):
            return Verdict(False, float("inf"), "line 2: np.exp on the data path")

        def design_prompt(self, subgoal, method, state, human, prior, prior_why):
            return "now the rtl", None

        def parse_design(self, reply, subgoal):
            return Candidate("c", "rtl", subgoal=subgoal), ""

        def build(self, cand, subgoal, state):
            return cand.artifact

    class Proposer:
        def propose(self, prompt, *, schema=None, tools=None, budget=None):
            return Reply.of(json.dumps({"prototype": "def design(x):\n    return np.exp(x)"}))

    said = []
    rec = Records(str(tmp_path / "r.db"), objective={"score": 1})
    st = LoopState(request=LoopRequest(repair_attempts=1, prototype_attempts=3),
                   say=said.append, proposer=Proposer(), feedback=None, workdir=".", records=rec)
    cand, built, reason = _generate_with_model(P(), "a", "", st, None)
    assert cand is None
    assert "none of 3 attempt(s) was measurable" in reason and "np.exp" in reason
    assert not any("best so far" in m for m in said)
    assert any("not measurable -- line 2: np.exp" in m for m in said)
    refused = rec.refusals(stage="prototype")
    assert len(refused) == 1 and "np.exp" in refused[0][1]


def test_candidate_key_covers_points_as_well_as_text():
    """A parameter-space problem has no text to build: its candidates are knobs, and
    they must cache and record by those knobs, not by an empty artifact."""
    from flux_loop import Candidate

    a = Candidate("a", knobs={"banks": 32, "swizzle": "xor"})
    b = Candidate("b", knobs={"swizzle": "xor", "banks": 32})
    c = Candidate("c", knobs={"banks": 16, "swizzle": "xor"})
    assert a.artifact == "" and a.key() == b.key() != c.key()
    assert a.with_knobs(banks=16).key() == c.key()
    t = Candidate("t", "module m; endmodule")
    import hashlib
    assert t.key() == hashlib.sha256(b"module m; endmodule").hexdigest()[:16]
    assert Candidate.from_record(a.to_record()) == a


def test_problem_is_the_four_roles_combined():
    from flux_loop import (EvaluatorRole, GeneratorRole, MentorRole, OrchestratorRole,
                           Problem)

    assert Problem.__mro__[1:5] == (MentorRole, OrchestratorRole, GeneratorRole, EvaluatorRole)
    assert {"objective", "prepare", "from_record"} <= set(vars(MentorRole))
    assert {"subgoals", "plan_next", "decide", "frontier_axes"} <= set(vars(OrchestratorRole))
    assert {"generate", "design_prompt", "patch_prompt", "prototype"} <= set(vars(GeneratorRole))
    assert {"build", "fast_check", "judge", "stages", "measure"} <= set(vars(EvaluatorRole))

    class Shared(EvaluatorRole):          # an evaluator role reused across problems
        def build(self, cand, subgoal, state):
            return cand.artifact.upper()

    class Mine(Shared, Problem):
        name = "mine"

    assert Mine().build(__import__("flux_loop").Candidate("x", "ab"), None, None) == "AB"
    assert Mine().stages() == ["screen"] and Mine().subgoals() == []


def test_graph_nodes_are_the_one_role_table():
    import flux_loop  # noqa: F401  (importing the graph registers its roles)
    from flux_profile import role_of

    assert role_of("DSE: step 3") == "orchestrator"
    assert role_of("critique: the plan") == "evaluator"
    assert role_of("template-fill: compose") == "generator"
    assert role_of("records: re-verify recip") == "mentor"
    assert role_of("input") == "io"
    assert role_of("invent: fabric") == "generator"        # an older loop's word still maps


def test_the_loop_package_is_split_by_node_group():
    import importlib

    for mod in ("types", "problem", "model", "observe", "patch", "compute", "gradient",
                "prototype", "generation", "records", "loop", "graph"):
        assert importlib.import_module(f"flux_loop.{mod}")
    import flux_loop
    assert set(flux_loop.__all__) >= {"run_loop", "Problem", "Candidate", "Gradient",
                                      "apply_patch", "run_compute", "prototype_schema"}


def test_the_loops_stage_names_are_one_vocabulary(tmp_path):
    """What the loop writes to the record and reads back comes from `StageNames` (D438)."""
    from flux_loop import StageNames
    from flux_records import Records

    assert (StageNames.GATE, StageNames.ADMIT, StageNames.PROTOTYPE) == ("gate", "admit", "prototype")
    r = Records(str(tmp_path / "r.db"), objective={"s": 1})
    r.trial({"name": "x", "artifact": "a", "score": 1.0}, "g:x", stage=StageNames.GATE, strategy="loop",
            metrics={"score": 1.0}, error="1 failing", analytic=False)
    assert r.refusals(stage=StageNames.GATE) and not r.refusals(stage=StageNames.ADMIT)


def test_a_new_prototype_that_drops_its_own_tables_is_told_so():
    """D483: the live model sent design() alone as a new prototype, leaving TABLE in the text
    it replaced; "TABLE is not defined" should say why."""
    from flux_loop.prototype import _dropped_definitions

    old = "T = 6\nSPACE = {}\nTABLE = rom(recip, 1, 2, 4, 6)\n\ndef design(x):\n    return TABLE[x]\n"
    new = "def design(x):\n    return TABLE[x >> T]\n"
    assert _dropped_definitions(old, new) == ["T", "TABLE"]
    assert _dropped_definitions(old, old) == [] and _dropped_definitions(old, "def design(x):\n    return x\n") == []
    assert _dropped_definitions("not python (", new) == []


def test_edits_pasted_with_the_listings_line_numbers_still_apply():
    """D484: a live edit quoted `find` as "   2 | M = 12" -- the reading-only listing's prefix."""
    from flux_loop.patch import apply_patch

    src = "M = 12\nFB = 10\n\ndef design(x):\n    return x\n"
    out, err = apply_patch(src, [{"find": "   2 | M = 12\n   3 | FB = 10", "replace": "   2 | M = 12\n   3 | FB = 11"}])
    assert err is None and out == "M = 12\nFB = 11\n\ndef design(x):\n    return x\n"
    out, err = apply_patch(src, [{"find": "FB = 10", "replace": "FB = 9"}])      # unchanged path
    assert err is None and "FB = 9" in out


# ---- D505: tools inside a turn
def test_a_turn_with_tools_checks_inside_it_and_a_passing_check_is_the_answer(tmp_path):
    """D505 (Cedric: "agentic capabilities ... tool calling"): with `tools` on, the prototype
    turn gets `compute`, `check`, `history` and `knowledge`; a prototype the model checked
    inside the turn and that PASSED is the attempt whatever it wrote afterwards; the stage
    reuses the verdict instead of running the check twice; the RTL turns get the tools
    without `check`."""
    from flux_loop import LoopRequest, LoopState, Candidate, Verdict, _generate_with_model

    checks: list[str] = []

    class P(Problem):
        name = "p"

        def prototype(self):
            return Prototype(check=self.prototype_check)

        def prototype_check(self, code, subgoal, state):
            checks.append(code)
            ok = "return 42" in code
            return Verdict(ok, 0.0 if ok else 1.0, "" if ok else "returns the wrong value")


        def design_prompt(self, subgoal, method, state, human, prior, prior_why):
            return "now the rtl", None

        def parse_design(self, reply, subgoal):
            return Candidate("c", "rtl 42", subgoal=subgoal), ""

        def build(self, cand, subgoal, state):
            return cand.artifact

    offered: list[list[str]] = []
    prompts: list[str] = []

    class Model:                                   # a proposer that CALLS its tools
        def propose(self, prompt, *, schema=None, tools=None, budget=None):
            prompts.append(prompt)
            names = [t.name for t in (tools or [])]
            offered.append(names)
            by = {t.name: t for t in (tools or [])}
            if "check" in by:
                assert by["history"].run({}) == "(nothing on record for this part yet)"   # the loop's history, from the record
                assert by["compute"].run({"code": "print(6 * 7)"}).strip() == "42"
                assert by["check"].run({"prototype": "def design(x):\n    return 7"}).startswith("refused, score 1")
                assert by["check"].run({"prototype": "def design(x):\n    return 42"}).startswith("PASSES")
                return Reply.of(json.dumps({"prototype": "def design(x):\n    return 7", "why": "oops, the wrong one"}))
            return Reply.of("rtl")

    st = LoopState(request=LoopRequest(repair_attempts=2, prototype_attempts=3, tools=True),
                   say=lambda _m: None, proposer=Model(), feedback=None, workdir=".")
    cand, built, reason = _generate_with_model(P(), "a", "", st, None)
    assert reason == "" and cand is not None
    assert "return 42" in st.prototypes["a"]                 # the passing check, not the reply
    # D506: a turn that checked something BETTER than it submitted -- the best of the turn is
    # the attempt (live: a turn checked its way to 1,224 over and sent a 3,270)
    said: list[str] = []

    class Worse(P):
        def prototype_check(self, code, subgoal, state):
            n = int(code.rsplit(" ", 1)[-1])
            return Verdict(n == 0, float(n), "" if n == 0 else f"{n} over")

    class Sends9:
        def propose(self, prompt, *, schema=None, tools=None, budget=None):
            by = {t.name: t for t in (tools or [])}
            if "check" in by:
                by["check"].run({"prototype": "return 7"}); by["check"].run({"prototype": "return 3"})
                return Reply.of(json.dumps({"prototype": "return 9"}))
            return Reply.of("rtl")

    st3 = LoopState(request=LoopRequest(repair_attempts=1, prototype_attempts=1, tools=True),
                    say=said.append, proposer=Sends9(), feedback=None, workdir=".")
    _generate_with_model(Worse(), "a", "", st3, None)
    assert st3.proto_best["a"][0] == 3.0 and st3.proto_best["a"][1] == "return 3"
    assert any("the reply scores 9 but the turn checked 3" in m for m in said)
    # ... and a text the turn measured is never refused as an unmeasured rewrite (D501's rule),
    # even next to a good seed (live: a 373 refused against a 1,644 seed)
    class Picky(Worse):
        """The generic rule refuses an UNMEASURED rewrite next to the 3-of-1000 seed."""

    class Checks2:
        def propose(self, prompt, *, schema=None, tools=None, budget=None):
            by = {t.name: t for t in (tools or [])}
            if "check" in by:
                by["check"].run({"prototype": "return 2"})
                return Reply.of(json.dumps({"prototype": "return 2", "why": "a new text, measured"}))
            return Reply.of("rtl")

    st4 = LoopState(request=LoopRequest(repair_attempts=1, prototype_attempts=2, tools=True),
                    say=lambda _m: None, proposer=Checks2(), feedback=None, workdir=".")
    st4.proto_best["a"] = (5.0, "return 5", "5 over")
    _generate_with_model(Picky(), "a", "", st4, None)
    assert st4.proto_best["a"][0] == 2.0                      # taken, not refused
    assert checks.count("def design(x):\n    return 42") == 1  # measured once, inside the turn
    assert offered[0] == ["compute", "check", "history", "knowledge"]
    assert "YOU HAVE TOOLS in this turn" in prompts[0] and "CHECK BEFORE YOU SUBMIT" in prompts[0]
    assert offered[-1] == ["compute", "history", "knowledge"]  # the RTL turn: no `check`
    assert "YOU HAVE TOOLS in this turn" in prompts[-1] and "CHECK BEFORE" not in prompts[-1]


def test_tools_stay_off_unless_asked_and_a_plain_proposer_is_unaffected():
    """Off by default: no tool reaches the proposer, the prompt carries the compute help as
    before (D422); on, a proposer without `tools` in its signature still gets a plain turn."""
    from flux_loop import LoopRequest, LoopState, Verdict, _generate_with_model
    from flux_loop.tools import Checked

    class P(Problem):
        name = "p"

        def prototype(self):
            return Prototype(check=self.prototype_check)

        def prototype_check(self, code, subgoal, state):
            return Verdict(True, 0.0, "")

        def design_prompt(self, subgoal, method, state, human, prior, prior_why):
            return "now the rtl", None

        def parse_design(self, reply, subgoal):
            from flux_loop import Candidate
            return Candidate("c", "rtl", subgoal=subgoal), ""

        def build(self, cand, subgoal, state):
            return cand.artifact

    seen: list[tuple[str, object]] = []

    class Plain:
        def propose(self, prompt, *, schema=None, tools=None, budget=None):
            seen.append((prompt, schema))
            return Reply.of(json.dumps({"prototype": "def design(x):\n    return 1"}))

    st = LoopState(request=LoopRequest(prototype_attempts=1), say=lambda _m: None, proposer=Plain(),
                   feedback=None, workdir=".")
    _generate_with_model(P(), "a", "", st, None)
    assert "YOU HAVE TOOLS" in seen[0][0]                     # offered by default (D507); the plain proposer just cannot
    st2 = LoopState(request=LoopRequest(prototype_attempts=1, tools=False), say=lambda _m: None, proposer=Plain(),
                    feedback=None, workdir=".")
    _generate_with_model(P(), "a", "", st2, None)
    assert "YOU HAVE TOOLS" not in seen[-1][0]
    # the turn's own arbitration: the best checked code, a finite score over a refusal
    c = Checked()
    c.note("a", Verdict(False, float("inf"), "refused")); c.note("b", Verdict(False, 3.0, "")); c.note("c", Verdict(False, 9.0, ""))
    assert c.best[0] == "b" and c.verdict_for("c").score == 9.0 and c.verdict_for("zz") is None


# ---- D506: the budget grows while the pass improves
def test_a_prototype_pass_that_keeps_improving_is_not_cut_off_at_its_budget():
    """D506 (Cedric: "the cap of 30 iterations/attempts might cut off a good candidate. Maybe
    it should be dynamic and increase based on improvement rate"): every new best grants
    `prototype_patience` attempts past the budget, up to `prototype_attempts_max`; a pass
    that stops improving ends where it would have."""
    from flux_loop import LoopRequest, LoopState, Verdict
    from flux_loop.prototype import _prototype_stage

    class P(Problem):
        name = "p"

        def prototype(self):
            return Prototype(check=self.prototype_check)

        def prototype_check(self, code, subgoal, state):
            n = int(code.split("=")[-1])
            return Verdict(n == 0, float(n), "" if n == 0 else f"{n} over")

    class Model:                                   # improves by one every attempt: 12, 11, ... 0
        def __init__(self):
            self.n = 13

        def propose(self, prompt, *, schema=None, tools=None, budget=None):
            self.n -= 1
            return Reply.of(json.dumps({"prototype": f"x = {self.n}"}))

    said: list[str] = []
    st = LoopState(request=LoopRequest(prototype_attempts=5, prototype_patience=3, prototype_attempts_max=20),
                   say=said.append, proposer=Model(), feedback=None, workdir=".")
    code, why = _prototype_stage(P(), "a", st, None)
    assert code == "x = 0" and why == ""                      # 13 attempts, past the budget of 5
    assert any("still improving at attempt 5; the pass continues to 8 attempts (at most 20)" in m for m in said)
    # the cap holds: a pass that improves forever still ends at the maximum
    class Slow:
        def __init__(self):
            self.n = 100

        def propose(self, prompt, *, schema=None, tools=None, budget=None):
            self.n -= 1
            return Reply.of(json.dumps({"prototype": f"x = {self.n}"}))

    st2 = LoopState(request=LoopRequest(prototype_attempts=5, prototype_patience=3, prototype_attempts_max=9),
                    say=lambda _m: None, proposer=Slow(), feedback=None, workdir=".")
    code, why = _prototype_stage(P(), "a", st2, None)
    assert code is None and "best reached score 91" in why   # 9 attempts: 99 .. 91
    # no patience: the fixed cap of before
    st3 = LoopState(request=LoopRequest(prototype_attempts=5, prototype_patience=0),
                    say=lambda _m: None, proposer=Slow(), feedback=None, workdir=".")
    code, why = _prototype_stage(P(), "a", st3, None)
    assert code is None and "best reached score 95" in why   # 5 attempts: 99 .. 95


def test_a_prototype_sent_as_a_text_check_call_or_a_fenced_block_is_read():
    """D506: the answering round often "asks to check" the text instead of sending it -- that
    is the model submitting it; a fenced python block with `def design(` counts too."""
    from flux_loop.prototype import _parse_prototype

    assert _parse_prototype('{"prototype": "def design(x):\\n    return 1"}') == "def design(x):\n    return 1"
    text = "Checking:\n<tool_call>\n<function=check>\n<parameter=prototype>\nT = 6\ndef design(x):\n    return x\n</parameter>\n</function>\n</tool_call>"
    assert _parse_prototype(text) == "T = 6\ndef design(x):\n    return x"
    fenced = "Here it is:\n```python\nT = 6\ndef design(x):\n    return x\n```\nDone."
    assert _parse_prototype(fenced) == "T = 6\ndef design(x):\n    return x\n"
    assert _parse_prototype("nothing here") is None

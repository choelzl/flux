"""One step loop over one work vocabulary (D457).

The orchestrator emits work: a part to write, a sub-task to run as its own loop, or a batch to gate
and measure. Both kinds happen in one pass; `next_work` may come from code or a model; an unusable
answer does not fail the run; a search that ends hands its steps to the parts; search-only and
parts-only problems behave as before.
"""

from __future__ import annotations

from flux_loop import Candidate, LoopRequest, Problem, SubLoop, Template, Verdict, run_loop

#: D738: these tests measure what one pass does with a whole search; one design a pass is the default
WHOLE = 10_000


class Both(Problem):
    """A problem with a part to write AND a space to search: the mixed case."""

    name = "both"

    def __init__(self, *, batches: int = 2) -> None:
        self.batches = batches
        self.asked: list[list[str]] = []
        self.seen_batches = 0

    def objective(self, request):
        return {"study": "both"}

    def subgoals(self):
        return ["piece"]

    def generator(self, subgoal, state):
        return Template(lambda attempt: Candidate(name="written", artifact="written"))

    def search(self, state):
        for i in range(self.batches):
            self.seen_batches += 1
            yield [Candidate(name=f"found{i}", artifact="f" * (i + 2))]

    def build(self, cand, subgoal, state):
        return cand.artifact

    def judge(self, built, cand, subgoal, state):
        return Verdict(True, 0.0)

    def compose(self, admitted, state):
        return admitted.get("piece")

    def stages(self):
        return ["stage"]

    def measure(self, cand, stage, state):
        return {"size": float(len(cand.artifact))}


def _request(tmp_path, **kw):
    kw.setdefault("steps", 4)
    return LoopRequest(batch=WHOLE, db=str(tmp_path / "w.db"), prototype=False,
                       critique_rounds=0, **kw)


def test_a_part_and_a_search_are_worked_in_the_same_pass(tmp_path):
    problem = Both()
    out = run_loop(problem, _request(tmp_path), proposer=None, log=lambda _m: None)
    assert list(out.admitted) == ["piece"], "the part was written and admitted"
    assert problem.seen_batches == 2, "and the search ran too, in the same pass"
    measured = {s.candidate.name for s in out.scored}
    assert {"found0", "found1", "written"} <= measured, measured


def test_the_orchestrator_says_what_the_next_step_is_for(tmp_path):
    """The decision is the problem's: code, a rule, or a model answering "what next?"."""
    problem = Both(batches=3)
    order: list[str] = []

    class ModelDirected(Both):
        def next_work(self, state, waiting):
            problem.asked.append(list(waiting))
            # a model would answer here; this one searches first and writes the part last
            kind = "batch" if state.step < 2 else "part"
            order.append(kind)
            return kind

    directed = ModelDirected(batches=3)
    directed.asked = problem.asked
    out = run_loop(directed, _request(tmp_path, steps=3), proposer=None, log=lambda _m: None)
    assert problem.asked and all(w == ["piece"] for w in problem.asked), problem.asked
    assert order[:2] == ["batch", "batch"], order
    assert directed.seen_batches == 2, "two steps went to the search, as it asked"
    assert list(out.admitted) == ["piece"], "and the part was written with the third"


def test_an_answer_the_loop_cannot_use_does_not_fail_the_run(tmp_path):
    said: list[str] = []

    class Confused(Both):
        def next_work(self, state, waiting):
            return "whatever the model felt like saying"

    out = run_loop(Confused(), _request(tmp_path, steps=2), proposer=None, log=said.append)
    assert any("is not work this step can do" in m for m in said), said
    assert list(out.admitted) == ["piece"], "the waiting part went first instead"


def test_a_what_next_that_raises_is_not_a_failed_run(tmp_path):
    said: list[str] = []

    class Broken(Both):
        def next_work(self, state, waiting):
            raise RuntimeError("the rule is broken")

    out = run_loop(Broken(), _request(tmp_path, steps=2), proposer=None, log=said.append)
    assert any("what-next did not answer" in m for m in said), said
    assert out.decision is not None


def test_a_search_that_ends_hands_its_remaining_steps_to_the_parts(tmp_path):
    """An exhausted generator does not eat a step: one batch, then the part, in two steps."""
    problem = Both(batches=1)

    class SearchFirst(Both):
        def next_work(self, state, waiting):
            return "batch"

    first = SearchFirst(batches=1)
    out = run_loop(first, _request(tmp_path, steps=2), proposer=None, log=lambda _m: None)
    assert first.seen_batches == 1
    assert list(out.admitted) == ["piece"], "the step the dead search would have eaten"
    assert problem.seen_batches == 0, "the fixture problem was not the one that ran"


def test_a_sub_loop_and_a_batch_in_the_same_pass(tmp_path):
    """Sub-loops (D455) and search (D446) in one run: the child decides its piece, the parent searches too."""

    class Leaf(Problem):
        name = "leaf"

        def objective(self, request):
            return {"study": "leaf"}

        def search(self, state):
            yield [Candidate(name="leaf-design", artifact="<leaf>")]

        def build(self, cand, subgoal, state):
            return cand.artifact

        def judge(self, built, cand, subgoal, state):
            return Verdict(True, 0.0)

        def stages(self):
            return ["leaf-stage"]

        def measure(self, cand, stage, state):
            return {"size": 6.0}

    class Parent(Both):
        def subgoals(self):
            return []

        def subproblems(self, state):
            return [SubLoop(name="child", problem=Leaf(), statement="its own study")]

        def compose(self, admitted, state):
            return admitted.get("child")

    out = run_loop(Parent(batches=1), _request(tmp_path, steps=3), proposer=None,
                   log=lambda _m: None)
    assert list(out.admitted) == ["child"], "the child's decision is the parent's part"
    assert {s.candidate.name for s in out.scored} >= {"found0", "leaf-design"}


def test_a_search_only_problem_still_says_when_it_ran_out_of_steps(tmp_path):
    class SearchOnly(Both):
        def subgoals(self):
            return []

    out = run_loop(SearchOnly(batches=9), _request(tmp_path, steps=2), proposer=None,
                   log=lambda _m: None)
    assert any("used every one of its 2 step(s)" in l for l in out.lessons), out.lessons

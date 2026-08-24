"""Knowledge's AI half: what was EXTRACTED from this project's own data (docs/decisions.md D462).

Cedric's fourth row: "Knowledge: Human fed feedback and insights <AND/OR> LLM extracts/learns
from data (traces, simulations, past-experiments, library of documents)". The extraction half
existed as a package (`flux_knowledge_mining`, D243/D297/D329) and was reachable only by writing
code that called it; the human half was a declared, selectable source (D449). This makes them
the same kind of thing.

What these pin: `mined` is a knowledge source like the others, it needs no configuration (the
run's own campaign store is the default), it is static so mining does not re-run every prompt,
what it renders carries the NOT-established boundary with every statement, a store with nothing
in it contributes nothing rather than failing, and both halves reach a prompt together.
"""

from __future__ import annotations

import pytest
from flux_knowledge import Corpus, Mentor, Mined
from flux_loop import (Candidate, LoopRequest, Problem, PromptProblem, Roles, TaskSpec, Verdict,
                       available_roles, make_role, rig, run_loop)


class Widths(Problem):
    """A tiny campaign whose measurements are what gets mined."""

    name = "mined-widths"

    def __init__(self, widths, roles=None) -> None:
        self.widths = list(widths)
        self._roles = roles

    def objective(self, request):
        return {"study": "mined-widths"}

    def search(self, state):
        yield [Candidate(name=f"w{w}", artifact="", knobs={"width": w}) for w in self.widths]

    def build(self, cand, subgoal, state):
        return cand.artifact

    def judge(self, built, cand, subgoal, state):
        return Verdict(True, 0.0)

    def stages(self):
        return ["real"]

    def measure(self, cand, stage, state):
        return {"cost": 10.0 + 2.0 * int(cand.knobs["width"])}


class _State:
    """The little of `LoopState` a knowledge source sees."""

    def __init__(self, db: str) -> None:
        self.request = LoopRequest(db=db)
        self.records = None
        self.human_notes: list = []


def _campaign(db: str) -> None:
    run_loop(Widths([1, 2, 3, 4]), LoopRequest(db=db, steps=1, prototype=False, compute=False,
                                               critique_rounds=0), log=lambda _m: None)


def test_mined_is_a_source_like_the_others():
    src = Mined()
    assert (src.key, src.static) == ("mined", True), (
        "static: mining reads the store with real queries and must not re-run every turn")
    assert available_roles("knowledge") == ["mined"]
    mentor = make_role("knowledge", "mined")
    assert isinstance(mentor, Mentor) and [s.key for s in mentor.sources] == ["mined"]


def test_it_needs_no_configuration_and_reads_the_runs_own_store(tmp_path):
    db = str(tmp_path / "c.db")
    _campaign(db)
    text = Mined().render(_State(db))
    assert text, "the campaign's own measurements were mined"
    assert "cost" in text, text


def test_every_statement_carries_what_it_does_not_establish(tmp_path):
    """The reason mined knowledge is safe in a prompt at all (D245)."""
    db = str(tmp_path / "c.db")
    _campaign(db)
    text = Mined().render(_State(db))
    assert "NOT established" in text or "not established" in text.lower(), text


def test_a_store_with_nothing_in_it_contributes_nothing(tmp_path):
    assert Mined().render(_State(str(tmp_path / "empty.db"))) == ""
    assert Mined().render(_State("")) == "", "and no store at all is not an error either"


def test_mining_runs_once_per_run_not_once_per_prompt(tmp_path):
    db = str(tmp_path / "c.db")
    _campaign(db)
    calls: list[int] = []

    class Counting(Mined):
        def render(self, state):
            calls.append(1)
            return "one fact\n  NOT established: everything else"

    mentor = Mentor([Counting()])
    state = _State(db)
    assert mentor.prefix(state) and mentor.prefix(state) and mentor.text("mined", state)
    assert len(calls) == 1, "a static source is read once and memoised (D449)"


def test_both_halves_reach_the_prompt_together(tmp_path):
    """AND, not just OR: what a person fed in and what was mined from the data."""
    db = str(tmp_path / "c.db")
    _campaign(db)
    doc = {"id": "rigged", "statement": "write the word good", "extension": ".txt",
           "knowledge": "A person's own note: prefer the narrow design.",
           "roles": {"knowledge": "mined"},
           "gate": {"test": ["true"]},
           "stages": [{"name": "size", "command": ["wc", "-c", "{artifact}"],
                      "metrics_re": {"bytes": r"(\d+)"}}]}
    problem = PromptProblem(TaskSpec.from_dict(doc))
    state = _State(db)
    state.plans = {}
    prefix = problem.prompt_prefix(None, state)
    assert "A person's own note" in prefix, "the human half"
    assert "Mined from this project's own data" in prefix, "and the extracted half"
    titles = [t for t, _text in problem.mentor_sections(state)]
    assert "task" in titles and "knowledge" in titles and "Mined from this project's own data" in titles


def test_a_problem_can_declare_both_sources_in_code(tmp_path):
    db = str(tmp_path / "c.db")
    _campaign(db)
    problem = Widths([1], Roles(knowledge=Mentor([Corpus("methods sheet", "measure twice"),
                                                  Mined()])))
    got = problem.knowledge().prefix(_State(db))
    assert "measure twice" in got and "Mined from" in got


def test_a_mined_source_the_document_cannot_mean_is_refused():
    with pytest.raises(ValueError, match="takes"):
        make_role("knowledge", {"mined": {"trained_on": "nothing"}})
    mentor = make_role("knowledge", {"mined": {"max_facts": 3, "calibration": "cal.db"}})
    assert mentor.sources[0].max_facts == 3 and mentor.sources[0].calibration == ("cal.db",)

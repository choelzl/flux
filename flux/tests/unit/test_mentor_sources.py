"""The mentor's knowledge as declared SOURCES (docs/decisions.md D449).

Every study composed its knowledge by hand: a sheet, a library lookup, a record read-back,
each wrapped in its own `try`, concatenated in its own order -- and NLU's `prompt_prefix` re-ran
the library's BM25 lookups on every generation turn for text that cannot change during a run.
These pin the assembly: what a source is, what is read once, what is re-read, what a budget
does, and that a source which fails costs the run nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flux_knowledge import Corpus, KnowledgeSource, Library, Mentor, Notes, RecordReadback


@dataclass
class Counting:
    """A source that counts how often it is actually read."""

    key: str = "sheet"
    title: str = "a sheet"
    static: bool = True
    calls: int = 0
    text: str = "the sheet says so"

    def render(self, state: Any) -> str:
        self.calls += 1
        return self.text


@dataclass(frozen=True)
class Broken:
    key: str = "library"
    title: str = "knowledge: library excerpts"
    static: bool = True

    def render(self, state: Any) -> str:
        raise RuntimeError("no index on this machine")


def test_a_static_source_is_read_once_a_dynamic_one_every_time():
    """The defect this exists for: the library lookup ran once per generation turn."""
    sheet, notes = Counting(), Counting(key="notes", title="operator notes", static=False)
    mentor = Mentor([sheet, notes])
    state = object()
    for _turn in range(4):
        assert mentor.text("sheet", state) == "the sheet says so"
        assert mentor.text("notes", state) == "the sheet says so"
    assert sheet.calls == 1, "a sheet cannot change during a run"
    assert notes.calls == 4, "what the operator typed can"


def test_a_source_that_fails_costs_a_prompt_nothing_and_says_so_in_the_tab():
    mentor = Mentor([Counting(), Broken()])
    state = object()
    titles = dict(mentor.sections(state))
    assert titles["a sheet"] == "the sheet says so"
    assert "unavailable" in titles["knowledge: library excerpts"]
    assert "no index on this machine" in titles["knowledge: library excerpts"]
    # the prompt gets the sheet and no mention of the missing library
    prefix = mentor.prefix(state)
    assert "the sheet says so" in prefix and "unavailable" not in prefix
    assert mentor.text("library", state) == ""


def test_the_prefix_carries_only_what_cannot_change_mid_run():
    """The record's read-back would break the model server's prefix cache every turn (D422),
    so the static block is addressed by key rather than being "everything the mentor knows"."""
    mentor = Mentor([Counting(), Counting(key="record", title="the record", static=False,
                                          text="booth4 beats wallace")])
    state = object()
    assert "booth4" not in mentor.prefix(state, keys=("sheet",))
    assert "booth4" in mentor.prefix(state)
    assert [t for t, _ in mentor.sections(state)] == ["a sheet", "the record"]


def test_the_budget_is_spent_front_to_back_and_says_where_it_ran_out():
    """Order is priority: a sheet a design cannot be written without must not be crowded out
    by the library."""
    mentor = Mentor([Counting(text="A" * 60), Counting(key="library", title="papers",
                                                       text="B" * 60)], budget=100)
    got = dict(mentor.sections(object()))
    assert got["a sheet"] == "A" * 60, "the first source is whole"
    assert got["papers"].startswith("B" * 40) and "window is full" in got["papers"]


def test_the_bound_is_the_models_window_not_a_prompt_budget():
    """D548 (Cedric: "knowledge should be in the context, not in the prompt"): with no budget
    set, the mentor spends `share` of the proposer's context window at its characters per
    token; a proposer that states no window, or none at all, leaves every source whole."""
    from types import SimpleNamespace

    from flux_knowledge import Corpus, Mentor

    class _Proposer:
        chars_per_token = 3.0

        def __init__(self, window):
            self.window = window

        def context_length(self):
            return self.window

    sources = [Corpus("sheet", "A" * 4000), Corpus("papers", "B" * 4000, key="library")]
    state = SimpleNamespace(proposer=_Proposer(4000), request=SimpleNamespace(knowledge_share=0.5))
    got = dict(Mentor(sources).sections(state))                  # 4000 tokens * 0.5 * 3 chars = 6000
    assert got["sheet"] == "A" * 4000 and got["papers"].startswith("B" * 2000) and "window is full" in got["papers"]
    whole = dict(Mentor(sources).sections(SimpleNamespace(proposer=_Proposer(None), request=None)))
    assert whole["papers"] == "B" * 4000
    assert dict(Mentor(sources).sections(SimpleNamespace(proposer=None)))["papers"] == "B" * 4000
    assert dict(Mentor(sources, budget=100).sections(state))["sheet"].startswith("A" * 100)


def test_a_source_over_its_room_is_compacted_by_rules_then_by_the_model_then_cut():
    """D549: repeats go first; when that is not enough and the run says `compact: llm`, the
    model condenses the source once (memoised); when it fails or the run says `rules`, the
    cut is said out loud."""
    from types import SimpleNamespace

    from flux_knowledge import Corpus, Mentor
    from flux_knowledge.compact import densify

    assert densify("a\n\n\nA\nb\n") == "a\n\nb"
    repeats = "\n".join(["one line of a sheet"] * 50)                       # 1000 chars, one line worth
    got = dict(Mentor([Corpus("sheet", repeats)], budget=200).sections(SimpleNamespace(proposer=None)))
    assert got["sheet"].startswith("one line of a sheet\n  (compacted 999 -> 19 chars: repeats dropped)")

    calls = []

    class _Model:
        def __init__(self, answer):
            self.answer = answer

        def propose(self, prompt, **kw):
            calls.append(prompt)
            return SimpleNamespace(text=self.answer)

    text = "\n".join(f"fact {i}: {i * 7} ULP" for i in range(200))              # 3000+ chars, no repeats
    llm = SimpleNamespace(proposer=_Model("facts 0..199: 7i ULP"), request=SimpleNamespace(compact="llm"))
    mentor = Mentor([Corpus("sheet", text)], budget=400)
    a = dict(mentor.sections(llm))["sheet"]
    assert a.startswith("facts 0..199: 7i ULP\n  (compacted") and "by the model" in a
    assert dict(mentor.sections(llm))["sheet"] == a and len(calls) == 1 and "at most 320 characters" in calls[0]
    long = SimpleNamespace(proposer=_Model("x" * 5000), request=SimpleNamespace(compact="llm"))
    b = dict(Mentor([Corpus("sheet", text)], budget=400).sections(long))["sheet"]
    assert b.endswith("(clipped: the model's context window is full)") and len(b) < 500
    rules = SimpleNamespace(proposer=_Model("short"), request=SimpleNamespace(compact="rules"))
    assert "window is full" in dict(Mentor([Corpus("sheet", text)], budget=400).sections(rules))["sheet"]


def test_over_the_room_the_paragraphs_nearest_the_part_in_hand_are_kept_whole():
    """D550: given the run's focus, the source's paragraphs are ranked against it and the
    nearest kept whole, in their order, while they fit; the note says which."""
    from types import SimpleNamespace

    from flux_knowledge import Corpus, Mentor
    from flux_knowledge.compact import select_relevant

    sheet = "\n\n".join([
        "exp: range reduction by ln2, then a degree-4 polynomial on the fraction; 1 ULP at FP16.",
        "gelu: tanh form with the 0.044715 cubic term; a rational fit beats it at 2 segments.",
        "log: leading-zero count gives the exponent; the mantissa by a table of 64 entries.",
        "gelu again: the erf form needs a 3-segment polynomial for 1 ULP over [-4, 4].",
    ])
    got = select_relevant(sheet, 220, "gelu")
    assert got is not None and got[1] == 2 and got[2] == 4
    assert got[0].startswith("gelu: tanh") and "erf form" in got[0] and "exp:" not in got[0]
    section = dict(Mentor([Corpus("sheet", sheet)], budget=330).sections(
        SimpleNamespace(proposer=None), focus="gelu"))["sheet"]
    assert "0.044715" in section and "exp:" not in section and "paragraphs nearest 'gelu' kept" in section
    plain = dict(Mentor([Corpus("sheet", sheet)], budget=330).sections(SimpleNamespace(proposer=None)))["sheet"]
    assert plain.endswith("(clipped: the model's context window is full)")     # no focus: the cut, said


def test_the_sources_this_repository_ships_are_sources():
    """Structural, so a new source cannot forget the protocol the loop reads it through."""
    for src in (Corpus("t", "x"), Library(queries=["q"]), RecordReadback(stage="r", metric="m"),
                Notes()):
        assert isinstance(src, KnowledgeSource)
        assert src.key and src.title and isinstance(src.static, bool)
    assert Corpus("t", "x").static and Library(queries=[]).static
    assert not RecordReadback(stage="r", metric="m").static and not Notes().static


def test_notes_render_what_the_operator_typed():
    from types import SimpleNamespace

    from flux_feedback import Note

    state = SimpleNamespace(human_notes=[Note(text="prefer fewer ways", received_at=1.0)])
    assert Notes().render(state) == "* prefer fewer ways"
    assert Notes().render(SimpleNamespace()) == ""


def test_a_record_readback_source_is_the_shared_renderer(tmp_path):
    from types import SimpleNamespace

    from flux_records import Records

    objective = {"study": "t"}
    first = Records(str(tmp_path / "r.db"), objective=objective)
    for knobs, value in (({"family": "xor", "banks": 32}, 4.0),
                         ({"family": "modulo", "banks": 32}, 40.0),
                         ({"family": "xor", "banks": 16}, 3.0),
                         ({"family": "modulo", "banks": 16}, 30.0)):
        first.trial(knobs, f"k{value}", stage="exhaustive", strategy="t",
                    metrics={"hardware_cost": value})
    fresh = RecordReadback(stage="exhaustive", metric="hardware_cost", knobs=("family",),
                           higher_is_better=False)
    assert fresh.render(SimpleNamespace(records=first)) == "", "a first run has nothing to read"
    resumed = Records(str(tmp_path / "r.db"), objective=objective)
    text = fresh.render(SimpleNamespace(records=resumed))
    # the duel runs the way the metric runs: fewer gates is better, so xor wins (D449)
    assert "WHAT THE RECORD SHOWS" in text and "family: xor beats modulo" in text
    louder = RecordReadback(stage="exhaustive", metric="hardware_cost", knobs=("family",))
    assert "family: modulo beats xor" in louder.render(SimpleNamespace(records=resumed)), (
        "and the other way when more of the metric IS better")


def test_the_loop_assembles_both_the_tab_and_the_prefix_from_the_declaration():
    """What a problem gets for declaring sources: the mentor tab and the static prompt prefix,
    with no `mentor_sections` or `prompt_prefix` of its own."""
    from flux_loop import Candidate, LoopRequest, LoopState, Problem, Verdict, run_loop

    sheet = Counting(text="the contract")

    class Declared(Problem):
        name = "declared"

        def __init__(self) -> None:
            self.mentor = Mentor([sheet, Counting(key="record", title="the record",
                                                  static=False, text="a duel")])

        def knowledge(self):
            return self.mentor

        def objective(self, request):
            return {"study": "declared"}

        def subgoals(self):
            return []

        def generate(self, subgoal, method, state, human):
            return Candidate(name="c", artifact="x"), "built", ""

        def build(self, cand, subgoal, state):
            return "built"

        def judge(self, built, cand, subgoal, state):
            return Verdict(True, 0.0)

        def compose(self, admitted, state):
            return next(iter(admitted.values()))

        def measure(self, cand, stage, state):
            return {"value": 1.0}

    problem = Declared()
    state = LoopState(request=LoopRequest(), say=lambda _m: None, proposer=None, feedback=None)
    assert [t for t, _ in problem.mentor_sections(state)] == ["a sheet", "the record"]
    assert problem.prompt_prefix(None, state) == "a sheet\nthe contract", (
        "the static block is the static sources, titled")
    out = run_loop(problem, LoopRequest(steps=1), log=lambda _m: None)
    assert out.decision is not None
    assert sheet.calls == 1, "the loop read the sheet once for the whole run"

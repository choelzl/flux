"""What the orchestrator is TOLD is outstanding (docs/decisions.md D314).

`NOT YET DONE` is assembled from unrun declared variants, and only an action with a finite variant
set can appear there. In the interconnect demo exactly one action has one -- `enumerate`, over the
five families -- so the line read "not yet done: <families>" every round while the Monte-Carlo
step, the proposer and the perturber were never mentioned at all. Over a real four-round run the
model chose `enumerate` twice against a space it had exhausted and never once chose `anneal`.

The line was arguing for one action by omission. These pin the fix.
"""

from __future__ import annotations

from flux_directed_search import Action, DirectedSearch, Outcome


def _search(**kwargs):
    def noop(_params):
        return Outcome(gained=0)

    actions = [
        Action("enumerate", "enumerate a family", noop, example={"family": "clos"},
               variants=({"family": "clos"}, {"family": "butterfly"}),
               variant_key=lambda p: f"enumerated:{p['family']}"),
        Action("anneal", "monte-carlo", noop, example={"chains": 8},
               variant_key=lambda p: f"annealed:{p['chains']}"),
        Action("propose", "ask the model", noop, example={"count": 6}),
    ]
    return DirectedSearch(actions, ask=None, problem="test", **kwargs)


def test_actions_without_declared_variants_are_named_as_available():
    """The whole bug. `anneal` and `propose` can be chosen at any time, and the line that tells
    the model what is outstanding never mentioned them."""
    note = _search()._remaining_note()
    assert "anneal" in note and "propose" in note


def test_the_enumerable_options_are_still_listed():
    note = _search()._remaining_note()
    assert "enumerated:clos" in note and "enumerated:butterfly" in note


def test_an_exhausted_space_says_so_rather_than_going_quiet():
    """When every family has run, the old text said only that "every listed option has been
    taken" -- which is exactly the moment the model most needs to know that enumerating again
    finds nothing and that other steps exist."""
    search = _search()
    search._done.extend(["enumerated:clos", "enumerated:butterfly"])
    note = search._remaining_note()
    assert "nothing new" in note
    assert "anneal" in note and "propose" in note


def test_a_menu_of_only_enumerable_actions_still_reads_sensibly():
    def noop(_params):
        return Outcome(gained=0)

    only = DirectedSearch(
        [Action("enumerate", "m", noop, variants=({"family": "clos"},),
                variant_key=lambda p: f"enumerated:{p['family']}")],
        ask=None, problem="test")
    assert "enumerated:clos" in only._remaining_note()
    assert "Always available" not in only._remaining_note()


def test_a_declined_step_says_why():
    """An action that refuses put its reason in the step record and printed nothing. A reader saw
    the orchestrator choose `repair`, then saw the run move on with no sign anything happened —
    which reads exactly like a step that ran and found nothing."""
    lines: list[str] = []

    def refuses(_params):
        return Outcome(progressed=False, detail="no near-miss failures to repair")

    search = DirectedSearch(
        [Action("repair", "m", refuses, variants=({"label": ""},),
                variant_key=lambda p: "repaired")],
        ask=None, problem="t", log=lines.append)
    search.run()
    assert any("declined: no near-miss failures to repair" in line for line in lines)


def test_a_step_that_ran_does_not_claim_to_have_declined():
    lines: list[str] = []

    def works(_params):
        return Outcome(gained=3, detail="found three")

    search = DirectedSearch(
        [Action("anneal", "m", works, variants=({"seed": 0},), variant_key=lambda p: "annealed")],
        ask=None, problem="t", log=lines.append)
    search.run()
    assert not any("declined" in line for line in lines)


def test_a_step_that_ran_and_gained_nothing_is_not_called_declined():
    """The distinction that matters. An annealing step screened 19,200 proposals and placed four
    of them, gaining no fabric the store had never seen — real work, zero `gained` — and was
    reported as "declined: 19,200 screened proposals -> 4 placed". Refusing to run and running
    without finding anything new are different outcomes and read differently to anyone watching."""
    lines: list[str] = []

    def ran_but_gained_nothing(_params):
        return Outcome(gained=0, detail="19,200 screened proposals -> 4 placed")

    search = DirectedSearch(
        [Action("anneal", "m", ran_but_gained_nothing, variants=({"seed": 0},),
                variant_key=lambda p: "annealed")],
        ask=None, problem="t", log=lines.append)
    search.run()
    assert not any("declined" in line for line in lines)

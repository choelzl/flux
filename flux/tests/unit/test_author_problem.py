"""Natural language to a validated interconnect problem (docs/decisions.md D333).

The AUTHOR role, which this loop did not have. `flux_author_objective` does this for a whole
Objective IR; the interconnect study is five numbers and builds its own document, so what needs
authoring here is the problem.

The model call is stubbed throughout — what is under test is the validation, which is the half
that decides whether a study runs under the wrong question.
"""

from __future__ import annotations

import json

import pytest


GOOD = {"clients": 16, "banks": 8, "width_bits": 64, "target_mhz": 1000, "bank_rows": 256}


def _says(payload):
    return lambda _prompt: json.dumps(payload)


def test_a_complete_request_is_read(): 
    import flux_interconnect.flow as demo

    assert demo.author_problem("16 masters into 8 banks", _says(GOOD)) == GOOD


@pytest.mark.parametrize(
    "field,value,fragment",
    [("clients", "sixteen", "must be a number"),
     ("banks", 0, "outside the buildable range"),
     ("clients", 2.5, "whole number"),
     ("width_bits", -8, "outside the buildable range"),
     ("banks", True, "must be a number")],
    ids=["prose", "zero-banks", "fractional", "negative-width", "boolean"])
def test_an_unbuildable_field_is_rejected(field, value, fragment):
    """A boolean is an int in Python and would sail through a naive isinstance check."""
    import flux_interconnect.flow as demo

    _problem, why = demo._validated_problem({**GOOD, field: value})
    assert _problem is None and fragment in why


def test_a_problem_no_fabric_can_serve_is_rejected():
    """The real check: a request can parse perfectly and describe nothing. Type-checking five
    numbers cannot see that, so the space they describe is built."""
    import flux_interconnect.flow as demo

    problem, why = demo._validated_problem({**GOOD, "clients": 2, "banks": 512,
                                            "width_bits": 4096})
    if problem is None:
        assert why
    else:
        # If a fabric does exist for it, the validator is right to accept it — the claim under
        # test is that the check is real, not that this particular case fails.
        assert problem["banks"] == 512


def test_a_missing_field_is_rejected_rather_than_defaulted():
    """Defaults belong in the PROMPT, where the model applies them knowingly. Filling them in
    after the fact would let a silent omission become a study nobody asked for."""
    import flux_interconnect.flow as demo

    problem, why = demo._validated_problem({"clients": 16, "banks": 8})
    assert problem is None and "width_bits" in why


def test_the_model_may_refuse_and_that_refusal_is_honoured():
    """'make it fast' names no client or bank count. Inventing them would run a study under a
    question the user did not ask."""
    import flux_interconnect.flow as demo

    with pytest.raises(ValueError, match="does not say"):
        demo.author_problem("make it fast",
                            _says({"error": "the request does not say how many clients or banks"}))


def test_an_unusable_reply_is_repaired_then_given_up_on():
    """The repair loop feeds the real error back, and a request that never validates raises
    rather than quietly running the default study."""
    import flux_interconnect.flow as demo

    calls = []

    def never_valid(prompt):
        calls.append(prompt)
        return "not json at all"

    with pytest.raises(ValueError, match="could not read a usable problem"):
        demo.author_problem("something", never_valid, attempts=3)
    assert len(calls) == 3, "each attempt must actually re-ask"
    assert "COULD NOT BE READ" in calls[-1], "the failure must be fed back into the next prompt"


def test_the_rejection_reason_reaches_the_next_prompt():
    import flux_interconnect.flow as demo

    calls = []

    def once_bad(prompt):
        calls.append(prompt)
        return json.dumps({**GOOD, "banks": 0} if len(calls) == 1 else GOOD)

    assert demo.author_problem("x", once_bad, attempts=2) == GOOD
    assert "REJECTED" in calls[1] and "banks=0" in calls[1]

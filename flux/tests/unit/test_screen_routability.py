"""The screen refuses fabrics that cannot route (docs/decisions.md D319).

`build` succeeding means a shape is CONSTRUCTIBLE, not that every client can reach every bank. A
chain whose ranks do not cover the banks builds fine, screens well, and delivers nothing — and it
screens well *because* it is broken: missing connections are missing mux bits, so it is cheap on
the area proxy. A search minimizing area therefore hunts them rather than avoiding them.

Measured: 0.5% of the enumerated space, and three of five finalists in a real run.
"""

from __future__ import annotations


import pytest
from flux_evaluator_abi import Budget, Candidate

# ITS OWN path setup. This file imports `demo` and did not add the directory, so it passed only
# when some other test file happened to be collected first and did it — nine of nineteen tests
# here failed the moment the file was run alone. A test that depends on collection order is a
# test that passes for a reason unrelated to what it checks.
from flux_evaluator_interconnect_struct.adapter import InterconnectStructuralEvaluator

CLIENTS, BANKS, WIDTH = 28, 32, 128


def _screen(stages):
    spec = {"kind": "xbar_staged", "clients": CLIENTS, "banks": BANKS, "width_bits": WIDTH,
            "stages": stages}
    result = InterconnectStructuralEvaluator().evaluate(
        Candidate(arch={"interconnect": spec}, workload={}, mapping={}),
        Budget(), frozenset({"mux_bits"}))
    return result


UNROUTABLE = [{"switches": 7, "in": 4, "out": 4}, {"switches": 7, "in": 4, "out": 4},
              {"switches": 28, "in": 1, "out": 2}]
ROUTABLE = [{"switches": 7, "in": 4, "out": 4}, {"switches": 4, "in": 7, "out": 8}]


def test_an_unroutable_fabric_carries_nothing():
    """Reported as zero capacity rather than raised: the study already refuses a fabric whose
    waist is too narrow, and a fabric that routes nothing has the narrowest waist there is. Every
    existing caller then refuses it without knowing this check exists."""
    metrics = _screen(UNROUTABLE).metrics
    assert metrics["max_throughput_words_per_cycle"].value == 0.0
    assert metrics["throughput_words_per_cycle"].value == 0.0


def test_it_says_why_rather_than_only_that():
    """The provenance carries the diagnosis, so a reader is not left to rediscover it by placing
    the thing for real."""
    note = _screen(UNROUTABLE).provenance.inputs["note"]
    assert note.startswith("UNROUTABLE:")
    assert "cannot reach banks" in note


def test_the_broken_fabric_is_cheap_precisely_because_it_is_broken():
    """The mechanism behind the whole failure. If unroutable designs were expensive nothing would
    select for them; they are cheaper than the real answer, so an area search prefers them."""
    assert _screen(UNROUTABLE).metrics["mux_bits"].value \
        < _screen(ROUTABLE).metrics["mux_bits"].value


def test_a_routable_fabric_is_untouched():
    metrics = _screen(ROUTABLE).metrics
    assert metrics["max_throughput_words_per_cycle"].value == CLIENTS
    assert metrics["throughput_words_per_cycle"].value > 0
    assert "UNROUTABLE" not in _screen(ROUTABLE).provenance.inputs["note"]


def test_the_other_metrics_are_still_reported_for_a_broken_fabric():
    """Area and wiring are facts about the shape and remain true; only what it DELIVERS is zero.
    Blanking everything would hide why it was rejected."""
    metrics = _screen(UNROUTABLE).metrics
    assert metrics["mux_bits"].value > 0
    assert metrics["latency_cycles"].value == 3


# -- the throughput rung must not answer for a fabric it cannot simulate ----------------------


def test_an_unroutable_fabric_gets_no_modelled_throughput():
    """THE bug that put a broken fabric at the top of the frontier (docs/decisions.md D325).

    `_measure_throughput` re-raises `FabricIncorrectError` — a fabric that delivers to the wrong
    bank is broken, not slow, and a modelled number would keep it in the running with a
    clean-looking score (D268). `UnroutableFabricError` did NOT re-raise. It fell to the generic
    handler, was read as "the simulator is missing", and was answered with
    `expected_served_per_cycle()` — a stage-load model that needs no routing and returns a
    plausible 8.4 words/cycle for a fabric where client 4 reaches no bank above 7.

    That number carried it to the top of the frontier as the smallest design meeting timing, and
    the only thing that ever contradicted it was the decision rung failing to place it.
    """
    from flux_evaluator_interconnect_phys.adapter import InterconnectPhysicalEvaluator
    from flux_interconnect import build
    from flux_interconnect.fabric import UnroutableFabricError

    spec = {"kind": "xbar_staged", "clients": CLIENTS, "banks": BANKS, "width_bits": WIDTH,
            "stages": UNROUTABLE}
    with pytest.raises(UnroutableFabricError):
        InterconnectPhysicalEvaluator()._measure_throughput(build(spec))


def test_the_model_would_have_answered_had_it_been_asked():
    """The fallback was plausible, which is why it survived: the analytic model computes stage
    loads and never consults a routing table, so it returns a healthy-looking number for a fabric
    that connects nothing. This pins the mechanism, not just the symptom."""
    from flux_interconnect import build

    spec = {"kind": "xbar_staged", "clients": CLIENTS, "banks": BANKS, "width_bits": WIDTH,
            "stages": UNROUTABLE}
    assert build(spec).expected_served_per_cycle() > 1.0


def test_a_routable_fabric_is_still_measured_normally():
    from flux_evaluator_interconnect_phys.adapter import InterconnectPhysicalEvaluator
    from flux_interconnect import build

    spec = {"kind": "xbar_staged", "clients": CLIENTS, "banks": BANKS, "width_bits": WIDTH,
            "stages": ROUTABLE}
    served, how = InterconnectPhysicalEvaluator()._measure_throughput(build(spec))
    assert served > 0 and how


# -- boundary waste, shown rather than banned ------------------------------------------------


@pytest.mark.parametrize(
    "stages,expected",
    [([{"switches": 7, "in": 4, "out": 4}, {"switches": 4, "in": 7, "out": 8}], (0, 0)),
     ([{"switches": 8, "in": 4, "out": 4}, {"switches": 4, "in": 8, "out": 8}], (4, 0)),
     ([{"switches": 7, "in": 4, "out": 4}, {"switches": 4, "in": 7, "out": 9}], (0, 4))],
    ids=["exact-fit", "four-dead-inputs", "four-dead-outputs"])
def test_dead_boundary_ports_are_counted(stages, expected):
    """Ports wired to nothing: a first stage admitting more than the 28 clients, or a last stage
    driving more than the 32 banks. All three of these were finalists in one run and placed within
    0.0002 mm2 of each other, because synthesis optimises the dead ports away."""
    import flux_interconnect.flow as demo

    spec = {"kind": "xbar_staged", "clients": CLIENTS, "banks": BANKS, "width_bits": WIDTH,
            "stages": stages}
    assert demo.dead_ports(spec) == expected


def test_dead_ports_are_not_a_reason_to_refuse_a_fabric():
    """Deliberately NOT a constraint. 717 of 1,184 feasible fabrics here carry at least one dead
    port and the cheapest routable fabric in the space has four inputs to nowhere — a switch count
    that divides evenly downstream can more than pay for unused ports upstream. Banning them would
    delete the cheapest design available, so they are reported and priced, never rejected."""
    import flux_interconnect.flow as demo
    from flux_interconnect.anneal import objective, screen

    wasteful = {"kind": "xbar_staged", "clients": CLIENTS, "banks": BANKS, "width_bits": WIDTH,
                "stages": [{"switches": 8, "in": 4, "out": 4},
                           {"switches": 4, "in": 8, "out": 8}]}
    assert demo.dead_ports(wasteful) == (4, 0)
    assert objective(screen(wasteful), clients=CLIENTS) is not None, (
        "a fabric with dead ports is wasteful, not infeasible")


def test_the_screen_charges_for_the_waste():
    """The screen prices dead ports (they are real muxes in its model) even though silicon does
    not. That disagreement is the reason the count is printed rather than left implicit."""
    from flux_interconnect.anneal import screen

    exact = {"kind": "xbar_staged", "clients": CLIENTS, "banks": BANKS, "width_bits": WIDTH,
             "stages": [{"switches": 7, "in": 4, "out": 4}, {"switches": 4, "in": 7, "out": 8}]}
    wasteful = {**exact, "stages": [{"switches": 8, "in": 4, "out": 4},
                                    {"switches": 4, "in": 8, "out": 8}]}
    assert screen(wasteful)["mux_bits"] > screen(exact)["mux_bits"]


def test_a_fabric_that_cannot_be_built_has_no_boundary_to_measure():
    import flux_interconnect.flow as demo

    assert demo.dead_ports({"kind": "xbar_staged", "stages": []}) == (0, 0)
    assert demo.dead_ports(None) == (0, 0)


@pytest.mark.parametrize(
    "stages,expected",
    [([{"switches": 7, "in": 4, "out": 4}, {"switches": 4, "in": 7, "out": 8}], ""),
     ([{"switches": 8, "in": 4, "out": 4}, {"switches": 4, "in": 8, "out": 8}],
      " [carries 4 unused client port(s)]"),
     ([{"switches": 7, "in": 4, "out": 4}, {"switches": 4, "in": 7, "out": 9}],
      " [carries 4 unused bank port(s)]")],
    ids=["exact-fit-unmarked", "dead-inputs-marked", "dead-outputs-marked"])
def test_a_fabric_named_as_an_answer_carries_its_marking(stages, expected):
    """Wherever a fabric is NAMED as an answer, not only where it is tabulated. Someone reading
    "SMALLEST that meets timing: xbar_staged-8x4x4-4x8x8" and acting on it should not have to
    cross-reference a column to learn four of its client ports go nowhere."""
    import flux_interconnect.flow as demo

    spec = {"kind": "xbar_staged", "clients": CLIENTS, "banks": BANKS, "width_bits": WIDTH,
            "stages": stages}
    assert demo.dead_note(spec) == expected


def test_an_exact_fit_is_not_annotated_with_noise():
    """A marking that appears on everything marks nothing."""
    import flux_interconnect.flow as demo

    exact = {"kind": "xbar_staged", "clients": CLIENTS, "banks": BANKS, "width_bits": WIDTH,
             "stages": ROUTABLE}
    assert demo.dead_note(exact) == ""


def test_both_kinds_of_waste_are_named_together():
    import flux_interconnect.flow as demo

    spec = {"kind": "xbar_staged", "clients": CLIENTS, "banks": BANKS, "width_bits": WIDTH,
            "stages": [{"switches": 8, "in": 4, "out": 4}, {"switches": 4, "in": 8, "out": 9}]}
    note = demo.dead_note(spec)
    assert "client port" in note and "bank port" in note

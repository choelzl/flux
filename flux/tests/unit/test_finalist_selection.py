"""Which fabrics earn a whole-fabric placement (docs/decisions.md D327).

A placement is minutes of real Yosys and OpenROAD. A shortlist that clusters spends all of them
learning one number: `xbar_staged-7x4x4-4x7x8`, `-8x4x4-4x8x8` and `-7x4x4-4x7x9` are structurally
distinct fabrics, took three of five placements in a real run, and came out within 0.0002 mm2 and
0.2 words/cycle of each other.

Tested at module level because that is the lesson of this session: every defect this demo shipped
lived in a function nested inside `main`, which nothing could import and no test ever called.
"""

from __future__ import annotations



# The real cluster, from a real run, plus the two genuinely different points beside it.
CLUSTER = [
    ("xbar_staged-7x4x4-4x7x8", {"area_mm2": 0.0153, "served": 14.9}),
    ("xbar_staged-8x4x4-4x8x8", {"area_mm2": 0.0154, "served": 14.9}),
    ("xbar_staged-7x4x4-4x7x9", {"area_mm2": 0.0155, "served": 14.7}),
    ("xbar_staged-7x4x8-8x7x4", {"area_mm2": 0.0212, "served": 17.1}),
    ("xbar_staged-4x7x8-8x4x4", {"area_mm2": 0.0158, "served": 15.5}),
]


def _pick(candidates, count, shape_of=lambda label: label):
    import flux_interconnect.flow as demo

    return [label for label, _ in
            demo.choose_finalists(candidates, count, shape_of, log=lambda _s: None)]


def test_the_observed_cluster_costs_one_placement_not_three():
    picked = _pick(CLUSTER, 3)
    assert "xbar_staged-7x4x4-4x7x8" in picked, "the cheapest of the cluster is still placed"
    assert "xbar_staged-8x4x4-4x8x8" not in picked
    assert "xbar_staged-7x4x4-4x7x9" not in picked


def test_the_budget_is_still_spent_in_full():
    """A diversity rule that quietly buys fewer measurements is a worse trade than the clustering
    it prevents. With only three distinct points and five placements budgeted, five are placed."""
    assert len(_pick(CLUSTER, 5)) == 5


def test_the_fill_pass_never_repeats_a_label():
    picked = _pick(CLUSTER, 5)
    assert len(picked) == len(set(picked))


def test_distinct_points_are_all_kept():
    """The rule must not eat the trade-off it exists to preserve."""
    spread = [("a", {"area_mm2": 0.010, "served": 8.0}),
              ("b", {"area_mm2": 0.020, "served": 14.0}),
              ("c", {"area_mm2": 0.030, "served": 18.0})]
    assert _pick(spread, 3) == ["a", "b", "c"]


def test_same_silicon_under_two_names_is_placed_once():
    """The pre-existing rule, kept: a radix-4 hybrid over 28 clients IS 7x(4x4)."""
    twins = [("xbar_staged-7x4x4-4x7x8", {"area_mm2": 0.0153, "served": 14.9}),
             ("hybrid-radixradix4-xbarswitches4", {"area_mm2": 0.0153, "served": 14.9})]
    assert _pick(twins, 2, shape_of=lambda _label: "one-shape") == ["xbar_staged-7x4x4-4x7x8"]


def test_a_label_with_no_known_shape_is_not_collapsed_onto_others():
    """`shape_of` returns None when a fabric's structure could not be recovered. That is missing
    information, not evidence that two fabrics are the same one."""
    unknown = [("a", {"area_mm2": 0.010, "served": 8.0}),
               ("b", {"area_mm2": 0.020, "served": 14.0})]
    assert _pick(unknown, 2, shape_of=lambda _label: None) == ["a", "b"]


def test_asking_for_none_places_none():
    assert _pick(CLUSTER, 0) == []


def test_asking_for_more_than_exist_returns_what_exists():
    assert len(_pick(CLUSTER, 99)) == len(CLUSTER)


def test_order_is_preserved_so_the_cheapest_is_placed_first():
    """`by_area` arrives sorted by the objective, and the decision rung prints as each lands —
    a shortlist that reordered would show the operator the wrong thing first."""
    picked = _pick(CLUSTER, 5)
    assert picked[0] == "xbar_staged-7x4x4-4x7x8"

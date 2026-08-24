"""The composition axis: which prefetchers run together, not just how Bingo is configured.

The only axis with a CONFIRMED full-length gain that knob tuning cannot reach — `bingo+sms`
measured 1.0586 against `bingo`'s 1.0542 at 100M+150M instructions (D351). Two properties make it
searchable at all: partners are tried in a measured order rather than arbitrarily, and known
simulator crashes are excluded rather than rediscovered at six minutes apiece.
"""

from __future__ import annotations

from pathlib import Path

FLUX_ROOT = Path(__file__).resolve().parents[2]

from flux_prefetcher.config import DEFAULT  # noqa: E402
from flux_prefetcher.objective import Baseline, score  # noqa: E402
from flux_prefetcher.space import KNOWN_UNSTABLE, PARTNERS, partner_stacks  # noqa: E402
from flux_prefetcher.study import ScoredConfig  # noqa: E402

BASE = Baseline(ipc={"fdd_su_v1_0": 0.69602, "tdd_dl_mu_v1_0": 0.99071,
                     "tdd_ul_mu_v1_0": 0.80232})


def test_a_candidate_carries_the_stack_it_was_measured_in():
    """`bingo` and `bingo+sms` on identical knobs are two designs, not one row."""
    alone = ScoredConfig(config=DEFAULT, score=score(dict(BASE.ipc), BASE, 1))
    paired = ScoredConfig(config=DEFAULT, score=score(dict(BASE.ipc), BASE, 1),
                          types=("bingo", "sms"))
    assert alone.stack == "bingo"
    assert paired.stack == "bingo+sms"
    assert alone.config == paired.config and alone.stack != paired.stack


def test_partners_are_offered_in_measured_order():
    """Best-alone first: a prefetcher that does nothing by itself rarely rescues a combination."""
    stacks = partner_stacks(("bingo",), limit=3)
    assert [s[-1] for s in stacks] == list(PARTNERS[:3])
    assert all(s[0] == "bingo" for s in stacks)


def test_actively_harmful_prefetchers_are_not_offered_at_all():
    """`bop` (0.9872) and `dspatch` (0.9571) are SLOWER than no prefetcher alone.

    Excluded from the partner list rather than measured: six minutes each to re-learn something
    `experiments/prefetcher_family.py` already established is the one cost this study cannot
    afford.
    """
    assert "bop" not in PARTNERS
    assert "dspatch" not in PARTNERS


def test_known_crashing_pairs_are_never_proposed():
    """Measured aborts and segfaults, excluded so a budget is not spent rediscovering them."""
    assert frozenset({"bingo", "scooby"}) in KNOWN_UNSTABLE
    for stack in partner_stacks(("bingo",), limit=len(PARTNERS)):
        assert "scooby" not in stack, "bingo+scooby aborts the simulator (exit 134)"
    for stack in partner_stacks(("bingo", "sms"), limit=len(PARTNERS)):
        assert "next_line" not in stack, "next_line+sms segfaults (exit 139)"


def test_a_partner_already_in_the_stack_is_not_offered_twice():
    stacks = partner_stacks(("bingo", "sms"), limit=len(PARTNERS))
    assert all(s.count("sms") == 1 for s in stacks)
    assert all(len(set(s)) == len(s) for s in stacks)


def test_the_stack_only_grows_by_one_per_round():
    """Greedy: each round adds a single partner, so each addition is attributable."""
    for stack in partner_stacks(("bingo",), limit=4):
        assert len(stack) == 2
    for stack in partner_stacks(("bingo", "sms"), limit=4):
        assert len(stack) == 3


def test_exhausting_the_partner_list_terminates():
    """A stack containing everything offers nothing, rather than looping."""
    everything = ("bingo",) + PARTNERS
    assert partner_stacks(everything) == []


def test_the_incumbent_survives_when_the_winner_shares_its_knobs():
    """The regression: dedupe on the Bingo config alone dropped the reference points.

    A composed winner very often carries DEFAULT's exact knobs — composition adds a partner, it
    does not have to change Bingo. Keying identity on the configuration alone then removed both
    the incumbent and the shipped-default reference from the finalist set, and the report said
    "beat the default: not established" beside a confidently negative tuning figure computed from
    the screened reference it had fallen back to.
    """
    from flux_prefetcher.flow import _finalists, _is_reference
    from flux_prefetcher.partners import defaults_for_stack

    def scored(g, types, provenance, knobs=()):
        return ScoredConfig(config=DEFAULT, score=score({k: v * g for k, v in BASE.ipc.items()},
                                                        BASE, 1),
                            provenance=provenance, types=types, partner_knobs=knobs)

    stack = ("bingo", "sms")
    winner = scored(1.07, stack, "compose")                       # same knobs as DEFAULT
    incumbent = scored(1.06, ("bingo",), "incumbent")
    reference = scored(1.065, stack, "reference",
                       tuple(sorted(defaults_for_stack(stack).items())))

    finalists = _finalists([winner, incumbent, reference], winner, 1)
    assert any(f.provenance == "incumbent" for f in finalists), "the incumbent must be confirmed"
    assert any(_is_reference(f) and f.types == stack for f in finalists), (
        "the stack's shipped-default reference must be confirmed on the same rung")


def test_identity_distinguishes_partner_knobs():
    """A tuned stack and the same stack at its defaults are two designs."""
    from flux_prefetcher.flow import _identity_of

    base = ScoredConfig(config=DEFAULT, score=score(dict(BASE.ipc), BASE, 1),
                        types=("bingo", "sms"),
                        partner_knobs=(("sms_pref_degree", 4),))
    tuned = ScoredConfig(config=DEFAULT, score=score(dict(BASE.ipc), BASE, 1),
                         types=("bingo", "sms"),
                         partner_knobs=(("sms_pref_degree", 8),))
    assert _identity_of(base) != _identity_of(tuned)
    assert _identity_of(base) == _identity_of(base)

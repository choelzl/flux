"""The partner prefetchers' knob spaces, checked against the simulator's own `.ini` files.

`PARTNER_KNOBS` records each prefetcher's shipped default as a COPY, because `proj/` is slated
for deletion (D349) and the study must not depend on it at run time. A copy can drift, and a
drifted default is not a cosmetic error: every "this knob improves on the default" result would
be measured against a value the simulator never used.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

FLUX_ROOT = Path(__file__).resolve().parents[2]

from flux_prefetcher.partners import (  # noqa: E402
    PARTNER_KNOBS, defaults_for, defaults_for_stack, knob_moves, render_partner_ini, tunable,
)

KEY_VALUE = re.compile(r"^\s*([a-z_0-9]+)\s*=\s*(\S+)\s*$")


def _source_tree():
    """The simulator's own source tree, from nixchip's package.

    These checks used to read `applications/prefetcher/proj/`, which no longer exists — so they
    skipped, silently, and the guarantee they exist to provide went with them. `nixchip.packages.
    pythia` installs the whole tree beside its binary, so they can read the authoritative source
    again rather than a copy someone kept.
    """
    try:
        from flux_evaluator_champsim_bingo import resolve_source_tree

        return resolve_source_tree()
    except Exception as exc:                                              # noqa: BLE001
        pytest.skip(f"no ChampSim source tree available ({exc}); run inside `nix develop .#python`")


def _shipped(name: str) -> dict[str, str]:
    path = _source_tree() / "config" / f"{name}.ini"
    if not path.is_file():
        pytest.skip(f"{path} is not present")
    out = {}
    for line in path.read_text().splitlines():
        m = KEY_VALUE.match(line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


@pytest.mark.parametrize("name", sorted(PARTNER_KNOBS))
def test_recorded_defaults_match_the_shipped_ini(name):
    """Every default this study assumes is the one the simulator actually starts from."""
    shipped = _shipped(name)
    for knob, (mine, _space) in PARTNER_KNOBS[name].items():
        assert knob in shipped, f"{name}.ini has no {knob}; the knob name is wrong"
        theirs = shipped[knob]
        assert float(theirs) == float(mine), (
            f"{knob}: this study assumes {mine}, {name}.ini ships {theirs}")


@pytest.mark.parametrize("name", sorted(PARTNER_KNOBS))
def test_every_space_contains_its_own_default(name):
    """A search that cannot express the incumbent cannot report that the incumbent won."""
    for knob, (shipped, space) in PARTNER_KNOBS[name].items():
        assert shipped in space, f"{knob}'s space {space} omits its shipped value {shipped}"


def test_a_stack_exposes_every_partners_knobs_and_no_others():
    stack = ("bingo", "sms", "stride")
    knobs = set(tunable(stack))
    assert knobs == set(PARTNER_KNOBS["sms"]) | set(PARTNER_KNOBS["stride"])
    assert not any(k.startswith("bingo_") for k in knobs), "Bingo's knobs live in config.py"


def test_moves_change_exactly_one_knob():
    stack = ("bingo", "sms")
    current = defaults_for_stack(stack)
    for move in list(knob_moves(stack, current))[:12]:
        differing = [k for k in move if move[k] != current.get(k)]
        assert len(differing) == 1, f"a move changed {differing}"


def test_moves_round_robin_across_knobs():
    """Same reason `diverse_neighbours` does: a wave of six must not be six of one knob."""
    stack = ("bingo", "sms")
    current = defaults_for_stack(stack)
    touched = []
    for move in list(knob_moves(stack, current))[:6]:
        touched += [k for k in move if move[k] != current.get(k)]
    assert len(set(touched)) == 6, f"six moves touched only {len(set(touched))} knobs: {touched}"


def test_booleans_render_the_way_the_parser_reads_them():
    """`str(True)` is 'True'; the simulator's ini parser wants 'true'."""
    assert "= true" in render_partner_ini({"sms_enable_pref_buffer": True})
    assert "= false" in render_partner_ini({"sms_enable_pref_buffer": False})
    assert render_partner_ini({}) == ""


def test_a_prefetcher_with_no_space_contributes_nothing():
    assert defaults_for("ipcp") == {}
    assert tunable(("bingo", "ipcp")) == []


COMPILED_DEFAULTS_THAT_DIVERGE = {
    "sms_pht_size": (16384, 2048),
    "sms_region_size": (2048, 4096),
    "stride_num_trackers": (64, 256),
}


@pytest.mark.parametrize("knob,expected", sorted(COMPILED_DEFAULTS_THAT_DIVERGE.items()))
def test_the_compiled_default_really_does_differ_from_the_ini(knob, expected):
    """`knobs.cc` initialises these before any config file is read, and disagrees with the ini.

    Recorded as a test because the divergence is invisible and expensive: measuring a stack with
    no partner keys in the file uses the COMPILED value, while the study's reference writes the
    INI value, so the same stack scored 1.0699 composed and 1.0693 as its own reference. Every
    measurement now writes every knob explicitly. If this test starts failing because upstream
    reconciled them, that is good news — but the explicit write must stay, because relying on
    either default is what caused the confusion.
    """
    compiled, ini = expected
    src = _source_tree() / "src" / "knobs.cc"
    if not src.is_file():
        pytest.skip("knobs.cc not present in the source tree")
    m = re.search(rf"^\s*uint32_t\s+{knob}\s*=\s*(\d+)\s*;", src.read_text(), re.M)
    assert m, f"knobs.cc no longer initialises {knob}"
    assert int(m.group(1)) == compiled, (
        f"{knob}'s compiled default moved from {compiled} to {m.group(1)}")
    assert PARTNER_KNOBS[knob.split("_")[0]][knob][0] == ini, (
        f"this study records the ini value for {knob}, which should be {ini}")

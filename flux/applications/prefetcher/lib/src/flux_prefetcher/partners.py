"""The OTHER prefetchers' knobs, so composition can be tuned and not just switched on.

Bingo is not the only thing in the L2 slot. `bingo+sms` beat `bingo` alone by +0.44 geomean at
full length (D351) with `sms` running entirely at its shipped defaults, and `sms` has eight knobs
of its own. Every partner this study adds has been untuned until now, which means every
composition result so far is a lower bound.

The spaces below are deliberately coarse. A partner is worth a handful of measurements, not a
second full study: the aim is to find whether ITS defaults are leaving anything on the table, the
same question this study asks of Bingo's. Degree knobs come first in each space because prefetch
aggressiveness is the classic lever and the cheapest thing to be wrong about.

THE SIMULATOR'S COMPILED DEFAULTS ARE NOT THESE VALUES. `src/knobs.cc` initialises each knob
before any config file is read, and three of those initialisers disagree with the `.ini` the
project ships:

    sms_pht_size          16384 compiled,  2048 in sms.ini
    sms_region_size        2048 compiled,  4096 in sms.ini
    stride_num_trackers      64 compiled,   256 in stride.ini

So "enable sms and write no sms keys" and "enable sms at its shipped defaults" are DIFFERENT
DESIGNS, and a study that does one while reporting the other compares two things it believes are
one. It showed up as the same stack scoring 1.0699 when composed and 1.0693 as its own reference.
Every measurement now writes every partner knob explicitly; nothing is left to the compiled value.

DEFAULTS ARE COPIES, not reads. `proj/` is slated for deletion (D349), so the shipped values are
recorded here and `tests/unit/test_partner_knobs.py` checks them against the `.ini` files while
those still exist. A default that silently drifted from the simulator's would make every
"improvement" measured against the wrong reference.
"""

from __future__ import annotations

from typing import Any, Iterator

#: prefetcher -> knob -> (shipped default, values worth trying)
PARTNER_KNOBS: dict[str, dict[str, tuple[Any, tuple[Any, ...]]]] = {
    "sms": {
        "sms_pref_degree":        (4, (1, 2, 4, 8, 16)),
        "sms_pht_size":           (2048, (512, 1024, 2048, 4096, 8192)),
        "sms_pht_assoc":          (16, (4, 8, 16, 32)),
        "sms_region_size":        (4096, (1024, 2048, 4096)),
        "sms_ft_size":            (64, (16, 32, 64, 128, 256)),
        "sms_at_size":            (32, (8, 16, 32, 64, 128)),
        "sms_pref_buffer_size":   (256, (64, 128, 256, 512)),
    },
    "stride": {
        "stride_pref_degree":     (2, (1, 2, 4, 8, 16)),
        "stride_num_trackers":    (256, (64, 128, 256, 512, 1024)),
    },
    "ampm": {
        "ampm_pref_degree":       (4, (1, 2, 4, 8, 16)),
        "ampm_pred_degree":       (4, (1, 2, 4, 8)),
        "ampm_pb_size":           (64, (16, 32, 64, 128, 256)),
        "ampm_pref_buffer_size":  (256, (64, 128, 256, 512)),
    },
    "streamer": {
        "streamer_pref_degree":   (5, (1, 2, 4, 5, 8, 16)),
        "streamer_num_trackers":  (64, (16, 32, 64, 128, 256)),
    },
    "next_line": {
        "next_line_pref_degree":  (2, (1, 2, 4, 8)),
        "next_line_deltas":       (1, (1, 2, 3, 4)),
    },
    "sandbox": {
        "sandbox_pref_degree":            (4, (1, 2, 4, 8)),
        "sandbox_num_access_in_phase":    (256, (64, 128, 256, 512)),
        "sandbox_bloom_filter_size":      (2048, (512, 1024, 2048, 4096)),
        "sandbox_num_cycle_offsets":      (4, (2, 4, 8)),
    },
    "spp_ppf_dev": {
        "ppf_perc_threshold_hi":  (-5, (-15, -10, -5, 0, 5)),
        "ppf_perc_threshold_lo":  (-15, (-25, -20, -15, -10, -5)),
    },
    "power7": {
        "power7_default_streamer_degree": (4, (1, 2, 4, 8, 16)),
        "power7_explore_epoch":           (20000, (5000, 20000, 80000)),
        "power7_exploit_epoch":           (200000, (50000, 200000, 800000)),
    },
}


def defaults_for(name: str) -> dict[str, Any]:
    """Every knob `name` reads, at the value the project ships."""
    return {knob: shipped for knob, (shipped, _space) in PARTNER_KNOBS.get(name, {}).items()}


def defaults_for_stack(types: tuple[str, ...]) -> dict[str, Any]:
    """The shipped knobs for every partner in a stack. Bingo's own live in `config.py`."""
    out: dict[str, Any] = {}
    for name in types:
        out.update(defaults_for(name))
    return out


def tunable(types: tuple[str, ...]) -> list[str]:
    """Which knobs a stack exposes, in the order the spaces list them (degrees first)."""
    return [knob for name in types for knob in PARTNER_KNOBS.get(name, {})]


def knob_moves(types: tuple[str, ...], current: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Every one-knob change available to a stack, round-robin across knobs.

    Round-robin for the same reason `diverse_neighbours` uses it: a search that can afford six
    measurements a round and takes them from the head of a per-knob enumeration explores one knob
    and concludes the rest are settled.
    """
    per_knob: list[list[dict[str, Any]]] = []
    for name in types:
        for knob, (_shipped, space) in PARTNER_KNOBS.get(name, {}).items():
            here = current.get(knob, _shipped)
            moves = [{**current, knob: value} for value in space if value != here]
            if moves:
                per_knob.append(moves)
    while any(per_knob):
        for bucket in per_knob:
            if bucket:
                yield bucket.pop(0)


def render_partner_ini(knobs: dict[str, Any]) -> str:
    """The extra `.ini` lines a stack's partners need.

    Booleans render as `true`/`false`: the simulator's ini parser reads them as strings, and
    Python's `str(True)` would give it `True`, which it does not recognise.
    """
    lines = []
    for knob, value in sorted(knobs.items()):
        if isinstance(value, bool):
            value = "true" if value else "false"
        lines.append(f"{knob} = {value}")
    return "\n".join(lines) + ("\n" if lines else "")

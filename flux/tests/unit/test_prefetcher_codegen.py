"""Generating a prefetcher: what the model writes, and what is written around it.

The split is the design. A model writes ONE header containing the class; the `.cc` stub, the
dispatch branch, the knob declarations and now the standard includes are all mechanical. That is
D48's rule for RTL — generated leaves, deterministic wiring — and it earns its keep here twice
over: a model editing a 300-line dispatch it did not write can break every OTHER prefetcher in the
build, and a missing `#include <algorithm>` says nothing about whether the DESIGN is any good.
"""

from __future__ import annotations

from pathlib import Path

FLUX_ROOT = Path(__file__).resolve().parents[2]

import pytest  # noqa: E402

from flux_codegen_champsim_prefetcher import (  # noqa: E402
    InvalidPrefetcherName, build_prompt, check_name, class_name_for, ensure_includes,
    parse_proposal, repair_prompt, truncation_reason,
)

HEADER = '''#ifndef MYP_H
#define MYP_H
#include <cstdint>
#include <vector>
#include "prefetcher.h"
namespace knob { extern uint32_t myp_degree; }
class MypPrefetcher : public Prefetcher {
public:
  MypPrefetcher(std::string t) : Prefetcher(t) {}
  void invoke_prefetcher(uint64_t, uint64_t, uint8_t, uint8_t, std::vector<uint64_t> &) {}
  void dump_stats() {}
  void print_config() {}
};
#endif
'''


def test_a_name_becomes_a_class_deterministically():
    """The dispatch patch has to predict the class name without reading the header."""
    assert class_name_for("myp") == "MypPrefetcher"
    assert class_name_for("fft_stride") == "FftStridePrefetcher"


def test_names_that_cannot_be_a_class_a_file_and_a_knob_prefix_are_refused():
    for bad in ("My", "1st", "with-dash", "x", "UPPER", "a" * 30, ""):
        with pytest.raises(InvalidPrefetcherName):
            check_name(bad)
    check_name("good_name")


def test_a_fenced_reply_parses():
    reply = f"IDEA: something.\n\n```cpp\n{HEADER}```\nKNOBS: myp_degree=4"
    got = parse_proposal("myp", reply)
    assert got is not None and got.class_name == "MypPrefetcher"
    assert got.knobs == {"myp_degree": 4}
    assert got.rationale.startswith("something")


def test_an_unfenced_reply_still_parses():
    """Models drop the fence often enough that refusing would waste rounds."""
    assert parse_proposal("myp", HEADER) is not None


def test_a_knob_used_but_not_declared_is_still_declared():
    """An undeclared knob fails at LINK time, with a message that names no cause."""
    reply = f"```cpp\n{HEADER}```\nKNOBS:"
    got = parse_proposal("myp", reply)
    assert "myp_degree" in got.knobs, "a knob referenced in the header must be declared"


def test_truncation_is_diagnosed_as_truncation():
    """The first live run said 'no header' three times when the real cause was an output budget.

    The replies each held a good header that stopped mid-statement at exactly 1200 tokens.
    Nothing in 'no header' pointed at `DEFAULT_NUM_PREDICT`.
    """
    assert "fence" in (truncation_reason("IDEA: x\n\n```cpp\n#ifndef A_H\nclass A {") or "")
    assert "guard" in (truncation_reason("#ifndef A_H\n#define A_H\nclass A { };") or "")
    assert truncation_reason("") is not None
    assert truncation_reason(f"```cpp\n{HEADER}```") is None


def test_missing_standard_includes_are_added_mechanically():
    """The single most common way generated C++ fails, and a repair round reproduced it."""
    without = HEADER.replace('#include <vector>\n', '')
    using = without.replace("void dump_stats() {}",
                            "void dump_stats() { std::find(v.begin(), v.end(), 1); }")
    fixed = ensure_includes(using)
    assert "#include <algorithm>" in fixed
    assert "#include <vector>" in fixed


def test_the_include_guard_stays_first():
    """Inserting before the guard would make the header include itself on every pass."""
    fixed = ensure_includes(HEADER.replace("#include <vector>\n", "") + "std::deque<int> d;\n")
    assert fixed.splitlines()[0].startswith("#ifndef")


def test_nothing_is_added_when_nothing_is_missing():
    assert ensure_includes(HEADER) == HEADER


def test_the_prompt_states_the_interface_and_the_target():
    prompt = build_prompt("myp", beat="bingo", beat_geomean=1.0607)
    assert "invoke_prefetcher" in prompt
    assert "1.0607" in prompt and "bingo" in prompt
    assert "MypPrefetcher" in prompt and "MYP_H" in prompt
    assert "70 LINES" in prompt, "the length budget is what keeps generation affordable"


def test_the_repair_prompt_carries_the_diagnostic_and_the_previous_source():
    got = parse_proposal("myp", f"```cpp\n{HEADER}```")
    prompt = repair_prompt(got, "inc/myp.h:9:3: error: no matching function for call to 'find'")
    assert "no matching function" in prompt
    assert "MypPrefetcher" in prompt
    assert "compile fix, not a redesign" in prompt


def test_a_class_missing_a_pure_virtual_is_rejected_before_building():
    """`invalid new-expression of abstract class type` cost a whole live round.

    Sixty seconds to compile, then a full generation to repair — for a fact checkable in
    microseconds. The compiler's message does not even name the missing method; this does.
    """
    from flux_codegen_champsim_prefetcher import unbuildable_reason

    complete = parse_proposal("myp", HEADER)
    assert unbuildable_reason(complete) is None

    for dropped in ("void dump_stats() {}", "void print_config() {}"):
        broken = parse_proposal("myp", HEADER.replace(dropped, ""))
        reason = unbuildable_reason(broken)
        assert reason and "abstract" in reason, f"removing {dropped!r} was not caught"


def test_a_signature_that_overrides_nothing_is_rejected():
    """The subtle version: it compiles as a NEW method and leaves the base pure virtual."""
    from flux_codegen_champsim_prefetcher import unbuildable_reason

    drifted = parse_proposal("myp", HEADER.replace("std::vector<uint64_t> &",
                                                   "std::vector<int> &"))
    reason = unbuildable_reason(drifted)
    assert reason and "invoke_prefetcher" in reason


def test_a_class_that_does_not_inherit_prefetcher_is_rejected():
    from flux_codegen_champsim_prefetcher import unbuildable_reason

    orphan = parse_proposal("myp", HEADER.replace(": public Prefetcher", ""))
    assert orphan is None or "inherit" in (unbuildable_reason(orphan) or "")


def test_the_prompt_offers_composition_as_a_way_to_win():
    """A design that catches what bingo MISSES is a better target than beating it outright.

    Measured on these traces: composition was worth +0.0075 confirmed at full length, parameter
    tuning +0.0008. A loop that only measured inventions alone could not notice a good partner.
    """
    prompt = build_prompt("myp", beat="bingo", beat_geomean=1.0607)
    assert "ALONGSIDE" in prompt
    assert "MISSES" in prompt
    assert "spatial" in prompt, "the model should know what it is complementing"


def test_the_brief_describes_what_is_already_covered():
    """A model told only "beat bingo" rebuilds a spatial prefetcher, which is already there twice.

    The reference is the study's best CONFIRMED stack (bingo+sms+stride at 1.0640) rather than
    Bingo alone at 1.0439 — so the prompt has to say what all three already do, and where the
    opening is. Otherwise the easiest design to write is the one least likely to add anything.
    """
    prompt = build_prompt("myp", beat="bingo+sms+stride", beat_geomean=1.0640)
    for covered in ("bingo", "sms", "stride"):
        assert covered in prompt
    assert "NOT covered" in prompt
    for opening in ("pointer-chasing", "across pages", "history longer than one step"):
        assert opening in prompt, f"the brief never names {opening!r} as an opening"

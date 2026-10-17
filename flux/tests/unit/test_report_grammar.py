"""D397 phase 4: the shared closing grammar renders exactly the sections the demos
converged on, and the refusal cap is honest about its tail."""

from __future__ import annotations

from flux_loop.report import banner, established, not_established, notes, refused


def test_banner_fills_to_width():
    b = banner("THE ANSWER")
    assert b.startswith("══ THE ANSWER ═") and len(b) == 79
    assert set(b) <= set("═ ANSWERTH")


def test_sections_render_and_empty_sections_vanish():
    assert established(["a lesson"]) == ["WHAT THIS RUN ESTABLISHED", "  - a lesson"]
    assert not_established(["no P&R"]) == ["NOT ESTABLISHED", "  - no P&R"]
    assert notes(["prefer x"])[0].startswith("OPERATOR GUIDANCE (1 note")
    for empty in (established([]), not_established([]), refused([]), notes([])):
        assert empty == []


def test_refused_caps_with_a_counted_tail_never_a_silent_drop():
    items = [{"config": f"c{i}", "why": "over budget"} for i in range(9)]
    lines = refused(items, render=lambda i: f"{i['config']}: {i['why']}")
    assert lines[0] == "REFUSED (9)"
    assert lines[1] == "  - c0: over budget" and len(lines) == 8
    assert lines[-1] == "  ... and 3 more"


def test_an_item_that_spans_lines_is_one_line():
    """D1183: a refusal's reason holding a check's output ("...\n0 failing of 0") was lines of its own in the
    log, each stamped as an event of its own."""
    assert refused(["x (generate): nothing built\n0 failing of 0\n(previous patch rejected)"]) == [
        "REFUSED (1)", "  - x (generate): nothing built 0 failing of 0 (previous patch rejected)"]
    assert established(["a\n  b"]) == ["WHAT THIS RUN ESTABLISHED", "  - a b"] and not_established(["c\nd"])[1] == "  - c d"

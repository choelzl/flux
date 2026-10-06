"""The setup screen before the loop screen (D587): a pure form, driven by key codes."""

from __future__ import annotations

import curses

from flux_tui.setup import AUTHORS, FIELDS, SetupForm


def keys(form: SetupForm, text: str) -> None:
    for ch in text:
        form.handle(ord(ch))


def test_the_form_takes_a_prompt_files_and_choices_and_starts(tmp_path):
    spec = tmp_path / "spec.md"
    spec.write_text("x")
    f = SetupForm()
    assert f.handle(curses.KEY_F5) is None and "prompt is empty" in f.message, "no start without a prompt"
    keys(f, "an 8-bit popcount")
    f.handle(10)                                   # Enter in the prompt is a new line
    keys(f, "at 2 GHz")
    assert f.prompt == "an 8-bit popcount\nat 2 GHz"
    f.handle(9)                                    # Tab: to the files
    assert f.field == "files"
    keys(f, str(tmp_path / "nope.md"))
    f.handle(10)
    assert f.files == [] and "no such file" in f.message
    f.handle(27)                                   # Esc clears the half-typed path, does not leave
    assert f.path_input == ""
    keys(f, str(tmp_path / "sp"))
    f.handle(9)                                    # Tab completes the path
    assert f.path_input == str(spec)
    f.handle(10)
    assert f.files == [str(spec)]
    f.handle(9)                                    # Tab with nothing typed: to the skills
    assert f.field == "skills"
    keys(f, str(tmp_path))                         # a folder without SKILL.md is not a skill
    f.handle(10)
    assert f.skills == [] and "not a skill" in f.message
    f.handle(27)
    f.handle(9)                                    # on to the author
    assert f.field == "author"
    f.handle(curses.KEY_RIGHT)
    assert f.author == AUTHORS[1]
    f.handle(curses.KEY_DOWN)
    assert f.passes == 0 and "until stopped" in "".join(t for _k, t in f.lines(120))   # the default (D593)
    f.handle(curses.KEY_RIGHT)
    assert f.passes == 1
    f.handle(curses.KEY_LEFT)
    f.handle(curses.KEY_LEFT)
    assert f.passes == 0
    f.handle(curses.KEY_DOWN)
    f.handle(32)
    assert f.screen_only is True
    s = f.settings()
    assert s["workdir"] == "out/ask_an_8_bit_popcount_at_2_ghz" and s["files"] == [str(spec)] and s["review"] is True
    while f.field != "start":
        f.handle(curses.KEY_DOWN)
    assert f.handle(10) == "start"


def test_the_file_list_removes_and_esc_leaves(tmp_path):
    f = SetupForm(prompt="x", files=["a", "b"], focus=FIELDS.index("files"))
    f.handle(curses.KEY_DC)
    assert f.files == ["a"] and "removed b" in f.message
    f.focus = FIELDS.index("author")
    assert f.handle(27) == "quit"
    g = SetupForm(prompt="keep me")
    assert g.handle(27) is None and g.prompt == "keep me", "Esc never wipes a prompt"


def test_the_form_draws_every_field_and_marks_the_focus():
    f = SetupForm(prompt="a multiplier", files=["/x/spec.pdf"], author="opencode", focus=FIELDS.index("author"))
    rows = f.lines(100)
    text = "\n".join(t for _k, t in rows)
    assert "a multiplier" in text and "spec.pdf" in text and "< opencode >" in text and "[ Start ]" in text
    assert [k for k, t in rows if "Author" in t] == ["focus"]


def test_the_form_keeps_its_footer_and_its_focus_on_a_small_screen():
    """D857, from an external review: at 60x18 with a six-line prompt, Start, Quit and the keys fell
    below the screen and a focused field could be out of sight. The footer stays; the fields scroll
    to the focus; long text wraps or shows its end; a screen too small says so."""
    prompt = "\n".join(f"line {i}: a fairly long requirement that goes past the right edge" for i in range(6))
    for name in FIELDS:
        f = SetupForm(prompt=prompt, files=["/x/" + "long-name-" * 8 + ".pdf"], focus=FIELDS.index(name))
        for width, height in ((59, 14), (39, 8), (79, 20)):
            rows = f.screen(width, height)
            assert len(rows) <= height and all(len(t) <= width for _k, t in rows), (name, width, height)
            text = "\n".join(t for _k, t in rows)
            assert "Start" in text and "Quit" in text, (name, width, height)
            assert any(k == "focus" for k, _t in rows), f"the focus is on the screen: {name} at {width}x{height}"
    f = SetupForm(prompt="x", focus=FIELDS.index("workdir"), workdir="/a/" + "deep/" * 30 + "end")
    (row,) = [t for k, t in f.screen(59, 14) if k == "focus"]
    assert "…" in row and row.rstrip().endswith("end▏"), "a long value shows its end, where one types"
    assert "too small" in f.screen(30, 2)[0][1]


def test_tab_completes_a_path_in_the_skills_field_as_in_files(tmp_path):
    """D903, from an external review: Tab in Skills left the field instead of completing the path."""
    skill = tmp_path / "my_unique_skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("x")
    f = SetupForm(prompt="x", focus=FIELDS.index("skills"))
    keys(f, str(tmp_path / "my_un"))
    f.handle(9)
    assert f.field == "skills" and f.path_input == str(skill) + "/"
    f.handle(10)
    assert f.skills == [str(skill) + "/"]
    f.handle(9)                                    # nothing typed: Tab moves on
    assert f.field == "author"


def test_the_pass_cap_is_an_editable_number_and_until_stopped_a_choice():
    """D902, from an external review: each digit replaced the whole value, so typing 10 saved 0 --
    until stopped. Now the first digit replaces, the next append, Backspace edits, a leading 0 is
    refused, and until stopped is chosen (u, or Left from 1), never typed by accident."""
    def typed(seq: str, start: int = 0) -> int:
        f = SetupForm(prompt="x", passes=start, focus=FIELDS.index("passes"))
        keys(f, seq)
        return f.settings()["passes"]

    assert [typed(s) for s in ("12", "10", "20", "0", "05", "100")] == [12, 10, 20, 0, 5, 100]
    assert typed("7", start=12) == 7, "the first digit on arriving replaces the value"
    f = SetupForm(prompt="x", focus=FIELDS.index("passes"))
    keys(f, "12")
    f.handle(127)                                  # Backspace edits
    keys(f, "5")
    assert f.passes == 15
    f.handle(curses.KEY_BACKSPACE)
    f.handle(curses.KEY_BACKSPACE)
    assert f.passes == 0 and "until stopped" in f.message
    keys(f, "3")
    f.handle(ord("u"))                             # the explicit choice
    assert f.passes == 0 and "< until stopped >" in "".join(t for _k, t in f.lines(100))
    f.handle(curses.KEY_RIGHT)
    assert f.passes == 1
    f.handle(9)
    f.handle(curses.KEY_BTAB)                      # back on the field: a digit replaces again
    keys(f, "42")
    assert f.settings()["passes"] == 42


def test_the_form_keeps_every_printable_character(tmp_path):
    """D902: the keys come wide (get_wch) -- a str per character -- and printable Unicode is kept in
    the prompt, a path and the directory; control keys stay keys."""
    from flux_tui.input import key_code

    text = "Zürich café λ ≤ 10ns"
    f = SetupForm()
    for ch in text:
        f.handle(key_code(ch))
    assert f.settings()["prompt"] == text
    folder = tmp_path / "Zürich λ"
    folder.mkdir()
    (folder / "spéc.md").write_text("x")
    f.handle(key_code("\t"))                       # a control character is the key it names
    assert f.field == "files"
    for ch in str(folder / "spéc.md"):
        f.handle(key_code(ch))
    f.handle(key_code("\n"))
    assert f.files == [str(folder / "spéc.md")]
    f.focus = FIELDS.index("workdir")
    for ch in "/tmp/λ":
        f.handle(key_code(ch))
    assert f.settings()["workdir"].endswith("/tmp/λ")


def test_a_long_prompt_shows_its_cursor_row_at_the_minimum_size():
    """D902: at 40x14 a wrapped prompt taller than the view showed its first rows while one typed
    on the last; the view follows the cursor, and no row is cut at the right edge."""
    f = SetupForm()
    keys(f, "a fairly long requirement " * 12 + "END")
    for width, height in ((39, 11), (39, 9), (59, 14)):
        rows = f.screen(width, height)
        assert any(k == "focus" and t.rstrip().endswith("END▏") for k, t in rows), (width, height, rows)
        assert all(len(t) <= width for _k, t in rows)

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

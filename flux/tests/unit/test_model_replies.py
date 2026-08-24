"""What a model's reply is read as (D603): JSON with raw newlines or inside prose, and
search/replace blocks, are read, since an unreadable repair costs a whole rewrite."""

from __future__ import annotations

from flux_loop import apply_patch, parse_patch
from flux_loop.model import _json


def test_json_with_raw_newlines_inside_strings_is_read():
    reply = '{"artifact": "module m;\n  assign y = a;\nendmodule", "why": "x"}'
    assert _json(reply) == {"artifact": "module m;\n  assign y = a;\nendmodule", "why": "x"}


def test_json_inside_prose_is_read():
    reply = 'Here is the fix:\n{"edits": [{"find": "a", "replace": "b"}], "why": "w"}\nThis removes the latch.'
    assert _json(reply)["edits"] == [{"find": "a", "replace": "b"}]


def test_plain_json_and_nothing_stay_as_they_were():
    assert _json('{"a": 1}') == {"a": 1}
    assert _json("no object here") is None
    assert _json("[1, 2]") == [1, 2]


def test_search_replace_blocks_are_edits():
    source = "module m(input a, output y);\n  always_comb begin\n    logic t;\n    y = a;\n  end\nendmodule\n"
    reply = ("Move the declaration out of the block.\n"
             "<<<<<<< SEARCH\n  always_comb begin\n    logic t;\n=======\n  logic t;\n  always_comb begin\n>>>>>>> REPLACE\n")
    edits, why = parse_patch(reply)
    assert why == "search/replace blocks" and len(edits) == 1
    patched, err = apply_patch(source, edits)
    assert err is None and "  logic t;\n  always_comb begin\n    y = a;" in patched


def test_a_reply_with_neither_is_still_refused_with_its_reason():
    assert parse_patch("I would rewrite the whole module.") == (None, "patch reply was not a JSON object")

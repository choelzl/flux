"""`flux example KIND NAME` (D598, D825: the worked examples; `flux new NAME` writes the baseline alone). Every kind
must load under `flux task check`; the sweep, which needs no model and no EDA tool, must run to
a decision."""

from __future__ import annotations

import json

import pytest

from flux_cli.main import main


@pytest.mark.parametrize("kind", ["python", "rtl", "sweep", "tune"])
def test_every_kind_writes_a_document_that_loads(tmp_path, capsys, kind):
    assert main(["example", kind, "demo", "--dir", str(tmp_path / kind)]) == 0
    doc = tmp_path / kind / "demo/problem.yaml"
    assert doc.is_file() and (doc.parent / "README.md").is_file()
    text = doc.read_text()
    assert "__NAME__" not in text and "id:" not in text, "the folder is the id (D786)"
    capsys.readouterr()
    assert main(["task", "check", str(doc)]) == 0
    assert "task demo:" in capsys.readouterr().out


def test_the_sweep_runs_to_a_decision_without_a_model(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("FLUX_TRACE_ROOT", str(tmp_path / "traces"))
    main(["example", "sweep", "primes", "--dir", str(tmp_path / "p")])
    answer = tmp_path / "answer.json"
    rc = main(["task", "run", str(tmp_path / "p/primes/problem.yaml"), "--passes", "20", "--json", str(answer)])
    got = json.loads(answer.read_text())
    assert rc == 0 and got["decision"]["name"].startswith(("odd_sieve", "slice_sieve"))
    assert len(got["frontier"]) == 6 and not got["refused"]


def test_a_bad_name_or_a_used_folder_is_refused(tmp_path, capsys):
    assert main(["new", "9lives", "--dir", str(tmp_path / "a")]) == 2
    (tmp_path / "b" / "ok").mkdir(parents=True)
    (tmp_path / "b" / "ok" / "x").write_text("mine")
    assert main(["new", "ok", "--dir", str(tmp_path / "b")]) == 2
    assert "not empty" in capsys.readouterr().out


def test_a_search_policy_of_your_own_beside_the_document(tmp_path, capsys, monkeypatch):
    """`flow.dse` may name `module:Class`, a Policy subclass beside the document; a misspelled class is a load error (D602)."""
    import yaml

    monkeypatch.setenv("FLUX_TRACE_ROOT", str(tmp_path / "traces"))
    main(["example", "sweep", "mine", "--dir", str(tmp_path / "m")])
    (tmp_path / "m" / "mine" / "every_other.py").write_text(
        "from dataclasses import dataclass\n"
        "from flux_loop.dse import Policy, points\n\n\n"
        "@dataclass\n"
        "class EveryOther(Policy):\n"
        "    name: str = 'every_other'\n"
        "    stride: int = 2\n\n"
        "    def walk(self, problem, state, space, seen):\n"
        "        state.say(f'  every_other: stride {self.stride}')\n"
        "        yield self.batch(problem, state, points(space)[::self.stride], seen)\n")
    doc_path = tmp_path / "m" / "mine/problem.yaml"
    for dse in ("every_other:EveryOther", [{"policy": "every_other:EveryOther", "stride": 3}]):
        doc = yaml.safe_load(doc_path.read_text())
        doc["flow"]["orchestrate"]["policy"] = dse
        doc_path.write_text(yaml.safe_dump(doc, sort_keys=False))
        answer = tmp_path / "a.json"
        assert main(["task", "run", str(doc_path), "--passes", "20", "--json", str(answer),
                     "--db", str(tmp_path / f"r{len(str(dse))}.db")]) == 0
        got = json.loads(answer.read_text())
        assert len(got["frontier"]) == (3 if isinstance(dse, str) else 2), got["frontier"]
    doc = yaml.safe_load(doc_path.read_text())
    doc["flow"]["orchestrate"] = "every_other:Nope"
    doc_path.write_text(yaml.safe_dump(doc, sort_keys=False))
    capsys.readouterr()
    assert main(["task", "check", str(doc_path)]) == 2
    assert "every_other:Nope" in capsys.readouterr().out


def test_the_tune_kind_runs_to_a_decision_without_a_model(tmp_path, monkeypatch):
    """Knobs go straight into the gate's and stage's commands, measured one at a time (D608)."""
    monkeypatch.setenv("FLUX_TRACE_ROOT", str(tmp_path / "traces"))
    main(["example", "tune", "mm", "--dir", str(tmp_path / "t")])
    doc = (tmp_path / "t" / "mm/problem.yaml").read_text()
    assert "workers: 1" in doc and "{block}" in doc
    answer = tmp_path / "answer.json"
    assert main(["task", "run", str(tmp_path / "t/mm/problem.yaml"), "--passes", "20", "--json", str(answer)]) == 0
    got = json.loads(answer.read_text())
    assert len(got["frontier"]) == 15 and not got["refused"] and got["decision"]["knobs"]["block"] in (64, 128, 256)


def test_the_banner_says_when_no_model_is_needed(tmp_path, capsys, monkeypatch):
    """A sweep or tuning never calls a model, and the banner says so (D608)."""
    from flux_loop import load_task
    from flux_loop.task import model_use

    for kind, needs in (("tune", False), ("sweep", False), ("python", True), ("rtl", True)):
        main(["example", kind, f"k_{kind}", "--dir", str(tmp_path / kind)])
        task = load_task(str(tmp_path / kind / f"k_{kind}/problem.yaml"))
        assert bool(model_use(task)) is needs, (kind, model_use(task))
    monkeypatch.setenv("FLUX_TRACE_ROOT", str(tmp_path / "traces"))
    capsys.readouterr()
    main(["task", "run", str(tmp_path / "tune/k_tune/problem.yaml"), "--passes", "20"])
    assert "model: none needed" in capsys.readouterr().out


def test_a_sweep_phase_moves_only_its_knobs(tmp_path, monkeypatch):
    """A sweep phase with `knobs` sweeps only those, holding the others, and the next phase starts where it ended (D608)."""
    import yaml

    monkeypatch.setenv("FLUX_TRACE_ROOT", str(tmp_path / "traces"))
    main(["example", "tune", "mm", "--dir", str(tmp_path / "t")])
    doc_path = tmp_path / "t" / "mm/problem.yaml"
    doc = yaml.safe_load(doc_path.read_text())
    doc["flow"]["orchestrate"]["policy"] = [{"name": "coarse", "policy": "sweep", "knobs": ["block"]},
                          {"name": "fine", "policy": "gradient", "hold": ["block"], "steps": 4}]
    doc["budget"]["steps"] = 4
    doc_path.write_text(yaml.safe_dump(doc, sort_keys=False))
    answer = tmp_path / "a.json"
    assert main(["task", "run", str(doc_path), "--passes", "20", "--json", str(answer)]) == 0
    got = json.loads(answer.read_text())
    blocks = {row["knobs"]["block"] for row in got["frontier"]}
    orders = {row["knobs"]["order"] for row in got["frontier"] if row["knobs"]["block"] != got["decision"]["knobs"]["block"]}
    assert blocks == {16, 32, 64, 128, 256} and orders == {"ijk"}, got["frontier"]
    assert len(got["frontier"]) < 15


def test_a_resumed_sweep_with_every_point_on_record_rests_instead_of_spinning(tmp_path, capsys, monkeypatch):
    """D695: its pass ended on "nothing left to do", not a rest, so pass after pass began at once."""
    monkeypatch.setenv("FLUX_TRACE_ROOT", str(tmp_path / "traces"))
    main(["example", "sweep", "primes", "--dir", str(tmp_path / "p")])
    doc, db = str(tmp_path / "p/primes/problem.yaml"), str(tmp_path / "p.db")
    assert main(["task", "run", doc, "--passes", "20", "--db", db]) == 0
    capsys.readouterr()
    assert main(["task", "run", doc, "--passes", "5", "--db", db]) == 0
    out = capsys.readouterr().out
    assert "sweep: 0 point(s) of 6" in out and "at rest: the search measured every point" in out
    assert "── pass 2 ──" not in out, "at rest with nothing to draft: the remaining passes are not run"


def test_the_report_of_a_tuning_ranks_every_point_with_its_knobs(tmp_path, monkeypatch):
    """A DSE's report is read from its points, ranked by the decision's rule; one objective gets no front, saying why (D609)."""
    monkeypatch.setenv("FLUX_TRACE_ROOT", str(tmp_path / "traces"))
    main(["example", "tune", "mm", "--dir", str(tmp_path / "t")])
    answer, db = tmp_path / "a.json", str(tmp_path / "mm.db")
    main(["task", "run", str(tmp_path / "t/mm/problem.yaml"), "--passes", "20", "--json", str(answer), "--db", db])
    out = tmp_path / "r.html"
    assert main(["report", db, "--out", str(out)]) == 0
    page = out.read_text()
    assert "Every point measured (15" in page and "<th>block</th><th>order</th>" in page
    first = page.split("Every point measured", 1)[1].split("<tr><td>1</td><td>", 1)[1].split("<", 1)[0]
    assert first == json.loads(answer.read_text())["decision"]["name"]
    assert "one objective: there is no front to draw" in page


def test_new_writes_the_baseline_and_nothing_of_a_case(tmp_path, capsys):
    """D825: `flux new NAME` is a loop's folder as it starts -- the skeleton problem.yaml with every
    part present and what goes there, a README of the folder's parts, an empty library/ -- no
    script, no golden model, no example; `flux task check` says what is missing."""
    import yaml

    assert main(["new", "mine", "--dir", str(tmp_path)]) == 0
    d = tmp_path / "mine"
    assert sorted(p.name for p in d.iterdir()) == ["README.md", "library", "problem.yaml"] and not any((d / "library").iterdir())
    text = (d / "problem.yaml").read_text()
    doc = yaml.safe_load(text)
    assert set(doc) == {"statement", "contract", "flow", "objectives", "budget"} and "# language:" in text, "D832: optional"
    for part in ("test", "measure", "generate", "orchestrate", "knowledge"):
        assert f"# {part} --" in text, part
    assert not any(w in text.lower() for w in ("prime", "adder", "popcount", "matrix")), "nothing of a case"
    readme = (d / "README.md").read_text()
    assert all(x in readme for x in ("`out/`", "`workbench/`", "`library/`", "`runs/`")) and "mine" in readme
    capsys.readouterr()
    assert main(["task", "check", str(d), "--no-sandbox"]) != 0, "a baseline to fill in: the check says so"

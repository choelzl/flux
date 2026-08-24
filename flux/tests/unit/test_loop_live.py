"""The loop, live, through the CLI (D559, review 2 R12): one `flux task run` per world, the
way an operator launches it -- the document, the record, the flags -- with a scripted model
where a model is asked and the real tools where a tool is. The digits example runs in the
core suite (no tool); every application is `heavy` and skips where its tools are missing,
which `flux task check` is the judge of. What these pin: the documents load and run end to
end through the CLI, decide something, and say so in the report.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

FLUX = Path(__file__).resolve().parents[2]
APPS = FLUX / "applications"


def flux(*args: str, timeout: float = 900.0) -> subprocess.CompletedProcess:
    """`flux <args>` as a subprocess of this interpreter: the CLI's own entry, the same
    packages, the same PATH the test runs with."""
    env = dict(os.environ)
    env.setdefault("FLUX_TRACE_ROOT", str(Path(env.get("TMPDIR", "/tmp")) / "flux-live-traces"))
    return subprocess.run([sys.executable, "-c", "import sys; from flux_cli.main import main; sys.exit(main(sys.argv[1:]))", *args],
                          capture_output=True, text=True, timeout=timeout, cwd=str(FLUX), env=env)


def _doc(app: str, tmp_path: Path, **patch) -> Path:
    """A copy of the application's document with `params:`/`budget:`/... patched, written
    beside a scratch record so the world resolves the same way `flux task run` resolves it."""
    src = APPS / app / f"{app}.problem.yaml"
    doc = yaml.safe_load(src.read_text())
    for key, value in patch.items():
        doc[key] = {**doc.get(key, {}), **value} if isinstance(value, dict) and isinstance(doc.get(key), dict) else value
    doc["campaign"] = {}                                     # a test's ask is not the document's campaign
    sheet = (doc.get("knowledge") or {}).get("sheet") if isinstance(doc.get("knowledge"), dict) else None
    if sheet and not os.path.isabs(sheet):
        doc["knowledge"]["sheet"] = str(src.parent / sheet)  # read beside the ORIGINAL document
    out = tmp_path / f"{app}.problem.yaml"
    out.write_text(yaml.safe_dump(doc, sort_keys=False))
    return out


def _tools_ok(doc: Path) -> bool:
    """`flux task check` exits 0 only when every tool the document needs is on PATH."""
    return flux("task", "check", str(doc), timeout=120).returncode == 0


def _replies(tmp_path: Path, replies: list[str]) -> Path:
    p = tmp_path / "replies.json"
    p.write_text(json.dumps(replies))
    return p


def test_the_digits_example_runs_through_the_cli_and_writes_its_artifact(tmp_path):
    replies = _replies(tmp_path, ["\n".join(str(i) for i in range(10)) + "\n"])
    out = tmp_path / "digits.txt"
    r = flux("task", "run", str(FLUX / "core/loop/examples/digits.task.json"), "--db", str(tmp_path / "d.db"),
             "--replies", str(replies), "--out", str(out), timeout=300)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    assert "ADMITTED digits: digits#1" in r.stdout and "DECISION digits#1 [gate" in r.stdout
    assert "WHAT THIS RUN ESTABLISHED" in r.stdout
    assert out.read_text().split() == [str(i) for i in range(10)]
    again = flux("task", "run", str(FLUX / "core/loop/examples/digits.task.json"), "--db", str(tmp_path / "d.db"),
                 "--replies", str(replies), timeout=300)
    assert again.returncode == 0 and "digits#1" in again.stdout               # resumed from the record
    # D578: with no --out the artifact lands beside the DOCUMENT, under out/, never in the cwd
    import shutil as _sh

    doc = tmp_path / "digits.task.json"
    _sh.copy(FLUX / "core/loop/examples/digits.task.json", doc)
    r = flux("task", "run", str(doc), "--replies", str(replies), timeout=300)
    assert r.returncode == 0 and (tmp_path / "out" / "digits.txt").is_file() and (tmp_path / "out" / "digits.db").is_file()
    assert not (FLUX / "digits.txt").exists() and not (FLUX / "digits.db").exists()


@pytest.mark.heavy
def test_every_application_document_passes_task_check():
    for app in sorted(p.name for p in APPS.iterdir() if p.is_dir()):
        r = flux("task", "check", str(APPS / app / f"{app}.problem.yaml"), timeout=120)
        assert r.returncode in (0, 1), f"{app}: {r.stderr[-800:]}"
        assert "flow (D542)" in r.stdout or r.returncode == 1, f"{app}: {r.stdout[-800:]}"


@pytest.mark.heavy
def test_the_bankmap_document_runs_solver_only(tmp_path):
    doc = _doc("bankmap", tmp_path, budget={"steps": 2})
    if not _tools_ok(doc):
        pytest.skip("the bank-mapping study's tools are not on PATH")
    r = flux("task", "run", str(doc), "--db", str(tmp_path / "b.db"), "--replies", str(_replies(tmp_path, ["{}"])))
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    assert "DECISION" in r.stdout and "WHAT THIS RUN ESTABLISHED" in r.stdout


@pytest.mark.heavy
def test_the_interconnect_mapping_document_runs_screen_only(tmp_path):
    doc = _doc("interconnect_mapping", tmp_path)
    if not _tools_ok(doc):
        pytest.skip("the interconnect mapping study's tools are not on PATH")
    r = flux("task", "run", str(doc), "--db", str(tmp_path / "i.db"), "--screen-only",
             "--replies", str(_replies(tmp_path, ["{}"])))
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    assert "DECISION" in r.stdout


@pytest.mark.heavy
def test_the_macarray_document_screens_one_pe_end_to_end(tmp_path):
    """Verilator verifies it, Yosys screens it, the decision names it: one PE of the space
    through the document's own DSE line (`dse: sweep`, D553)."""
    doc = _doc("macarray", tmp_path, params={"multipliers": ["behavioral"], "reducers": ["tree"], "pipelines": [0],
                                             "include_invented": False},
               budget={"steps": 1, "passes": 1, "finalists": 0})
    if not _tools_ok(doc):
        pytest.skip("verilator/yosys/openroad are not on PATH")
    r = flux("task", "run", str(doc), "--db", str(tmp_path / "m.db"), "--screen-only",
             "--replies", str(_replies(tmp_path, ["{}"])), timeout=1200)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    assert "1 of 1 correct" in r.stdout and "DECISION behavioral-tree-p0" in r.stdout
    assert "no model: the space is measured" not in r.stdout                  # steps 1: no invention round asked


@pytest.mark.heavy
def test_the_nlu_document_runs_one_part_with_a_scripted_model(tmp_path):
    """One operator, the rules orchestrator, a scripted model that answers the prototype
    stage with a known-good exp: the part is prototyped, transpiled, built and admitted
    through `flux task run`, the way the campaign runs."""
    sys.path.insert(0, str(FLUX / "tests" / "unit"))
    from test_nlu_family import knobbed_exp

    doc = _doc("nlu", tmp_path, parts=["exp"],
               params={"ops": ["exp"], "test_rounds": 0, "seed": 1},
               roles={"orchestrator": "rules"}, flow={"critique": "none"},
               budget={"steps": 3, "repair_attempts": 1, "prototype_attempts": 2, "finalists": 0})
    if not _tools_ok(doc):
        pytest.skip("verilator/yosys are not on PATH")
    replies = _replies(tmp_path, [json.dumps({"prototype": knobbed_exp()})])
    r = flux("task", "run", str(doc), "--db", str(tmp_path / "n.db"), "--screen-only", "--replies", str(replies), timeout=1800)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-3000:]
    assert "ADMITTED exp" in r.stdout, r.stdout[-4000:]


@pytest.mark.heavy
def test_the_prefetcher_document_runs_when_its_simulator_and_traces_are_there(tmp_path):
    doc = _doc("prefetcher", tmp_path, params={"measurements": 2, "llm_round": 0}, budget={"steps": 4, "finalists": 0})
    if not _tools_ok(doc):
        pytest.skip("ChampSim or the traces are not on this machine")
    r = flux("task", "run", str(doc), "--db", str(tmp_path / "p.db"), "--screen-only",
             "--replies", str(_replies(tmp_path, ["{}"])), timeout=3600)
    # screen-only: the CLI says NO CANDIDATE SURVIVED (nothing confirmed at full length) and
    # exits 1 -- the honest verdict; what this pins is the run through ChampSim to a decision
    assert r.returncode in (0, 1), r.stdout[-3000:] + r.stderr[-3000:]
    assert "DECISION (screen stage)" in r.stdout and "geomean speedup" in r.stdout, r.stdout[-3000:]


@pytest.mark.heavy
def test_the_mul8_example_runs_with_no_code_of_its_own(tmp_path):
    """D579: a document, a prompt, a golden model -- the two rtl commands as its gate and
    stages -- through `flux task run`, a scripted model writing the module."""
    doc = FLUX / "applications/mul8/mul8.problem.yaml"
    if not _tools_ok(doc):
        pytest.skip("verilator/yosys/openroad are not on PATH")
    module = ("module mul8(input logic signed [7:0] a, input logic signed [7:0] w, output logic signed [15:0] p);\n"
              "  wire signed [15:0] t;\n  assign t = a * w;\n  assign p = t;\nendmodule\n")
    r = flux("task", "run", str(doc), "--db", str(tmp_path / "m.db"), "--screen-only", "--steps", "1",
             "--replies", str(_replies(tmp_path, [module])), "--out", str(tmp_path / "mul8.sv"), timeout=900)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    assert "ADMITTED mul8" in r.stdout and "DECISION mul8#1" in r.stdout and (tmp_path / "mul8.sv").read_text().strip() == module.strip()

"""The loop through the CLI, one `flux task run` per world (D559), with a scripted model and the
real tools. The digits example runs in the core suite; every application is `heavy` and skips
where `flux task check` finds its tools missing. Each document loads, runs end to end, decides
something and says so in the report.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

FLUX = Path(__file__).resolve().parents[2]
APPS = FLUX / "applications"


def flux(*args: str, timeout: float = 900.0) -> subprocess.CompletedProcess:
    """`flux <args>` as a subprocess of this interpreter, with the same packages and PATH."""
    env = dict(os.environ)
    env.setdefault("FLUX_TRACE_ROOT", str(Path(env.get("TMPDIR", "/tmp")) / "flux-live-traces"))
    return subprocess.run([sys.executable, "-c", "import sys; from flux_cli.main import main; sys.exit(main(sys.argv[1:]))", *args],
                          capture_output=True, text=True, timeout=timeout, cwd=str(FLUX), env=env)


def _doc(app: str, tmp_path: Path, **patch) -> Path:
    """A copy of the application's document with sections patched, written beside a scratch
    record so the world resolves as `flux task run` resolves it."""
    src = APPS / app / "problem.yaml"
    doc = yaml.safe_load(src.read_text())
    for key, value in patch.items():
        doc[key] = {**doc.get(key, {}), **value} if isinstance(value, dict) and isinstance(doc.get(key), dict) else value
    know = (doc.get("flow") or {}).get("knowledge")
    sheet = know.get("sheet") if isinstance(know, dict) else None
    if sheet and not os.path.isabs(sheet):
        know["sheet"] = str(src.parent / sheet)  # read beside the ORIGINAL document
    out = tmp_path / f"{app}.problem.yaml"
    out.write_text(yaml.safe_dump(doc, sort_keys=False))
    return out


def _tools_ok(doc: Path) -> bool:
    """`flux task check` exits 0 only when every tool the document needs is on PATH. A document
    that does not load or cannot be answered is a failure, never a skip."""
    r = flux("task", "check", str(doc), timeout=120)
    assert "not a task" not in r.stdout and "NOT ANSWERABLE" not in r.stdout, r.stdout[-2000:]
    return r.returncode == 0


def _replies(tmp_path: Path, replies: list[str]) -> Path:
    p = tmp_path / "replies.json"
    p.write_text(json.dumps(replies))
    return p


def test_the_digits_example_runs_through_the_cli_and_writes_its_artifact(tmp_path):
    replies = _replies(tmp_path, ["\n".join(str(i) for i in range(10)) + "\n"])
    out = tmp_path / "digits.txt"
    r = flux("task", "run", str(FLUX / "core/loop/examples/digits/problem.json"), "--db", str(tmp_path / "d.db"),
             "--replies", str(replies), "--out", str(out), timeout=300)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    assert "ADMITTED digits: digits#1" in r.stdout and "DECISION digits#1 [gate" in r.stdout
    assert "WHAT THIS RUN ESTABLISHED" in r.stdout
    assert out.read_text().split() == [str(i) for i in range(10)]
    again = flux("task", "run", str(FLUX / "core/loop/examples/digits/problem.json"), "--db", str(tmp_path / "d.db"),
                 "--replies", str(replies), timeout=300)
    assert again.returncode == 0 and "digits#1" in again.stdout               # resumed from the record
    # with no --out the artifact lands beside the document, under out/, never in the cwd (D578)
    import shutil as _sh

    home = tmp_path / "digits"                       # D786: the folder is the problem, its name the id
    home.mkdir()
    _sh.copy(FLUX / "core/loop/examples/digits/problem.json", home / "problem.json")
    r = flux("task", "run", str(home), "--replies", str(replies), timeout=300)
    assert r.returncode == 0 and (home / "out" / "digits.txt").is_file() and (home / "out" / "digits.db").is_file()
    assert not (FLUX / "digits.txt").exists() and not (FLUX / "digits.db").exists()


@pytest.mark.heavy
def test_every_application_document_passes_task_check():
    for app in sorted(p.name for p in APPS.iterdir() if p.is_dir()):
        r = flux("task", "check", str(APPS / app / "problem.yaml"), timeout=120)
        assert r.returncode in (0, 1), f"{app}: {r.stderr[-800:]}"
        assert "flow (D542)" in r.stdout or r.returncode == 1, f"{app}: {r.stdout[-800:]}"


@pytest.mark.heavy
def test_the_bankmap_document_runs_solver_only(tmp_path):
    doc = _doc("bankmap", tmp_path, budget={"steps": 2})
    if not _tools_ok(doc):
        pytest.skip("the bank-mapping study's tools are not on PATH")
    r = flux("task", "run", str(doc), "--db", str(tmp_path / "b.db"), "--passes", "3")      # a round a pass (D799)
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
    """One PE of the space verified by Verilator, screened by Yosys and decided, via `dse: sweep` (D553)."""
    import yaml

    doc = _doc("macarray", tmp_path, budget={"passes": 1, "batch": 4})
    raw = yaml.safe_load(doc.read_text())                 # D798: the space is the document's own
    raw["flow"]["orchestrate"]["space"] = {"multiplier": ["behavioral"], "reducer": ["tree"], "pipeline": [0]}
    doc.write_text(yaml.safe_dump(raw, sort_keys=False))
    if not _tools_ok(doc):
        pytest.skip("verilator/yosys/openroad are not on PATH")
    r = flux("task", "run", str(doc), "--db", str(tmp_path / "m.db"), "--screen-only", timeout=1200)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    assert "DECISION behavioral-tree-pipeline=0" in r.stdout and "model: none needed" in r.stdout


@pytest.mark.heavy
def test_the_prefetcher_document_runs_when_its_simulator_and_traces_are_there(tmp_path):
    doc = _doc("prefetcher", tmp_path, budget={"steps": 2},
               flow={"generate": {"catalog": [str(APPS / "prefetcher" / "bingo_default.ini")]}, "select": {"finalists": 0}})   # no model
    short = re.sub(r"--warmup \d+ --sim \d+", "--warmup 100000 --sim 1000000", doc.read_text())   # minutes, not hours
    doc.write_text(short)
    for f in ("bingo.py", "knobs.md", "bingo_default.ini"):
        (tmp_path / f).write_text((APPS / "prefetcher" / f).read_text())
    (tmp_path / "traces").symlink_to(APPS / "prefetcher" / "traces")
    if not any((tmp_path / "traces").glob("*.gz")) or not _tools_ok(doc):
        pytest.skip("ChampSim or the traces are not on this machine")
    r = flux("task", "run", str(doc), "--db", str(tmp_path / "p.db"), "--screen-only", "--passes", "1", timeout=3600)
    assert r.returncode in (0, 1), r.stdout[-3000:] + r.stderr[-3000:]
    assert "DECISION" in r.stdout and "geomean_speedup" in r.stdout, r.stdout[-3000:]


@pytest.mark.heavy
def test_the_mul8_example_runs_with_no_code_of_its_own(tmp_path):
    """A document with a prompt, a golden model and the rtl commands as gate and stages, a scripted model writing the module (D579)."""
    doc = FLUX / "applications/mul8/problem.yaml"
    if not _tools_ok(doc):
        pytest.skip("verilator/yosys/openroad are not on PATH")
    module = ("module mul8(input logic signed [7:0] a, input logic signed [7:0] w, output logic signed [15:0] p);\n"
              "  wire signed [15:0] t;\n  assign t = a * w;\n  assign p = t;\nendmodule\n")
    r = flux("task", "run", str(doc), "--db", str(tmp_path / "m.db"), "--screen-only", "--steps", "1",
             "--replies", str(_replies(tmp_path, [module])), "--out", str(tmp_path / "mul8.sv"), timeout=900)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    assert "ADMITTED mul8" in r.stdout and "DECISION mul8#1" in r.stdout and (tmp_path / "mul8.sv").read_text().strip() == module.strip()


@pytest.mark.heavy
def test_the_adder16_dse_runs_with_no_world(tmp_path):
    """Every point of a generated space is proved and screened, and the decision is one of them (D581)."""
    doc = FLUX / "applications/adder16/problem.yaml"
    if not _tools_ok(doc):
        pytest.skip("verilator/yosys are not on PATH")
    # one pass: a sweep with no model waits once every point is measured (D593), so cap it
    r = flux("task", "run", str(doc), "--db", str(tmp_path / "a.db"), "--screen-only", "--passes", "1",
             "--out", str(tmp_path / "adder16.v"), timeout=900)
    assert r.returncode in (0, 1), r.stdout[-3000:] + r.stderr[-2000:]
    assert "sweep: 12 point(s) of 12" in r.stdout and "DECISION" in r.stdout, r.stdout[-3000:]
    assert "module adder16" in (tmp_path / "adder16.v").read_text()


@pytest.mark.heavy
def test_the_npu_gemm_sweep_picks_the_smallest_array_that_makes_its_cycles(tmp_path):
    """An accelerator sized by ZigZag from a document (D625): 32 PEs is the least area at <= 500 cycles."""
    doc = FLUX / "applications/npu_gemm/problem.yaml"
    r = flux("task", "run", str(doc), "--db", str(tmp_path / "n.db"), "--passes", "1",
             "--out", str(tmp_path / "n.yaml"), timeout=900)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    assert "DECISION pe_x=32-gbuf_kb=16" in r.stdout and "latency_cycles=341" in r.stdout, r.stdout[-3000:]

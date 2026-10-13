"""D1024: `fluxpreset pto` -- a pyCircuit design against a PTO-style spec's ASL model. The interface
(units, op codes, data types) is read from the spec; each unit's reference vectors are the spec's
answers, one ASLRef run a unit, a vector the model asserts on set aside and the run resumed, kept in
a cache by the spec's content; a design is built with pyCircuit and each wrong vector is a line."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from flux_presets import pto

FIXTURE = Path(__file__).parent / "fixtures" / "pto-spec-mini"

#: answers the driver's vectors as the fixture's spec would: U8/S16 sums, differences and complements,
#: FP16 refused; the model asserts on TSUB of S16 0x8000 (after printing the answers before it)
FAKE_ASLREF = r'''import re, sys
text = open(sys.argv[1]).read()
unit = re.search(r"InstructionContractExecute_(\w+)\(2", text).group(1)
width = {"U8": 8, "S16": 16, "FP16": 16}
for t, a, b in re.findall(r"FluxVec\(TileDataType_(\w+), (\d+), (\d+)\);", text):
    a, b, w = int(a), int(b), width[t]
    if t == "FP16":
        print("FLUXREF F")
        continue
    if unit == "TSUB" and t == "S16" and a == 0x8000:
        print("ASL Dynamic error (DE_DAF): Assertion failed: TSUB overflow", file=sys.stderr)
        sys.exit(1)
    y = {"TADD": a + b, "TSUB": a - b, "TNOT": ~a}[unit] & ((1 << w) - 1)
    print(f"FLUXREF R 0x{y:016x}")
'''


@pytest.fixture
def spec(tmp_path):
    copy = tmp_path / "spec"
    shutil.copytree(FIXTURE, copy)
    return copy


@pytest.fixture
def aslref(tmp_path):
    fake = tmp_path / "aslref"
    fake.write_text(f"#!{sys.executable}\n" + FAKE_ASLREF)
    fake.chmod(0o755)
    return str(fake)


def test_the_interface_is_read_from_the_spec(spec, capsys):
    assert pto.data_types(spec) == [("TileDataType_FP16", 16), ("TileDataType_U4X2", 4), ("TileDataType_U8", 8),
                                    ("TileDataType_S16", 16)]
    assert pto.main(["ops", "--spec", str(spec)]) == 0
    out = capsys.readouterr().out
    assert [ln.split()[:3] for ln in out.splitlines()[1:4]] == [["0", "TADD", "2"], ["1", "TNOT", "1"], ["2", "TSUB", "2"]]
    assert "U4X2" not in out and "2      U8              8" in out, "a 4-bit type is no type of the interface"
    assert pto.main(["ops", "--names", "--spec", str(spec)]) == 0
    assert capsys.readouterr().out.splitlines() == ["TADD Add corresponding elements of two Local Tiles.",
                                                    "TNOT Complement every element of one Local Tile.",
                                                    "TSUB Subtract corresponding elements of two Local Tiles."]


def test_references_are_the_specs_answers_set_aside_where_it_asserts_and_cached(spec, aslref, tmp_path, capsys):
    cache = tmp_path / "cache"
    args = ["ref", "TADD", "TSUB", "--spec", str(spec), "--cache", str(cache), "--aslref", aslref, "--per-type", "5"]
    assert pto.main(args) == 0
    out = capsys.readouterr().out
    assert "PTO-TILE-TADD: 15 vectors, 5 refused by the spec, 0 the spec's model asserts on" in out, out
    assert "PTO-TILE-TSUB: 15 vectors, 5 refused by the spec, 1 the spec's model asserts on" in out
    assert "S16: 1 -- assert" not in out and "S16: 1 -- ASL Dynamic error" in out, "the model's own words"
    (doc,) = [json.loads(p.read_text()) for p in cache.rglob("TSUB-*.json")]
    s16 = [v for v in doc["vectors"] if v["type"] == "TileDataType_S16"]
    assert [bool(v.get("spec_error")) for v in s16] == [False, False, False, True, False], "the run resumed after it"
    assert s16[4]["y"] == (s16[4]["a"] - s16[4]["b"]) & 0xFFFF
    assert pto.main([*args[:-4], "--aslref", "/nonexistent", "--per-type", "5"]) == 0, "from the cache: no ASLRef"
    assert (cache / pto.spec_digest(spec) / "model.asl").read_text().count("TileElementBits") == 1


def test_a_unit_the_driver_cannot_drive_costs_no_other(spec, aslref, tmp_path, capsys):
    broken = tmp_path / "aslref-broken"
    broken.write_text(f"#!{sys.executable}\nimport re, sys\ntext = open(sys.argv[1]).read()\n"
                      "if 'Execute_TNOT(2' in text:\n    print('ASL Type error (TE_BC): no match', file=sys.stderr); sys.exit(1)\n"
                      + FAKE_ASLREF.split("\n", 1)[1])
    broken.chmod(0o755)
    assert pto.main(["ref", "all", "--spec", str(spec), "--cache", str(tmp_path / "c"), "--aslref", str(broken),
                     "--per-type", "2"]) == 0
    got = capsys.readouterr()
    assert "TNOT" in got.err and "Type error" in got.err
    assert "PTO-TILE-TADD: 6 vectors" in got.out and "PTO-TILE-TSUB" in got.out


DESIGN = '''from pycircuit import CycleAwareCircuit, CycleAwareDomain, cas, mux, wire_of


def build(m: CycleAwareCircuit, domain: CycleAwareDomain) -> None:
    op = cas(domain, m.input("op", width=8), cycle=0)
    dtype = cas(domain, m.input("dtype", width=5), cycle=0)
    a = cas(domain, m.input("a", width=64), cycle=0)
    b = cas(domain, m.input("b", width=64), cycle=0)
    y = mux(op == 1, ~a, a + b)                     # TNOT right; TSUB (code 2) wrongly adds
    m.output("y", wire_of(y))
    m.output("fault", wire_of(dtype == 0))          # FP16 refused


build.__pycircuit_name__ = "pto_elementwise"
'''


@pytest.mark.skipif(shutil.which("pycircuit") is None, reason="pyCircuit (nixchip's pycircuit) is not on PATH")
def test_a_design_is_built_and_each_wrong_vector_said(spec, aslref, tmp_path, capsys):
    design = tmp_path / "design.py"
    design.write_text(DESIGN)
    common = ["--spec", str(spec), "--cache", str(tmp_path / "cache"), "--aslref", aslref, "--per-type", "3"]
    assert pto.main(["check", str(design), "--units", "TADD", "TNOT", *common]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "0 failing of 18"
    assert pto.main(["check", str(design), "--units", "TSUB", *common]) == 1
    out = capsys.readouterr().out.splitlines()
    # U8 and S16 differences but 0 - 0; FP16 refused, as the design does
    assert out == ["TSUB U8 a=0x1 b=0x1: expected y=0x0, got 0x2", "TSUB U8 a=0xff b=0x1: expected y=0xfe, got 0x0",
                   "TSUB S16 a=0x1 b=0x1: expected y=0x0, got 0x2", "TSUB S16 a=0xffff b=0x1: expected y=0xfffe, got 0x0",
                   "4 failing of 9"], out
    assert pto.main(["check", str(design), "--measure", *common]) == 0
    assert capsys.readouterr().out.splitlines() == ["vectors_failing=4", "units_passing=2", "units=3"]
    design.write_text("def build(m, domain):\n    m.output('y', undefined_name)\n")
    assert pto.main(["check", str(design), "--build-only", "--spec", str(spec)]) == 3
    assert "did not build" in capsys.readouterr().out

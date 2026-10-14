"""A pyCircuit design checked against a PTO-style spec's ASL model, unit by unit (D1024).

    fluxpreset pto ops --spec {home}/spec                       # the interface: op codes, data type codes
    fluxpreset pto ref TADD TSUB --spec {home}/spec             # the units' reference vectors (ASLRef, cached)
    fluxpreset pto check {artifact} --units TADD --spec {home}/spec   # the design against them:
                                                                # each wrong vector, then "N failing of M"

THE INTERFACE a design keeps -- one combinational datapath for the spec's elementwise units:

    inputs  op (8 bits)     the unit, by its code from `ops`
            dtype (5 bits)  the data type, by its position in the spec's `TileDataType` enumeration
            a, b (64 bits)  the source elements, in the low bits (b unused by a one-source unit)
    outputs y (64 bits)     the destination element, in the low bits (the type's width is compared)
            fault (1 bit)   1 when the spec refuses the operation (an unsupported data type, ...);
                            y is then not compared

The design is a pyCircuit file defining `build(m, domain)`; its outputs are read in the cycle its
inputs are driven. The units are the spec's `elementwise-tile-tile` ones taking one or two sources
(not TCVT, whose second type is no input here), coded in mnemonic order; the types are those of 8
bits or more.

A reference vector is the spec's own answer: a generated ASL driver configures three one-element
tiles of the type, writes a and b, asks `InstructionContractOperandsLegal_<UNIT>` and runs
`InstructionContractExecute_<UNIT>`, and prints the destination element or a fault -- one ASLRef
run per unit for all its vectors (special values and seeded random ones, the same each time).
They are kept in `--cache` (~/.cache/flux/pto-ref) under the spec's content, so a design is
checked without ASLRef until the spec changes. `check` builds the design with the testbench
(`pycircuit build --target cpp`), one vector a cycle, and compares what it prints. Files: the
cache, and a temporary folder.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from ._pto import Unit, dependents, find, load

DRIVER_VERSION = 1
MARK = "FLUXREF"


def catalog() -> list[dict]:
    """This preset's catalog entries (D1024)."""
    from ._entries import N_FAILING, param
    return [{"id": "pto-check", "role": "check", "title": "The design matches the spec's ASL model",
             "what": "Builds a pyCircuit design (pycircuit, the C++ model) and drives it with each unit's reference "
                     "vectors -- the spec's own answers from ASLRef, cached per spec -- one wrong vector a line, "
                     "then the count. The interface: `fluxpreset pto ops`.",
             "run": "fluxpreset pto check {artifact} --units {units} --spec {home}/spec",
             "params": {"units": param("Units (mnemonics, comma-separated; all: every one)", "all")},
             "needs": ["pycircuit", "aslref"], "pass": N_FAILING, "languages": ["python"], "kinds": [], "short": "spec"}]


# ---------------------------------------------------------------- the spec's interface
def data_types(spec: Path) -> list[tuple[str, int]]:
    """The spec's data types in enumeration order, each with its element width (bits)."""
    text = "".join(p.read_text(errors="replace") for p in sorted((spec / "asl").rglob("*.asl"))
                   if "TileDataType" in p.read_text(errors="replace"))
    m = re.search(r"type\s+TileDataType\s+of\s+enumeration\s*\{([^}]*)\}", text)
    names = re.findall(r"TileDataType_\w+", m.group(1)) if m else []
    width: dict[str, int] = {}
    body = re.search(r"func\s+TileElementBits\s*\([^)]*\)[^\n]*\n\s*begin(.*?)\n\s*end;\s*\n\s*end;", text, re.S)
    for names_said, bits in re.findall(r"when\s+([\w,\s]+?)\s*=>\s*return\s+(\d+);", body.group(1) if body else ""):
        for n in re.findall(r"TileDataType_\w+", names_said):
            width[n] = int(bits)
    return [(n, width.get(n, 0)) for n in names]


def arity(unit: Unit) -> int:
    """How many tiles the unit's `InstructionContractExecute_<M>` takes (destination included); 0 when
    it or its `OperandsLegal` takes anything else (TCMP's comparison: no port of the interface)."""
    text = unit.path.read_text(errors="replace")
    counts = []
    for hook in ("Execute", "OperandsLegal"):
        m = re.search(rf"func\s+InstructionContract{hook}_{re.escape(unit.mnemonic)}\s*\(([^)]*)\)", text)
        params = [p for p in (m.group(1).split(",") if m else []) if p.strip()]
        if not m or any(not re.search(r":\s*TileIndex\s*$", p.strip()) for p in params):
            return 0
        counts.append(len(params))
    return counts[0] if counts[0] == counts[1] else 0


def ops(units: dict[str, Unit]) -> list[tuple[int, Unit, int]]:
    """The interface's units: (op code, unit, its sources). Every elementwise unit has a code, in
    mnemonic order, those the interface leaves out (TCMP's comparison, TCVT's second type, three
    sources) too -- a code moves only when the spec adds or removes an elementwise unit."""
    every = sorted((u for u in units.values() if "elementwise-tile-tile" in u.classification), key=lambda u: u.mnemonic)
    out = [(code, u, arity(u) - 1) for code, u in enumerate(every) if u.mnemonic != "TCVT"]
    return [(code, u, n) for code, u, n in out if n in (1, 2)]


def spec_digest(spec: Path) -> str:
    """The spec's content, as far as the model is concerned: asl/ and scripts/ (how it is assembled)."""
    h = hashlib.sha256(f"driver {DRIVER_VERSION}".encode())
    for folder in ("asl", "scripts"):
        for p in sorted((spec / folder).rglob("*")) if (spec / folder).is_dir() else []:
            if p.is_file() and "__pycache__" not in p.parts:
                h.update(str(p.relative_to(spec)).encode() + b"\0" + p.read_bytes())
    return h.hexdigest()[:20]


# ---------------------------------------------------------------- the vectors
def vectors(unit: Unit, sources: int, types: list[tuple[str, int]], per_type: int, seed: int) -> list[dict]:
    """Each type of 8 bits or more: special values, then seeded random ones -- the same every call."""
    rng = random.Random(f"{seed}:{unit.mnemonic}")
    out: list[dict] = []
    for code, (name, width) in enumerate(types):
        if width < 8:
            continue
        mask, top = (1 << width) - 1, 1 << (width - 1)
        special = [(0, 0), (1, 1), (mask, 1), (top, top), (top - 1, 1), (mask, mask), (2, 3)]
        pairs = special[:per_type] + [(rng.getrandbits(width), rng.getrandbits(width)) for _ in range(max(0, per_type - len(special)))]
        for a, b in pairs:
            out.append({"op": unit.mnemonic, "dtype": code, "type": name, "width": width, "a": a, "b": b if sources == 2 else 0})
    return out


def _driver(unit: Unit, sources: int, vecs: list[dict]) -> str:
    call = "2, 0, 1" if sources == 2 else "2, 0"
    lines = [f"""func FluxVec(dt: TileDataType, a: integer, b: integer)
begin
    ResetProfileState();
    let width = TileElementBits(dt);
    let rows = (1024 DIV (width * 4)) as integer {{0..65535}};
    for index = 0 to 2 looplimit 3 do
        ConfigureTile(index as TileIndex, 128, rows, 4, 1, 1, dt, TileLayout_RowMajor);
    end;
    WriteTileElement(0, 0, 0, Zeros{{PTO_XLEN}} + a);
    WriteTileElement(1, 0, 0, Zeros{{PTO_XLEN}} + b);
    if InstructionContractOperandsLegal_{unit.mnemonic}({call}) then
        InstructionContractExecute_{unit.mnemonic}({call});
        print("{MARK} R ");
        println(ReadTileElement(2, 0, 0));
    else
        println("{MARK} F");
    end;
end;

func main() => integer
begin"""]
    lines += [f"    FluxVec({v['type']}, {v['a']}, {v['b']});" for v in vecs]
    lines += ["    return 0;", "end;", ""]
    return "\n".join(lines)


def _mirror(spec: Path, into: Path) -> Path:
    root = into / "spec"
    root.mkdir()
    for entry in spec.resolve().iterdir():
        if entry.name not in ("build", ".cache"):
            (root / entry.name).symlink_to(entry)
    return root


_PREPARE = r"""
import sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, str(root))
from scripts.asl_units import generate_source_order
from scripts.asl_validation_shards import EMPTY_VALIDATION_SHARD
(root / "build").mkdir(exist_ok=True)
(root / "build/flux-order.txt").write_text("\n".join(generate_source_order(root)) + "\n")
(root / "build/flux-validation.asl").write_text(EMPTY_VALIDATION_SHARD)
"""


def model(spec: Path, cache: Path, timeout: float) -> Path | str:
    """The spec's assembled model with an empty validation shard, assembled once per spec content
    (as its own runner assembles it), or why it could not be."""
    out = cache / spec_digest(spec) / "model.asl"
    if out.is_file():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="flux-pto-", ignore_cleanup_errors=True) as d:
        root = _mirror(spec, Path(d))
        try:
            steps = [[sys.executable, "-c", _PREPARE, str(root)],
                     [sys.executable, str(root / "scripts/generate-asl-decoders"), "--kind", "decoder"],
                     [str(root / "scripts/assemble-asl"), "--order", str(root / "build/flux-order.txt"),
                      "--decoder", str(root / "build/flux-decoders.asl"), "--output", str(root / "build/flux-model.asl")]]
            for i, cmd in enumerate(steps):
                done = subprocess.run(cmd, cwd=root, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=timeout)
                if done.returncode != 0:
                    return f"assembling the spec's model failed at {Path(cmd[1] if i else 'prepare').name}: {(done.stderr or done.stdout)[-400:]}"
                if i == 1:
                    (root / "build/flux-decoders.asl").write_text(done.stdout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"assembling the spec's model failed: {exc}"
        tmp = out.with_suffix(".tmp")
        tmp.write_text((root / "build/flux-model.asl").read_text() + "\n" + (root / "build/flux-validation.asl").read_text())
        tmp.replace(out)
    return out


def _answer(unit: Unit, sources: int, batch: list[dict], model_text: str, aslref: str, timeout: float) -> str | None:
    """Each vector's answer, in place: one ASLRef run for the unit's vectors, then another after each
    vector the model asserts on (it ends the run) -- that vector is the spec's own error, not compared;
    after three on one data type, the rest of that type too. None, or why they could not be answered.
    (A run is mostly ASLRef reading the model, ~8 s: a unit's vectors share one.)"""
    rest: list[dict] = list(batch)
    asserted: dict[str, int] = {}
    while rest:
        with tempfile.TemporaryDirectory(prefix="flux-pto-", ignore_cleanup_errors=True) as d:
            prog = Path(d) / f"{unit.mnemonic}.asl"
            prog.write_text(model_text + "\n" + _driver(unit, sources, rest))
            try:
                done = subprocess.run([aslref, str(prog)], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                                      timeout=timeout)
            except subprocess.TimeoutExpired:
                return f"ASLRef timed out after {timeout:g} s on {unit.mnemonic}'s vectors"
        said = [ln.split(MARK, 1)[1].split() for ln in done.stdout.splitlines() if MARK in ln]
        for v, got in zip(rest, said):
            v["fault"] = got[0] == "F"
            if not v["fault"]:
                v["y"] = int(got[1], 16) & ((1 << v["width"]) - 1)
        if done.returncode == 0 and len(said) == len(rest):
            return None
        text = done.stderr + "\n" + done.stdout
        if len(said) >= len(rest) or "Dynamic error" not in text:
            err = [ln for ln in text.splitlines() if "error" in ln.lower()]
            return f"ASLRef on {unit.mnemonic}'s vectors: exit {done.returncode}; " + " ".join(err[-2:])[:400]
        lines = text.splitlines()
        at = next((i for i, ln in enumerate(lines) if "Dynamic error" in ln), len(lines) - 1)
        why = " ".join(" ".join(lines[max(0, at - 3):at + 3]).split())
        why = (why[why.find("assert"):] if "assert" in why else why)[:400]
        bad = rest[len(said)]
        bad["spec_error"] = why
        asserted[bad["type"]] = asserted.get(bad["type"], 0) + 1
        rest = rest[len(said) + 1:]
        if asserted[bad["type"]] >= 3:            # the model asserts on this type whatever the values
            for v in rest:
                if v["type"] == bad["type"]:
                    v["spec_error"] = why
            rest = [v for v in rest if v["type"] != bad["type"]]
    return None


def references(spec: Path, picked: list[tuple[Unit, int]], a: argparse.Namespace) -> list[dict] | str:
    """Each unit's vectors with the spec's answer ({"unit", "vectors"}), from the cache or ASLRef -- every
    unit's data types answered on one pool of `--jobs` threads."""
    types, digest = data_types(spec), spec_digest(spec)
    out: list[dict | None] = []
    todo: list[tuple[int, Unit, int, list[dict], Path]] = []
    for i, (unit, sources) in enumerate(picked):
        vecs = vectors(unit, sources, types, a.per_type, a.seed)
        sig = hashlib.sha256(json.dumps(vecs, sort_keys=True).encode()).hexdigest()[:12]
        path = Path(a.cache) / digest / f"{unit.mnemonic}-{sig}.json"
        if path.is_file():
            out.append(json.loads(path.read_text()))
            continue
        out.append(None)
        todo.append((i, unit, sources, vecs, path))
    if not todo:
        return [d for d in out if d is not None]
    m = model(spec, Path(a.cache), a.timeout)
    if isinstance(m, str):
        return m
    aslref = a.aslref or os.environ.get("ASLREF") or shutil.which("aslref")
    if not aslref:
        return "ASLRef is not available: put `aslref` on PATH or give --aslref"
    model_text = m.read_text()
    print(f"reference vectors: {len(todo)} {'unit' if len(todo) == 1 else 'units'} on {min(a.jobs, len(todo))} {'thread' if min(a.jobs, len(todo)) == 1 else 'threads'}, one ASLRef run each "
          "(and one more after each vector the spec's model asserts on)", file=sys.stderr)

    def one(t: tuple) -> str | None:
        i, unit, sources, vecs, path = t
        why = _answer(unit, sources, vecs, model_text, aslref, a.timeout)
        if why:
            return why
        doc = {"unit": unit.id, "spec": digest, "vectors": vecs}
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc))
        tmp.replace(path)                         # each unit kept once answered: a failing one costs no other
        out[i] = doc
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, a.jobs)) as pool:
        bad = [e for e in pool.map(one, todo) if e]
    for e in bad:
        print(e, file=sys.stderr)
    return [d for d in out if d is not None]


def _picked(a: argparse.Namespace) -> tuple[dict[str, Unit], list[tuple[Unit, int]]] | None:
    spec = Path(a.spec)
    units = load(spec)
    if not units:
        print(f"no spec units in {spec} (a spec checkout: its asl/ folder with PTO-INSTRUCTION headers)", file=sys.stderr)
        return None
    menu = ops(units)
    names = [n for raw in a.units for n in raw.replace(",", " ").split()]
    if not names or names == ["all"]:
        return units, [(u, n) for _c, u, n in menu]
    codes = {u.id: (u, n) for _c, u, n in menu}
    out: list[tuple[Unit, int]] = []
    for name in names:
        u = find(units, name)
        if u is None:
            print(f"no unit {name!r} in {spec}", file=sys.stderr)
            return None
        for x in [u, *(dependents(units, u) if getattr(a, "dependents", False) else [])]:
            if x.id in codes and codes[x.id] not in out:
                out.append(codes[x.id])
        if u.id not in codes:
            print(f"{u.mnemonic} is not a unit of the interface (`fluxpreset pto ops`)", file=sys.stderr)
            return None
    return units, out


def _references(a: argparse.Namespace, picked: list[tuple[Unit, int]]) -> list[dict] | None:
    got = references(Path(a.spec), picked, a)
    if isinstance(got, str):
        print(got, file=sys.stderr)
        return None
    return got


# ---------------------------------------------------------------- the commands
def _ops(a: argparse.Namespace) -> int:
    got = _picked(argparse.Namespace(**{**vars(a), "units": []}))
    if got is None:
        return 2
    _units, menu = got
    if a.names:                                   # D1024: the scopes a loop's passes pick from
        for u, _n in menu:
            print(f"{u.mnemonic} {u.summary}")
        return 0
    print("op  unit       sources  summary")
    for code, (u, n) in ((c, (u, n)) for c, u, n in ops(_units)):
        print(f"{code:<3} {u.mnemonic:<10} {n}        {u.summary}")
    print("\ndtype  type            bits")
    for code, (name, width) in enumerate(data_types(Path(a.spec))):
        if width >= 8:
            print(f"{code:<6} {name.removeprefix('TileDataType_'):<15} {width}")
    return 0


def _ref(a: argparse.Namespace) -> int:
    got = _picked(a)
    if got is None:
        return 2
    refs = _references(a, got[1])
    if refs is None:
        return 1
    for r in refs:
        errs = [v for v in r["vectors"] if v.get("spec_error")]
        faults = sum(1 for v in r["vectors"] if v.get("fault") and not v.get("spec_error"))
        print(f"{r['unit']}: {len(r['vectors'])} vectors, {faults} refused by the spec, {len(errs)} the spec's model asserts on")
        for t in sorted({v["type"] for v in errs}):
            first = next(v for v in errs if v["type"] == t)
            print(f"  {t.removeprefix('TileDataType_')}: {sum(1 for v in errs if v['type'] == t)} -- {first['spec_error'][:200]}")
    return 0


#: a one-vector testbench: `pycircuit build` then makes the design's C++ model, which RUNNER drives
_TB = """from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pycircuit import Tb
from pycircuit.design import testbench
from design import build  # noqa: E402,F401


@testbench
def tb(t: Tb) -> None:
    t.timeout(4)
    t.drive("op", 0, at=0)
    t.drive("dtype", 0, at=0)
    t.drive("a", 0, at=0)
    t.drive("b", 0, at=0)
    t.finish(at=1)
"""

#: every vector through the model from a file, one a cycle: what a testbench pyCircuit generates per
#: vector costs more than linear time to build (7 minutes for 4840; this, seconds)
_RUNNER = """#include <cstdint>
#include <fstream>
#include <iostream>
#include <cpp/pyc_tb.hpp>
#include "@HEADER@"

int main(int argc, char **argv) {
  pyc::gen::@CLASS@ dut;
  pyc::cpp::Testbench<pyc::gen::@CLASS@> tb(dut);
  std::ifstream in(argv[1]);
  unsigned long long op, dtype, a, b;
  for (unsigned long long i = 0; in >> op >> dtype >> a >> b; ++i) {
    dut.op = pyc::cpp::Wire<@OP@>({op});
    dut.dtype = pyc::cpp::Wire<@DTYPE@>({dtype});
    dut.a = pyc::cpp::Wire<@A@>({a});
    dut.b = pyc::cpp::Wire<@B@>({b});
    tb.runSteps(1);
    std::cout << "V" << i << " y=0x" << std::hex << (unsigned long long)dut.y.value() << " fault=0x"
              << (unsigned long long)dut.fault.value() << std::dec << "\\n";
  }
  return 0;
}
"""


def _ports(header: str) -> dict[str, int]:
    """The model's ports and their widths, as its header declares them (`pyc::cpp::Wire<N> name`)."""
    return {name: int(w) for w, name in re.findall(r"Wire<(\d+)>\s+(\w+)\s*[;{=]", header)}


def _check(a: argparse.Namespace) -> int:
    design = Path(a.design)
    if not design.is_file():
        print(f"no design at {design}", file=sys.stderr)
        return 2
    got = _picked(a)
    if got is None:
        return 2
    units, picked = got
    codes = {u.mnemonic: code for code, u, _n in ops(units)}
    if a.build_only:
        vecs: list[dict] = []
    else:
        refs = _references(a, picked)
        if refs is None:
            return 1
        vecs = [{**v, "code": codes[v["op"]]} for r in refs for v in r["vectors"]]
    if not shutil.which(a.pycircuit):
        print(f"pyCircuit is not available: `{a.pycircuit}` on PATH (nixchip's pycircuit, in Flux's dev shell)", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="flux-pto-", ignore_cleanup_errors=True) as d:
        work = Path(d)
        shutil.copyfile(design, work / "design.py")
        (work / "tb_design.py").write_text(_TB)
        try:
            built = subprocess.run([a.pycircuit, "build", "tb_design.py", "--out-dir", str(work / "out"), "--target", "cpp"],
                                   cwd=work, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=a.timeout)
        except subprocess.TimeoutExpired:
            print(f"pycircuit build timed out after {a.timeout:g} s", file=sys.stderr)
            return 3
        manifest = work / "out" / "cpp_project_manifest.json"
        if built.returncode != 0 or not manifest.is_file():
            text = (built.stderr + "\n" + built.stdout).strip()
            lines = [ln for ln in text.splitlines() if ln.strip()]
            pick = [ln for ln in lines if re.search(r"[Ee]rror|Traceback|line \d+", ln)] or lines
            print("the design did not build (pycircuit build):\n" + "\n".join(pick[-25:]))
            return 3
        man = json.loads(manifest.read_text())
        header = Path(man["headers"][0])
        ports = _ports(header.read_text())
        missing = [p for p in ("op", "dtype", "a", "b", "y", "fault") if p not in ports]
        if missing:
            print(f"the design has no port {', '.join(missing)}: the interface is op, dtype, a, b -> y, fault "
                  f"(it has {', '.join(sorted(ports)) or 'none'})")
            return 3
        if a.build_only:
            print("0 failing of 0")
            return 0
        runner = work / "runner.cpp"
        runner.write_text(_RUNNER.replace("@HEADER@", header.name).replace("@CLASS@", man["target_name"])
                          .replace("@OP@", str(ports["op"])).replace("@DTYPE@", str(ports["dtype"]))
                          .replace("@A@", str(ports["a"])).replace("@B@", str(ports["b"])))
        rt = man.get("runtime") or {}
        cxx = os.environ.get("CXX") or shutil.which("c++") or "c++"
        cmd = [cxx, "-std=" + str(man.get("cxx_standard") or "c++17"), "-O1", "-w",
               *[f"-I{x}" for x in [*man.get("include_dirs", []), *rt.get("include_dirs", [])]],
               str(runner), *man.get("sources", []), *rt.get("library_files", []), "-o", str(work / "runner")]
        try:
            compiled = subprocess.run(cmd, cwd=work, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=a.timeout)
        except subprocess.TimeoutExpired:
            print(f"compiling the design's model timed out after {a.timeout:g} s", file=sys.stderr)
            return 3
        if compiled.returncode != 0:
            print("the design's C++ model did not compile:\n" + "\n".join(compiled.stderr.splitlines()[-25:]))
            return 3
        (work / "vectors.txt").write_text("".join(f"{v['code']} {v['dtype']} {v['a']} {v['b']}\n" for v in vecs))
        try:
            ran = subprocess.run([str(work / "runner"), str(work / "vectors.txt")], cwd=work, capture_output=True, text=True,
                                 stdin=subprocess.DEVNULL, timeout=a.timeout)
        except subprocess.TimeoutExpired:
            print(f"the design's simulation timed out after {a.timeout:g} s", file=sys.stderr)
            return 1
    seen: dict[int, dict[str, int]] = {}
    for ln in ran.stdout.splitlines():
        m = re.match(r"V(\d+)\b(.*)", ln)
        if m:
            seen[int(m.group(1))] = {k: int(x, 0) for k, x in re.findall(r"(\w+)=(0x[0-9a-fA-F]+|\d+)", m.group(2))}
    wrong: list[str] = []
    for i, v in enumerate(vecs):
        if v.get("spec_error"):
            continue                              # the spec's model asserts on it: nothing to compare with
        out = seen.get(i)
        what = f"{v['op']} {v['type'].removeprefix('TileDataType_')} a=0x{v['a']:x}" + (f" b=0x{v['b']:x}" if v["b"] else "")
        if out is None or "y" not in out or "fault" not in out:
            wrong.append(f"{what}: no output from the design")
        elif v["fault"] and out["fault"] != 1:
            wrong.append(f"{what}: the spec refuses it (fault=1), the design gave fault={out['fault']} y=0x{out['y']:x}")
        elif not v["fault"] and out["fault"] != 0:
            wrong.append(f"{what}: expected y=0x{v['y']:x}, the design faulted")
        elif not v["fault"] and out["y"] & ((1 << v["width"]) - 1) != v["y"]:
            wrong.append(f"{what}: expected y=0x{v['y']:x}, got 0x{out['y'] & ((1 << v['width']) - 1):x}")
    by_unit: dict[str, list[int]] = {}
    for v in vecs:
        if not v.get("spec_error"):
            by_unit.setdefault(v["op"], [0, 0])[1] += 1
    for w in wrong:
        by_unit[w.split()[0]][0] += 1
    if a.measure:
        print(f"vectors_failing={len(wrong)}")
        print(f"units_passing={sum(1 for f, _n in by_unit.values() if f == 0)}")
        print(f"units={len(by_unit)}")
        print("; ".join(f"{u}: {f} of {n} wrong" for u, (f, n) in by_unit.items()), file=sys.stderr)
        return 0
    for w in wrong[:a.show]:
        print(w)
    if len(wrong) > a.show:
        print(f"... and {len(wrong) - a.show} more")
    print("; ".join(f"{u}: {f} of {n} wrong" for u, (f, n) in by_unit.items()), file=sys.stderr)
    if ran.returncode != 0 and not wrong:
        print(f"the design's simulation exited {ran.returncode}: {(ran.stderr or ran.stdout)[-300:]}")
        print(f"{len(vecs)} failing of {len(vecs)}")
        return 1
    compared = sum(1 for v in vecs if not v.get("spec_error"))
    if compared < len(vecs):
        print(f"{len(vecs) - compared} {'vector' if len(vecs) - compared == 1 else 'vectors'} not compared: the spec's own model asserts on them "
              "(`fluxpreset pto ref` lists them)", file=sys.stderr)
    print(f"{len(wrong)} failing of {compared}")
    return 1 if wrong else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fluxpreset pto", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    cache = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "flux", "pto-ref")
    for name, fn, helped in (("ops", _ops, "the interface: each unit's op code, each data type's code"),
                             ("ref", _ref, "the units' reference vectors, from the cache or ASLRef"),
                             ("check", _check, "the design against the units' reference vectors")):
        p = sub.add_parser(name, help=helped)
        if name == "check":
            p.add_argument("design", help="the pyCircuit design (a file defining build(m, domain))")
            p.add_argument("--units", nargs="*", default=["all"], help="the units, by mnemonic (all: every unit of the interface)")
            p.add_argument("--dependents", action="store_true", help="the units that depend on them too")
            p.add_argument("--pycircuit", default="pycircuit", help="the pyCircuit driver (default: on PATH)")
            p.add_argument("--show", type=int, default=40, help="wrong vectors listed, at most")
            p.add_argument("--measure", action="store_true",
                           help="a measurement, not a check: vectors_failing=, units_passing=, units= (exit 0 once built)")
            p.add_argument("--build-only", action="store_true", help="only build the design: 0 failing of 0 once it builds")
        elif name == "ops":
            p.add_argument("--names", action="store_true", help="each unit's mnemonic and summary only (a loop's scope list)")
        elif name == "ref":
            p.add_argument("units", nargs="*", default=["all"], help="the units, by mnemonic (all: every unit of the interface)")
        p.add_argument("--spec", default="spec", help="the spec checkout (default: ./spec)")
        if name != "ops":
            p.add_argument("--cache", default=cache, help="where reference vectors are kept, per spec content")
            p.add_argument("--aslref", default="", help="the ASLRef program (default: $ASLREF, else `aslref` on PATH)")
            p.add_argument("--per-type", type=int, default=10, help="vectors per data type")
            p.add_argument("--seed", type=int, default=1, help="the random vectors' seed")
            p.add_argument("--jobs", type=int, default=os.cpu_count() or 1, help="units' references computed at once")
            p.add_argument("--timeout", type=float, default=900.0, help="seconds for one ASLRef run, one build, one simulation")
        p.set_defaults(fn=fn)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())

"""`flux rtl test` and `flux rtl measure` (D579): the two tools an RTL problem needs, as
commands a DOCUMENT can name -- so a new RTL problem is a document, a prompt and a golden
model in Python, and no world package.

    gate: {test: ["{python}", "-m", "flux_cli.main", "rtl", "test", "{artifact}", "--golden", "{home}/golden.py"],
           count_re: "(\\d+) failing"}
    stages:
      - {name: screen,  command: ["{python}", "-m", "flux_cli.main", "rtl", "measure", "{artifact}", "--stage", "synth", "--clock-ps", "1000"],
         metrics_re: {fmax_mhz: "fmax_mhz=([0-9.]+)", area_um2: "area_um2=([0-9.]+)", power_w: "power_w=([0-9.e-]+)"}}
      - {name: confirm, command: [..., "--stage", "place", ...], metrics_re: {...}}

The golden model (`golden.py`) declares `PORTS` -- `[{name, dir, bits}, ...]`, ints, signed
unless `"unsigned": true` -- and `golden(**inputs) -> {output: value}`; optionally `VECTORS`
(explicit `{inputs, expected}` rows), `COUNT` (random vectors, default 32), `SEED`,
`CLOCK` / `RESET` (port names of a clocked design). `flux rtl test` builds the vectors (the
corners of every input, then random), the harness's design spec, Verilates the artifact with
the harness's testbench and prints the failing vectors and `N failing of M` (exit 1 when any
fail). `flux rtl measure` synthesises on ASAP7 (`synth`: Yosys + OpenSTA), places (`place`) or
routes (`route`) with OpenROAD and prints `metric=value` lines.
"""

from __future__ import annotations

import argparse
import importlib.util
import random
import re
from pathlib import Path
from typing import Any

__all__ = ["cmd_rtl_measure", "cmd_rtl_test", "load_golden", "spec_from_golden", "vectors_from_golden"]


def load_golden(path: str | Path) -> Any:
    p = Path(path)
    if not p.is_file():
        raise SystemExit(f"golden model {p} is not a file")
    spec = importlib.util.spec_from_file_location(f"golden_{abs(hash(str(p)))}", p)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    ports = getattr(mod, "PORTS", None)
    if not isinstance(ports, list) or not ports or not callable(getattr(mod, "golden", None)):
        raise SystemExit(f"{p}: a golden model declares PORTS = [{{name, dir, bits}}, ...] and golden(**inputs) -> {{output: value}}")
    for port in ports:
        if not {"name", "dir", "bits"} <= set(port) or port["dir"] not in ("in", "out"):
            raise SystemExit(f"{p}: port {port!r} needs name, dir (in|out) and bits")
    return mod


def _corners(bits: int, unsigned: bool) -> list[int]:
    if unsigned:
        return [0, 1, (1 << bits) - 1, 1 << (bits - 1)]
    lo, hi = -(1 << (bits - 1)), (1 << (bits - 1)) - 1
    return [lo, hi, 0, -1, 1]


def vectors_from_golden(mod: Any) -> list[dict[str, Any]]:
    """Explicit `VECTORS` when the model gives them; else the corners of every input (each
    corner of each input against the corners of the others, pairwise), then `COUNT` random
    vectors from `SEED`; `expected` from `golden` every time."""
    ins = [p for p in mod.PORTS if p["dir"] == "in"]
    explicit = getattr(mod, "VECTORS", None)
    rows: list[dict[str, Any]] = []
    if explicit:
        for row in explicit:
            inputs = dict(row["inputs"]) if "inputs" in row else {k: v for k, v in row.items() if k != "expected"}
            rows.append({"inputs": inputs, "expected": dict(row.get("expected") or mod.golden(**inputs))})
        return rows
    rng = random.Random(getattr(mod, "SEED", 0))
    seen: set = set()

    def add(inputs: dict[str, int]) -> None:
        key = tuple(sorted(inputs.items()))
        if key in seen:
            return
        seen.add(key)
        rows.append({"inputs": dict(inputs), "expected": {k: int(v) for k, v in mod.golden(**inputs).items()}})

    corners = {p["name"]: _corners(int(p["bits"]), bool(p.get("unsigned"))) for p in ins}
    base = {p["name"]: 0 for p in ins}
    for p in ins:
        for c in corners[p["name"]]:
            add({**base, p["name"]: c})
    for i, p in enumerate(ins):
        for q in ins[i + 1:]:
            for c in corners[p["name"]]:
                for d in corners[q["name"]]:
                    add({**base, p["name"]: c, q["name"]: d})
    count = int(getattr(mod, "COUNT", 32))
    target = len(rows) + count                        # the corners, then `count` random vectors
    tries = 0
    while len(rows) < target and tries < count * 20:
        tries += 1
        inputs = {}
        for p in ins:
            bits = int(p["bits"])
            inputs[p["name"]] = rng.randrange(0, 1 << bits) if p.get("unsigned") else rng.randrange(-(1 << (bits - 1)), 1 << (bits - 1))
        before = len(rows)
        add(inputs)
        if len(rows) == before:
            continue
    return rows


def spec_from_golden(mod: Any, module_name: str, vectors: list[dict[str, Any]]) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "schema_version": "0.1.0", "id": f"document/{module_name}", "module_name": module_name,
        "ports": [{"name": p["name"], "dir": p["dir"], "dtype": "int", "bits": max(2, int(p["bits"]))} for p in mod.PORTS],
        "behavior": str(getattr(mod, "BEHAVIOR", "") or getattr(mod.golden, "__doc__", "") or "the golden model"),
        "test_vectors": vectors,
    }
    if getattr(mod, "CLOCK", None):
        spec["is_clocked"] = True
    return spec


def _module_name(source: str, given: str | None) -> str:
    if given:
        return given
    m = re.search(r"^\s*module\s+([A-Za-z_]\w*)", source, re.M)
    if not m:
        raise SystemExit("no `module <name>` in the artifact; say --module")
    return m.group(1)


def cmd_rtl_test(args: argparse.Namespace) -> int:
    from flux_codegen_rtl_harness import CompileError, compile_and_run, design_spec_from_dict, explain_diagnostic

    source = Path(args.artifact).read_text()
    mod = load_golden(args.golden)
    name = _module_name(source, args.module)
    vectors = vectors_from_golden(mod)
    spec = design_spec_from_dict(spec_from_golden(mod, name, vectors))
    try:
        run = compile_and_run(source, spec, timeout_s=float(args.timeout))
    except CompileError as exc:
        print("did not compile: " + explain_diagnostic(str(exc), source))
        print(f"{len(vectors)} failing of {len(vectors)}")
        return 1
    failing = [ln for ln in (run.failing_vector_lines or [])]
    for ln in failing[: int(args.show)]:
        m = re.match(r"VECTOR (\d+) FAIL", ln)
        extra = ""
        if m and int(m.group(1)) < len(vectors):
            v = vectors[int(m.group(1))]
            extra = " -- for " + ", ".join(f"{k}={val}" for k, val in v["inputs"].items()) + " expected " + ", ".join(f"{k}={val}" for k, val in v["expected"].items())
        print(ln + extra)
    n_fail = len(vectors) - int(run.passed_vectors)
    print(f"{n_fail} failing of {len(vectors)}")
    return 0 if run.all_passed else 1


def cmd_rtl_measure(args: argparse.Namespace) -> int:
    from flux_evaluator_openroad import run_ppa_flow, run_synthesis_flow

    source = Path(args.artifact).read_text()
    name = _module_name(source, args.module)
    kw: dict[str, Any] = dict(clock_port=args.clock_port or None, reset_port=args.reset_port or None,
                              clock_period_ps=float(args.clock_ps), timeout_s=float(args.timeout))
    if args.stage == "synth":
        r = run_synthesis_flow(source, name, **kw)
    else:
        r = run_ppa_flow(source, name, flow_depth="placement" if args.stage == "place" else "routed", **kw)
    path_ps = r.clock_period_ps - r.worst_slack_ps
    print(f"fmax_mhz={r.fmax_mhz:.3f} area_um2={r.area_um2:.3f} power_w={r.power_total_w:.6g} cell_count={r.cell_count} "
          f"path_ps={path_ps:.2f} stage={args.stage} flow_depth={r.flow_depth}")
    if r.critical_path:
        print("critical_path=" + str(r.critical_path)[:400])
    return 0

"""The bank-mapping study's phases as commands (D799): what `applications/bankmap/problem.yaml`
runs, so the study is a document and these scripts -- no world.

    python -m flux_bankmap.steps search HISTORY STATE PARAMS   # the chain, a round per call
    python -m flux_bankmap.steps check ARTIFACT PARAMS         # the exhaustive conflict checker
    python -m flux_bankmap.steps cost ARTIFACT                 # the hardware cost, a formula

The chain, cheapest and most certain first (D356), one round per call of `search`:

  1. BASELINE   the plain modulo, checked rather than assumed.
  2. PROOF      a pigeonhole witness that no mapping exists (microseconds) -- then the partial
                answers (the largest concurrency, the largest stride subset) and done.
  3. z3         the XOR-fold family searched exactly; it may choose the lane wiring too.
  4. MODEL      `llm_round` mappings a model proposes per round, told what failed and why;
                `model_rounds` rounds (0: the solver only).

A candidate's artifact is its Verilog with a first line `// flux_bankmap: {...}` -- the mapping
and the wiring it is checked against -- so `check` and `cost` read it back.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from itertools import combinations
from pathlib import Path
from typing import Any, Callable

from .check import check
from .impossible import find_impossibility, max_feasible_concurrency
from .mapping import Mapping, Modulo, XorFold, from_dict
from .problem import MappingRequest

HEADER = "// flux_bankmap: "


# ---- the request, as wired
def request_of(params: dict[str, Any]) -> MappingRequest:
    return MappingRequest.from_params({k: v for k, v in params.items() if k != "model_rounds"})


def wired(req: MappingRequest, partition: Any) -> MappingRequest:
    """The request with the lane wiring the solver chose on its unsolved stages."""
    if not partition:
        return req
    part = tuple(tuple(int(x) for x in b) for b in partition)
    return replace(req, stages=tuple(replace(st, partition=part) if st.unsolved else st for st in req.stages))


def rule_wired(req: MappingRequest) -> MappingRequest:
    """Unsolved free stages wired by rule (the interleave) for checking only (D372): an unsolved
    stage constrains no pair, so checking against it would judge hardware that does not exist."""
    if not any(st.unsolved for st in req.stages):
        return req
    fixed = []
    for st in req.stages:
        if st.unsolved:
            blocks = min(st.blocks, req.concurrent)
            fixed.append(replace(st, partition=tuple(tuple(range(b, req.concurrent, blocks)) for b in range(blocks))))
        else:
            fixed.append(st)
    return replace(req, stages=tuple(fixed))


def artifact(mapping: Mapping, req: MappingRequest, partition: Any = None) -> str:
    head = json.dumps({"mapping": mapping.to_dict(), "wiring": partition})
    return f"{HEADER}{head}\n{mapping.verilog(req.address_bits, req.bank_bits)}\n"


def read_artifact(path: str) -> tuple[Mapping, Any]:
    first = Path(path).read_text().splitlines()[0]
    if not first.startswith(HEADER):
        raise ValueError(f"{path}: no `{HEADER.strip()}` line: not a bank mapping this study wrote")
    head = json.loads(first[len(HEADER):])
    return from_dict(head["mapping"]), head.get("wiring")


def candidate(mapping: Mapping, req: MappingRequest, by: str, why: str = "", partition: Any = None) -> dict[str, Any]:
    return {"name": mapping.describe(), "artifact": artifact(mapping, req, partition), "by": by,
            "knobs": {"family": type(mapping).__name__}, **({"why": why} if why else {})}


# ---- the partial answers, when the request is not achievable
def feasible_concurrency(request: MappingRequest, log: Callable[[str], None], start: int | None = None
                         ) -> tuple[int, XorFold | None]:
    """The largest N for which an XOR-fold exists, by descent (`start`: the pigeonhole bound)."""
    from .solve_z3 import solve

    top = request.concurrent - 1 if start is None else min(start, request.concurrent - 1)
    for n in range(top, 0, -1):
        m, _ = solve(replace(request, concurrent=n), timeout_s=min(request.z3_seconds, 20))
        if m is not None:
            log(f"feasible: {n} concurrent accesses are conflict-free for all strides "
                f"({m.describe()}, {m.hardware_cost()} XOR)")
            return n, m
    return 0, None


def feasible_strides(request: MappingRequest, log: Callable[[str], None]) -> tuple[tuple[int, ...], XorFold | None]:
    """The largest stride subset the requested concurrency admits, greedily by stride order."""
    from .solve_z3 import solve

    kept: list[int] = []
    best: XorFold | None = None
    proved, unsettled = [], []
    budget = min(request.z3_seconds, 20)
    for s in request.strides:
        m, trace = solve(replace(request, strides=tuple(kept + [s])), timeout_s=budget)
        if m is not None:
            kept.append(s)
            best = m
        elif "unsat" in trace.outcome:
            proved.append(s)
        else:
            unsettled.append(s)
    if kept and len(kept) < len(request.strides):
        parts = []
        if proved:
            parts.append(f"adding any of {proved} is proved impossible for the linear family")
        if unsettled:
            parts.append(f"no fold was found within {budget}s when adding any of {unsettled} "
                         "(not a proof; a larger z3_seconds may settle it)")
        log(f"feasible: strides {kept} together admit {request.concurrent} concurrent accesses; " + "; ".join(parts))
    return tuple(kept), best


def stride_compatibility(request: MappingRequest) -> list[str]:
    """Which strides can coexist at all (proved per pair with the pigeonhole stage, D363)."""
    strides = request.strides
    if len(strides) < 2 or len(strides) > 10:
        return []
    alone = {s: find_impossibility(replace(request, strides=(s,))) is None for s in strides}
    pairs = list(combinations(strides, 2))
    bad = [(s, t) for s, t in pairs if find_impossibility(replace(request, strides=(s, t))) is not None]
    if all(alone.values()) and len(bad) == len(pairs):
        return [f"each stride alone is servable at {request.concurrent} concurrent, and NO two of them together "
                f"are -- every pair is proved impossible for any mapping"]
    if bad:
        return [f"{len(bad)} of {len(pairs)} stride pairs are proved impossible together: "
                + ", ".join(f"{s}+{t}" for s, t in bad[:8]) + (" ..." if len(bad) > 8 else "")]
    return []


def partial_answer(req: MappingRequest, lessons: list[str], start: int | None = None, impossible: bool = False
                   ) -> dict[str, Any] | None:
    """The best partial answer as the run's conclusion (INFERENCE: nothing conflict-free exists)."""
    partial: list[tuple[str, XorFold]] = []
    n_ok, m_n = feasible_concurrency(req, lessons.append, start=start)
    if m_n is not None:
        partial.append((f"{n_ok} concurrent (all strides)", m_n))
    strides_ok, m_s = feasible_strides(req, lessons.append)
    if m_s is not None and strides_ok:
        partial.append((f"strides {list(strides_ok)} at {req.concurrent} concurrent", m_s))
    if impossible and len(strides_ok) <= 1:
        lessons.extend(stride_compatibility(req))
    lessons.extend(f"best partial answer: {label} -- {m.describe()}" for label, m in partial)
    if not partial:
        return None
    best = partial[0][1]
    return {"decision": best.describe(), "conflict_free": False, "hardware_cost": best.hardware_cost(),
            "partial": [{"what": label, "mapping": m.describe()} for label, m in partial]}


# ---- the commands
def search(history_path: str, state_path: str, params_path: str) -> dict[str, Any]:
    history = json.loads(Path(history_path).read_text())
    params = json.loads(Path(params_path).read_text())
    try:
        st = json.loads(Path(state_path).read_text())
    except (OSError, ValueError):
        st = {}
    req = wired(request_of(params), st.get("wiring"))
    out: dict[str, Any] = {"lessons": [], "not_established": []}
    solved = [m for m in history["measured"] if m.get("stage") == "exhaustive"]
    phase = st.get("phase", "baseline")

    def save() -> None:
        Path(state_path).write_text(json.dumps(st))

    if solved and phase in ("model", "done"):              # a conflict-free mapping is measured
        out["done"] = True
        out["candidates"] = []
        return out
    if phase == "baseline":
        st["phase"] = "proof"
        save()
        out["lessons"] += [f"interconnect: {note}" for note in req.notes]
        return {**out, "candidates": [candidate(Modulo(0), req, "baseline")], "say": "the plain modulo"}
    if phase == "proof":
        if solved:                                          # the baseline is conflict-free
            out["lessons"].append("the plain modulo mapping is already conflict-free for these strides; no hashing is needed")
            return {**out, "candidates": [], "done": True}
        witness = find_impossibility(req)
        if witness is not None:                            # proved: no mapping exists
            bound = max_feasible_concurrency(req)
            out["lessons"] += [witness.explain(),
                               f"any mapping can serve at most {bound} of these accesses concurrently without a "
                               f"conflict; the request asks for {req.concurrent}"]
            out["not_established"].append(
                f"no conflict-free mapping exists for {req.concurrent} concurrent accesses across "
                f"{list(req.strides)} -- proved, not searched. Ask for at most {bound}, or drop a stride")
            out["conclusion"] = partial_answer(req, out["lessons"], start=bound, impossible=True)
            st["phase"] = "done"
            save()
            return {**out, "candidates": [], "done": True}
        from .solve_z3 import solve

        fold, trace = solve(req, log=lambda m: None)
        if trace.partition is not None:
            st["wiring"] = [list(b) for b in trace.partition]
            req = wired(req, st["wiring"])
            out["lessons"].append(f"the solver chose the lane-to-crossbar wiring jointly with the mapping: {st['wiring']}")
        elif any(s.unsolved for s in req.stages):
            req = rule_wired(req)
            st["wiring"] = [list(b) for b in req.stages[0].partition] if req.stages and req.stages[0].partition else None
            out["lessons"].append("no wiring admits a linear fold (proved over every assignment); the model round runs "
                                  "on the interleaved wiring, fixed by rule for having the fewest co-located pairs")
        st["z3"] = trace.outcome
        st["counter_examples"] = [f"stride {s}, start 0x{a:x}" for s, a in trace.counter_examples[:6]]
        st["phase"], st["round"] = "model", 0
        if fold is not None:
            save()
            out["lessons"].append(f"z3 found {fold.describe()} -- {fold.hardware_cost()} XOR gate(s), in {trace.rounds} round(s)")
            return {**out, "candidates": [candidate(fold, req, "z3", partition=st.get("wiring"))], "say": "z3's fold"}
        if "unsat" in trace.outcome:
            out["lessons"].append(f"NO XOR-fold is conflict-free for {req.concurrent} concurrent accesses across strides "
                                  f"{list(req.strides)}: the solver proved the linear family cannot do it")
        st["partial"] = partial_answer(req, out["lessons"])
        save()
    if st.get("phase") == "model":
        rounds = int(params.get("model_rounds", 2))
        if st.get("round", 0) < rounds and int(params.get("llm_round", 6)) > 0 and not any(s.unsolved for s in req.stages):
            st["round"] = st.get("round", 0) + 1
            save()
            got = propose(req, st, history, params)
            if got:
                return {**out, "candidates": [candidate(m, req, "llm", why, partition=st.get("wiring")) for m, why in got],
                        "say": f"model round {st['round']}: {len(got)} proposal(s)"}
        st["phase"] = "done"
        save()
        out["not_established"].append(
            f"no conflict-free mapping was found for {req.concurrent} concurrent accesses across all of "
            f"{list(req.strides)}; the linear family was proved insufficient and the model's proposals were all refused")
        if st.get("partial"):
            out["conclusion"] = st["partial"]
    return {**out, "candidates": [], "done": True}


def propose(req: MappingRequest, st: dict[str, Any], history: dict[str, Any], params: dict[str, Any]
            ) -> list[tuple[Mapping, str]]:
    """A model's proposals, told the solver's outcome, the counter-examples and what was refused --
    the run's own model, from its settings (FLUX_REMOTE_*, a local Ollama)."""
    from flux_llm import OpenAIChatProposer

    from .propose import build_prompt, parse_proposals

    tried = [(r["name"], str(r.get("why", ""))[:90]) for r in history.get("refused") or []]
    prompt = build_prompt(req, baseline_summary=next((t[1] for t in tried if t[0] == Modulo(0).describe()), ""),
                          z3_summary=str(st.get("z3", "")), counter_examples=list(st.get("counter_examples") or []),
                          count=int(params.get("llm_round", 6)), tried=tried, problem=params.get("problem"),
                          guidance=history.get("guidance") or None)
    try:
        return parse_proposals(OpenAIChatProposer().propose(prompt).text, req.bank_bits)
    except Exception as exc:  # noqa: BLE001 -- no model: the chain ends where the solver did
        print(f"the proposer did not run ({type(exc).__name__}: {exc!s:.100})", file=sys.stderr)
        return []


def check_cmd(artifact_path: str, params_path: str) -> int:
    mapping, wiring = read_artifact(artifact_path)
    req = request_of(json.loads(Path(params_path).read_text()))
    req = wired(req, wiring) if wiring else rule_wired(req)
    v = check(mapping, req)
    print(v.summary(req.concurrent))
    bad = 0 if v.conflict_free else max(1, round((1.0 - v.clean_fraction) * 1000))
    print(f"{bad} failing")                      # per-mille of conflicting start addresses, at the worst resource
    return 0 if v.conflict_free else 1


def cost_cmd(artifact_path: str) -> int:
    mapping, _ = read_artifact(artifact_path)
    print(f"hardware_cost={float(mapping.hardware_cost())} clean_fraction=1.0")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m flux_bankmap.steps", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search")
    s.add_argument("history"); s.add_argument("state"); s.add_argument("params")
    c = sub.add_parser("check")
    c.add_argument("artifact"); c.add_argument("params")
    k = sub.add_parser("cost")
    k.add_argument("artifact")
    args = p.parse_args(argv)
    if args.cmd == "search":
        print(json.dumps(search(args.history, args.state, args.params), default=str))
        return 0
    if args.cmd == "check":
        return check_cmd(args.artifact, args.params)
    return cost_cmd(args.artifact)


if __name__ == "__main__":
    sys.exit(main())

"""The banked-L1 conflict study's phases as commands (D800): what
`applications/interconnect_mapping/problem.yaml` runs, so the study is a document and these
scripts -- no world.

    python -m flux_imapping.steps search HISTORY STATE PARAMS   # the rounds of the big loop
    python -m flux_imapping.steps check ARTIFACT PARAMS         # the hash is injective
    python -m flux_imapping.steps score ARTIFACT PARAMS         # the cycle law, train and holdout
    python -m flux_imapping.steps phys ARTIFACT PARAMS          # Yosys + OpenSTA on its blocks

A candidate is a pair -- a map policy and a switching fabric -- and its artifact is the pair as
JSON: a catalog policy by name, any other as its XOR taps; the fabric's fields. The search's
rounds (D386, D392):

  round 1    the interconnects found under a reference mapping (little loop A), the policy
             field -- the catalog, the XOR hill-climb, bankmap's z3 fold -- and their cross product.
  model      `llm_rounds` rounds: a model proposes XOR taps, paired with every fabric.
  coordinate `coordination_rounds` rounds of the big loop, the two little loops alternated on
             the front, until it stops moving; then the conclusion: the balanced pick, the
             corners, the consensus fabric and the certificates by exhaustion over the front.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

from .fabric import FabricModel
from .model import Memory, Mode
from .solutions import Solution, _global_hash, catalog, injective

CERTIFY_MODES = (Mode.Loop_Row_Col, Mode.Loop_Col_Row, Mode.Loop_4x4_H)
KNOWN = ("seed", "ops", "vu_probability", "dma_probability", "climb_rounds", "llm_rounds",
         "coordination_rounds", "certify_tiles", "bank_bits")


# ---- the ask and the traffic
def ask_of(params: dict[str, Any]) -> dict[str, Any]:
    bad = sorted(set(params) - set(KNOWN))
    if bad:
        raise SystemExit(f"params {bad} are not the interconnect-mapping study's; known: {', '.join(KNOWN)}")
    return {"seed": 0, "ops": 8, "vu_probability": 0.7, "dma_probability": 0.6, "climb_rounds": 40,
            "llm_rounds": 0, "coordination_rounds": 2, "certify_tiles": [[8, 4, 1], [4, 16, 1]], "bank_bits": 5,
            **params}


@lru_cache(maxsize=4)
def traffic(seed: int, ops: int, vu: float, dma: float) -> tuple[list, list]:
    from .workloads import train_holdout

    return train_holdout(seed, ops=ops, vu_probability=vu, dma_probability=dma)


def _traffic(ask: dict[str, Any]) -> tuple[list, list]:
    return traffic(int(ask["seed"]), int(ask["ops"]), float(ask["vu_probability"]), float(ask["dma_probability"]))


# ---- a pair as JSON, and back
def policy_spec(sol: Solution, mem: Memory) -> dict[str, Any]:
    if sol.name in {s.name for s in catalog(mem)}:
        return {"catalog": sol.name}
    from .model import TensorLayout

    h = sol.hash_of(TensorLayout(r=8, c=8, l=2, mode=Mode.Loop_Row_Col, base=0))
    return {"name": sol.name, "taps": [list(t) for t in h.mapping.taps], "schedule": sol.schedule,
            "extra_latency": sol.extra_latency, "buffer_bits": sol.buffer_bits}


def policy_of(spec: dict[str, Any], mem: Memory) -> Solution:
    if "catalog" in spec:
        return next(s for s in catalog(mem) if s.name == spec["catalog"])
    from flux_bankmap.mapping import XorFold

    fold = XorFold(taps=tuple(tuple(int(b) for b in t) for t in spec["taps"]), name=spec["name"])
    return Solution(name=spec["name"], hash_of=_global_hash(fold, mem), schedule=spec.get("schedule", "shared"),
                    extra_latency=float(spec.get("extra_latency", 0.0)), buffer_bits=int(spec.get("buffer_bits", 0)))


def fabric_spec(f: FabricModel) -> dict[str, Any]:
    return dataclasses.asdict(f)


def fabric_of(spec: dict[str, Any]) -> FabricModel:
    return FabricModel(**{**spec, "levels": tuple(tuple(int(x) for x in lv) for lv in spec.get("levels") or ())})


def pair(sol: Solution, fabric: FabricModel, mem: Memory, by: str) -> dict[str, Any]:
    art = {"policy": policy_spec(sol, mem), "fabric": fabric_spec(fabric)}
    return {"name": f"{sol.name} + {fabric.name}", "artifact": json.dumps(art, sort_keys=True), "by": by,
            "knobs": {"policy": sol.name, "fabric": fabric.name, "schedule": sol.schedule,
                      "pipe_latency": fabric.pipe_latency, "area_units": fabric.gate_units}}


def read_pair(path: str, mem: Memory) -> tuple[Solution, FabricModel]:
    art = json.loads(Path(path).read_text())
    return policy_of(art["policy"], mem), fabric_of(art["fabric"])


# ---- the commands
def search(history_path: str, state_path: str, params_path: str) -> dict[str, Any]:
    from .flow import climb_xor, interconnect_loop, z3_mapping_policy

    history = json.loads(Path(history_path).read_text())
    ask = ask_of(json.loads(Path(params_path).read_text()))
    mem = Memory(m=int(ask["bank_bits"]))
    train, holdout = _traffic(ask)
    try:
        st = json.loads(Path(state_path).read_text())
    except (OSError, ValueError):
        st = {}
    lessons: list[str] = []

    def save() -> None:
        Path(state_path).write_text(json.dumps(st))

    if "pairs" not in st:                                       # round 1: the field x the fabrics
        field_ = catalog(mem)
        ref = next(s for s in field_ if s.name == "S1-xor-global")
        fabrics = interconnect_loop(train, holdout, mem, ref, say=lessons.append)
        if int(ask["climb_rounds"]) > 0:
            searched = climb_xor(train, holdout, mem, ref, rounds=int(ask["climb_rounds"]), seed=int(ask["seed"]))
            if searched is not None:
                field_.append(searched.solution)
        z3 = z3_mapping_policy(train, mem, say=lessons.append)
        if z3 is not None:
            field_.append(z3)
        st.update(pairs={}, fabrics=[fabric_spec(f) for f in fabrics], llm=0, coordinated=0)
        cands = [pair(sol, f, mem, "cross") for sol in field_ for f in fabrics]
        st["pairs"].update({c["name"]: c["artifact"] for c in cands})
        save()
        return {"candidates": cands, "lessons": lessons, "say": f"{len(field_)} policies x {len(fabrics)} fabrics"}

    fabrics = [fabric_of(f) for f in st["fabrics"]]
    if st["llm"] < int(ask["llm_rounds"]):                     # a model's taps, with every fabric
        st["llm"] += 1
        save()
        sol, why = propose(st["llm"], history, mem, train, holdout)
        if sol is None:
            return {"candidates": [], "not_established": [why], "done": False,
                    "say": f"model round {st['llm']}: refused"} if st["llm"] < int(ask["llm_rounds"]) \
                else _coordinate_or_conclude(st, history, ask, mem, train, holdout, save, [why])
        cands = [pair(sol, f, mem, "llm") for f in fabrics]
        st["pairs"].update({c["name"]: c["artifact"] for c in cands})
        save()
        return {"candidates": cands, "say": f"model round {st['llm']}: {sol.name}"}
    return _coordinate_or_conclude(st, history, ask, mem, train, holdout, save, [])


def _scored_front(st: dict[str, Any], history: dict[str, Any], mem: Memory, train: list, holdout: list) -> list:
    """The measured pairs' front, rebuilt as the study's scored pairs (the cycle law is exact,
    so a pair scored again is the pair measured)."""
    from .flow import pareto_front, score

    on = {m["name"]: m["metrics"] for m in history["measured"] if m.get("stage") == "analytic"}
    if not on:
        return []

    def costs(m: dict[str, float]) -> tuple:
        return (m["area_score"], m["pad_fraction"], m["holdout_latency"], -m["holdout_throughput"])

    names = [n for n, m in on.items() if not any(
        all(a <= b for a, b in zip(costs(o), costs(m))) and costs(o) != costs(m) for o in on.values())]
    out = []
    for name in names:
        art = st["pairs"].get(name)
        if art is None:
            continue
        a = json.loads(art)
        out.append(score(policy_of(a["policy"], mem), fabric_of(a["fabric"]), train, holdout, mem))
    return pareto_front(out)


def _coordinate_or_conclude(st, history, ask, mem, train, holdout, save, not_established) -> dict[str, Any]:
    from .flow import coordinate

    front = _scored_front(st, history, mem, train, holdout)
    before = sorted(s.pair_name for s in front)
    if st["coordinated"] < int(ask["coordination_rounds"]) and front and before != st.get("front_before"):
        st["coordinated"] += 1
        st["front_before"] = before
        new = coordinate(front, train, holdout, mem, climb_rounds=int(ask["climb_rounds"]) or 30,
                         seed=int(ask["seed"]) + 100 + st["coordinated"])
        cands = [pair(s.solution, s.fabric, mem, "coordinate") for s in new if s.pair_name not in st["pairs"]]
        st["pairs"].update({c["name"]: c["artifact"] for c in cands})
        save()
        if cands:
            return {"candidates": cands, "not_established": not_established,
                    "say": f"coordination round {st['coordinated']}: {len(cands)} new pair(s)"}
    st["done"] = True
    save()
    out = conclude_front(front, ask, mem)
    return {"candidates": [], "done": True, "not_established": not_established, **out}


def certificates(front: list, ask: dict[str, Any], mem: Memory) -> list:
    """Certificates by exhaustion over the front: three modes x the document's tiles."""
    from .flow import certify

    out = []
    for s in front:
        for mode in CERTIFY_MODES:
            for tile in ask["certify_tiles"]:
                rt, ct, lt = (int(x) for x in tile)
                out.append(certify(s.solution, mem, mode=mode, rt=rt, ct=ct, lt=lt, dim=64, fabric=s.fabric,
                                   label=s.pair_name))
    return out


def conclude_front(front: list, ask: dict[str, Any], mem: Memory) -> dict[str, Any]:
    """The decision-first summary over the front, with certificates by exhaustion (three modes x
    the document's tiles): lessons for the report, the conclusion for the record."""
    from .flow import conclude

    certs = certificates(front, ask, mem)
    c = conclude(front, front, certs) if front else {"note": "nothing measured"}
    lessons = []
    bal = c.get("balanced_pick") or {}
    if bal.get("pair"):
        lessons.append(f"the balanced pick (the knee over latency, throughput, area, padding): {bal['pair']}")
    for label, key in (("latency corner", "latency_corner"), ("throughput corner", "throughput_corner"),
                       ("area corner", "area_corner")):
        if (c.get(key) or {}).get("pair"):
            lessons.append(f"{label}: {c[key]['pair']}")
    if c.get("consensus_fabric"):
        lessons.append(f"consensus fabric: {c['consensus_fabric']}")
    proved = [x for x in certs if x.holds]
    lessons.append(f"certificates: {len(proved)} proved by exhaustion, {len(certs) - len(proved)} refuted with a counterexample")
    lessons += [f"proved {x.solution}: {x.mode} tile {list(x.tile)} ({x.checked_origins} origins)" for x in proved[:6]]
    return {"lessons": lessons, "conclusion": {"conclusion": c}}


def propose(k: int, history: dict[str, Any], mem: Memory, train: list, holdout: list) -> tuple[Solution | None, str]:
    """A model's XOR taps as JSON, told what was measured; refused with the reason when it
    cannot be parsed or is not injective (D297)."""
    from flux_bankmap.mapping import XorFold
    from flux_llm import OpenAIChatProposer, strip_markdown_fence

    measured = sorted(((m["name"], m["metrics"].get("train_latency", 0.0)) for m in history["measured"]
                       if m.get("stage") == "analytic"), key=lambda t: t[1])[:12]
    lines = "\n".join(f"{n}: train avg latency {v:.3f}" for n, v in measured)
    prompt = ((history.get("guidance") + "\n" if history.get("guidance") else "")
              + "Bank-hash design: 32 banks, bank bit i = XOR of address bits taps[i].\n"
              "Propose taps as JSON {\"taps\": [[..5 lists of address-bit indices..]]}, address bits 0..15, at "
              "most 4 bits per bank bit. The low 5x5 submatrix must be invertible over GF(2) or the hash corrupts "
              f"data and is refused.\nMeasured so far, best first:\n{lines}\nJSON only.")
    try:
        reply = json.loads(strip_markdown_fence(OpenAIChatProposer().propose(prompt).text))
        taps = tuple(tuple(int(b) for b in t) for t in reply["taps"])
        mapping = XorFold(taps=taps, name=f"xor-llm-{k}")
    except Exception as exc:  # noqa: BLE001 -- a refusal, not a crash
        return None, f"model round {k}: unparseable or no proposal ({exc!s:.120})"
    if not injective(mapping, mem.m):
        return None, f"model round {k}: taps {taps} not injective on the low {mem.m} bits"
    return Solution(name=f"S8-xor-llm-{k}", hash_of=_global_hash(mapping, mem), assumptions=("model-proposed taps",)), ""


def check_cmd(artifact: str, params_path: str) -> int:
    from .model import TensorLayout

    mem = Memory(m=int(ask_of(json.loads(Path(params_path).read_text()))["bank_bits"]))
    sol, _fabric = read_pair(artifact, mem)
    bad = []
    for c in (4, 8, 16, 32, 64):
        h = sol.hash_of(TensorLayout(r=8, c=c, l=2, mode=Mode.Loop_Row_Col, base=0))
        if not injective(h.mapping, h.effective_bits):
            bad.append(c)
    if bad:
        print(f"{sol.name}: non-injective hash for pitches {bad}")
    print(f"{len(bad)} failing")
    return 1 if bad else 0


def score_cmd(artifact: str, params_path: str) -> int:
    from .flow import score

    ask = ask_of(json.loads(Path(params_path).read_text()))
    mem = Memory(m=int(ask["bank_bits"]))
    sol, fabric = read_pair(artifact, mem)
    train, holdout = _traffic(ask)
    s = score(sol, fabric, train, holdout, mem)
    print(f"area_score={float(s.area_score)} pad_fraction={s.pad_fraction} holdout_latency={s.holdout.avg_latency} "
          f"holdout_throughput={s.holdout.throughput} train_latency={s.train.avg_latency}")
    return 0


def phys_cmd(artifact: str, params_path: str) -> int:
    from .flow import score
    from .phys import screen_pairs

    ask = ask_of(json.loads(Path(params_path).read_text()))
    mem = Memory(m=int(ask["bank_bits"]))
    sol, fabric = read_pair(artifact, mem)
    train, holdout = _traffic(ask)
    s = score(sol, fabric, train, holdout, mem)
    sc = screen_pairs([s]).get(s.pair_name)
    if sc is None:
        print(f"{s.pair_name}: not screened", file=sys.stderr)
        return 1
    out = (f"area_score={float(s.area_score)} pad_fraction={s.pad_fraction} holdout_latency={s.holdout.avg_latency} "
           f"holdout_throughput={s.holdout.throughput} worst_slack_ps={float(sc.worst_slack_ps)}")
    if sc.composed_um2 is not None:
        out += f" area_um2={float(sc.composed_um2)}"
    print(out)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m flux_imapping.steps", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search")
    s.add_argument("history"); s.add_argument("state"); s.add_argument("params")
    for name in ("check", "score", "phys"):
        c = sub.add_parser(name)
        c.add_argument("artifact"); c.add_argument("params")
    args = p.parse_args(argv)
    if args.cmd == "search":
        print(json.dumps(search(args.history, args.state, args.params), default=str))
        return 0
    return {"check": check_cmd, "score": score_cmd, "phys": phys_cmd}[args.cmd](args.artifact, args.params)


if __name__ == "__main__":
    sys.exit(main())

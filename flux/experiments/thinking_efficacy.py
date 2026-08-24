"""Does letting a reasoning model THINK produce better fabric proposals?

Two arms, differing in exactly one variable: whether the generator's prompt carries qwen3's
`/no_think` switch. Everything else — the problem, the knowledge block, the seed, the proposal
budget, the constructor, the screen — is identical, and every proposal is validated and screened
by the real machinery in both arms.

WHY THIS EXISTS. `/no_think` was adopted under duress, not on evidence: at a 4,096-token serving
window the reasoning trace was charged to the same budget as the answer and every proposal died at
`finish_reason="length"` (docs/decisions.md D289). Zero proposals is worse than any proposal, so
the switch was a workaround for a trace that did not FIT. It says nothing about quality, and the
two roles this repo gives a model are not alike: choosing among four enumerated scopes leaves
reasoning little to add, while inventing a fabric under coupled constraints is exactly where
losing it could cost something real. This measures the second one.

The comparison is only runnable on a serving window with room for a trace AND an answer. At 4,096
the thinking arm produces nothing to compare, which is not a result about thinking.

    FLUX_PROMPT_BUDGET_CHARS=60000 nix develop .#physical --command \\
        python3 experiments/thinking_efficacy.py

Each arm proposes into its OWN store, so neither can see the other's fabrics as cache hits or
mine them as facts. Both are seeded from the same measured store, copied, so both start from the
same evidence.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

FLUX_ROOT = Path(__file__).resolve().parents[0].parent
# The study moved into `flux_interconnect.flow` when it became callable by another orchestrator
# (D346); before that it was a script here, and before that in a `demos/` directory that no
# longer exists. It is an importable package now, so nothing needs to be put on the path.

REPS = 3          # proposal rounds per arm
PER_ROUND = 6     # fabrics requested per round


def _tally(db: str) -> dict:
    # `fabrics_attempted` became `campaign_tally` in the campaign package when the demo's
    # generic bookkeeping was extracted for reuse (D298). Same keys, same meaning.
    from flux_search_campaign.progress import campaign_tally

    return campaign_tally(db)


def _best(db: str) -> tuple[float | None, float | None]:
    """(smallest area meeting both constraints, its throughput) over everything MEASURED."""
    import sqlite3

    con = sqlite3.connect(db)
    best: tuple[float, float] | None = None
    try:
        rows = con.execute(
            "select t.candidate_json, r.result_json from trials t "
            "join results r on t.result_id = r.id where r.evaluator like '%phys%'")
        for _cj, rj in rows:
            m = json.loads(rj).get("metrics", {})

            def val(k):
                return (m.get(k) or {}).get("value")

            area, fmax = val("area_mm2"), val("fmax_mhz")
            cap, thr = val("max_throughput_words_per_cycle"), val("throughput_words_per_cycle")
            if area and fmax and fmax >= 600 and cap == 28 and (best is None or area < best[0]):
                best = (area, thr or 0.0)
    finally:
        con.close()
    return best if best else (None, None)


def run_arm(log, arm: str, seed_db: Path, workdir: Path) -> dict:
    """One arm: REPS proposal rounds against a private copy of the seeded store."""
    import flux_interconnect.flow as demo

    db = workdir / f"{arm}.db"
    shutil.copy(seed_db, db)
    # The ONLY difference between the arms. Set per-arm rather than per-process so both run in
    # one invocation against the same model, the same machine and the same warm cache.
    if arm == "think":
        os.environ["FLUX_LLM_THINK"] = "1"
    else:
        os.environ.pop("FLUX_LLM_THINK", None)

    before = _tally(str(db))
    rounds, t0 = [], time.time()
    for rep in range(REPS):
        started = time.time()
        try:
            report = demo.llm_round(str(db), f"{arm}-{rep}", PER_ROUND)
        except Exception as exc:  # noqa: BLE001 — a dead round is data, not a crash
            log(f"# {arm} round {rep}: FAILED {type(exc).__name__}: {str(exc)[:80]}")
            rounds.append({"rep": rep, "failed": f"{type(exc).__name__}"})
            continue
        after = _tally(str(db))
        rounds.append({
            "rep": rep,
            "seconds": round(time.time() - started, 1),
            "new_fabrics": after["attempted"] - before["attempted"],
            "measured": after["measured"],
            "status": (report or {}).get("status"),
        })
        log(f"# {arm} round {rep}: {rounds[-1]['new_fabrics']} new fabric(s) in "
            f"{rounds[-1]['seconds']}s")
        before = after

    final = _tally(str(db))
    area, thr = _best(str(db))
    return {
        "arm": arm,
        "seconds": round(time.time() - t0, 1),
        "rounds": rounds,
        "fabrics_attempted": final["attempted"],
        "fabrics_measured": final["measured"],
        "proposed": final["proposed"],
        "refused": final["refused"],
        "failed": final["failed"],
        "best_area_mm2": area,
        "best_area_throughput": thr,
    }


def main() -> None:
    import tempfile

    def log(line: str) -> None:
        print(line, flush=True)

    from flux_llm import default_local_model

    seed = FLUX_ROOT / "demo-interconnect.db"
    if not seed.exists():
        raise SystemExit(f"no seeded store at {seed}; run the demo first so both arms start "
                         "from the same measured evidence")

    log(f"# model: {default_local_model()}")
    log(f"# knowledge budget: {os.environ.get('FLUX_PROMPT_BUDGET_CHARS', '9000 (default)')}")
    log(f"# arms: think vs no_think, {REPS} rounds x {PER_ROUND} proposals each")

    with tempfile.TemporaryDirectory(prefix="thinking-efficacy-") as d:
        workdir = Path(d)
        # no_think FIRST: if the machine dies partway, the arm that already works is the one
        # already measured, and the new claim is the one left unproven.
        results = [run_arm(log, arm, seed, workdir) for arm in ("no_think", "think")]

    log("SUMMARY " + json.dumps(results, indent=2))
    a, b = results[0], results[1]
    log(f"\n{'':10}{'new fabrics':>13}{'measured':>10}{'best area':>11}{'seconds':>9}")
    for r in (a, b):
        area = f"{r['best_area_mm2']:.4f}" if r["best_area_mm2"] else "-"
        log(f"{r['arm']:10}{r['fabrics_attempted']:>13}{r['fabrics_measured']:>10}"
            f"{area:>11}{r['seconds']:>9.0f}")
    log("\nREAD THIS HONESTLY: n is tiny. Three rounds per arm cannot separate small differences "
        "from noise, and a difference in best area may just be which fabrics happened to be "
        "proposed. Treat a large gap as worth investigating and a small one as nothing.")


if __name__ == "__main__":
    main()

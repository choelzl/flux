"""`flux_invent_prefetcher` -- the prefetcher world's invention round as one agent-callable node.

The round itself is the world's (`flux_prefetcher.invent.invent`, review 2 step R3 of
docs/decisions.md D539: it used to be an engine of its own in this package): a model writes a
NEW L2 prefetcher as one C++ header, the simulator's own Makefile builds it, the compiler's
first diagnostic and the counters' diagnosis repair it, the same evaluator and traces the
study uses measure it against the tallest stack the kept library vouches for, and a design
that beat the reference on the screen is confirmed at full length. This node only names the
model and hands the call through; `flux_prefetcher_dse_loop` runs the same round inside the
study when its document says `invent_rounds`.
"""

from __future__ import annotations

from typing import Any

from chia.base.ChiaFunction import ChiaFunction


@ChiaFunction()
def flux_invent_prefetcher(
    *,
    rounds: int = 4,
    repair_attempts: int = 3,
    inert_repairs: int = 1,
    problem: str | None = None,
    traces_dir: str | None = None,
    source_tree: str | None = None,
    parallelism: int = 12,
    llm_model: str | None = None,
    num_predict: int = 2400,
    scratch_root: str | None = None,
    keep_dir: str | None = None,
    confirm_best: bool = True,
    reference_stack: list[str] | None = None,
) -> dict[str, Any]:
    """Invent L2 prefetchers, compile them, and measure them against the study's own baseline.

    Each round asks for a design that beats the best thing measured so far -- starting from the
    tallest stack the kept library's records claim, re-measured. Designs that fail to compile
    are repaired from the compiler's first diagnostic, up to `repair_attempts` times; designs
    that compile but emit nothing are handed the counters' diagnosis, up to `inert_repairs`
    times; designs that run are measured on the cheap stage and ranked. Only a design that beat
    the reference on the screen is confirmed at full length (D351: a screened number orders
    candidates and must not be quoted).

    Returns every attempt with what happened to it, so a run that produced nothing usable says
    what went wrong rather than reporting an empty list.
    """
    from flux_prefetcher.invent import invent

    return invent(rounds, repair_attempts=repair_attempts, inert_repairs=inert_repairs,
                  problem=problem, traces_dir=traces_dir, source_tree=source_tree,
                  workers=parallelism, llm_model=llm_model, num_predict=num_predict,
                  scratch_root=scratch_root, keep_dir=keep_dir, confirm_best=confirm_best,
                  reference_stack=reference_stack)

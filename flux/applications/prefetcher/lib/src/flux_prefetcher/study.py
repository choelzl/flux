"""What the document asks for, and what one measurement of it is (docs/decisions.md D349, D533).

`PrefetcherRequest` is `prefetcher.problem.yaml`'s `params:` as a frozen object, read once by
the world (`from_params`). The loop's own knobs -- how many finalists are confirmed, whether
the chain stops at the screen, how many simulations run at once, how often an invention is
repaired -- are the document's `budget:` and never appear here. `ScoredConfig` is one design
and everything the simulator said about it, the payload every loop `Scored` of this world
carries.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import BingoConfig
from .objective import RETENTION_FLOOR, Score


@dataclass(frozen=True)
class PrefetcherRequest:
    """One prefetcher study's settings: the document's `params:` (D533)."""

    problem: str | None = None
    traces_dir: str | None = None
    champsim_bin: str | None = None
    stage: int = 2                      # 1 = speedup only; 2 = also minimise storage
    measurements: int = 24              # configurations MEASURED per stage
    llm_round: int = 8                  # configurations the model is asked for (0 = never asked)
    seed: int = 0
    retention_floor: float = RETENTION_FLOOR
    #: How stage 1 spends its budget. "climb" keeps one best and expands it (D349).
    #: "pareto-uct" grows a tree over (speedup, storage) and expands the node whose branch
    #: contributes most to the frontier -- hypervolume improvement plus crowding plus decaying
    #: exploration, MicroEvo's tree policy (arXiv:2608.06183) on this study's own move
    #: generators and stages (D368). Same budget, same gates; only the allocation differs.
    strategy: str = "climb"
    #: The other axis, as a bound. A configuration whose modelled storage exceeds this is refused
    #: at the validity gate -- unmeasured, in microseconds -- so the search spends its budget in
    #: the region that could be built, and the proposer is told the budget. None searches freely
    #: and reports the whole IPC-vs-storage frontier for the reader to choose from (D362).
    max_storage_bytes: int | None = None
    # Partners to try alongside Bingo in the L2 slot, greedily, keeping each one only if it earns
    # its place. 0 searches Bingo alone. Some pairs abort the simulator (`bingo+scooby`) and some
    # segfault (`next_line+sms`), so this axis has to treat a crash as a measurement, not an error.
    compose_rounds: int = 2
    #: Rounds of hill-climbing over the PARTNERS' own knobs, once the stack is chosen. Every
    #: composition result before this existed ran its partners at their shipped defaults.
    tune_partners: int = 12
    #: Offer the kept inventions as partners too. Costs one simulator build (~1 min, cached by
    #: content) and puts a design that beat the stack on the compose menu.
    include_invented: bool = True
    #: Invent NEW prefetchers during this run: the model designs them against the tallest stack
    #: the kept library can vouch for, the compiler and the screen judge them, and whatever
    #: survives joins the compose menu alongside the kept designs from earlier runs. Each round
    #: is a model call (~3 min), a build (~1 min) and a screen wave. 0 reuses what earlier runs kept.
    invent_rounds: int = 0
    invented_dir: str | None = None     # where inventions are kept; None = the application's own

    @classmethod
    def from_params(cls, params: dict[str, Any]) -> "PrefetcherRequest":
        """The document's `params:`, typed by the loop's one reader (D557): an unknown key is
        a load error, a null keeps the default unless the field means "none" when null."""
        from flux_loop.params import from_params

        out = from_params(cls, params, optional=("problem", "traces_dir", "champsim_bin", "max_storage_bytes", "invented_dir"),
                          what="the prefetcher study")
        if out.strategy not in ("climb", "pareto-uct"):
            raise ValueError(f"strategy is climb or pareto-uct, not {out.strategy!r}")
        return out


@dataclass(frozen=True)
class ScoredConfig:
    """A configuration and everything measured about it."""

    config: BingoConfig
    score: Score
    provenance: str = ""                # who proposed it: incumbent, llm, neighbour, shrink, ...
    # The L2 prefetcher stack this was measured in. Bingo alone is the study's starting point, but
    # it is not the only legal answer: the `multi` slot runs several at once, and `bingo+sms`
    # confirmed +0.44 geomean over `bingo` at full length. A candidate that did not carry its
    # stack could not express that.
    types: tuple[str, ...] = ("bingo",)
    #: The partners' own knobs this was measured with, as sorted (name, value) pairs so the whole
    #: candidate stays hashable and comparable.
    partner_knobs: tuple[tuple[str, Any], ...] = ()

    @property
    def stack(self) -> str:
        return "+".join(self.types) if self.types else "none"

    @property
    def geomean_speedup(self) -> float:
        return self.score.geomean_speedup

    @property
    def storage_bytes(self) -> int:
        return self.score.storage_bytes


__all__ = ["PrefetcherRequest", "ScoredConfig"]

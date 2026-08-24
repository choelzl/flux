"""What a caller asks for, and what comes back (docs/decisions.md D349).

The same shape `flux_interconnect.study` established (D345): a frozen request whose field names
match the command line, and a result that separates what was DECIDED from what was MEASURED from
what was NOT ESTABLISHED. An orchestrator running a larger design can hand this study a
requirement and get an answer back without knowing anything about ChampSim.

`met_requirement` stays deliberately separate from `decision`. A study that searched honestly and
found nothing beating the shipped configuration has a decision (keep the default) and has not met
the requirement, and collapsing the two would report the first as if it were the second.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import BingoConfig
from .objective import RETENTION_FLOOR, Score


@dataclass(frozen=True)
class PrefetcherRequest:
    """One prefetcher study. Field names match `demo.py`'s argparse namespace exactly."""

    db: str
    problem: str | None = None
    traces_dir: str | None = None
    champsim_bin: str | None = None
    stage: int = 2                      # 1 = speedup only; 2 = also minimise storage
    budget: int = 24                    # measured configurations per stage
    llm_round: int = 8                  # LLM-proposed configurations (0 = deterministic run)
    parallelism: int = 16               # concurrent ChampSim runs
    seed: int = 0
    retention_floor: float = RETENTION_FLOOR
    #: How stage 1 spends its climb budget. "climb" keeps one best and expands it (D349).
    #: "pareto-uct" grows a tree over (speedup, storage) and expands the node whose branch
    #: contributes most to the frontier -- hypervolume improvement plus crowding plus decaying
    #: exploration, MicroEvo's tree policy (arXiv:2608.06183) on this study's own move
    #: generators and rungs (D368). Same budget, same gates; only the allocation differs.
    strategy: str = "climb"
    #: The other axis, as a bound. A configuration whose modelled storage exceeds this is refused
    #: at the validity gate -- unmeasured, in microseconds -- so the search spends its budget in
    #: the region that could be built, and the proposer is told the budget. None searches freely
    #: and reports the whole IPC-vs-storage frontier for the reader to choose from (D362).
    max_storage_bytes: int | None = None
    # How long to simulate is the STUDY's decision, not the caller's: the search runs on the cheap
    # rung and the answer is confirmed on the expensive one. `decide_on_finalists` is how many
    # candidates get that confirmation; 0 leaves every number a screen estimate, which the result
    # then says so in `not_established` rather than quietly reporting a ranking hint as an answer.
    decide_on_finalists: int = 4
    screen_only: bool = False
    # Partners to try alongside Bingo in the L2 slot, greedily, keeping each one only if it earns
    # its place. 0 searches Bingo alone. Some pairs abort the simulator (`bingo+scooby`) and some
    # segfault (`next_line+sms`), so this axis has to treat a crash as a measurement, not an error.
    compose_rounds: int = 2
    #: Rounds of hill-climbing over the PARTNERS' own knobs, once the stack is chosen. Every
    #: composition result before this existed ran its partners at their shipped defaults.
    tune_partners: int = 12
    #: Offer the invention loop's kept designs as partners too. Costs one simulator build
    #: (~1 min, cached by content) and puts a design that beat the stack on the compose menu.
    include_invented: bool = True
    #: Invent NEW prefetchers during this run: the local model designs them against the best
    #: known stack, the compiler and the screen judge them, and whatever survives joins the
    #: compose menu alongside the kept designs from earlier runs. Each round is a model call
    #: (~3 min), a build (~1 min) and a screen wave. 0 reuses what earlier runs kept.
    invent_rounds: int = 0


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


@dataclass(frozen=True)
class PrefetcherResult:
    """What the study concluded, and what it could not."""

    decision: BingoConfig | None = None
    decision_score: Score | None = None
    stage1_best: ScoredConfig | None = None
    stage2_best: ScoredConfig | None = None
    measured: list[ScoredConfig] = field(default_factory=list)
    #: The shipped configuration, measured on the same rung as the decision. The reference the
    #: whole study is quoted against, so it is carried explicitly rather than left for a reader
    #: to find among `measured`.
    incumbent_score: Score | None = None
    #: stack -> geomean with every prefetcher in it at its SHIPPED default. The denominator for
    #: "did tuning help", as distinct from `baseline_ipc`, which answers "did prefetching help".
    stack_references: dict[str, float] = field(default_factory=dict)
    #: The IPC-vs-storage frontier on the rung this report quotes, smallest first: every design
    #: that is faster than everything smaller. The decision is one of its points; the others are
    #: the trade-off the reader may prefer (D362).
    frontier: list[ScoredConfig] = field(default_factory=list)
    refused: list[tuple[str, str]] = field(default_factory=list)
    baseline_ipc: dict[str, float] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    lessons: list[str] = field(default_factory=list)
    not_established: list[str] = field(default_factory=list)

    @property
    def improved_on_incumbent(self) -> bool:
        """Did the search actually beat the configuration the project ships?

        Compared against the INCUMBENT, not against no-prefetcher. The previous version asked
        `geomean_speedup > 1.0`, which Bingo clears in almost any configuration, so a run that
        searched hard and returned the shipped `bingo.ini` unchanged still reported "met
        requirement: yes". The question a study of this kind exists to answer is whether tuning
        was worth doing, and that has a `no` answer.

        Both numbers must come from the same rung: an incumbent screened at 2M+3M against a
        decision confirmed at 100M+150M would compare fidelities, not designs (D351), which is
        why the incumbent is always among the confirmed finalists.
        """
        if not (self.decision_score and self.incumbent_score):
            return False
        return self.decision_score.geomean_speedup > self.incumbent_score.geomean_speedup

    @property
    def met_requirement(self) -> bool:
        """Kept as the name callers and the CHIA payload already use."""
        return self.improved_on_incumbent

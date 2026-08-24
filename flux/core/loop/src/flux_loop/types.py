"""The loop's vocabulary (D421): what a candidate, a verdict, a request and the loop's state are. Problem-agnostic by construction."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

__all__ = ["BuildError", "Candidate", "Improve", "LoopRequest", "LoopResult", "LoopState", "StageNames",
           "Scored", "SubLoop", "Verdict"]

class StageNames:
    """The loop's own stage vocabulary (D438): the names its record writes and reads back.
    `GATE` is a judged attempt (refused or not), `ADMIT` a part the gate admitted, `PROTOTYPE`
    a prototype-stage attempt. A problem's costed stages are its own (`Problem.stages()`, the
    task document's `stages`), and the first of them is the analytical one."""

    GATE = "gate"
    ADMIT = "admit"
    PROTOTYPE = "prototype"


class BuildError(RuntimeError):
    """`Problem.build` refused the artifact; `str(exc)` is what the repair prompt reads."""


@dataclass(frozen=True)
class Candidate:
    """One thing the loop can build and judge. `artifact` is the text a tool runs
    (RTL, a config document, a mapping expression, C++) -- or "" for a problem whose
    candidates are points, not text (a bank mapping, a prefetcher configuration),
    which then live in `knobs`: the declared design decisions the record keeps and
    the extractor duels over; `meta` is anything else the problem wants to carry
    (tables requested, latency, ...)."""

    name: str
    artifact: str = ""
    knobs: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    subgoal: str | None = None

    def with_artifact(self, artifact: str, **meta: Any) -> "Candidate":
        return Candidate(self.name, artifact, dict(self.knobs), {**self.meta, **meta},
                         self.subgoal)

    def with_knobs(self, **knobs: Any) -> "Candidate":
        return Candidate(self.name, self.artifact, {**self.knobs, **knobs}, dict(self.meta),
                         self.subgoal)

    def key(self) -> str:
        """What makes two candidates the same measurement (D427): the artifact's text
        when there is one, else the knobs -- so a parameter-space problem (no text to
        build) caches and records exactly like a text problem."""
        import hashlib

        body = self.artifact or json.dumps(self.knobs, sort_keys=True, default=str)
        return hashlib.sha256(body.encode()).hexdigest()[:16]

    def to_record(self) -> dict[str, Any]:
        return {"name": self.name, "artifact": self.artifact, "knobs": dict(self.knobs),
                "meta": dict(self.meta), "subgoal": self.subgoal}

    @classmethod
    def from_record(cls, doc: dict[str, Any]) -> "Candidate":
        return cls(str(doc.get("name", "?")), str(doc.get("artifact", "")),
                   dict(doc.get("knobs") or {}), dict(doc.get("meta") or {}),
                   doc.get("subgoal"))


@dataclass(frozen=True)
class SubLoop:
    """A part whose generator is ANOTHER LOOP (D455).

    The drawing's composition orchestrator: a problem too big to write as one candidate is
    divided into sub-tasks, each with its own gate, its own stages and its own record, and the
    parent composes what they decided. `problem` is the child's `Problem`; `request` is its own
    knobs (None: the parent's, so the child writes into the same store); `statement` is what
    this sub-task IS, for the record and for a prompt that has to explain it.

    The division comes from either side (Cedric, 2026-09-14): a problem may DECLARE its
    children (`Problem.subproblems`, a task document's `subtasks`), or its orchestrator may
    decide them at runtime and return them from `decompose` -- the same work item either way.
    """

    name: str
    problem: Any
    request: "LoopRequest | None" = None
    statement: str = ""


@dataclass(frozen=True)
class Improve:
    """A measured candidate handed BACK TO THE GENERATOR, with what the numbers said (D463).

    An evaluator has two edges out, not one (Cedric's drawing): a result can go to the
    ORCHESTRATOR, which decides what to try next, or back to the GENERATOR, which improves the
    design it already has. The second edge is what `Problem.route` returns after a stage: these
    candidates are worth another draft, and `why` is what the model or the template is told --
    the numbers, not just "it failed".

    `subgoal` names the part this candidate belongs to, if any; `stage` is where the numbers
    came from, so a report can say which evaluator sent it back.
    """

    candidate: Candidate
    why: str
    stage: str = ""
    subgoal: str | None = None


@dataclass(frozen=True)
class Verdict:
    """The gate's answer. `score` is the distance from passing (0 == admitted; lower
    is better) so the loop can keep the BEST refused attempt without knowing what
    the units are; `why` is the failure text a prompt carries; `payload` is the
    problem's own report (a ULP table, a counter-example, ...)."""

    ok: bool
    score: float
    why: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Scored:
    """A candidate after a costed stage: its metrics (every number the stage returned), which
    stage produced them, and `payload`: whatever else the stage returned (D446) -- a problem's
    own scored object, a critical path, a per-benchmark table -- so a problem ported onto the
    loop keeps its result types without a parallel bookkeeping of its own."""

    candidate: Candidate
    stage: str
    metrics: dict[str, float]
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.candidate.name


@dataclass(frozen=True)
class LoopRequest:
    """The loop's own knobs -- every problem shares them. Problem-specific settings
    travel in `params` and are the problem's to read."""

    db: str = ""
    steps: int = 24                 # work items per pass: parts, sub-tasks, batches (D457)
    repair_attempts: int = 12       # inner-loop attempts per generation
    explore_every: int = 4          # 1 in N generations starts fresh, not from best
    cooldown_after: int = 3         # consecutive no-builds before a subgoal yields
    structured: bool = True         # schema-constrained decoding when the proposer allows
    patching: bool = True           # repair by find/replace edits, not rewrites
    revert_after: int = 3           # failed compile-repairs before reverting to last good
    screen_only: bool = False       # skip the costliest stage
    finalists: int = 3              # confirm this many, spread along the frontier (`Problem.finalists`)
    regress_after: int = 2          # D423/D504: the base tolerance for worsening edits; progress earns more
    regenerate: tuple = ()          # D476: parts whose record is history, not a starting point
    prototype: bool = True          # D424: prove the algorithm in Python before the target
    prototype_unmeasured_stop: int = 4   # D480: consecutive unmeasurable attempts that end a pass; skipped
                                    # when the problem has no prototype_spec
    prototype_attempts: int = 30    # cheap turns (seconds each), so a bigger budget
    compute: bool = True            # D422: replies may run sandboxed numpy snippets
    compute_timeout_s: float = 10.0
    patch_context_lines: int = 40   # D422: a patch prompt shows this much around a fault
    critique_rounds: int = 1        # D433: times a critic may send a passing part back; 0 = no critic
    max_depth: int = 3              # D455: how deep sub-loops may nest; a loop that returns
                                    # itself as its own sub-task stops here instead of forever
    budget_s: float | None = None    # D463: OPTIONAL wall clock for the step loop, checked
                                     # before each step. None (the default) = no clock; only
                                     # `steps` bounds the pass
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class LoopState:
    """Everything a pass accumulates. Problems receive it read-mostly; the loop owns it.

    Who writes what (D440): `records.py` fills `admitted`, `best` and `prototypes` on reload;
    `loop.py` advances `step`, `judged`, `fail_streak`, `plans`, `critiqued`, `admitted`,
    `best`, `refused`, `scored`, `pool`, `lessons`, `not_established`; `generation.py` counts
    `attempts` and `prototype.py` keeps `prototypes`; `compute.py` appends `compute_notes`;
    `drain()` extends `human_notes`. A problem reads; the one thing it may add is a lesson."""

    request: LoopRequest
    say: Callable[[str], None]
    proposer: Any
    feedback: Any
    records: Any = None
    cache: Any = None
    workdir: str = ""
    admitted: dict[str, Candidate] = field(default_factory=dict)   # subgoal -> frozen
    subloops: dict[str, Any] = field(default_factory=dict)          # D455: name -> SubLoop
    improve: list[Improve] = field(default_factory=list)   # D463: what an evaluator sent back
    children: dict[str, Any] = field(default_factory=dict)          # name -> the child LoopResult
    depth: int = 0                                                  # 0 = the top-level pass
    best: dict[str, tuple[float, Candidate, str]] = field(default_factory=dict)
    attempts: dict[str, int] = field(default_factory=dict)
    fail_streak: dict[str, int] = field(default_factory=dict)
    scored: list[Scored] = field(default_factory=list)
    pool: list[Candidate] = field(default_factory=list)   # batches (D446): gate-admitted, in order
    refused: list[tuple[str, str]] = field(default_factory=list)
    lessons: list[str] = field(default_factory=list)
    not_established: list[str] = field(default_factory=list)
    human_notes: list[Any] = field(default_factory=list)
    compute_notes: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    prototypes: dict[str, str] = field(default_factory=dict)   # part -> VERIFIED prototype
    proto_best: dict[str, tuple[float, str, str]] = field(default_factory=dict)  # D480: best refused prototype per part (score, code, why)
    plans: dict[str, dict[str, Any]] = field(default_factory=dict)  # part -> brief, budget (D432)
    critiqued: dict[str, int] = field(default_factory=dict)     # part -> times sent back (D433)
    judged: int = 0
    step: int = 0
    stopped: str = ""                                    # D463: why the step loop stopped
    fresh: bool = True         # D463: something changed since the chain was last climbed
    routed: set = field(default_factory=set)   # (candidate key, stage) already sent back once
    bias: dict[tuple[str, str], Any] = field(default_factory=dict)   # D464: (stage, metric) ->
                                                                     # Bias, what a costly stage
                                                                     # said about a cheap one
    reached: str = ""          # the stage the chain reached, and that stage's pool: the chain's
    on_stage: dict[str, Any] = field(default_factory=dict)      # own bookkeeping, read by the
                                                               # decision
    started: float = 0.0

    def drain(self) -> str | None:
        """Operator guidance typed since the last drain, rendered for a prompt -- together
        with what earlier runs' notes the record reloaded (D403), so a resumed run with no
        channel open still carries them, stamped as an earlier run's."""
        from flux_feedback import drain_guidance, guidance_lesson, note_sink

        # Where the note actually goes, said honestly (D388): a run with no model role
        # records and reports it and says it reached no prompt.
        reaches = "the next prompt" if self.proposer is not None else None
        on_note = note_sink(self.records,
                            lambda text: self.lessons.append(guidance_lesson(text,
                                                                             reaches=reaches)))
        return drain_guidance(self.feedback, self.human_notes, on_note=on_note)


@dataclass(frozen=True)
class LoopResult:
    """What a pass concluded. The three measurement fields are per-stage on purpose (D454):
    `scored` is every measurement the pass made, each carrying its own `stage`; `frontier` is
    the frontier over the stage the DECISION was made on; `confirmed` is that stage's results
    when it was not the first one. A caller that wants one stage filters `scored` by it."""

    decision: Scored | None
    decided_by: str
    frontier: list[Scored]
    confirmed: list[Scored]
    scored: list[Scored]
    admitted: dict[str, Candidate]
    refused: list[tuple[str, str]]
    lessons: list[str]
    not_established: list[str]
    notes: list[str]
    provenance: dict[str, Any]
    #: Why the pass stopped working (D463): "the step budget", "the wall clock", "good enough:
    #: <why>", "nothing left to do" -- so a caller can tell a finished search from a cut one.
    stopped: str = ""

    @property
    def cut_short(self) -> bool:
        """Whether the pass ran out of budget rather than finishing what it had to do."""
        return self.stopped in ("the step budget", "the wall clock")

"""The loop's vocabulary: candidate, verdict, request and loop state. Problem-agnostic (D421)."""

from __future__ import annotations

from .ledger import Ledger

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

__all__ = ["AgentSession", "BuildError", "Candidate", "Improve", "LoopRequest", "LoopResult", "LoopState", "Option",
           "StageNames", "Scored", "SubLoop", "Verdict"]

class StageNames:
    """Stage names the loop's record writes and reads back (D438).

    `GATE` is a judged attempt, `ADMIT` a part the gate admitted, `PROTOTYPE` a prototype-stage
    attempt. A problem's costed stages come from `Problem.stages()`; the first is analytical."""

    GATE = "gate"
    ADMIT = "admit"
    PROTOTYPE = "prototype"


class BuildError(RuntimeError):
    """`Problem.build` refused the artifact; `str(exc)` is what the repair prompt reads."""


@dataclass(frozen=True)
class Candidate:
    """One thing the loop can build and judge.

    `artifact` is the text a tool runs (RTL, a config, C++), or "" for a parameter-space
    problem whose candidate lives entirely in `knobs` (the declared design decisions);
    `meta` is anything else the problem carries."""

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
        """Identity of the measurement: the artifact's text when there is one, else the knobs (D427)."""
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
    """A part whose generator is another loop (D455).

    Each sub-task has its own gate, stages and record; the parent composes what they decided.
    `problem` is the child's `Problem`; `request` its knobs (None: the parent's, so the child
    writes into the same store); `statement` describes the sub-task for the record and prompts.
    Children are declared (`Problem.subproblems`, a document's `subtasks`) or returned from
    `decompose` at runtime.
    """

    name: str
    problem: Any
    request: "LoopRequest | None" = None
    statement: str = ""


@dataclass
class Option:
    """One step of a problem's improve ladder, as data (D505).

    `why` is the line an orchestrator reads; `due` is the rules' opinion (they take the first
    due option); `run` performs it and returns what `Problem.improve` returns. Rules and an
    agent orchestrator pick from the same list."""

    name: str
    why: str
    due: bool = True
    run: Any = None                         # () -> (Candidate | None, built, reason)


@dataclass(frozen=True)
class Improve:
    """A measured candidate sent back to the generator with what the numbers said (D463).

    Returned by `Problem.route` after a stage. `why` is what the model or template is told;
    `stage` is the evaluator that sent it back; `subgoal` the part it belongs to, if any.
    """

    candidate: Candidate
    why: str
    stage: str = ""
    subgoal: str | None = None
    #: sent back because the campaign was at rest, not because a number fell short: goes straight
    #: to the generator, past the rested improve ladder (D593)
    explore: bool = False


@dataclass(frozen=True)
class Verdict:
    """The gate's answer.

    `score` is the distance from passing (0 == admitted, lower is better), so the loop can keep
    the best refused attempt without knowing units; `why` is the failure text a prompt carries;
    `payload` is the problem's own report."""

    ok: bool
    score: float
    why: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    seconds: float = 0.0        # what the judgement cost, for the row


@dataclass(frozen=True)
class Scored:
    """A candidate after a costed stage: its metrics, the stage, and `payload` for anything
    else the stage returned (a critical path, a per-benchmark table) (D446)."""

    candidate: Candidate
    stage: str
    metrics: dict[str, float]
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.candidate.name


@dataclass(frozen=True)
class LoopRequest:
    """The loop's own knobs, shared by every problem. Problem-specific settings go in `params`."""

    db: str = ""
    steps: int = 24                 # work items per pass: parts, sub-tasks, batches (D457)
    passes: int = 0                 # passes the document caps the run at; 0 = until stopped (D593)
    explore: int = 0                # consecutive at-rest passes before this one; > 0 = send every
                                    # admitted design back to the generator
    repair_attempts: int = 12       # inner-loop attempts per generation
    explore_every: int = 4          # 1 in N generations starts fresh, not from best
    cooldown_after: int = 3         # consecutive no-builds before a subgoal yields
    structured: bool = True         # schema-constrained decoding when the proposer allows
    patching: bool = True           # repair by find/replace edits, not rewrites
    revert_after: int = 3           # failed compile-repairs before reverting to last good
    screen_only: bool = False       # skip the costliest stage
    finalists: int = 3              # confirm this many, spread along the frontier (`Problem.finalists`)
    regress_after: int = 2          # base tolerance for worsening edits; progress earns more (D504)
    max_tolerance: int = 4          # the most regressions in a row the tolerance can grow to
    regenerate: tuple = ()          # parts whose record is history, not a starting point
    prototype: bool = True          # prove the algorithm in Python before the target (D424)
    prototype_unmeasured_stop: int = 4   # consecutive unmeasurable attempts that end a pass; skipped
                                    # when the problem declares no prototype
    prototype_attempts: int = 30    # cheap turns (seconds each), so a bigger budget
    prototype_table_max: int = 64  # max entries in a golden-checked prototype's module-level table
    prototype_shrink_attempts: int = 8   # turns spent making a verified prototype cheaper before it is spelled
    prototype_cost_max: float = 0.0      # a prototype over this cost is never spelled nor synthesised;
                                         # 0 = the default (2,000), negative = no ceiling
    prototype_patience: int = 8     # attempts granted after each new best, past the budget (D506)
    prototype_attempts_max: int = 90   # the most a pass may grow to
    compute_timeout_s: float = 10.0
    tools: bool = True              # the model calls tools inside a turn (compute, check, ...); needs a server with tool calls
    tool_hops: int = 6              # rounds of calls a turn may make before it must answer
    hop_share: float = 0.5          # share of the context window a tool-calling round may write; 0 = the turn's cap
    knowledge_share: float = 0.5    # share of the context window static knowledge may hold; no window = no bound
    compact: str = "rules"         # how over-window text is compacted: "rules" (drop repeats, keep nearest
                                    # paragraphs, digest results) or "llm" (the model condenses) (D549)
    compact_share: float = 0.6      # a tool turn past this share of the window has older rounds compacted
    tool_result_chars: int = 40000  # tool result chars the model reads before the cut; smaller cut the
                                    # check's report mid-table (D543)
    agent: tuple[str, ...] = ()     # the halves the agent takes: "tools", "orchestrate", "plan" (D505)
    plan_file: str | None = None    # a loop plan document (hand-written, or the agent's, kept)
    patch_context_lines: int = 40   # a patch prompt shows this much around a fault
    critique_rounds: int = 1        # times a critic may send a passing part back; 0 = no critic
    calibrate: bool = True          # the calibrate node between stages; `flow: {calibrate: off}` turns it off
    workers: int = 0                # tool runs at once (a stage's candidates, a sweep's points); 0 = the box decides
    batch: int = 1                  # D738: the search's designs one pass carries (made and measured side by side)
    parallel: int = 1               # D747: passes run at once, each its own design and branch (an admin allows it on a server)
    parallel_parts: int = 1         # parts drafted at once on worker threads; admission, the record's row
                                    # and the state stay on the loop's thread (D569)
    ahead: bool = True              # measure an admitted part alone on a worker while the model writes the
                                    # next; `budget: {ahead: false}` keeps the tools in line (D563)
    max_depth: int = 3              # how deep sub-loops may nest; stops a loop that returns itself as a sub-task
    budget_s: float | None = None    # optional wall clock for the step loop, checked before each step;
                                     # None = only `steps` bounds the pass
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentSession:
    """A coding agent's session the loop resumes (D669): the tool, the directory it works in
    (a resume must run where the session began), its id once a turn reported one, its turns."""

    tool: str
    workdir: Path
    id: str | None = None
    turns: int = 0
    schema: str = ""                       # a box's answer schema, said once per session


@dataclass
class PartState:
    """What the loop knows about one part beyond its admitted design: its numbers measured
    alone, its depth proxies, and the improve ladder's counters (D509)."""

    alone: dict[str, float] = field(default_factory=dict)   # its numbers measured alone on the deepest stage
    shrunk: str = ""                                          # digest of the prototype already made cheaper
    depth_by_digest: dict[str, Any] = field(default_factory=dict)   # prototype digest -> logic-depth proxy
    redesigns: int = 0                     # different algorithms asked for this run
    redesign: str | None = None            # the note asking for one, while its pass runs
    optimise: dict[str, Any] | None = None  # {"depth": d0, "target": d} while a depth pass runs
    taken_from: str | None = None          # the record row's digest a taken design came from
    proto_passes: int = 0                  # prototype passes this run (the trace directory's number)
    timing: dict[str, Any] | None = None   # the placed critical path of its design alone, as data
    shortlist: list[dict[str, Any]] = field(default_factory=list)   # its admitted designs on record, best first
    # D669: "generate" / "prototype" -> the agent's session, kept until the part (or its
    # prototype) passes, so repairs and send-backs continue the same conversation
    sessions: dict[str, AgentSession] = field(default_factory=dict)


@dataclass
class LoopState:
    """Everything a pass accumulates; the loop owns it.

    Writers (D440): `records.py` fills `admitted`, `best`, `prototypes` on reload; `loop.py`
    advances `step`, `judged`, `fail_streak`, `plans`, `critiqued`, `admitted`, `best`,
    `refused`, `scored`, `pool`, `lessons`, `not_established`; `generation.py` counts
    `attempts`; `prototype.py` keeps `prototypes`; `drain()` extends `human_notes`.
    A problem only reads, except that it may add a lesson."""

    request: LoopRequest
    say: Callable[[str], None]
    proposer: Any
    feedback: Any
    records: Any = None
    cache: Any = None
    workdir: str = ""
    admitted: dict[str, Candidate] = field(default_factory=dict)   # subgoal -> frozen
    subloops: dict[str, Any] = field(default_factory=dict)          # name -> SubLoop
    improve: list[Improve] = field(default_factory=list)   # what an evaluator sent back
    children: dict[str, Any] = field(default_factory=dict)          # name -> the child LoopResult
    depth: int = 0                                                  # 0 = the top-level pass
    best: dict[str, tuple[float, Candidate, str]] = field(default_factory=dict)
    attempts: dict[str, int] = field(default_factory=dict)
    fail_streak: dict[str, int] = field(default_factory=dict)
    scored: list[Scored] = field(default_factory=list)
    pool: list[Candidate] = field(default_factory=list)   # batches: gate-admitted, in order
    refused: list[tuple[str, str]] = field(default_factory=list)
    lessons: list[str] = field(default_factory=list)
    not_established: list[str] = field(default_factory=list)
    human_notes: list[Any] = field(default_factory=list)
    prototypes: dict[str, str] = field(default_factory=dict)   # part -> VERIFIED prototype
    proto_best: dict[str, tuple[float, str, str]] = field(default_factory=dict)  # best refused prototype per part (score, code, why)
    plans: dict[str, dict[str, Any]] = field(default_factory=dict)  # part -> brief, budget
    dse: list[dict[str, Any]] = field(default_factory=list)   # the seeds, then what each DSE phase did
    plan: dict[str, Any] = field(default_factory=dict)   # the loop plan this pass follows (applied)
    critiqued: dict[str, int] = field(default_factory=dict)     # part -> times sent back
    judged: int = 0
    step: int = 0
    stopped: str = ""                                    # why the step loop stopped
    parts: dict[str, PartState] = field(default_factory=dict)   # per-part numbers and counters
    versions: dict[str, str] = field(default_factory=dict)     # the problem's transpiler/judge versions
    trying: tuple[str, str | None] | None = None         # (part, method) under way, for the standings
    standings_args: tuple | None = None                  # what the last `_publish` was called with
    rested: list[str] = field(default_factory=list)      # parts whose improve step chose to stand
    sent_back: int = 0                                   # designs the stages routed back this pass
    last_prompt_sha: str | None = None                   # digest of the last model turn's prompt, for its rows
    improved: int = 0                                    # improve steps that produced a design
    fresh: bool = True         # something changed since the chain was last climbed
    explored: bool = False     # this pass already sent its designs back to explore
    search_done: bool = False  # the search handed over its last batch (a sweep knows)
    routed: set = field(default_factory=set)   # (candidate key, stage) already sent back once
    bias: dict[tuple[str, str], Any] = field(default_factory=dict)   # (stage, metric) -> Bias:
                                                                     # what a costly stage said
                                                                     # about a cheap one
    compositions: set = field(default_factory=set)   # keys of the composed designs measured this pass
    reached: str = ""          # the stage the chain reached, and that stage's pool,
    on_stage: dict[str, Any] = field(default_factory=dict)      # read by the decision
    started: float = 0.0
    ahead: Any = None          # `flux_loop.measure.Ahead`: the tools working while the model thinks
    tool_runs: int = 0         # measurements the tools ran this pass, and what the cache served
    prompt_sha_by_thread: dict = field(default_factory=dict)   # the last prompt's digest, per drafting thread
    cited: dict = field(default_factory=dict)   # prompt digest -> the library files its excerpts cite (D648)
    cache_hits: int = 0
    #: stage -> {"skipped": estimated to fail, "measured": estimated and measured} (D665)
    estimates: dict = field(default_factory=dict)
    agent_sessions: dict[str, AgentSession] = field(default_factory=dict)   # box -> its `session: pass` session (D669)
    pending_turns: list[dict[str, Any]] = field(default_factory=list)       # agent_turn rows drafted off the loop's thread
    loop_thread: int = field(default_factory=threading.get_ident)          # the thread that owns the record

    def part(self, name: str | None) -> PartState:
        """The part's state, made on first use."""
        return self.parts.setdefault(name or "*", PartState())

    @property
    def ledger(self) -> "Ledger":
        """The campaign's ledger over this run's record."""
        return Ledger(self.records)

    def drain(self) -> str | None:
        """Operator guidance since the last drain, rendered for a prompt, plus earlier runs'
        notes reloaded from the record (stamped as such) (D403)."""
        from flux_feedback import drain_guidance, guidance_lesson, note_sink

        # With no model role the note is recorded but reaches no prompt, and says so (D388).
        reaches = "the next prompt" if self.proposer is not None else None
        on_note = note_sink(self.records,
                            lambda text: self.lessons.append(guidance_lesson(text,
                                                                             reaches=reaches)))
        return drain_guidance(self.feedback, self.human_notes, on_note=on_note)


@dataclass(frozen=True)
class LoopResult:
    """What a pass concluded.

    `scored` is every measurement, each with its `stage`; `frontier` is over the stage the
    decision was made on; `confirmed` is that stage's results when it was not the first (D454)."""

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
    #: why the pass stopped: "the step budget", "the wall clock", "good enough: <why>",
    #: "nothing left to do"
    stopped: str = ""
    #: the pass changed nothing and every design sent back stood: every ladder is spent, so a
    #: caller that loops passes stops rather than spinning
    at_rest: bool = False
    #: whether a model could draft something new; an at-rest pass with nothing explorable waits
    #: for a stop or a note (D593)
    explorable: bool = True
    #: D802: each sub-loop's own result, by name (a parent of sub-loops that composes no whole)
    children: dict[str, Any] = field(default_factory=dict)

    @property
    def cut_short(self) -> bool:
        """Whether the pass ran out of budget rather than finishing what it had to do."""
        return self.stopped in ("the step budget", "the wall clock")

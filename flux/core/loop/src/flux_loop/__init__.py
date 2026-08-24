"""flux_loop: the problem-agnostic loop (D421).

Every Flux application is the same five nodes talking to each other -- the drawing
Cedric made, and the repository's top-level directories:

    input / feedback / knowledge / records  -->  (1) ORCHESTRATION   orchestrator (+model)
                                                        |
                                                 (2) GENERATION      generator (+model)
                                                        |  <-->  (3) TEST / VALIDATE   evaluator (fast)
                                                        v
                                                 (4) EVALUATION      evaluator (the chain)
                                                        |
                                            (5) records  <--'  --> output

This package is the loop; a PROBLEM is what plugs into it. A new problem implements
`Problem` -- what a candidate is, how to ask a model for one, how to build it, the
fast test the generator iterates against, the gate that admits, the costlier stages
that measure, how to compose sub-goal results, how to decide -- and `run_loop` does
everything else: the campaign record and its read-back, operator feedback, resuming
from proven and best-so-far designs, the planner with its cooldowns, the
generation<->test inner loop with structured decoding, find/replace patching,
revert-on-breakage and problem-supplied tools, the exhaustive-or-not judge, the
composition of admitted sub-goals, the cached evaluation chain, the frontier, the
decision and the conclusion written back.

Divide / conquer / combine (the third drawing) is native: `Problem.subgoals()`
names the parts, each part runs the inner loop, admitted parts are frozen, and
`Problem.compose` joins them for the outer evaluation.

A part may itself be a LOOP (`SubLoop`, D455): a problem too big to write as one
candidate is divided into sub-tasks, each with its own gate, its own stages and its
own record, and the parent composes what they decided. The division comes from
either side -- declared by the problem (`subproblems`, a task document's `subtasks`)
or decided by the orchestrator at runtime and returned from `decompose` -- and the
loop treats both as the same work item.

The CHAIN OF EVALUATORS is stage by stage (D454), and fast-then-slow is the common case
rather than the rule -- a stage is whatever the problem declares, in its order: each stage's
results are measured, `Problem.cutoff` drops what cannot be the answer whatever else it
does, the frontier and `finalists` choose who is worth the next stage, and the decision is
made on the highest stage that produced results. Every stage's numbers stay separate, because
comparing a placed number with a screened one compares fidelities rather than designs.

`calibrate` is a node (D464): after every step up the chain,
`flux_loop.calibrate.bias` says how far the costly stage's numbers were from the cheap stage's
over the designs both measured, in the report, the record and `Problem.calibrated`.

AN EVALUATOR HAS TWO EDGES OUT (D463). Everything measured reaches the orchestrator, which
chooses what to try next; `Problem.route(stage, scored, state)` is the other edge, back to
the generator, handing a design and ITS NUMBERS over to be improved. An `Improve` is a kind
of work like a part or a batch, and climbing the chain is one too, so a pass can go
evaluate -> improve -> evaluate rather than leaving it to the next run. `LoopRequest.budget_s`
(a wall clock) and `Problem.good_enough` (a target met) stop a pass early when a caller asks
for it, both off by default; `LoopResult.stopped` says what ended it. `Problem.validate`
refuses a mis-posed problem before anything is spent.

THE FOUR ROLES ARE SWAPPABLE COMPONENTS (D460), each with an AI and a no-AI half:
orchestration (`roles.Rules` / `Given` decide in code or from the user's own list,
`roles.ModelOrchestrator` asks a model), generation (`sources.Template` / `Catalog` /
`Solver` against `sources.Model`), evaluation (the problem's `stages`: analytical models and
real tools) and knowledge (`flux_knowledge`'s declared sources). `Roles` is the bundle, and
`flux_loop.rig(orchestrator="rules", ...)` builds one from names -- so a document, a command
line or an application switches one role without touching the other three. Every slot `None`
(the default) is the problem's own hooks.

GENERATION is a sub-loop of the same shape (D456): draft, build, fast-check, and again
with the failure in hand. `Problem.generator` names WHO drafts -- `sources.Model` (the
model inner loop with patching, reverting and prototypes), `sources.Template` (a renderer
the framework runs, no model), `sources.Catalog` (designs that already exist) or
`sources.Solver` (a candidate computed from the constraints and told the last
counter-example) -- and the loop around it, its phases and its refusal reasons do not
change with the choice.

A problem that ENUMERATES, SOLVES or PROPOSES candidates in batches instead of
writing one part at a time works in BATCHES (D446): `Problem.search` is a
generator yielding them; the loop gates each batch, measures the admitted on the
first stage together (`measure_batch`), hands the batch's `Scored` back to the
generator, and then climbs the chain over everything measured -- `frontier`,
`finalists`, the last stage, `decide` -- exactly as for a composed part. The
applications that predate the document (bankmap, interconnect mapping, prefetcher,
omni) run on it this way.

What is deliberately NOT here: prompts' wording, reference functions, tool
adapters -- those are the problem's. The closing grammar every report ends with is
`flux_loop.report`'s (D558), so a loop's closing sections keep the shared vocabulary.
"""

from __future__ import annotations

from .calibrate import Bias, bias
from .compute import preamble_lines, run_compute, screen_snippet  # noqa: F401
from .capabilities import Prototype, Target, Toolkit
from .check import Judgement, check_prototype
from .ladder import Ladder
from .ledger import Entry, Kind, Ledger
from .objective import Objective, Objectives
from .provenance import git_revision, stamp, toolchain, trace_dir, trace_root, turn_cost
from .cutoff import Cutoff, above, below, within_best
from .generation import _generate_with_model  # noqa: F401  (tests drive the inner loop directly)
from .gradient import Gradient
from .graph import FLOW, GROUPS, NODES, Node, node  # noqa: F401  (the node vocabulary)
from .loop import run_loop
from .measure import cached_measure, measure_many  # noqa: F401  (a problem's own batch path)
from .patch import apply_patch, focus_window, parse_patch, patch_prompt, patch_schema
from .problem import EvaluatorRole, GeneratorRole, MentorRole, OrchestratorRole, Problem
from .prototype import PROTOTYPE_HELP, prototype_schema  # noqa: F401
from .records import _reload  # noqa: F401  (tests resume a record directly)
from .records import prototype_digest  # noqa: F401
from .roles import (Given, ModelOrchestrator, Orchestrator, ROLES, Roles, Rules,
                    available_roles, make_role, register_role, rig)
from .sources import (Attempt, Catalog, Model, Solver, Source, Template, from_file,
                      iterate)
from .task import (PromptProblem, TaskError, TaskSpec, load_task, request_for,
                   task_report_lines)
from .types import (BuildError, Candidate, Improve, LoopRequest, LoopResult, LoopState, Option,
                    PartState, StageNames, Scored, SubLoop, Verdict)

__all__ = ["Entry", "Judgement", "Kind", "Ladder", "Ledger", "Prototype", "Target", "Toolkit", "check_prototype", "Objective", "Objectives", "PartState", "git_revision", "stamp", "toolchain", "trace_dir", "trace_root", "turn_cost", "FLOW", "GROUPS", "NODES", "Node", "node",
    "Attempt", "Bias", "Catalog", "Cutoff", "Given", "Model", "bias",
    "ModelOrchestrator", "Orchestrator", "ROLES", "Roles", "Rules", "Solver", "Source", "Template", "available_roles",
    "from_file", "iterate",
    "make_role", "register_role", "rig",
    "above", "below", "within_best",
    "BuildError", "Candidate", "EvaluatorRole", "GeneratorRole", "Gradient", "Improve", "Option",
    "LoopRequest",
    "LoopResult", "LoopState", "MentorRole", "OrchestratorRole", "Problem", "PromptProblem",
    "StageNames", "Scored", "SubLoop", "TaskError", "TaskSpec", "Verdict", "apply_patch",
    "focus_window", "load_task", "parse_patch", "patch_prompt", "patch_schema",
    "preamble_lines", "prototype_schema", "request_for", "run_compute", "screen_snippet", "run_loop", "task_report_lines",
]

"""flux_loop: the problem-agnostic loop (D421).

Every Flux application is the same five nodes, mirrored by the top-level directories:

    input / feedback / knowledge / records  -->  (1) ORCHESTRATION   orchestrator (+model)
                                                        |
                                                 (2) GENERATION      generator (+model)
                                                        |  <-->  (3) TEST / VALIDATE   evaluator (fast)
                                                        v
                                                 (4) EVALUATION      evaluator (the chain)
                                                        |
                                            (5) records  <--'  --> output

A problem implements `Problem` -- what a candidate is, how to ask a model for one, how to build
it, the fast test, the admitting gate, the costlier stages, how to compose sub-goal results,
how to decide -- and `run_loop` does the rest: the campaign record and resume, operator
feedback, the planner, the generation<->test inner loop (structured decoding, patching,
revert-on-breakage, problem tools), composition, the cached evaluation chain, the frontier,
the decision and the conclusion.

Divide / conquer / combine: `Problem.subgoals()` names the parts, each runs the inner loop,
admitted parts are frozen, and `Problem.compose` joins them. A part may itself be a loop
(`SubLoop`, D455), declared by the problem or returned by the orchestrator's `decompose`.

The chain of evaluators runs stage by stage in the problem's order (D454): `Problem.cutoff`
drops what cannot be the answer, the frontier and `finalists` choose who goes on, and the
decision is made on the highest stage with results. Stages' numbers stay separate, since
comparing fidelities is not comparing designs. `flux_loop.calibrate.bias` reports how far a
costly stage was from the cheaper one on shared designs (D464).

An evaluator has two edges out (D463): everything measured reaches the orchestrator, and
`Problem.route` hands a design with its numbers back to the generator as an `Improve`.
`LoopRequest.budget_s` and `Problem.good_enough` stop a pass early (both off by default);
`LoopResult.stopped` says what ended it. `Problem.validate` refuses a mis-posed problem first.

The four roles are swappable, each with an AI and a no-AI half (D460): orchestration
(`roles.Rules` / `Given` / `roles.ModelOrchestrator`), generation (`sources.Template` /
`Catalog` / `Solver` / `Model`), evaluation (the problem's `stages`) and knowledge
(`flux_knowledge`'s declared sources). `flux_loop.rig(orchestrator="rules", ...)` builds a
`Roles` bundle from names; every slot `None` means the problem's own hooks.

Generation is a sub-loop of the same shape (D456): draft, build, fast-check, retry with the
failure. `Problem.generator` names who drafts; the loop around it does not change.

A problem that enumerates, solves or proposes in batches uses `Problem.search`, a generator
of batches (D446): the loop gates each, measures the admitted on the first stage together,
hands the `Scored` back, then climbs the chain as for a composed part.

Not here: prompt wording, reference functions, tool adapters -- those are the problem's. The
closing report grammar is `flux_loop.report`'s (D558).
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
from .task import PromptProblem, task_report_lines
from .document import TaskError, TaskSpec, load_task, request_for
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

"""Command implementations for the Flux CLI."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import flux_ir
from flux_evaluator_abi import Budget, Candidate
from flux_store import ResultStore

from flux_evaluator_abi import DEFAULT_METRICS, evaluator_name_for, make_evaluator

def _detect_kind(doc: dict[str, Any]) -> str:
    """Best-effort IR kind detection from a document's shape; `--kind` overrides it."""
    if "ops" in doc:
        return "workload"
    if "hierarchy" in doc:
        return "architecture"
    if "for_op" in doc:
        return "mapping"
    raise ValueError(
        "could not auto-detect IR kind (found none of 'ops', 'hierarchy', 'for_op'); "
        "pass --kind explicitly"
    )


def cmd_import(args: argparse.Namespace) -> int:
    doc = flux_ir.load_document(args.file)
    try:
        kind = args.kind or _detect_kind(doc)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        flux_ir.validate(kind, doc)
    except flux_ir.SchemaValidationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    content_hash = flux_ir.content_hash(doc)
    print(f"kind: {kind}")
    print(f"id:   {doc.get('id', '<no id>')}")
    print(f"hash: {content_hash}")

    if args.store:
        with ResultStore(args.store) as store:
            stored_hash = store.put_document(kind, doc)
            assert stored_hash == content_hash
        print(f"stored in {args.store}")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    workload = flux_ir.load_document(args.workload)
    try:
        flux_ir.validate("workload", workload)
    except flux_ir.SchemaValidationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    arch = None
    if args.arch:
        arch = flux_ir.load_document(args.arch)
        try:
            flux_ir.validate("architecture", arch)
        except flux_ir.SchemaValidationError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    metrics = frozenset(args.metrics.split(",")) if args.metrics else DEFAULT_METRICS
    candidate = Candidate(workload=workload, arch=arch, mapping=None)

    try:
        evaluator = make_evaluator(args.backend)
        result = evaluator.evaluate(candidate, Budget(), metrics)
    except Exception as exc:  # noqa: BLE001 — surfaced to the user, not swallowed
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result.to_dict(), indent=2))

    if args.store:
        with ResultStore(args.store) as store:
            workload_hash = store.put_document("workload", workload)
            arch_hash = store.put_document("architecture", arch) if arch is not None else None
            row_id = store.put_result(result, workload_hash=workload_hash, arch_hash=arch_hash)
        print(f"stored in {args.store}: result id={row_id}", file=sys.stderr)
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    with ResultStore(args.store) as store:
        record = store.get_result(args.result_id)
        if record is None:
            print(f"error: no result with id={args.result_id} in {args.store}", file=sys.stderr)
            return 1

        workload = store.get_document(record["workload_hash"])
        if workload is None:
            print(
                f"error: workload {record['workload_hash']} referenced by result "
                f"{args.result_id} is not in {args.store} (was it stored with `flux eval "
                "--store`?)",
                file=sys.stderr,
            )
            return 1
        arch = store.get_document(record["arch_hash"]) if record["arch_hash"] else None
        # The mapping is part of the candidate: replaying without it silently re-evaluates a
        # different design (D189).
        mapping = store.get_document(record["mapping_hash"]) if record["mapping_hash"] else None
        if record["mapping_hash"] and mapping is None:
            print(
                f"error: mapping {record['mapping_hash']} referenced by result "
                f"{args.result_id} is not in {args.store} — replaying without it would evaluate "
                "a different candidate",
                file=sys.stderr,
            )
            return 1

    try:
        backend_name = evaluator_name_for(record["evaluator"])
        evaluator = make_evaluator(backend_name)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    candidate = Candidate(workload=workload, arch=arch, mapping=mapping)
    stored_metrics: dict[str, Any] = record["result"]["metrics"]
    metrics = frozenset(stored_metrics)

    try:
        fresh_result = evaluator.evaluate(candidate, Budget(), metrics)
    except Exception as exc:  # noqa: BLE001 - same "error:, exit 1" shape as every path above
        print(f"error: re-evaluation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    fresh_metrics = fresh_result.to_dict()["metrics"]

    print(f"replaying result id={args.result_id} (backend={backend_name})")
    all_match = True
    for metric_name, stored_estimate in stored_metrics.items():
        stored_value = stored_estimate["value"]
        fresh_value = fresh_metrics.get(metric_name, {}).get("value")
        match = fresh_value == stored_value
        all_match = all_match and match
        status = "OK" if match else "MISMATCH"
        print(f"  {metric_name:20s} stored={stored_value!r:>15}  fresh={fresh_value!r:>15}  [{status}]")

    if all_match:
        print("replay: all metrics match")
        return 0
    print("replay: MISMATCH — see above", file=sys.stderr)
    return 1


def _library_line(task: Any, problem: Any) -> str:
    """What the library holds and who reads it (D648), one line."""
    from pathlib import Path

    from flux_knowledge import status
    from flux_knowledge.library import library_files
    from flux_loop.document import LIBRARY_FOLDER, library_folders, library_on

    folders = library_folders(task)
    st = status(folders)
    if not st["documents"]:
        return (f"library: empty -- drop papers in {LIBRARY_FOLDER}/ beside the document, "
                f"or in {st['path']} for every loop")
    own = sum(len(library_files([f])) - len(library_files()) for f in folders)       # D735: the loop's own
    head = (f"library: {st['documents']} documents ({st['pdfs']} PDFs, pdftotext "
            f"{'present' if st['pdftotext'] else 'missing'})"
            + (f", {own} of them the loop's own ({', '.join(Path(f).name + '/' for f in folders)})" if folders else ""))
    if not library_on(task):
        return head + ", off (flow.knowledge: off)"
    try:
        mentor = problem.knowledge()
        used = mentor is not None and hasattr(mentor, "source") and mentor.source("library") is not None
    except Exception:  # noqa: BLE001
        used = False
    return head + (", used by: prompts, plan, agents" if used else ", not read by this world's knowledge")


def pick_document(path: str) -> str | None:
    """The document a `task run`/`task check` of a folder means (D787): the folder itself when it
    holds one problem that loads; with several, the one the user picks at the terminal -- or,
    with no terminal, None, the problems said."""
    from pathlib import Path

    from flux_loop import TaskError, load_task
    from flux_loop.document import ManyDocuments, record_name

    if not Path(path).is_dir():
        return path
    try:
        load_task(path)
        return path
    except ManyDocuments as exc:
        docs, said = exc.documents, str(exc)
    except TaskError:
        return path                                   # the command says why, as for one document
    if not sys.stdin.isatty():
        print(f"{said}: run one of them --\n" + "\n".join(f"  {d}" for d in docs))
        return None
    print(f"{path}: {len(docs)} problems here")
    for i, d in enumerate(docs, 1):
        print(f"  {i}. {d.name:<28} record {record_name(d)}")
    while True:
        try:
            got = input(f"which one [1-{len(docs)}]? ").strip()
        except EOFError:
            return None
        if got.isdigit() and 1 <= int(got) <= len(docs):
            return str(docs[int(got) - 1])
        hit = [d for d in docs if got in (d.name, d.name.split(".")[0])]
        if len(hit) == 1:
            return str(hit[0])


def cmd_task_check(args: argparse.Namespace) -> int:
    """Validate a task document, list what it declares, and name any tool it needs that
    is not on PATH; runs nothing."""
    from flux_loop import PromptProblem, TaskError, load_task

    try:
        task = load_task(args.file)
    except TaskError as exc:
        print(f"not a task: {exc}")
        return 2
    problem = PromptProblem(task)
    print(f"task {task.id}: {task.statement[:100]}")
    print(f"  language {task.language} ({task.extension}); parts: "
          + ("decided by the model (decompose)" if task.decompose
             else ", ".join(p.name for p in task.parts) or "one goal"))
    if task.split or task.subtasks:      # each one is its own loop
        print("  sub-tasks: " + ("decided by the model (decompose), at most "
                                 f"{task.max_subtasks}" if task.split
                                 else ", ".join(c.id for c in task.subtasks)))
    print("  drafted by: " + _drafted_by(task))
    print("  roles: " + _roles_line(task))
    from flux_loop.document import describe_flow

    print("  flow (D542): one line per box of the drawing, the half in force")
    for line in describe_flow(task, problem):
        print(f"    {line}")
    if task.gate:                                      # the checks in order, each with its pass rule (D652)
        print(f"  gate: {len(task.gate)} check(s) in order; the first that fails refuses the design")
        for i, c in enumerate(task.gate, 1):
            print(f"    {i}. {c.name}: {' '.join(c.run)}\n       {c.rule}")
    for label, cmd in task.commands():
        if not label.startswith("gate "):
            print(f"  {label}: {' '.join(cmd)}")
    print("  stages: " + ", ".join(_stage_label(r) for r in task.stages) if task.stages
          else "  stages: " + ", ".join(problem.stages()))
    from flux_loop import Objectives

    print("  objectives: " + (Objectives(task.objectives).describe() or "none"))    # limits, then what decides (D658)
    print(f"  record: {task.record}")
    if task.ladder:
        print("  ladder: " + ("the default" if task.ladder is True else ", ".join(f"{k}={v}" for k, v in task.ladder.items())))
    if task.knowledge_sheet:
        print(f"  knowledge: {task.knowledge_sheet} ({len(task.knowledge)} chars)")
    print("  " + _library_line(task, problem))
    from flux_loop.golden_proto import golden_path

    gp = golden_path(task)
    if gp is not None and not gp.is_file():
        print(f"  golden model: {gp.name} does not exist -- the model writes it before the first pass (D604)")
    elif gp is not None and (cap := problem.prototype()) is not None:
        how = ("then spelled as RTL by the loop (D604, D611)" if cap.language == "python"
               else "then the model writes the RTL from it (D635)")
        print(f"  prototype: {cap.language}, checked against {gp.name}'s vectors, {how}"
              + ("" if task.budget.get("prototype", True) else " -- off here (budget.prototype: false)"))
    from flux_loop import request_for

    wrong = problem.validate(request_for(task))           # what a run's first phase would refuse
    for why in wrong:
        print(f"  NOT ANSWERABLE: {why}")
    missing = problem.tools_missing()
    skipped = problem.skipped_stages()
    for name, tools in skipped:
        print(f"  WILL SKIP stage {name}: needs {', '.join(tools)}, not on PATH")
    if missing:
        print(f"  MISSING: {', '.join(missing)}")          # tools not on PATH, or a world's inputs (traces)
        return 1
    if wrong:
        return 1
    print("  tools: " + ("present for every stage" if not skipped else
                         "present for the gate and the stages that run (see WILL SKIP above)"))
    from flux_loop.agent_check import agents_used, check_agent

    unready = []
    for agent in agents_used(task):                      # D751: the agents it hands work to, set up for you?
        got = check_agent(agent)
        bad = next((st for st in got["steps"] if not st["ok"]), None)
        print(f"  agent {agent}{' ' + got['version'] if got.get('version') else ''}: "
              + ("set up (`flux agent test " + agent + " --live` asks it)" if bad is None else f"NOT READY: {bad['step']}: {bad['said']}"))
        if bad is not None:
            unready.append(agent)
    if unready:
        return 1
    from flux_loop.task import model_use

    if model_use(task):                                  # the model a run would use, asked now
        try:
            from flux_llm import OpenAIChatProposer, describe_model

            prop = OpenAIChatProposer(None)
            down = prop.preflight()
            print(f"  {describe_model(prop)}: " + (f"NOT READY: {down}" if down else "ready"))
        except Exception as exc:  # noqa: BLE001
            print(f"  model: NOT READY: {exc}")
    return 0


def _roles_from(flags: list[str] | None) -> Any:
    """`--role orchestrator=rules` (repeatable) as a `Roles` bundle. Only the roles
    named are filled, so the document's own choices for the others stand."""
    from flux_loop import Roles, make_role

    if not flags:
        return None
    out = Roles()
    for flag in flags:
        role, _, name = str(flag).partition("=")
        if not name:
            raise ValueError(f"--role takes ROLE=NAME, not {flag!r}")
        out = out.with_role(role.strip(), make_role(role.strip(), name.strip()))
    return out


def _drafted_by(task: Any) -> str:
    """Who writes the candidates: the model unless the document says otherwise; a parent's are
    its sub-loops' (D804: drafted by its model or agent, unless a folder says otherwise)."""
    spec = task.generator
    if getattr(task, "subtasks", None):
        if spec.get("command"):
            return "its sub-loops, composed by the generator command"
        who = f"the coding agent `{spec['agent']}`" if spec.get("agent") else "a model"
        return f"its sub-loops, each by {who} unless its folder says otherwise"
    if spec.get("catalog"):
        return f"a catalog of {len(spec['catalog'])} design(s) that already exist (no model)"
    if spec.get("command"):
        return "the generator command (no model)"
    if spec.get("agent"):
        return f"the coding agent `{spec['agent']}`"
    return "a model"


def _roles_line(task: Any) -> str:
    """Who fills each of the four roles, and what else this loop could be switched to."""
    from flux_loop import ROLES, available_roles

    said = dict(task.roles)
    if task.generator and "generator" not in said:
        said["generator"] = (f"the coding agent {task.generator['agent']}" if task.generator.get("agent")
                             else "the document's own generator command/catalog")
    parts = []
    for role in ROLES:
        chosen = said.get(role)
        choices = available_roles(role)
        parts.append(f"{role}={chosen if chosen else 'the problem default'}"
                     + (f" (or: {', '.join(c for c in choices if c != chosen)})" if choices
                        else ""))
    return "; ".join(parts)


def _stage_label(stage: Any) -> str:
    """A stage, what it needs on PATH and what it takes to climb past it."""
    needs = f" [needs {', '.join(stage.needs)}]" if getattr(stage, "needs", ()) else ""
    gates = [c for c in stage.cutoffs if c]
    if not gates:
        return stage.name + needs

    def said(cut: dict) -> str:
        if "at" in cut:
            return f"{cut['metric']} >= {cut['at']:g}"
        if "below" in cut:
            return f"{cut['metric']} <= {cut['below']:g}"
        return f"{cut['metric']} within {float(cut['within']):.0%} of the best"

    if len(gates) == 1:
        return f"{stage.name}{needs} (cutoff: {said(gates[0])})"
    return f"{stage.name}{needs} (cutoffs, all must pass: " + ", then ".join(said(c) for c in gates) + ")"


def cmd_task_run(args: argparse.Namespace) -> int:
    """Run a task document through the loop and print the standard report, optionally in the
    TUI, for N passes, with the agent's halves and reasoning."""
    from pathlib import Path

    from flux_loop import (PromptProblem, TaskError, load_task, request_for, run_loop,
                           task_report_lines)

    try:
        task = load_task(args.file)
        if getattr(args, "skill", None):                 # skills beside the document's own
            from dataclasses import replace as _replace

            from flux_loop.skills import SkillError, load_skills

            try:
                extra = tuple(str(sk.path) for sk in load_skills(args.skill))
            except SkillError as exc:
                raise TaskError(f"--skill: {exc}") from exc
            task = _replace(task, skills=tuple(dict.fromkeys(task.skills + extra)))
    except TaskError as exc:
        print(f"not a task: {exc}")
        return 2
    try:
        problem = PromptProblem(task, roles=_roles_from(getattr(args, "role", None)))
    except (TaskError, ValueError) as exc:
        print(f"not a role this loop has: {exc}")
        return 2
    if problem.roles().named():
        print("roles: " + ", ".join(f"{r}={n}" for r, n in sorted(problem.roles().named().items())))
    missing = problem.tools_missing()
    if missing:
        print(f"{', '.join(missing)} not on PATH; `flux task check` lists what the task needs")
        return 1
    for name, tools in problem.skipped_stages():
        print(f"warning: stage {name} will not run -- needs {', '.join(tools)}, not on PATH")
    db = args.db or str(task.out_dir() / f"{(task.record or task.id).split('/')[0]}.db")          # beside the document, under out/
    overrides: dict[str, Any] = {"db": db}
    for flag, knob in (("steps", "steps"), ("repair", "repair_attempts"), ("tool_hops", "tool_hops"),
                       ("hop_share", "hop_share"), ("patience", "prototype_patience")):
        value = getattr(args, flag, None)
        if value is not None:
            overrides[knob] = value
    if args.no_structured:
        overrides["structured"] = False
    if getattr(args, "no_patching", False):
        overrides["patching"] = False
    if getattr(args, "no_prototype", False):
        overrides["prototype"] = False
    if getattr(args, "screen_only", False):
        overrides["screen_only"] = True
    if getattr(args, "regenerate", None):
        overrides["regenerate"] = tuple("*" if o == "all" else o for o in args.regenerate)
    halves = tuple(getattr(args, "agent", ()) or ())
    if "all" in halves:
        halves = ("tools", "orchestrate", "plan")
    if halves:
        overrides["agent"] = halves
        overrides["tools"] = "tools" in halves
    if getattr(args, "plan", None):
        overrides["plan_file"] = args.plan
    request = request_for(task, **overrides)
    if args.replies:
        from flux_llm import ScriptedProposer

        proposer: Any = ScriptedProposer(json.loads(Path(args.replies).read_text()))
        model_name = "scripted replies"
    else:
        from flux_llm import OpenAIChatProposer

        proposer = OpenAIChatProposer(args.model, num_predict=int(getattr(args, "num_predict", None) or 6000))
        model_name = f"{proposer.model} (predict {getattr(args, 'num_predict', None) or 6000})"
        if getattr(args, "think", False):
            from flux_llm import set_think_override

            set_think_override(True)
    if not args.replies:
        from flux_loop.task import model_use

        needs = model_use(task)
        down = proposer.preflight() if needs else ""
        if down and needs != "its world may ask one":
            print(f"cannot start: {down}")                     # the model writes the candidates: no run without it
            return 1
        if down:
            print(f"warning: {down}; the steps that ask a model will be skipped")
    from flux_loop.author import write_golden

    fault = write_golden(task, proposer, say=print)            # a golden the document names but lacks
    if fault:
        print(f"no golden model to test against: {fault}")
        return 1
    problem.__dict__.pop("_golden_cap", None)                 # the prototype stage reads the new file
    tui = bool(getattr(args, "tui", False))
    passes = int(request.passes if getattr(args, "passes", None) is None else args.passes)   # the document's
    if args.replies and getattr(args, "passes", None) is None and not request.passes:
        passes = 1                                     # a script is finite: one pass unless asked for more
    if not tui:                                        # every run says what it uses, up front
        from flux_llm import describe_model
        from flux_loop.provenance import trace_root

        from flux_loop.task import model_use

        uses = model_use(task)
        said = ("model: scripted replies" if args.replies else
                f"{describe_model(proposer)} -- {uses}" if uses else "model: none needed (no step of this document asks one)")
        print(f"{task.id}: {said}"
              f"\n  record: {db}\n  traces: {trace_root()}")

    no_feedback = task.flow.get("feedback") == "none"     # the document declined the channel

    def _passes(fb=None):
        """Run passes until stopped (`flux stop`, Ctrl-C, q) or the `--passes` / `budget.passes`
        cap; a pass at rest is followed by one that explores (D593)."""
        from flux_loop.passes import run_passes

        return run_passes(lambda req, feed: run_loop(problem, req, proposer=proposer, feedback=feed, log=print),
                          request, passes=passes, feedback=fb, proposer=proposer, notes=not no_feedback)

    def _print(out) -> None:
        print()
        print("\n".join(task_report_lines(task, out, problem)))

    objectives = problem.objectives().describe()       # every limit, then what decides (D660)
    info = {"db": db, "parts": " ".join(problem.subgoals()) or "(one artifact)",
            "objectives": objectives or "the gate",
            "budget": f"{request.steps} steps x {request.repair_attempts} generation attempts",
            "model": model_name + (" (reasoning on)" if getattr(args, "think", False) else ""),
            "agent": (", ".join(halves) + (f" ({request.tool_hops} hops)" if "tools" in halves else "")
                      if halves else "off: the code's own rules")}
    try:
        from flux_tui import demo_run

        out = demo_run(_passes, tui=tui, title=f"flux · {task.id}", subtitle=db,
                       print_report=_print, info=info)
    except KeyboardInterrupt:
        from flux_loop.passes import mark

        mark("ended", why="stopped now")
        print("run abandoned; the campaign record holds what was judged")
        return 130
    _print(out)
    target = None
    if out.decision is not None:
        target = Path(args.out or task.out_dir() / f"{task.id}{task.extension}")
        target.write_text(out.decision.candidate.artifact)
        print(f"\nartifact written to {target}")
    if getattr(args, "json", None):
        Path(args.json).write_text(json.dumps(_answer(task, db, out, problem, target), indent=2, default=str))
        print(f"answer written to {args.json}")
    _mark_outputs(task, out, problem, target, getattr(args, "json", None))
    return 0 if out.decision is not None else 1


def _mark_outputs(task, out, problem, target: Any, answer: str | None) -> None:
    """The run's end in its journal (D739): the decision, what it established, the files written."""
    from flux_loop.passes import mark
    from flux_loop.task import task_report_lines

    lines = task_report_lines(task, out, problem)
    head = next((i for i, ln in enumerate(lines) if ln.strip().startswith("WHAT THIS RUN ESTABLISHED")), None)
    established = [ln.strip() for ln in lines[head + 1:] if ln.strip()][:20] if head is not None else []
    dec = out.decision
    mark("outputs", stopped=out.stopped, decided_by=out.decided_by,
         decision=({"name": dec.candidate.name, "stage": dec.stage, "metrics": dec.metrics} if dec is not None else None),
         front=len(out.frontier), refused=len(out.refused), lessons=list(out.lessons)[-12:], established=established,
         not_established=list(out.not_established)[:12], design=str(target) if target else None, answer=answer)


def _answer(task, db: str, out, problem, artifact: Any) -> dict[str, Any]:
    """`flux task run --json`: what the pass decided, for a script -- the decision, the
    frontier, what was refused and why, what is not established, and the world's own answer
    (its `result` hook) when it has one."""
    from flux_loop.task import task_report_lines

    def row(sc) -> dict[str, Any]:
        return {"name": sc.candidate.name, "stage": sc.stage, "knobs": sc.candidate.knobs, "metrics": sc.metrics}

    answer: dict[str, Any] = {
        "task": task.id, "record": db, "stopped": out.stopped, "decided_by": out.decided_by,
        "decision": row(out.decision) if out.decision is not None else None,
        "artifact": str(artifact) if artifact else None,
        "frontier": [row(sc) for sc in out.frontier],
        "refused": [{"name": n, "why": why} for n, why in out.refused],
        "not_established": list(out.not_established), "lessons": list(out.lessons),
        "report": task_report_lines(task, out, problem)}
    return answer


def cmd_ask(args: argparse.Namespace) -> int:
    """`flux ask "<prompt>" --file ...`: an author (model or coding agent) writes the problem
    document, the loop checks and runs it, the author revises from the report, up to
    `--passes` times."""
    import re as _re
    from pathlib import Path

    from flux_loop import request_for, run_loop
    from flux_loop.author import Ask, drive, workspace

    tui = bool(getattr(args, "tui", False)) or not args.prompt
    review = False
    if tui:
        from flux_tui import SetupForm, run_setup

        try:
            settings = run_setup(SetupForm(prompt=args.prompt or "", files=list(args.file), skills=list(args.skill), author=str(args.author),
                                           passes=args.passes, screen_only=bool(args.screen_only), workdir=args.dir or ""))
        except Exception as exc:  # noqa: BLE001 -- no terminal: say how to run without one
            print(f"the setup screen needs a terminal ({exc}); give the prompt: flux ask \"...\" --file ...")
            return 2
        if settings is None:
            print("left the setup screen; nothing ran")
            return 0
        args.prompt, args.file, args.author = settings["prompt"], settings["files"], settings["author"]
        args.passes, args.screen_only, args.dir = settings["passes"], settings["screen_only"], settings["workdir"]
        args.skill = settings.get("skills", args.skill)
        review = bool(settings["review"])
    slug = _re.sub(r"[^a-z0-9]+", "_", args.prompt.lower()).strip("_")[:40] or "ask"
    workdir = Path(args.dir or Path("out") / f"ask_{slug}").resolve()       # D786: its name is the problem's id
    from flux_loop.author import workspace_skills
    from flux_loop.skills import SkillError

    try:
        inputs = workspace(args.file, workdir)
        skills = workspace_skills(args.skill, workdir)
    except (FileNotFoundError, SkillError) as exc:
        print(str(exc))
        return 2
    author: Any = args.author
    if isinstance(author, str) and author.strip().startswith("{"):
        author = json.loads(author)

    def model(replies: str | None) -> Any:
        if replies:
            from flux_llm import ScriptedProposer

            return ScriptedProposer(json.loads(Path(replies).read_text()))
        from flux_llm import OpenAIChatProposer

        return OpenAIChatProposer(args.model, num_predict=int(args.num_predict or 6000))

    models: dict[str, Any] = {}
    if (author in (None, "", "model")) and not getattr(args, "author_replies", None):
        down = model(None).preflight()                  # the model is the author: no ask without it
        if down:
            print(f"cannot start: {down}")
            return 1

    def loop_model() -> Any:                       # built when a pass runs: `--no-run` needs none
        if "loop" not in models:
            models["loop"] = model(args.replies)
        return models["loop"]

    author_model = (model(args.author_replies) if args.author_replies else loop_model()) if author in (None, "model") else None
    from flux_llm import describe_model

    print(f"ask: {args.prompt}\nworking in {workdir}"
          + (f"\n{describe_model(author_model)} (the author)" if author_model is not None and not args.author_replies else "") + (f"\ninputs: {', '.join(map(str, inputs))}" if inputs else "")
          + (f"\nskills: {', '.join(sk.name for sk in skills)}" if skills else "")
          + f"\nauthor: {author if isinstance(author, str) else json.dumps(author)}")

    channel: dict[str, Any] = {}

    def run_pass(task: Any, problem: Any, explore: int = 0) -> Any:
        if args.no_run:
            raise _NoRun()
        overrides: dict[str, Any] = {"db": str(task.out_dir() / f"{(task.record or task.id).split('/')[0]}.db"), "explore": explore}
        if args.steps is not None:
            overrides["steps"] = args.steps
        if args.screen_only:
            overrides["screen_only"] = True
        return run_loop(problem, request_for(task, **overrides), proposer=loop_model(), log=print,
                        feedback=channel.get("fb"))

    ask = Ask(prompt=args.prompt, workdir=workdir, inputs=inputs, author=author, checks=args.checks, passes=args.passes,
              skills=skills, no_run=bool(args.no_run))
    if tui:
        return _ask_tui(ask, run_pass=run_pass, proposer=author_model, review=review, channel=channel)
    try:
        got = drive(ask, run_pass=run_pass, proposer=author_model, loop_proposer=loop_model() if args.replies else None)
    except _NoRun:
        print(f"\nthe document is written and checked: {workdir / 'problem.yaml'}\n"
              f"run it: flux task run {workdir / 'problem.yaml'}")
        return 0
    if got["error"]:
        print(f"\nno runnable document after {args.checks} repair(s): {got['error']}")
        return 1
    print("\n" + "\n".join(got["report"]))
    out = got["result"]
    print(f"\ndocument: {got['document']}")
    if out is not None and out.decision is not None:
        task = got["task"]
        target = task.out_dir() / f"{task.id}{task.extension}"
        target.write_text(out.decision.candidate.artifact)
        print(f"artifact written to {target}")
        return 0
    return 1


def _ask_tui(ask: Any, *, run_pass: Any, proposer: Any, review: bool, channel: dict[str, Any]) -> int:
    """`flux ask` under the loop TUI: the author's turns as task rows, the document in the
    results tab for review (`f`, then `run`, a note to revise, or `stop`), then the passes."""
    import time
    from pathlib import Path

    from flux_loop.author import drive
    from flux_tui import run_tui

    def target(bus: Any, fb: Any) -> Any:
        channel["fb"] = fb

        def look(path: Path, task: Any, problem: Any) -> str | None:
            bus.result(f"── the problem the author wrote: {path} ──")
            for line in path.read_text().splitlines():
                bus.result(line)
            named = sorted(p.name for p in path.parent.iterdir() if p.is_file() and p.name not in (path.name, "AUTHOR-BRIEF.md"))
            bus.result(f"── beside it: {', '.join(named) or 'nothing'} ──")
            ask_line = "press f and type `run` to run it, `stop` to end here, or a note for the author to revise it"
            bus.result(f"── REVIEW: {ask_line} ──")
            print(f"REVIEW: the problem is written and checked ({task.id}): results tab (3); {ask_line}")
            from flux_profile import phase

            with phase("review: waiting for you", why="the problem is in the results tab (3): f, then run / a note / stop"):
                return _await(fb)

        def _await(fb: Any) -> str | None:
            while True:
                for note in fb.drain():
                    text = note.text.strip()
                    if text.lower() in ("run", "go", "ok", "yes"):
                        print("running the problem")
                        return None
                    if text.lower() in ("stop", "quit", "no"):
                        raise _Stopped()
                    if text:
                        print(f"your note goes to the author: {text}")
                        return text
                time.sleep(0.2)

        # at rest: the passes were the author's; the TUI's loop toggle must not restart the
        # whole ask and re-author the problem
        try:
            return {**drive(ask, run_pass=run_pass, proposer=proposer, say=print, review=look if review else None),
                    "at_rest": True}
        except _Stopped:
            return {"stopped": True, "at_rest": True, "report": ["stopped at the review; the problem is in " + str(ask.workdir)]}

    def on_result(got: Any) -> list[str]:
        if not isinstance(got, dict):
            return []
        if got.get("error"):
            return [f"no runnable document: {got['error']}"]
        return list(got.get("report") or []) + ["", f"document: {got.get('document', '')}"]

    try:
        got = run_tui(target, title="flux ask", subtitle=str(ask.workdir), feedback_enabled=True, on_result=on_result,
                      info={"ask": ask.prompt[:200], "author": str(ask.author), "inputs": ", ".join(map(str, ask.inputs)) or "none",
                            "passes": ask.passes, "directory": str(ask.workdir)})
    except KeyboardInterrupt:
        print(f"run abandoned; what was written is in {ask.workdir}")
        return 130
    if not isinstance(got, dict) or got.get("error") or got.get("stopped"):
        print(got.get("error") or got["report"][0] if isinstance(got, dict) else "nothing ran")
        return 1 if isinstance(got, dict) and got.get("error") else 0
    print("\n".join(got["report"]))
    out = got["result"]
    print(f"\ndocument: {got['document']}")
    if out is not None and out.decision is not None:
        task = got["task"]
        target_path = task.out_dir() / f"{task.id}{task.extension}"
        target_path.write_text(out.decision.candidate.artifact)
        print(f"artifact written to {target_path}")
        return 0
    return 1


class _Stopped(Exception):
    """`stop` typed at the review: the run ends before the loop starts."""


class _NoRun(Exception):
    """`flux ask --no-run`: the document is checked; the loop is not started."""


def cmd_gc(args: argparse.Namespace) -> int:
    """Remove trace directories no record points at.

    Passes write under `<trace root>/<campaign>/<stamp>`; a directory older than `--keep-days`
    that no row names is litter. `--apply` removes it (else only reports)."""
    import shutil
    import sqlite3
    import time
    from pathlib import Path

    from flux_loop import trace_root

    root = Path(args.root or trace_root())
    referenced: set[str] = set()
    for db in args.db or []:
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            for (doc,) in con.execute("SELECT candidate_json FROM trials"):
                if '"trace"' not in doc:
                    continue
                try:
                    trace = ((json.loads(doc).get("meta") or {}).get("provenance") or {}).get("trace")
                except Exception:  # noqa: BLE001
                    continue
                if trace:
                    referenced.add(str(Path(trace).resolve()))
            con.close()
        except Exception as exc:  # noqa: BLE001
            print(f"  {db}: could not read ({exc!s:.80})")
    candidates: list[Path] = []
    if root.is_dir():
        for camp in sorted(root.iterdir()):
            if camp.is_dir():
                candidates.extend(sorted(p for p in camp.iterdir() if p.is_dir()))

    def size_of(p: Path) -> int:
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())

    total, freed = 0, 0
    for p in candidates:
        try:
            age_days = (time.time() - p.stat().st_mtime) / 86400
        except OSError:
            continue
        held = any(r.startswith(str(p.resolve())) for r in referenced)
        size = size_of(p)
        total += size
        doomed = not held and age_days >= float(args.keep_days)
        mark = "REMOVE" if (doomed and args.apply) else ("would remove" if doomed else ("named by a record" if held else "recent"))
        print(f"  {size / 1e6:9.1f} MB  {age_days:6.1f} d  {mark:16s} {p}")
        if doomed and args.apply:
            shutil.rmtree(p, ignore_errors=True)
            freed += size
    print(f"{len(candidates)} trace director{'y' if len(candidates) == 1 else 'ies'}, {total / 1e9:.2f} GB"
          + (f"; removed {freed / 1e9:.2f} GB" if args.apply else "; nothing removed (add --apply)"))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """The loop-level report as one HTML page: frontier and hypervolume per pass, best-so-far
    per objective, per-part small multiples. `--objective` (repeatable,
    `metric[:direction][:goal][:stage][:tie]`) stands in for a record without an objective vector."""
    from flux_loop import Objective, Objectives
    from flux_loop.report import write

    objectives = None
    if args.objective:
        items = []
        for spec in args.objective:
            f = (spec.split(":") + [""] * 5)[:5]
            doc = {"metric": f[0].strip()}
            if f[1].strip():
                doc["direction"] = {"max": "maximize", "min": "minimize"}.get(f[1].strip(), f[1].strip())
            if f[2].strip():
                doc["goal"] = f[2].strip()
            if f[3].strip():
                doc["stage"] = f[3].strip()
            if f[4].strip():
                doc["tie"] = float(f[4])
            items.append(Objective.from_doc(doc))
        objectives = Objectives(items)
    out = args.out or (args.db.rsplit(".", 1)[0] + "-report.html")
    rep = write(args.db, out, campaign=args.campaign, objectives=objectives)
    print(f"campaign {rep.campaign[:12]}: {len(rep.rows)} measured rows, {len(rep.passes)} pass(es), "
          f"objective {rep.objectives.describe() or '(none)'}")
    if rep.agent_turns:
        fell = sum(1 for t in rep.agent_turns if not t.get("ok") and t.get("box") not in ("generate", "prototype"))
        resumed = sum(1 for t in rep.agent_turns if t.get("session") == "resumed")
        print(f"  agent turns: {len(rep.agent_turns)} (" + ", ".join(sorted({str(t.get('box')) for t in rep.agent_turns}))
              + f"), {resumed} resumed a session, {fell} fell back to the rules half")
    if rep.library:
        print(f"  library: {len(rep.library)} file(s) cited by the drafts' prompts: "
              + ", ".join(f"{k} ({v})" for k, v in sorted(rep.library.items(), key=lambda t: -t[1])[:8]))
    for n in rep.notes:
        print(f"  {n}")
    print(f"wrote {out}")
    return 0


def _campaign_of(db: str, prefix: str | None) -> str:
    from flux_store import CampaignStore

    store = CampaignStore(db)
    try:
        rows = store.list_campaigns()
    finally:
        store.close()
    if not rows:
        raise SystemExit(f"{db}: no campaign in this record")
    if prefix:
        rows = [r for r in rows if r["campaign_id"].startswith(prefix)]
        if not rows:
            raise SystemExit(f"{db}: no campaign starts with {prefix!r}")
    return rows[-1]["campaign_id"]


def cmd_run(args: argparse.Namespace) -> int:
    """Start a command detached: output goes to a log under the trace root, the loop registers
    the run under its campaign, and `flux status`/`stop`/`attach` find it there.
    `FLUX_RUN_LOG` names the log to the loop."""
    import os
    import subprocess
    import time

    from flux_loop import trace_root

    if not args.argv:
        raise SystemExit("flux run -- <command...>: nothing to run")
    argv = list(args.argv)
    if argv and argv[0] == "--":
        argv = argv[1:]
    logs = os.path.join(trace_root(), "runs")
    os.makedirs(logs, exist_ok=True)
    log = args.log or os.path.join(logs, time.strftime("%Y%m%dT%H%M%S", time.gmtime()) + ".log")
    env = {**os.environ, "FLUX_RUN_LOG": log}
    with open(log, "ab") as f:
        f.write((" ".join(argv) + "\n").encode())
        proc = subprocess.Popen(argv, stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                start_new_session=True, env=env)
    print(f"started pid {proc.pid}; log {log}")
    print("the run registers under its campaign when the loop opens the record: "
          "`flux status <db>` shows it, `flux stop <db>` ends it at the pass boundary, `flux attach <db>` tails the log")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    import time

    from flux_loop import ops

    cid = _campaign_of(args.db, args.campaign)
    st = ops.status(cid, args.db)
    print(f"campaign {cid[:12]}: {st['state']}")
    if st["state"] != "none":
        started = time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime(float(st.get("started") or 0)))
        print(f"  pid {st.get('pid')} started {started}; passes this run: {st.get('passes', 0)}"
              + (" (at rest)" if st.get("at_rest") else "")
              + (f"; log {st.get('log')}" if st.get("log") else "; no log registered (not started by `flux run`)"))
        print(f"  argv: {' '.join(str(a) for a in st.get('argv') or [])[:200]}")
        if st["state"] == "stale":
            print("  the registered process is gone; the registration is stale")
    if st.get("stop"):
        print(f"  stop requested: {st['stop']}")
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    from flux_loop import ops

    cid = _campaign_of(args.db, args.campaign)
    if args.now:
        st = ops.status(cid, args.db)
        if ops.interrupt(cid, args.db):
            to = f"the sandbox {st['container']}" if st.get("container") else f"pid {st.get('pid')}"
            print(f"campaign {cid[:12]}: SIGINT sent to {to}; the pass ends now, the record holds what was judged")
            return 0
        print(f"campaign {cid[:12]}: no running process registered")
        return 1
    p = ops.request_stop(cid, args.why or "flux stop", db=args.db)
    st = ops.status(cid, args.db)
    print(f"campaign {cid[:12]}: stop requested at the pass boundary ({p})"
          + ("" if st["state"] == "running" else f"; note: no running process is registered ({st['state']})"))
    return 0


def cmd_attach(args: argparse.Namespace) -> int:
    import subprocess

    from flux_loop import ops

    cid = _campaign_of(args.db, args.campaign)
    st = ops.status(cid, args.db)
    log = st.get("log")
    if not log:
        print(f"campaign {cid[:12]}: {st['state']}; no log registered -- a run started by `flux run` has one")
        return 1
    print(f"campaign {cid[:12]}: {st['state']}; tailing {log} (Ctrl-C leaves the run going)")
    try:
        subprocess.run(["tail", "-n", str(args.lines), "-f", log], check=False)
    except KeyboardInterrupt:
        pass
    return 0


def cmd_knowledge_digest(args: argparse.Namespace) -> int:
    """`flux knowledge digest --db X`: the library's documents the record does not hold a
    digest of yet, digested by the model, once each."""
    import json
    from pathlib import Path

    from flux_knowledge import digest_library

    if args.replies:
        from flux_llm import ScriptedProposer

        proposer: Any = ScriptedProposer(json.loads(Path(args.replies).read_text()))
    else:
        from flux_llm import OpenAIChatProposer

        proposer = OpenAIChatProposer(args.model, num_predict=int(getattr(args, "num_predict", None) or 2000))
    made = digest_library(args.db, proposer, say=print)
    print(f"{len(made)} digest(s) made; `flux knowledge show --db {args.db}` prints them")
    return 0


def cmd_knowledge_show(args: argparse.Namespace) -> int:
    """`flux knowledge show --db X`: every digest the record holds, its source and its text."""
    from flux_knowledge import digests_in

    held = digests_in(args.db)
    if not held:
        print("no digests in this record yet: `flux knowledge digest --db` makes them (a model, once per document)")
        return 1
    for path, d in sorted(held.items()):
        print(f"== {path} ({d.get('chars', 0):,} chars, {d.get('model', '?')})")
        print(d.get("digest", ""))
        print()
    print(f"{len(held)} digest(s)")
    return 0


_NEW_README = {
    "python": """# {name}

A Python problem for Flux, written by `flux new {name} --kind python`. The model writes
`count_primes(n)`; `check.py` refuses a wrong one (the gate); `bench.py` times the survivors
(the stage); the loop keeps the fastest and asks for faster.

| file | what it is |
|---|---|
| `problem.yaml` | the ask: statement, contract, gate, stage, objective, budget |
| `check.py` | the gate: known cases against a reference, prints `N failing of M` |
| `bench.py` | the stage: times the candidate, prints `time_ms=` |

    flux task check problem.yaml
    flux task run problem.yaml --passes 1      # one pass; without --passes it runs until stopped

A model is needed: a local Ollama, or `FLUX_REMOTE_BASE_URL` / `FLUX_REMOTE_MODEL` for a server
(README.md, "A run with a model"). To make it yours, change the statement and the contract,
put your own cases in `check.py` and your own workload in `bench.py`.
""",
    "rtl": """# {name}

An RTL problem for Flux, written by `flux new {name} --kind rtl`. The model writes the module;
`flux rtl test` proves it against `golden.py` on Verilator (the gate); `flux rtl measure` times
it with Yosys and OpenSTA, then places it with OpenROAD on ASAP7 (the stages).

| file | what it is |
|---|---|
| `problem.yaml` | the ask: statement, contract, gate, stages, objectives, budget |
| `golden.py` | what the module must compute: `PORTS` and `golden(**inputs)` |

    flux task check problem.yaml
    flux task run problem.yaml --passes 1 --screen-only    # synthesis only, one pass

It needs the dev shell's tools and a model. To make it yours, change the statement, the
contract and `golden.py`; `flux/core/loop/src/flux_loop/author_reference.md` has the rules for
golden models (floats, clocks, tolerances).
""",
    "rtl-sweep": """# {name}

A hardware design-space sweep for Flux with no model, written by `flux new {name} --kind
rtl-sweep`. `gen.py` spells one module per point of the document's `flow.dse.space`; `flux rtl test`
proves each against `golden.py` on Verilator; `flux rtl measure` synthesises the survivors with
Yosys and OpenSTA on ASAP7.

| file | what it is |
|---|---|
| `problem.yaml` | the space, the search, the gate, the stage, the objectives |
| `gen.py` | the generator: a 16-bit popcount as a sum, an adder tree, or small tables |
| `golden.py` | what the module must compute |

    flux task run problem.yaml --passes 6      # a pass a point of `flow.dse.space` (D738)

Add an architecture to `gen.py` and its name to `flow.dse.space`, or add knobs (widths, pipeline
depth, table size). For placed numbers, add the `confirm` stage from `flux new --kind rtl`.
""",
    "tune": """# {name}

A tuning problem for Flux, written by `flux new {name} --kind tune`. No model and no generated
code: every point of the document's `flow.dse.space` is a setting, handed to the gate and the stage as
`{{knob}}` placeholders. `check.py` refuses a setting that breaks the result; `bench.py` measures
the rest; the fastest wins.

| file | what it is |
|---|---|
| `problem.yaml` | the knobs, the search, the gate, the stage, the objective |
| `workload.py` | the program being tuned: a blocked matrix multiply (block size, loop order) |
| `check.py` / `bench.py` | the gate (still correct?) and the stage (`time_ms=`) |

    flux task run problem.yaml --passes 15      # a pass a point of `flow.dse.space` (D738)

To tune your own program, replace `workload.py`, list its knobs under `flow.dse.space`, and make the gate
and the stage run it with them. They can be any command: a build with flags, a solver with
parameters, a training script with hyperparameters. For a space too big to sweep, set
`flow.dse` to `gradient`, `anneal` or `genetic`; for a trade-off, add a second objective and use
`pareto`. `docs/extending.md` has the rest.
""",
    "sweep": """# {name}

A design-space sweep for Flux with no model, written by `flux new {name} --kind sweep`.
`render.py` writes one candidate per point of the document's `flow.dse.space`; `check.py` refuses a
wrong one; `bench.py` times the survivors; the fastest wins.

| file | what it is |
|---|---|
| `problem.yaml` | the ask: the space, the search, the gate, the stage, the objective |
| `render.py` | the generator: one candidate per point (`render.py <out> <algorithm> <wheel>`) |
| `check.py` / `bench.py` | the gate and the stage |

    flux task run problem.yaml --passes 6      # a pass a point of `flow.dse.space` (D738)

To try another idea, add a value to `flow.dse.space` and its code to `render.py`; to search instead of
sweeping, set `flow.dse` to `gradient`, `anneal`, `genetic` or `pareto`. Set
`flow.generate: model` (and drop `flow.dse`) to let a model write candidates instead.
""",
}


#: What each `flux new` kind is, in a line (the web's "Start from an example", D719).
NEW_KINDS = {
    "sweep": "A script writes every point of a knob space; the fastest wins. No model needed.",
    "tune": "Knobs go straight to your own commands (build flags, block sizes). No model needed.",
    "python": "A model writes a Python function; a checker and a benchmark judge it.",
    "rtl": "A model writes a SystemVerilog module; Verilator and ASAP7 synthesis judge it.",
    "rtl-sweep": "A script spells one module per knob point; Verilator and Yosys judge them. No model needed.",
}


def template_files(name: str, kind: str) -> list[tuple[str, str]]:
    """`flux new`'s problem of `kind` named `name`: (file name, text) pairs, the document as
    `problem.yaml` (D786: in a folder named `name`, its id), and its README."""
    from pathlib import Path

    if kind not in NEW_KINDS:
        raise ValueError(f"a kind is one of {', '.join(NEW_KINDS)}")
    source = Path(__file__).with_name("templates") / kind
    out = [(f.name, f.read_text().replace("__NAME__", name))
           for f in sorted(source.iterdir()) if f.is_file()]
    return [*out, ("README.md", _NEW_README[kind].format(name=name))]


def cmd_new(args: argparse.Namespace) -> int:
    """`flux new NAME --kind python|rtl|sweep`: a working problem from
    `flux_cli/templates/<kind>/`, with its README."""
    import re as _re
    from pathlib import Path

    name = args.name
    if not _re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name):
        print(f"flux new: {name!r} is not a name (a letter, then letters, digits or _)")
        return 2
    target = Path(args.dir) / name if args.dir else Path(name)     # D786: the folder is the problem, its name the id
    if target.exists() and any(target.iterdir()):
        print(f"flux new: {target} exists and is not empty; choose another name or --dir")
        return 2
    target.mkdir(parents=True, exist_ok=True)
    written = []
    for rel, text in template_files(name, args.kind):
        (target / rel).write_text(text)
        written.append(rel)
    doc = target
    print(f"wrote {target}/: {', '.join(written)}")
    points = {"sweep": 6, "rtl-sweep": 6, "tune": 15}.get(args.kind, 1)     # D738: a pass a point
    print(f"next:\n  flux task check {doc}\n  flux task run {doc} --passes {points}"
          + (" --screen-only" if args.kind == "rtl" else ""))
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    """`flux probe gate|measure FILE`: an agent's check through the loop's own tools (D678)."""
    from flux_loop.probe import probe

    code, text = probe(args.what, args.file, args.stage, gate_first=getattr(args, "gate", False))
    print(text)
    return code


def cmd_log(args: argparse.Namespace) -> int:
    """`flux log <record>`: the turns the run's transcript holds, one line each, or
    whole with `--turn K` / `--full`."""
    import os
    import time as _time

    from flux_loop import ops

    cid = _campaign_of(args.db, args.campaign)
    path = os.path.join(ops.run_dir(cid, args.db), "turns.jsonl")
    if not os.path.isfile(path):
        print(f"campaign {cid[:12]}: no transcript yet ({path}); turns are recorded from D599 on")
        return 1
    turns = []
    with open(path) as f:
        for line in f:
            try:
                turns.append(json.loads(line))
            except ValueError:
                continue
    numbered = list(enumerate(turns, 1))
    if args.turn is not None:
        numbered = [(k, t) for k, t in numbered if k == args.turn]
        if not numbered:
            print(f"no turn {args.turn}; the transcript has {len(turns)}")
            return 1
    elif args.last:
        numbered = numbered[-args.last:]
    whole = args.full or args.turn is not None
    print(f"campaign {cid[:12]}: {len(turns)} turn(s) in {path}")
    for k, t in numbered:
        who = t.get("model") or t.get("agent") or t.get("kind")
        when = _time.strftime("%m-%d %H:%M:%S", _time.localtime(t.get("ts", 0)))
        hops = t.get("hops") or []
        status = ("ERROR " + str(t["error"])[:160]) if t.get("error") else \
            (f"exit {t.get('rc')}" if t.get("kind") == "agent" and not t.get("ok") else "ok")
        print(f"\n#{k} {when} {t.get('kind')} {who} {t.get('seconds', 0):.1f}s {status}"
              + (f", {len(hops)} tool call(s)" if hops else ""))
        prompt, reply = str(t.get("prompt") or ""), str(t.get("reply") or "")
        if whole:
            print("--- prompt ---\n" + prompt)
            for h in hops:
                print("--- tool --- " + h)
            if t.get("stderr"):
                print("--- stderr ---\n" + t["stderr"])
            print("--- reply ---\n" + reply)
        else:
            flat = lambda x: " ".join(x.split())  # noqa: E731
            print(f"  prompt ({len(prompt)} chars): ...{flat(prompt)[-160:]}")
            for h in hops[:3]:
                print(f"  tool: {h[:160]}")
            print(f"  reply ({len(reply)} chars): {flat(reply)[:200]}")
    return 0


def cmd_consult(args: argparse.Namespace) -> int:
    """`flux consult "<question>" --loop <folder> --out <folder>`: an agent (or the model) reads the
    loop -- its files, a snapshot of its record, its log -- and answers in `<out>/answer.md` (D705)."""
    from pathlib import Path

    from flux_loop.consult import consult

    loop, out = Path(args.loop).resolve(), Path(args.out).resolve()
    if not loop.is_dir():
        print(f"flux consult: no loop folder {loop}")
        return 2
    proposer = None
    if args.author in (None, "", "model"):
        from flux_llm import OpenAIChatProposer

        proposer = OpenAIChatProposer(args.model, num_predict=6000)
        down = proposer.preflight()
        if down:
            print(f"cannot answer: {down}")
            return 1
    print(f"consult: {args.question}\nthe loop: {loop}\nthe answer: {out / 'answer.md'}\nwho answers: {args.author}", flush=True)
    got = consult(args.question, loop, out, author=args.author, proposer=proposer)
    print("\n" + got["answer"])
    return 0 if got["ok"] else 1

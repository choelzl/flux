"""Command implementations for the Flux CLI (docs/roadmap.md Phase 1: `flux eval`, `flux import`,
`flux replay`).
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import flux_ir
from flux_evaluator_abi import Budget, Candidate
from flux_store import ResultStore

from flux_evaluator_abi import DEFAULT_METRICS, evaluator_name_for, make_evaluator

_KNOWN_KINDS = ("workload", "architecture", "mapping")


def _detect_kind(doc: dict[str, Any]) -> str:
    """Best-effort IR kind detection from a document's shape. `--kind` always overrides this —
    it exists for convenience, not as the source of truth.
    """
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
        # The mapping is part of the candidate, and replaying without it re-evaluates a different
        # design — silently, since the resulting difference reads as non-determinism rather than
        # as a dropped input. Every mapping-space search stores one (docs/decisions.md D189).
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


def cmd_task_check(args: argparse.Namespace) -> int:
    """Validate a task document, list what it declares, and name any tool it needs that
    is not on PATH -- runs nothing (D430)."""
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
    if task.split or task.subtasks:      # D455: each one is its own loop
        print("  sub-tasks: " + ("decided by the model (decompose), at most "
                                 f"{task.max_subtasks}" if task.split
                                 else ", ".join(c.id for c in task.subtasks)))
    print("  drafted by: " + _drafted_by(task))       # D456
    print("  roles: " + _roles_line(task))            # D460
    from flux_loop.task import describe_flow

    print("  flow (D542): one line per box of the drawing, the half in force")
    for line in describe_flow(task, problem):
        print(f"    {line}")
    for label, cmd in task.commands():
        print(f"  {label}: {' '.join(cmd)}")
    print("  stages: " + ", ".join(_stage_label(r) for r in task.stages) if task.stages
          else "  stages: " + ", ".join(problem.stages()))
    print("  objectives: " + (", ".join(f"{o.direction} {o.metric}" + (f" (goal {o.goal:g}{(' ' + o.unit) if o.unit else ''})" if o.goal is not None else "")
                                        for o in task.objectives) or "none"))
    if task.world:                                     # D519
        from flux_loop.task import contract_lines

        bound = {n for n in problem.__dict__ if callable(problem.__dict__[n]) and not n.startswith("_")}
        print(f"  world: {task.world} -- fills {len(bound)} hook(s) of the contract ([filled], *core; D561):")
        for line in contract_lines(bound):
            print(f"    {line}")
    for name, spec in sorted(task.hooks.items()):
        print(f"  hook {name}: {spec}")
    if task.campaign:
        print("  campaign: " + ", ".join(f"{k}={v}" for k, v in task.campaign.items()))
    if task.ladder:
        print("  ladder: " + ("the default" if task.ladder is True else ", ".join(f"{k}={v}" for k, v in task.ladder.items())))
    if task.knowledge_sheet:
        print(f"  knowledge: {task.knowledge_sheet} ({len(task.knowledge)} chars)")
    missing = problem.tools_missing()
    if missing:
        print(f"  MISSING on PATH: {', '.join(missing)}")
        return 1
    print("  tools: all present")
    return 0


def _roles_from(flags: list[str] | None) -> Any:
    """`--role orchestrator=rules` (repeatable) as a `Roles` bundle (D460). Only the roles
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
    """WHO writes the candidates (D456): the model unless the document says otherwise."""
    spec = task.generator
    if spec.get("catalog"):
        return f"a catalog of {len(spec['catalog'])} design(s) that already exist (no model)"
    if spec.get("command"):
        return "the generator command (no model)"
    return "a model"


def _roles_line(task: Any) -> str:
    """Who fills each of the four roles, and what else this loop could be switched to."""
    from flux_loop import ROLES, available_roles

    said = dict(task.roles)
    if task.generator and "generator" not in said:
        said["generator"] = "the document's own generator command/catalog"
    parts = []
    for role in ROLES:
        chosen = said.get(role)
        choices = available_roles(role)
        parts.append(f"{role}={chosen if chosen else 'the problem default'}"
                     + (f" (or: {', '.join(c for c in choices if c != chosen)})" if choices
                        else ""))
    return "; ".join(parts)


def _stage_label(stage: Any) -> str:
    """A stage, what it needs on PATH (D519) and what it takes to climb past it (D454)."""
    cut = stage.cutoff
    needs = f" [needs {', '.join(stage.needs)}]" if getattr(stage, "needs", ()) else ""
    if not cut:
        return stage.name + needs
    if "at" in cut:
        rule = f"{cut['metric']} >= {cut['at']:g}"
    elif "below" in cut:
        rule = f"{cut['metric']} <= {cut['below']:g}"
    else:
        rule = f"{cut['metric']} within {float(cut['within']):.0%} of the best"
    return f"{stage.name}{needs} (cutoff: {rule})"


def cmd_task_run(args: argparse.Namespace) -> int:
    """Run a task document through the loop and print the standard report (D430) -- in the
    TUI, for N passes, with the agent's halves, with reasoning: the flags every application
    demo used to carry (D519: the demo is the command line now)."""
    from pathlib import Path

    from flux_loop import (PromptProblem, TaskError, load_task, ops, request_for, run_loop,
                           task_report_lines)

    try:
        task = load_task(args.file)
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
    db = args.db or str(task.out_dir() / f"{task.id}.db")          # D578: beside the document, under out/
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
    tui = bool(getattr(args, "tui", False))
    passes = int(request.passes if getattr(args, "passes", None) is None else args.passes)   # D551: the document's

    no_feedback = task.flow.get("feedback") == "none"     # D542: the document declined the channel

    def _run(fb=None):
        return run_loop(problem, request, proposer=proposer, feedback=None if no_feedback else fb, log=print)

    def _passes(fb=None):
        """The passes (D513, D551): `--passes N` or the document's `budget.passes`, 0 = until
        the campaign is at rest or `flux stop` asks for the boundary -- under the TUI too, so a
        document that says "until stopped" is not one pass and a prompt (the macarray, D551)."""
        n = 0
        while True:
            out = _run(fb)
            n += 1
            if (passes and n >= passes) or out.at_rest:
                return out
            asked = ops.stop_requested()
            if asked:
                ops.clear_stop()
                print(f"stopping at the pass boundary: {asked}")
                return out
            print(f"\n── pass {n + 1} ──")

    def _print(out) -> None:
        print()
        print("\n".join(task_report_lines(task, out, problem)))

    objectives = ", ".join(f"{o.metric} {o.direction}" + (f" (goal {o.goal:g}{(' ' + o.unit) if o.unit else ''})" if o.goal is not None else "")
                           for o in problem.objectives())
    info = {"db": db, "parts": " ".join(problem.subgoals()) or "(one artifact)",
            "objectives": objectives or "the gate", "world": task.world or "the document alone",
            "budget": f"{request.steps} steps x {request.repair_attempts} generation attempts",
            "model": model_name + (" (reasoning on)" if getattr(args, "think", False) else ""),
            "agent": (", ".join(halves) + (f" ({request.tool_hops} hops)" if "tools" in halves else "")
                      if halves else "off: the code's own rules")}
    try:
        from flux_tui import demo_run

        out = demo_run(_passes, tui=tui, title=f"flux · {task.id}", subtitle=db,
                       print_report=_print, info=info)
    except KeyboardInterrupt:
        print("run abandoned; the campaign record holds what was judged")
        return 130
    _print(out)
    if out.decision is not None:
        target = Path(args.out or task.out_dir() / f"{task.id}{task.extension}")   # D578
        target.write_text(out.decision.candidate.artifact)
        print(f"\nartifact written to {target}")
        return 0
    return 1


def cmd_migrate(args: argparse.Namespace) -> int:
    """A campaign record at the current schema (D524): opening it drops the accelerator era's
    columns in place and marks the file; `--rename OLD=NEW` puts a campaign the loop opened
    under an objective hash under its document's name, with every trial and event, and a
    `renamed` event saying so. Run with the campaign's process stopped."""
    from flux_store import CampaignStore, CampaignStoreError

    with CampaignStore(args.db) as store:
        if store.upgraded:
            print(f"{args.db}: schema v{store.schema_version()}; dropped {', '.join(store.upgraded)} from trials")
        else:
            print(f"{args.db}: schema v{store.schema_version()} already")
        rows = store.list_campaigns()
        for spec in args.rename or []:
            old, _, new = str(spec).partition("=")
            found = [r["campaign_id"] for r in rows if r["campaign_id"].startswith(old.strip())]
            if len(found) != 1:
                print(f"--rename {spec}: {'no campaign' if not found else str(len(found)) + ' campaigns'} start with {old!r}")
                return 2
            try:
                store.rename_campaign(found[0], new.strip())
            except CampaignStoreError as exc:
                print(f"--rename {spec}: {exc}")
                return 2
            print(f"campaign {found[0][:12]} -> {new.strip()} ({sum(1 for _ in store.trials(new.strip()))} trials follow)")
        if store.upgraded or args.rename:
            # the rebuild and the renames leave the old pages behind; the file is packed once here,
            # never on an ordinary open (a VACUUM of a large record is minutes on a slow disk)
            store._conn.execute("VACUUM")
            print(f"{args.db}: packed")
        for r in store.list_campaigns():
            print(f"  {r['campaign_id'][:24]:24} {r['status']:8} {r['created_at']}")
    return 0


def cmd_gc(args: argparse.Namespace) -> int:
    """Traces a record no longer points at (D510): every pass writes its prompts, replies and
    checked prototypes under `<trace root>/<campaign>/<stamp>`, and a prototype row names
    its directory. A directory older than `--keep-days` that no row of the given records
    names is litter; `--apply` removes it, without it the command only says. `--legacy`
    adds the pre-D510 `flux-<problem>-XXXX` temp directories, which nothing ever named."""
    import shutil
    import sqlite3
    import tempfile
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
    if args.legacy:
        candidates.extend(sorted(p for p in Path(tempfile.gettempdir()).glob("flux-*-*") if p.is_dir()))

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
    """The loop-level report (D512): how a campaign moved -- frontier evolution and hypervolume
    per pass, best-so-far per objective, per-part small multiples annotated from the ledger --
    as one HTML page, read from the record and the objective vector on it. `--objective`
    (repeatable, `metric[:direction][:goal][:stage][:tie]`) stands in for a record written
    before D512 kept the vector."""
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
    """Start a command detached (D513): its output goes to a log under the trace root, the
    loop registers the run under its campaign when it opens the record, and `flux status`,
    `flux stop`, `flux attach` find it from there. `FLUX_RUN_LOG` names the log to the loop."""
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
    st = ops.status(cid)
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
        if ops.interrupt(cid):
            print(f"campaign {cid[:12]}: SIGINT sent to pid {ops.status(cid).get('pid')}; the pass ends now, the record holds what was judged")
            return 0
        print(f"campaign {cid[:12]}: no running process registered")
        return 1
    p = ops.request_stop(cid, args.why or "flux stop")
    st = ops.status(cid)
    print(f"campaign {cid[:12]}: stop requested at the pass boundary ({p})"
          + ("" if st["state"] == "running" else f"; note: no running process is registered ({st['state']})"))
    return 0


def cmd_attach(args: argparse.Namespace) -> int:
    import subprocess

    from flux_loop import ops

    cid = _campaign_of(args.db, args.campaign)
    st = ops.status(cid)
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
    digest of yet, digested by the model, once each (D576)."""
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

"""Flux CLI entry point: the one way in, for people, scripts and agents alike (docs/agent-surface.md).

`flux task run|check` runs or validates a problem document (`--json FILE` writes the answer for a
script), `flux ask` drives the loop from a prompt, `flux rtl lint|test|measure`, `flux prog time|count|size` and `flux champsim run|build|check` are the tools a
document names, `flux report` reads a campaign's record, and `flux run/status/stop/attach` manage a
detached run; `flux eval`, `flux import` and `flux replay` are the IR evaluator commands.
"""

from __future__ import annotations

import argparse
import sys

from .champsim import cmd_champsim_build, cmd_champsim_check, cmd_champsim_run
from .rtl import cmd_rtl_lint, cmd_rtl_measure, cmd_rtl_proto, cmd_rtl_test
from .selftest import cmd_selftest
from .tools import cmd_tools
from .commands import (cmd_knowledge_digest, cmd_knowledge_show, cmd_attach, cmd_eval, cmd_gc, cmd_import, cmd_replay, cmd_report, cmd_run, cmd_status,
                       cmd_stop, cmd_task_check, cmd_task_run, cmd_ask, cmd_consult, cmd_new, cmd_log, cmd_probe)
from flux_evaluator_abi import available_evaluators


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flux",
        description="Flux: an AI-driven design-space exploration loop for hardware. A model or a coding agent "
                    "proposes designs, real tools check and measure them, the loop decides and keeps a record.",
        epilog="start with:\n  flux new myproblem --kind python|rtl|sweep|tune|rtl-sweep\n"
               "  flux task check <folder>\n  flux task run <folder>\n"
               "  flux ask \"what you want\" --file spec.pdf\ndocs: README.md and docs/usage-guide.md",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(
        dest="command", required=True, title="commands",
        # import, eval, replay and migrate still work but are hidden from the listing
        metavar="{new,ask,task,tools,rtl,prog,champsim,report,log,run,status,stop,attach,knowledge,gc,selftest}")

    import_p = subparsers.add_parser("import"
    )
    import_p.add_argument("file", help="Path to a YAML/JSON IR document.")
    import_p.add_argument(
        "--kind",
        choices=["workload", "architecture", "mapping"],
        default=None,
        help="IR kind (auto-detected from document shape if omitted).",
    )
    import_p.add_argument("--store", default=None, help="SQLite ResultStore path to store into.")
    import_p.set_defaults(func=cmd_import)

    eval_p = subparsers.add_parser("eval"
    )
    eval_p.add_argument("--workload", required=True, help="Path to a Workload IR document.")
    eval_p.add_argument("--arch", default=None, help="Path to an Architecture IR document.")
    eval_p.add_argument("--backend", required=True, choices=available_evaluators())
    eval_p.add_argument(
        "--metrics", default=None, help="Comma-separated metric names (default: latency_cycles,energy_pj)."
    )
    eval_p.add_argument("--store", default=None, help="SQLite ResultStore path to store into.")
    eval_p.set_defaults(func=cmd_eval)

    replay_p = subparsers.add_parser("replay"
    )
    replay_p.add_argument("result_id", type=int)
    replay_p.add_argument("--store", required=True, help="SQLite ResultStore path.")
    replay_p.set_defaults(func=cmd_replay)

    log_p = subparsers.add_parser(
        "log", help="Every model and coding-agent turn of a campaign's runs: prompts, replies, tool calls, errors.")
    log_p.add_argument("db", help="The campaign record.")
    log_p.add_argument("--campaign", default=None, help="A campaign id prefix (default: the latest).")
    log_p.add_argument("--last", type=int, default=10, metavar="N", help="The last N turns (default 10; 0 for all).")
    log_p.add_argument("--turn", type=int, default=None, metavar="K", help="Turn K whole: the prompt, the reply, the tool calls.")
    log_p.add_argument("--full", action="store_true", help="Every listed turn whole, not a line each.")
    log_p.set_defaults(func=cmd_log)

    probe_p = subparsers.add_parser(
        "probe", help="Inside an agent's turn: check a file with the loop's own gate or stage (D678).")
    probe_p.add_argument("what", choices=["gate", "measure"], help="gate: the correctness checks; measure: stages.")
    probe_p.add_argument("file", help="The file to check.")
    probe_p.add_argument("--stage", action="append", default=None,
                         help="A stage to measure (repeat for several: each on its own, side by side; default: the first).")
    probe_p.add_argument("--gate", action="store_true", help="measure: run the gate first.")
    probe_p.set_defaults(func=cmd_probe)

    serve_p = subparsers.add_parser("serve", help="The web interface: accounts, applications, runs followed live (D683).")
    serve_p.add_argument("--host", default="127.0.0.1", help="Address to listen on (default 127.0.0.1; put a TLS proxy in front for others).")
    serve_p.add_argument("--port", type=int, default=8765)
    serve_p.add_argument("--data", default=None, help="The server's data (default: $XDG_DATA_HOME/flux/web).")
    serve_p.add_argument("--max-running", type=int, default=4, help="Runs at once per user.")
    serve_p.add_argument("--secure-cookie", action="store_true", help="Session cookie over HTTPS only (behind a TLS proxy).")
    serve_p.add_argument("--no-sandbox", action="store_true", help="Runs on the host, not sandboxed: a single trusted user only.")
    serve_p.set_defaults(func=_cmd_serve)
    user_p = subparsers.add_parser("user", help="The web interface's accounts, from the server's machine.")
    user_p.add_argument("action", choices=["add", "list", "passwd", "disable", "enable", "role"])
    user_p.add_argument("name", nargs="?", default=None)
    user_p.add_argument("--admin", action="store_true", help="add: an admin (as --role admin).")
    user_p.add_argument("--role", choices=["admin", "internal", "external"], default=None,
                        help="add or role: internal (the server's model and agent settings, the default), external (their own, "
                             "their own home for the agents' logins) or admin (D734).")
    user_p.add_argument("--data", default=None, help="The server's data (default: $XDG_DATA_HOME/flux/web).")
    user_p.set_defaults(func=_cmd_user)

    st_p = subparsers.add_parser("selftest", help="Does Flux work on this machine: tools, a sweep, an RTL sweep, the model, a model-written problem.")
    st_p.add_argument("--full", action="store_true", help="Also the README's first run (adder16, about three minutes).")
    st_p.add_argument("--no-model", action="store_true", help="Only the checks that need no model.")
    st_p.add_argument("--model", default=None, help="The model name to check (default: the one a run would use).")
    st_p.add_argument("--model-timeout", type=float, default=1800.0, help="Seconds for the model-written problem.")
    st_p.set_defaults(func=cmd_selftest)

    new_p = subparsers.add_parser(
        "new", help="Write a working problem to start from: a document and its checker, ready to run and change.")
    new_p.add_argument("name", help="The problem's name (letters, digits, _): its id, and its folder unless --dir.")
    new_p.add_argument("--kind", choices=("python", "rtl", "sweep", "tune", "rtl-sweep"), default="python",
                       help="python: the model writes a function, a checker and a benchmark judge it (default); "
                            "rtl: the model writes a module, Verilator and ASAP7 judge it; "
                            "sweep: a script renders every point of a knob space, no model needed; "
                            "tune: the knobs go straight to your own commands (build flags, block sizes, hyperparameters), no model; "
                            "rtl-sweep: a script spells a module per knob point, Verilator and Yosys judge them, no model.")
    new_p.add_argument("--dir", default=None, help="Where to write it (default: ./<name>); it must not exist or be empty.")
    new_p.set_defaults(func=cmd_new)

    co_p = subparsers.add_parser(
        "consult", help="A question about a loop, answered by an agent that reads it and changes nothing (D705).")
    co_p.add_argument("question", help="What you want to know, in words.")
    co_p.add_argument("--loop", required=True, help="The loop's folder (its document, files, out/<id>.db, runs/loop.log).")
    co_p.add_argument("--out", required=True, help="Where the answer goes (answer.md); the agent's working folder.")
    co_p.add_argument("--author", default="model", help="Who answers: model (default), or a coding agent preset (opencode, claude, codex).")
    co_p.add_argument("--model", default=None, help="The model, when the model answers.")
    co_p.add_argument("--no-sandbox", action="store_true", help="Run on this machine, not in the sandbox (also FLUX_SANDBOX=0).")
    co_p.set_defaults(func=cmd_consult)

    lo_p = subparsers.add_parser(
        "login", help="A coding agent's login, its home a given folder (D734: an external user's agents in the web).")
    lo_p.add_argument("--home", required=True, help="The folder the agent's login is written to: HOME, writable.")
    lo_p.add_argument("--no-sandbox", action="store_true", help="Run on this machine, not in the sandbox (also FLUX_SANDBOX=0).")
    lo_p.add_argument("cmd", nargs=argparse.REMAINDER, help="-- and the agent's login command, e.g. -- opencode auth login")
    lo_p.set_defaults(func=_cmd_login)

    ag_p = subparsers.add_parser("agent", help="Coding agents: is one ready for you (D751).")
    ag_sub = ag_p.add_subparsers(dest="agent_command", required=True)
    at_p = ag_sub.add_parser("test", help="The agent's program, its login, its own status; --live: one short answer as a loop asks it.")
    at_p.add_argument("agent", help="opencode, claude, codex, or an agent the server adds (D807)")
    at_p.add_argument("--live", action="store_true", help="Also ask it one short question (a few hundred tokens).")
    at_p.add_argument("--json", default=None, help="Also write the result as JSON to this file ('-': stdout only).")
    at_p.add_argument("--no-sandbox", action="store_true", help="Run on this machine, not in the sandbox (also FLUX_SANDBOX=0).")
    at_p.set_defaults(func=_cmd_agent_test)

    ask_p = subparsers.add_parser(
        "ask", help="The loop from a prompt and files: an author writes the problem, the loop runs it, the author steers.")
    ask_p.add_argument("prompt", nargs="?", default=None, help="What you want, in words (none: the setup screen opens).")
    ask_p.add_argument("--tui", action="store_true", help="The setup screen, then the loop screen with a review of the problem before it runs.")
    ask_p.add_argument("--file", "-f", action="append", default=[], help="An input: a spec, code, a reference, a PDF, tests (repeatable; a folder is copied whole).")
    ask_p.add_argument("--skill", action="append", default=[], help="A skill folder (SKILL.md), or a folder of them, for the author and the designers (repeatable).")
    ask_p.add_argument("--author", default="model", help="Who writes the problem: model (default), a coding agent preset (opencode, claude, codex), or a JSON agent spec.")
    ask_p.add_argument("--dir", default=None, help="The working directory (default: ./out/ask_<slug>; its name is the problem's id).")
    ask_p.add_argument("--passes", type=int, default=0, help="Stop after N passes (default: run until stopped -- `flux stop`, Ctrl-C, the TUI).")
    ask_p.add_argument("--checks", type=int, default=3, help="Repairs of a refused document per pass (default 3).")
    ask_p.add_argument("--no-run", action="store_true", help="Write and check the document; run nothing.")
    ask_p.add_argument("--steps", type=int, default=None, help="The loop's steps per pass (default: the document's).")
    ask_p.add_argument("--screen-only", action="store_true", help="Stop every pass at the first stage.")
    ask_p.add_argument("--model", default=None, help="The model (the author when --author model, and the loop's).")
    ask_p.add_argument("--num-predict", type=int, default=None)
    ask_p.add_argument("--replies", default=None, help="Scripted replies (a JSON list) for the loop's model: no model.")
    ask_p.add_argument("--author-replies", default=None, help="Scripted replies for a model author (tests, dry runs).")
    ask_p.add_argument("--no-sandbox", action="store_true",
                       help="Run on this machine, not in the Docker sandbox (also FLUX_SANDBOX=0).")
    ask_p.set_defaults(func=cmd_ask)

    task_p = subparsers.add_parser(
        "task", help="Check or run a problem document through the loop."
    )
    task_sub = task_p.add_subparsers(dest="task_command", required=True)
    check_p = task_sub.add_parser("check", help="Validate a task document and its tools; run nothing.")
    check_p.add_argument("file", help="The problem's folder, or one of its documents (problem.yaml, NAME.problem.yaml).")
    check_p.add_argument("--no-sandbox", action="store_true",
                         help="Check on this machine, not in the sandbox (the check imports the document's code).")
    check_p.set_defaults(func=cmd_task_check)
    mig_p = task_sub.add_parser("migrate", help="Bring a problem's documents of an earlier form to today's (D811).")
    mig_p.add_argument("folder", help="The problem's folder.")
    mig_p.add_argument("--write", action="store_true", help="Write them (each only when it loads; the original kept as <file>.orig).")
    mig_p.set_defaults(func=_cmd_task_migrate)
    run_p = task_sub.add_parser("run", help="Run a task document through the loop.")
    run_p.add_argument("file", help="The problem's folder, or one of its documents (problem.yaml, NAME.problem.yaml).")
    run_p.add_argument("--skill", action="append", default=[], help="A skill folder (SKILL.md), or a folder of them, beside the document's own (repeatable).")
    run_p.add_argument("--db", default=None, help="Campaign record (default: <document dir>/out/<task id>.db).")
    run_p.add_argument("--steps", type=int, default=None, help="Planner steps (default: the document's).")
    run_p.add_argument("--repair", type=int, default=None, help="Repair attempts per generation.")
    run_p.add_argument("--model", default=None, help="The model name on the endpoint in use: the local Ollama by default, or the server FLUX_REMOTE_BASE_URL names.")
    run_p.add_argument("--replies", default=None,
                       help="A JSON list of scripted replies: runs without a model.")
    run_p.add_argument("--out", default=None, help="Write the decided artifact here (default: <document dir>/out/<task id><extension>).")
    run_p.add_argument("--no-structured", action="store_true", help="Plain decoding, no schema.")
    run_p.add_argument("--role", action="append", metavar="ROLE=NAME", default=None,
                       help="Switch who fills one of the four roles, repeatable: "
                            "--role orchestrator=rules. `flux task check` lists the choices.")
    run_p.add_argument("--agent", nargs="+", default=(), metavar="HALF",
                       choices=["tools", "orchestrate", "plan", "all"],
                       help="The AGENT takes these halves: tools (the model calls compute/"
                            "check/history/knowledge inside its turns), orchestrate (it picks what "
                            "next, which part, which step of the ladder, with its reasons on record), plan (it "
                            "writes the loop plan the pass follows); all for the three.")
    run_p.add_argument("--plan", default=None, metavar="FILE",
                       help="A loop plan document to follow: parts, budget, stages, roles, "
                            "tools; a field set to \"agent\" is the agent's to fill.")
    # run-time options shared by every application (D519)
    run_p.add_argument("--tui", action="store_true", help="The curses screen: tasks, results, log, the r loop toggle.")
    run_p.add_argument("--think", action="store_true", help="Ask the model for its reasoning on every turn.")
    run_p.add_argument("--num-predict", type=int, default=None, help="Output tokens per turn (default 6000).")
    run_p.add_argument("--passes", type=int, default=None, metavar="N",
                       help="Stop after N passes. Default: the document's `budget.passes`, else run until stopped "
                            "(`flux stop`, Ctrl-C, q in the TUI); a pass at rest is followed by one that explores.")
    run_p.add_argument("--tool-hops", type=int, default=None, help="Rounds of tool calls a turn may make.")
    run_p.add_argument("--hop-share", type=float, default=None,
                       help="Share of the model's context window a round that may call tools may write (0.5).")
    run_p.add_argument("--patience", type=int, default=None, help="Prototype turns granted after each new best.")
    run_p.add_argument("--regenerate", nargs="+", default=(), metavar="PART",
                       help="Parts to draft again instead of resuming from the record; all for every part.")
    run_p.add_argument("--screen-only", action="store_true", help="Stop the chain at the synthesis screen.")
    run_p.add_argument("--json", default=None, metavar="FILE", help="Also write the answer as JSON: the decision, the frontier, what was refused, the application's own result.")
    run_p.add_argument("--no-prototype", action="store_true", help="No prototype stage: the target directly.")
    run_p.add_argument("--no-patching", action="store_true", help="Repair by rewrite, not by edits.")
    run_p.add_argument("--no-sandbox", action="store_true",
                       help="Run on this machine, not in the Docker sandbox (also FLUX_SANDBOX=0).")
    run_p.set_defaults(func=cmd_task_run)

    gc_p = subparsers.add_parser("gc", help="Remove trace directories no campaign record names.")
    gc_p.add_argument("--db", action="append", metavar="DB", help="A campaign record whose rows name traces to keep (repeatable).")
    gc_p.add_argument("--root", default=None, help="The trace root (default: FLUX_TRACE_ROOT or <tmp>/flux-traces).")
    gc_p.add_argument("--keep-days", type=float, default=7.0, help="Keep everything younger than this (default 7).")
    gc_p.add_argument("--apply", action="store_true", help="Remove; without it, only say what would go.")
    gc_p.set_defaults(func=cmd_gc)

    rep_p = subparsers.add_parser("report", help="How a campaign moved: frontier evolution, hypervolume, best-so-far, the parts.")
    rep_p.add_argument("db", help="The campaign record.")
    rep_p.add_argument("--campaign", default=None, help="A campaign id prefix (default: the latest in the record).")
    rep_p.add_argument("--objective", action="append", metavar="SPEC",
                       help="metric[:direction][:goal][:stage][:tie], repeatable, in order; stands in for a record without the vector.")
    rep_p.add_argument("--out", default=None, help="Write the HTML report here (default: next to the record, <record>-report.html).")
    rep_p.set_defaults(func=cmd_report)

    run_cmd = subparsers.add_parser("run", help="Start a command detached, its log under the trace root.")
    run_cmd.add_argument("--log", default=None, help="The log file (default: <trace root>/runs/<stamp>.log).")
    run_cmd.add_argument("argv", nargs=argparse.REMAINDER, help="-- the command and its arguments")
    run_cmd.set_defaults(func=cmd_run)
    for name, fn, help_ in (("status", cmd_status, "Is the campaign running, since when, how many passes."),
                            ("stop", cmd_stop, "Stop the campaign's run at the pass boundary (or --now).")):
        sp = subparsers.add_parser(name, help=help_)
        sp.add_argument("db", help="The campaign record.")
        sp.add_argument("--campaign", default=None, help="A campaign id prefix (default: the latest).")
        if name == "stop":
            sp.add_argument("--now", action="store_true", help="SIGINT the run now instead of waiting for the pass boundary.")
            sp.add_argument("--why", default=None, help="A word on why, kept with the request.")
        sp.set_defaults(func=fn)
    kn_p = subparsers.add_parser("knowledge", help="The library's digests in a campaign record.")
    kn_sub = kn_p.add_subparsers(dest="knowledge_command", required=True)
    dig_p = kn_sub.add_parser("digest", help="Digest every library document the record does not hold yet, one model call each.")
    dig_p.add_argument("--db", required=True, help="The campaign record (the digests live in its store).")
    dig_p.add_argument("--model", default=None, help="The model; default as `flux task run`.")
    dig_p.add_argument("--num-predict", type=int, default=None, help="Output tokens per digest (default 2000).")
    dig_p.add_argument("--replies", default=None, help="A JSON list of scripted replies: runs without a model.")
    dig_p.set_defaults(func=cmd_knowledge_digest)
    show_p = kn_sub.add_parser("show", help="Print the digests the record holds.")
    show_p.add_argument("--db", required=True)
    show_p.set_defaults(func=cmd_knowledge_show)

    tools_p = subparsers.add_parser("tools", help="The checks a gate may run and the stages a document may measure with.")
    tools_p.add_argument("--json", action="store_true", help="The catalog as JSON (what the loop crafter reads).")
    tools_p.set_defaults(func=cmd_tools)

    rtl_p = subparsers.add_parser("rtl", help="The tools an RTL document names: lint, test against a golden model, check a prototype, measure on ASAP7.")
    rtl_sub = rtl_p.add_subparsers(dest="rtl_command", required=True)
    rl = rtl_sub.add_parser("lint", help="Verilator lint for hardware defects (latches, multiple drivers, combinational "
                                         "loops, `<=` in combinational logic, mixed `=`/`<=`, implicit nets); prints each and "
                                         "`N failing`; exit 3 when it does not parse.")
    rl.add_argument("artifact"); rl.add_argument("--module", default=None, help="The top module (default: the first in the artifact).")
    rl.add_argument("--extra", action="append", default=[], help="Another source file the module instantiates (repeatable).")
    rl.add_argument("--timeout", type=float, default=120.0)
    rl.set_defaults(func=cmd_rtl_lint)
    rt = rtl_sub.add_parser("test", help="Verilate the artifact against golden.py's vectors; prints the failing ones and `N failing of M`.")
    rt.add_argument("artifact"); rt.add_argument("--golden", required=True, help="golden.py: PORTS and golden(**inputs).")
    rt.add_argument("--module", default=None, help="The module under test (default: the first `module` in the artifact).")
    rt.add_argument("--timeout", type=float, default=300.0); rt.add_argument("--show", type=int, default=8, help="Failing vectors to print.")
    rt.add_argument("--extra", action="append", default=[], help="Another source file the module instantiates (repeatable).")
    rt.set_defaults(func=cmd_rtl_test)
    rp = rtl_sub.add_parser("proto", help="Check a Python prototype `design(**inputs)` against golden.py -- every input when "
                                          "they total 20 bits or fewer -- as the prototype stage does; prints where it fails "
                                          "and `N failing of M`.")
    rp.add_argument("prototype"); rp.add_argument("--golden", required=True, help="golden.py: PORTS and golden(**inputs).")
    rp.add_argument("--table-max", type=int, default=None, help="The largest module-level table allowed (default 64).")
    rp.add_argument("--timeout", type=float, default=120.0)
    rp.set_defaults(func=cmd_rtl_proto)
    rm_ = rtl_sub.add_parser("measure", help="Synthesise (synth), place or route the artifact on ASAP7; prints metric=value lines.")
    rm_.add_argument("artifact"); rm_.add_argument("--stage", choices=("stat", "synth", "place", "route"), default="synth",
                     help="stat: Yosys alone, area_um2 and cell_count (no timing); synth: + OpenSTA; place, route: OpenROAD.")
    rm_.add_argument("--clock-ps", type=float, default=1000.0); rm_.add_argument("--module", default=None)
    rm_.add_argument("--clock-port", default="auto", help="auto: clk when the module has one; none: combinational.")
    rm_.add_argument("--reset-port", default="auto", help="auto: rst_n when the module is clocked and has one.")
    rm_.add_argument("--repair-design", action="store_true", help="Buffer long wires and high fanout after placement.")
    rm_.add_argument("--timeout", type=float, default=900.0)
    rm_.set_defaults(func=cmd_rtl_measure)

    from .prog import add_parsers as add_prog

    add_prog(subparsers)

    cs_p = subparsers.add_parser("champsim", help="ChampSim as tools a document names: run an .ini or a prefetcher header on traces, build, check.")
    cs_sub = cs_p.add_subparsers(dest="champsim_command", required=True)
    cr = cs_sub.add_parser("run", help="Measure ARTIFACT (an .ini, or a .h prefetcher built in) on every trace; prints name=value lines.")
    cr.add_argument("artifact"); cr.add_argument("--traces", required=True, help="A directory of *.gz / *.xz traces.")
    cr.add_argument("--warmup", type=int, required=True); cr.add_argument("--sim", type=int, required=True)
    cr.add_argument("--with", dest="with_", default=None, help="More L2 prefetchers to run alongside, comma-separated.")
    cr.add_argument("--jobs", type=int, default=None, help="Simulations at once (default: one per trace).")
    cr.add_argument("--config", default=None, help="An .ini of knobs (and types) added to the artifact's: a header's partners need theirs.")
    cr.set_defaults(func=cmd_champsim_run)
    cb = cs_sub.add_parser("build", help="Build a prefetcher header into ChampSim; prints `0 failing` or the first error and `1 failing` (exit 3).")
    cb.add_argument("header")
    cb.set_defaults(func=cmd_champsim_build)
    cc = cs_sub.add_parser("check", help="Build a prefetcher header and smoke-run it on one trace; fails when it issues no prefetches.")
    cc.add_argument("header"); cc.add_argument("--traces", required=True)
    cc.set_defaults(func=cmd_champsim_check)

    att_p = subparsers.add_parser("attach", help="Tail the log of the campaign's run (started by `flux run`).")
    att_p.add_argument("db", help="The campaign record.")
    att_p.add_argument("--campaign", default=None)
    att_p.add_argument("--lines", type=int, default=40)
    att_p.set_defaults(func=cmd_attach)

    return parser


#: The commands that read an existing campaign record named by their `db` argument.
_READS_A_RECORD = frozenset({"report", "status", "stop", "attach", "log"})


def _cmd_serve(args):
    from flux_web.cli import serve

    return serve(args)


def _cmd_user(args):
    from flux_web.cli import user

    return user(args)


def _cmd_task_migrate(args: argparse.Namespace) -> int:
    """`flux task migrate FOLDER [--write]` (D811): each document, what it changes; exit 1 when one
    needs a person or would not load."""
    from pathlib import Path

    from flux_loop.migrate import migrate_loop

    folder = Path(args.folder)
    if folder.is_file():
        folder = folder.parent
    got = migrate_loop(folder, write=args.write)
    for d in got["documents"]:
        print(f"{d['file']}{' -> ' + d['to'] if d['to'] != d['file'] else ''}: {d['status']}" + (f" -- {d['why']}" if d["why"] else ""))
        for x in d["said"]:
            print(f"  {x}")
        for x in d["manual"]:
            print(f"  NEEDS A PERSON: {x}")
    if not args.write and any(d["status"] == "would migrate" for d in got["documents"]):
        print("(nothing written: --write writes them)")
    return 1 if any(d["status"] in ("needs a hand", "failed") for d in got["documents"]) else 0


def _cmd_agent_test(args: argparse.Namespace) -> int:
    """`flux agent test NAME [--live]` (D751): each step said, exit 0 when the agent is ready."""
    import json
    from pathlib import Path

    from flux_loop.agent_check import check_agent

    got = check_agent(args.agent, live=args.live)
    if args.json == "-":
        print(json.dumps(got))
        return 0 if got["ok"] else 1
    print(f"{args.agent}{' ' + got['version'] if got.get('version') else ''}: {'READY' if got['ok'] else 'NOT READY'}"
          f" ({got.get('seconds', 0)} s)")
    for s in got["steps"]:
        print(f"  {'ok  ' if s['ok'] else 'FAIL'} {s['step']}: {s['said']}")
    if args.json:
        Path(args.json).write_text(json.dumps(got, indent=1))
    return 0 if got["ok"] else 1


def _cmd_login(args: argparse.Namespace) -> int:
    """The agent's login command, run with HOME the given folder, under a terminal of its own
    (D734): a login made for a person (a menu, a prompt) needs one, and the container is started
    without one -- Podman's terminal mode fails here past a few dozen mounts. This relays the
    terminal to stdin and stdout, so the web's pipes drive it. In the sandbox the container's HOME
    is that folder already; on the host it is set here."""
    import fcntl
    import os
    import pty
    import select
    import struct
    import termios

    cmd = args.cmd[1:] if args.cmd[:1] == ["--"] else list(args.cmd)
    if not cmd:
        print("flux login: which command? e.g. flux login --home DIR -- opencode auth login", file=sys.stderr)
        return 2
    if not os.environ.get("FLUX_SANDBOXED"):                 # in the sandbox HOME is that folder already (D744: /home/flux)
        os.makedirs(args.home, exist_ok=True)
        os.environ["HOME"] = str(args.home)
    os.environ.setdefault("TERM", "xterm-256color")
    pid, fd = pty.fork()
    if pid == 0:                                             # the agent, on its terminal, in HOME
        try:
            os.chdir(os.environ.get("HOME") or args.home)
            os.execvp(cmd[0], cmd)
        except OSError as exc:
            os.write(2, f"flux login: {cmd[0]}: {exc.strerror or exc}\n".encode())
        os._exit(127)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 400, 0, 0))   # D748: wide, so a sign-in link is one line
    ins = [sys.stdin.fileno()]
    while True:
        try:
            ready, _, _ = select.select([fd, *ins], [], [])
        except InterruptedError:
            continue
        if fd in ready:
            try:
                data = os.read(fd, 4096)
            except OSError:
                data = b""
            if not data:
                break
            os.write(sys.stdout.fileno(), data)
        if ins and ins[0] in ready:
            data = os.read(ins[0], 4096)
            if data:
                os.write(fd, data)
            else:
                ins = []                                     # the page went away: the agent goes on until it ends
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status) if hasattr(os, "waitstatus_to_exitcode") else status >> 8


def main(argv: list[str] | None = None) -> int:
    """The CLI. An unexpected failure prints one line naming the error (D590); `FLUX_DEBUG=1`
    shows the traceback."""
    import os
    from pathlib import Path

    import sys

    from flux_llm.openai_compat import load_user_config

    load_user_config()                       # ~/.config/flux/flux.env: the model settings (D651)
    for stream in (sys.stdout, sys.stderr):
        # line-buffer when piped, or a live run looks frozen (D597)
        if not stream.isatty() and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(line_buffering=True)
            except (ValueError, OSError):
                pass
    from .sandbox import enabled, launch, relay_proxy

    relay_proxy()                            # inside an allowlisted sandbox: the way out (D680)
    parser = build_parser()
    args = parser.parse_args(argv)
    # `task check` too: building the problem imports its world hooks and its golden model (D683)
    boxed = ("task run" if args.command == "task" and getattr(args, "task_command", None) == "run"
             else "task check" if args.command == "task" and getattr(args, "task_command", None) == "check"
             else "ask" if args.command == "ask" else "consult" if args.command == "consult" else "login" if args.command == "login"
             else "agent test" if args.command == "agent" else "")
    if boxed in ("task run", "task check"):  # D787: a folder of several problems: which one
        from .commands import pick_document

        chosen = pick_document(args.file)
        if chosen is None:
            return 2
        if chosen != args.file:
            argv = [chosen if a == args.file else a for a in (list(argv) if argv is not None else sys.argv[1:])]
            args.file = chosen
    if boxed and enabled(args):              # D680: the run re-launched in its container
        return launch(list(argv) if argv is not None else sys.argv[1:], args, boxed)
    db = getattr(args, "db", None)
    if args.command in _READS_A_RECORD and db and not Path(db).is_file():
        print(f"flux {args.command}: no campaign record at {db} (a run writes <document dir>/out/<id>.db)")
        return 2
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130
    except BrokenPipeError:
        # `flux ... | head` closed the pipe: not an error; send further output to devnull (D600)
        import sys

        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 141
    except Exception as exc:  # noqa: BLE001 -- the last line of defence, said plainly
        if os.environ.get("FLUX_DEBUG"):
            raise
        print(f"flux {args.command}: {type(exc).__name__}: {exc}\n(set FLUX_DEBUG=1 for the traceback)")
        return 1


if __name__ == "__main__":
    sys.exit(main())

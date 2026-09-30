"""A running campaign the command line can see, stop at a pass boundary and re-attach to (D513):
the registration, the stop request, and `flux status` / `flux stop`."""

from __future__ import annotations

import argparse
import json
import os

from flux_llm import ScriptedProposer
from flux_loop import Candidate, LoopRequest, Problem, Verdict, ops, run_loop
from flux_records import Records


class One(Problem):
    name = "one"

    def subgoals(self):
        return ["n"]

    def design_prompt(self, subgoal, method, state, human, prior, prior_why):
        return "design n", None

    def parse_design(self, reply, subgoal):
        return Candidate("n-v", reply.strip(), subgoal=subgoal), ""

    def build(self, cand, subgoal, state):
        return int(cand.artifact)

    def judge(self, built, cand, subgoal, state):
        return Verdict(built == 7, float(abs(built - 7)), "")

    def compose(self, admitted, state):
        return admitted.get("n")

    def stages(self):
        return ["screen"]

    def measure(self, cand, stage, state):
        return {"cost": float(int(cand.artifact))}


def test_a_run_registers_under_its_campaign_and_counts_its_passes(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUX_TRACE_ROOT", str(tmp_path / "traces"))
    monkeypatch.setenv("FLUX_RUN_LOG", str(tmp_path / "run.log"))
    db = str(tmp_path / "one.db")
    req = LoopRequest(db=db, steps=2, prototype=False, critique_rounds=0)
    run_loop(One(), req, proposer=ScriptedProposer(["7"]), log=lambda _m: None)
    cid = Records(db, objective=One().objective(req)).campaign_id
    st = ops.status(cid)
    assert st["state"] == "running" and st["pid"] == os.getpid() and st["passes"] == 1
    assert st["log"] == str(tmp_path / "run.log") and st["dir"] == os.path.join(str(tmp_path / "traces"), cid[:12])
    run_loop(One(), req, proposer=ScriptedProposer(["7"]), log=lambda _m: None)
    again = ops.status(cid)                  # D594: the next pass of the same process keeps counting
    assert again["passes"] == 2 and again["started"] == st["started"], "flux status said 0 passes on a run in its third"
    doc = json.load(open(os.path.join(st["dir"], "run.json")))
    assert doc["campaign"] == cid and doc["argv"]


def test_a_stop_request_is_seen_by_the_running_process_and_a_dead_pid_is_stale(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUX_TRACE_ROOT", str(tmp_path / "traces"))
    ops.register("abcdef0123456789", str(tmp_path))
    assert ops.stop_requested() is None
    p = ops.request_stop("abcdef0123456789", "the tree changed")
    assert os.path.exists(p) and ops.stop_requested().startswith("the tree changed")
    assert ops.status("abcdef0123456789")["stop"].startswith("the tree changed")
    ops.clear_stop()
    assert ops.stop_requested() is None and ops.status("abcdef0123456789")["stop"] is None
    # D695: a run stopped at once leaves its stop behind; the next run's registration is not bound by it
    ops.request_stop("abcdef0123456789", "stopped from the web")
    ops.register("abcdef0123456789", str(tmp_path))                 # the same process: the ask holds
    assert ops.stop_requested().startswith("stopped from the web")
    json.dump({"pid": 2 ** 22 - 1}, open(os.path.join(ops.run_dir("abcdef0123456789"), "run.json"), "w"))
    ops.register("abcdef0123456789", str(tmp_path))                 # a new run: the old ask is gone
    assert ops.stop_requested() is None
    # a registration whose process is gone is stale, never "running"
    d = ops.run_dir("deadbeefdeadbeef")
    os.makedirs(d)
    json.dump({"pid": 2 ** 22 - 1, "passes": 3, "started": 0}, open(os.path.join(d, "run.json"), "w"))
    assert ops.status("deadbeefdeadbeef")["state"] == "stale"
    assert ops.status("0000000000000000")["state"] == "none"
    assert not ops.interrupt("deadbeefdeadbeef")


def test_flux_status_and_stop_read_and_write_the_registration(tmp_path, monkeypatch, capsys):
    from flux_cli.commands import cmd_status, cmd_stop

    monkeypatch.setenv("FLUX_TRACE_ROOT", str(tmp_path / "traces"))
    db = str(tmp_path / "one.db")
    req = LoopRequest(db=db, steps=2, prototype=False, critique_rounds=0)
    run_loop(One(), req, proposer=ScriptedProposer(["7"]), log=lambda _m: None)
    assert cmd_status(argparse.Namespace(db=db, campaign=None)) == 0
    out = capsys.readouterr().out
    assert ": running" in out and "passes this run: 1" in out and "no log registered" in out
    assert cmd_stop(argparse.Namespace(db=db, campaign=None, now=False, why="a new tree")) == 0
    assert "stop requested at the pass boundary" in capsys.readouterr().out
    cid = Records(db, objective=One().objective(req)).campaign_id
    assert ops.stop_requested(cid).startswith("a new tree")
    assert cmd_status(argparse.Namespace(db=db, campaign=None)) == 0
    assert "stop requested: a new tree" in capsys.readouterr().out


def test_the_tui_loop_holds_at_the_boundary_when_a_stop_was_asked(tmp_path, monkeypatch):
    from flux_tui.app import _stop_requested

    monkeypatch.setenv("FLUX_TRACE_ROOT", str(tmp_path / "traces"))
    ops.register("feedfacefeedface", str(tmp_path))
    assert _stop_requested() is None
    ops.request_stop("feedfacefeedface", "flux stop")
    assert _stop_requested().startswith("flux stop") and _stop_requested() is None, "consumed once seen"


def test_the_record_finds_the_run_whatever_the_trace_root(tmp_path, monkeypatch):
    """A shell with another trace root finds and stops the run when given the record, through
    the pointer the registration leaves beside it (D597)."""
    db = str(tmp_path / "rec.db")
    monkeypatch.setenv("FLUX_TRACE_ROOT", str(tmp_path / "run-root"))
    d = ops.register("abcdef0123456789", str(tmp_path), db=db)
    monkeypatch.setenv("FLUX_TRACE_ROOT", str(tmp_path / "shell-root"))     # the other shell
    assert ops.status("abcdef0123456789")["state"] == "none"
    st = ops.status("abcdef0123456789", db)
    assert st["state"] == "running" and st["dir"] == d
    ops.request_stop("abcdef0123456789", "from another shell", db=db)
    assert ops.stop_requested().startswith("from another shell")           # the running process sees it
    ops.clear_stop()

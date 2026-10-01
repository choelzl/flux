"""`flux_evaluator_abi.tools` (D429): one tool launch, one "not on PATH" refusal, one tail."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from flux_evaluator_abi import ToolRun, ensure_binary, run_tool, tails


def test_run_tool_returns_a_typed_run_and_times_it(monkeypatch):
    run = run_tool([sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr)"],
                   timeout_s=30, what="a python one-liner")
    assert isinstance(run, ToolRun) and run.ok and run.stdout.strip() == "out"
    assert "err" in run.stderr and run.cmd[0] == sys.executable
    assert run.tail() == "--- stdout (tail) ---\nout\n\n--- stderr (tail) ---\nerr\n"


def test_run_tool_names_the_failure_and_the_missing_binary():
    class MyError(RuntimeError):
        pass

    with pytest.raises(MyError, match=r"compile: 'no-such-binary-xyz' not found on PATH"):
        run_tool(["no-such-binary-xyz"], timeout_s=5, what="compile", error=MyError)
    with pytest.raises(MyError, match=r"compile failed \(exit=3\):\n--- stdout"):
        run_tool([sys.executable, "-c", "raise SystemExit(3)"], timeout_s=30, what="compile",
                 error=MyError)
    run = run_tool([sys.executable, "-c", "raise SystemExit(3)"], timeout_s=30)   # no error type
    assert not run.ok and run.returncode == 3
    with pytest.raises(RuntimeError, match="timed out after 0.2s"):
        run_tool([sys.executable, "-c", "import time; time.sleep(5)"], timeout_s=0.2, what="sleep")


def test_tails_reads_any_process_shape_and_caps():
    proc = SimpleNamespace(stdout="a" * 5000, stderr="b" * 10)
    text = tails(proc)
    assert text.startswith("--- stdout (tail) ---\n")
    assert text.split("\n")[1] == "a" * 4000                    # capped at TAIL_CHARS
    assert text.endswith("--- stderr (tail) ---\n" + "b" * 10)
    assert tails(proc, stdout=False) == "--- stderr (tail) ---\n" + "b" * 10
    assert tails(proc, stderr=False, chars=3) == "--- stdout (tail) ---\naaa"
    assert tails(SimpleNamespace(stdout=None, stderr=None)) == \
        "--- stdout (tail) ---\n\n--- stderr (tail) ---\n"


def test_ensure_binary_finds_or_refuses_with_the_hint():
    assert ensure_binary("python3").endswith("python3") or ensure_binary("python3")
    with pytest.raises(FileNotFoundError, match="'no-such-binary-xyz' not found on PATH -- nix develop"):
        ensure_binary("no-such-binary-xyz", hint="nix develop provides it", error=FileNotFoundError)


def test_tool_source_builds_once_reuses_while_valid_and_takes_a_provided_one(tmp_path):
    from pathlib import Path

    from flux_evaluator_abi import ToolSource

    built: list[Path] = []

    def build(work_dir: Path) -> Path:
        p = work_dir / "tool.bin"
        p.write_text("x")
        built.append(p)
        return p

    src = ToolSource("demo", build)
    first = src.ensure()
    assert first.exists() and src.ensure() is first and len(built) == 1
    first.unlink()                                          # the artifact vanished: rebuilt
    second = src.ensure()
    assert second != first and second.exists() and len(built) == 2
    provided = tmp_path / "given.bin"
    provided.write_text("y")
    src2 = ToolSource("demo", build)
    assert src2.ensure(provided=lambda: provided) == provided and len(built) == 2
    assert src2.ensure(provided=lambda: None) == provided  # memoised, never rebuilt
    # a tuple artifact is valid while every path in it exists; a non-path artifact by `valid`
    tup = ToolSource("pair", lambda d: (d / "a", 7), valid=lambda a: a[1] == 7)
    assert tup.ensure()[1] == 7 and tup.ensure() is tup.ensure()


def test_clone_and_build_step_name_the_failure():
    from pathlib import Path

    from flux_evaluator_abi import build_step, clone

    with pytest.raises(RuntimeError, match=r"git clone of Nothing failed \(exit="):
        clone("file:///no/such/repo/xyz", Path("/tmp/flux-clone-test-nothing"), what="Nothing",
              timeout_s=30)
    with pytest.raises(RuntimeError, match=r"Demo build failed \(exit=2\) — needs a compiler"):
        build_step([sys.executable, "-c", "raise SystemExit(2)"], cwd=Path("/tmp"), what="Demo build",
                   timeout_s=30, hint="needs a compiler")
    with pytest.raises(RuntimeError, match=r"Demo build failed \(exit=0\)"):
        build_step([sys.executable, "-c", "pass"], cwd=Path("/tmp"), what="Demo build", timeout_s=30,
                   expect=Path("/tmp/flux-no-such-artifact-xyz"))
    run = build_step([sys.executable, "-c", "print('ok')"], cwd=Path("/tmp"), what="Demo build", timeout_s=30)
    assert run.ok and run.stdout.strip() == "ok"


class _Seen:
    """A profile listener: the tool task's start, its live updates, its end."""

    def __init__(self):
        self.starts, self.updates, self.ends = [], [], []

    def phase_start(self, name, why, params):
        self.starts.append((name, why, params))
        return len(self.starts)

    def phase_update(self, token, name, output):
        self.updates.append(dict(output))

    def phase_end(self, token, name, seconds, failed, output):
        self.ends.append((name, failed, dict(output)))


@pytest.fixture()
def seen():
    import flux_profile

    s = _Seen()
    flux_profile.add_listener(s)
    yield s
    flux_profile.remove_listener(s)


def test_a_tool_task_says_what_ran_and_how_it_went(tmp_path, seen):
    """D709: the command and folder, the ends of stdout and stderr live while it runs and at
    its end, the exit; stdin still reaches it."""
    code = ("import sys, time\nprint(sys.stdin.read().upper(), flush=True)\ntime.sleep(2.5)\n"
            "print('done', flush=True)\nprint('warned', file=sys.stderr)\nraise SystemExit(4)")
    run = run_tool([sys.executable, "-c", code], cwd=tmp_path, timeout_s=30, what="stage bench x-1", stdin="hello")
    assert run.returncode == 4 and run.stdout == "HELLO\ndone\n"
    name, why, params = seen.starts[-1]
    assert name == "tool:" + sys.executable.rsplit("/", 1)[-1] and why == "stage bench x-1"
    assert params["command"].startswith(sys.executable) and "SystemExit(4)" in params["command"]
    assert params["folder"] == str(tmp_path)
    assert any(u.get("stdout (live tail)") == "HELLO\n" for u in seen.updates), "what it printed, while it runs"
    _n, failed, out = seen.ends[-1]
    assert not failed and out == {"exit": 4, "stdout": "HELLO\ndone\n", "stderr": "warned\n"}


def test_a_timed_out_tool_keeps_what_it_printed_and_odd_bytes_are_replaced(seen):
    with pytest.raises(RuntimeError, match="timed out"):
        run_tool([sys.executable, "-c", "import time; print('started', flush=True); time.sleep(30)"], timeout_s=6)   # time to start under load
    _n, failed, out = seen.ends[-1]
    assert failed and out["exit"] == "timed out" and out["stdout"] == "started\n"
    run = run_tool([sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'a\\xffb\\r\\nc')"], timeout_s=30)
    assert run.stdout == "a�b\nc", "never an exception for a byte that is not UTF-8; newlines as text mode reads them"

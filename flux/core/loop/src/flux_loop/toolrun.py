"""Running an external tool, once (D429).

The loop's checks, probes and stages launch real tools (yosys, Verilator, ChampSim, make).
This module is the shared launcher, "not on PATH" refusal and output tail, so a tool's error
text has one shape and a tool launch is timed in the profile tree. The launch's task says what
ran and how it went (D709): its command and folder, its output's ends while it runs, its exit.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

__all__ = ["TAIL_CHARS", "ToolRun", "run_tool",
           "tails"]

TAIL_CHARS = 4000


@dataclass(frozen=True)
class ToolRun:
    """What one tool launch produced. `ok` is a zero exit; `tail()` is the text an error
    message carries."""

    cmd: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def tail(self, *, stdout: bool = True, stderr: bool = True, chars: int = TAIL_CHARS) -> str:
        return tails(self, stdout=stdout, stderr=stderr, chars=chars)


def tails(proc: Any, *, stdout: bool = True, stderr: bool = True, chars: int = TAIL_CHARS) -> str:
    """The last `chars` of a process's stdout and/or stderr, labelled -- the one shape
    every adapter's error message quotes a tool through. `proc` is a `ToolRun` or a
    `subprocess.CompletedProcess` (anything with `.stdout` / `.stderr` strings)."""
    parts = []
    if stdout:
        parts.append(f"--- stdout (tail) ---\n{(proc.stdout or '')[-chars:]}")
    if stderr:
        parts.append(f"--- stderr (tail) ---\n{(proc.stderr or '')[-chars:]}")
    return "\n".join(parts)


def _decode(chunks: list[bytes]) -> str:
    """As `text=True` reads it -- universal newlines -- but a byte that is not UTF-8 is replaced,
    never an exception."""
    return b"".join(chunks).decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")


def _end(chunks: list[bytes], chars: int = TAIL_CHARS) -> str:
    """The last `chars` of what a stream printed so far, read without joining it all."""
    got: list[bytes] = []
    n = 0
    for c in reversed(chunks[:]):
        got.append(c)
        n += len(c)
        if n >= chars * 2:
            break
    text = _decode(list(reversed(got)))
    return text[-chars:]


def run_tool(cmd: list[str], *, cwd: str | Path | None = None, timeout_s: float,
             env: Mapping[str, str] | None = None, what: str = "",
             error: type[Exception] | None = None, stdin: str | None = None) -> ToolRun:
    """Launch `cmd` and return its `ToolRun`. Timed as a `tool:<binary>` phase in the
    profile tree when `flux_profile` is present (the accounting cannot drift out of step
    with the code, D295); the phase carries the command and folder, the ends of its output
    live (each second) and at its end, and the exit (D709). A missing binary or a timeout
    raises `error` (default `RuntimeError`) with a message that names the tool and `what` it
    was doing; a non-zero exit raises the same when `error` is given and is otherwise the
    caller's to read from `.ok` / `.tail()` -- some tools exit non-zero and still report."""
    exc_type = error or RuntimeError
    binary = cmd[0].rsplit("/", 1)[-1]
    label = what or binary
    try:
        from flux_profile import phase, progress
    except Exception:  # noqa: BLE001
        from contextlib import contextmanager

        @contextmanager
        def phase(name: str, **_kw: Any):  # type: ignore[misc]
            yield {}

        def progress(**_kw: Any) -> None:  # type: ignore[misc]
            return None
    shown = shlex.join(str(c) for c in cmd)
    shown = shown if len(shown) <= 2000 else shown[:2000] + " ..."
    try:
        with phase(f"tool:{binary}", why=what, command=shown, folder=str(cwd or os.getcwd()),
                   **({"stdin": stdin[:48000] + ("\n… stdin truncated after 48000 characters" if len(stdin) > 48000 else "")} if stdin is not None else {})) as out:
            # D854: the tool and everything it starts are one process group of their own: the whole
            # call is bounded by `timeout_s` -- its output drained too -- and the group goes with it
            proc = subprocess.Popen(
                cmd, cwd=None if cwd is None else str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                stdin=None if stdin is None else subprocess.PIPE, env=None if env is None else dict(env),
                start_new_session=True)
            streams: dict[str, list[bytes]] = {"stdout": [], "stderr": []}

            def read(fh: Any, into: list[bytes]) -> None:
                fd = fh.fileno()
                while chunk := os.read(fd, 65536):
                    into.append(chunk)
                fh.close()

            readers = [threading.Thread(target=read, args=(getattr(proc, k), streams[k]), daemon=True) for k in streams]
            if stdin is not None:
                def feed() -> None:
                    try:
                        proc.stdin.write(stdin.encode())
                        proc.stdin.close()
                    except OSError:
                        pass
                readers.append(threading.Thread(target=feed, daemon=True))
            for t in readers:
                t.start()
            end, said = time.monotonic() + timeout_s, (0, 0)
            try:
                while True:
                    try:
                        proc.wait(timeout=max(0.0, min(1.0, end - time.monotonic())))
                        break
                    except subprocess.TimeoutExpired:
                        if time.monotonic() >= end:
                            raise
                        now = (len(streams["stdout"]), len(streams["stderr"]))
                        if now != said:                         # what it printed since: the ends, live
                            said = now
                            progress(**{"stdout (live tail)": _end(streams["stdout"]), "stderr (live tail)": _end(streams["stderr"])})
                # the tool ended; a child it left may still hold its output open -- within the same bound
                for t in readers:
                    t.join(max(0.0, end - time.monotonic()))
                if any(t.is_alive() for t in readers):
                    raise subprocess.TimeoutExpired(cmd, timeout_s)
            except subprocess.TimeoutExpired:
                _end_group(proc)
                for t in readers:
                    t.join(5)
                out.update(exit="timed out", stdout=_end(streams["stdout"]), stderr=_end(streams["stderr"]))
                raise
            except BaseException:                           # an interruption: the tool goes with it
                _end_group(proc)
                raise
            # a normal end leaves the group be: its leader reaped, its number could be another's
            run = ToolRun(tuple(cmd), proc.returncode, _decode(streams["stdout"]), _decode(streams["stderr"]))
            out.update(exit=run.returncode, stdout=run.stdout[-TAIL_CHARS:], stderr=run.stderr[-TAIL_CHARS:])
    except FileNotFoundError as exc:
        raise exc_type(f"{label}: {cmd[0]!r} not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise exc_type(f"{label}: timed out after {timeout_s:g}s") from exc
    if error is not None and not run.ok:
        raise error(f"{label} failed (exit={run.returncode}):\n{run.tail()}")
    return run


def _end_group(proc: subprocess.Popen) -> None:
    """The tool's process group killed (D854): the tool, and every process it started that is
    still there -- called while one of them lives (the tool itself, or a child holding its
    output), so the group's number is still theirs. Quiet when they are all gone already."""
    import signal

    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        proc.wait(5)
    except (subprocess.TimeoutExpired, OSError):
        pass

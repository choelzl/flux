"""Running an external tool, once (D429).

Every adapter launches a real tool (yosys, Verilator, Booksim2, gem5, 3D-ICE, git, make).
This module is the shared launcher, "not on PATH" refusal and output tail, so an adapter's error
text has one shape and a tool launch is timed in the profile tree. The launch's task says what
ran and how it went (D709): its command and folder, its output's ends while it runs, its exit.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

__all__ = ["TAIL_CHARS", "ToolRun", "ToolSource", "build_step", "clone", "ensure_binary", "run_tool",
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


def ensure_binary(name: str, *, hint: str = "", error: type[Exception] = RuntimeError) -> str:
    """The absolute path of `name` on PATH, or `error` naming what is missing and how to
    get it -- an adapter refuses loudly before spending anything (docs/evaluator-abi.md)."""
    found = shutil.which(name)
    if found is None:
        raise error(f"{name!r} not found on PATH" + (f" -- {hint}" if hint else ""))
    return found


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
        with phase(f"tool:{binary}", why=what, command=shown, folder=str(cwd or os.getcwd())) as out:
            proc = subprocess.Popen(
                cmd, cwd=None if cwd is None else str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                stdin=None if stdin is None else subprocess.PIPE, env=None if env is None else dict(env))
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
            while True:
                try:
                    proc.wait(timeout=max(0.0, min(1.0, end - time.monotonic())))
                    break
                except subprocess.TimeoutExpired:
                    if time.monotonic() >= end:
                        proc.kill()
                        proc.wait()
                        for t in readers:
                            t.join(5)
                        out.update(exit="timed out", stdout=_end(streams["stdout"]), stderr=_end(streams["stderr"]))
                        raise
                    now = (len(streams["stdout"]), len(streams["stderr"]))
                    if now != said:                         # what it printed since: the ends, live
                        said = now
                        progress(**{"stdout (live tail)": _end(streams["stdout"]), "stderr (live tail)": _end(streams["stderr"])})
            for t in readers:
                t.join()
            run = ToolRun(tuple(cmd), proc.returncode, _decode(streams["stdout"]), _decode(streams["stderr"]))
            out.update(exit=run.returncode, stdout=run.stdout[-TAIL_CHARS:], stderr=run.stderr[-TAIL_CHARS:])
    except FileNotFoundError as exc:
        raise exc_type(f"{label}: {cmd[0]!r} not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise exc_type(f"{label}: timed out after {timeout_s:g}s") from exc
    if error is not None and not run.ok:
        raise error(f"{label} failed (exit={run.returncode}):\n{run.tail()}")
    return run


def clone(url: str, into: Path, *, what: str, timeout_s: float, ref: str | None = None,
          shallow: bool = True) -> Path:
    """`git clone` of `url` into `into` (shallow by default; `ref` as `--branch` when given), a
    failure raised as RuntimeError naming `what` and quoting the tail (D437)."""
    cmd = ["git", "clone"] + (["--depth", "1"] if shallow else []) \
        + (["--branch", ref] if ref else []) + [url, str(into)]
    run = run_tool(cmd, cwd=into.parent, timeout_s=timeout_s, what=f"git clone of {what}")
    if not run.ok:
        raise RuntimeError(f"git clone of {what} failed (exit={run.returncode}).\n"
                           f"{run.tail(stdout=False)}")
    return into


def build_step(cmd: list[str], *, cwd: Path, what: str, timeout_s: float, hint: str = "",
               env: Mapping[str, str] | None = None, expect: Path | None = None) -> ToolRun:
    """One build command in `cwd`; a non-zero exit, or a missing `expect` artifact after a
    zero exit, is a RuntimeError naming `what`, the `hint` (what the machine needs on PATH)
    and the tool's tail (D437)."""
    run = run_tool(cmd, cwd=cwd, timeout_s=timeout_s, env=env, what=what)
    if not run.ok or (expect is not None and not expect.exists()):
        raise RuntimeError(f"{what} failed (exit={run.returncode})"
                           + (f" — {hint}" if hint else "") + f"\n{run.tail()}")
    return run


def _paths_exist(artifact: Any) -> bool:
    items = artifact if isinstance(artifact, (tuple, list)) else (artifact,)
    return all(p.exists() for p in items if isinstance(p, Path))


@dataclass
class ToolSource:
    """A tool obtained once per process -- cloned and built, compiled, or handed over by the
    environment -- under one lock, and reused while its artifact still exists (D437).
    `build(work_dir)` returns the artifact: a `Path`, a tuple of
    paths, or anything else (`valid` then says whether a memoised one still stands)."""

    name: str
    build: Callable[[Path], Any]
    valid: Callable[[Any], bool] = _paths_exist
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _artifact: Any = field(default=None, repr=False)
    work_dir: Path | None = field(default=None, repr=False)

    def ensure(self, *, provided: Callable[[], Any | None] | None = None) -> Any:
        """The artifact: the memoised one when it still stands, else what `provided()` hands
        over (an environment-supplied binary), else a fresh build in a new temp dir."""
        with self._lock:
            if self._artifact is not None and self.valid(self._artifact):
                return self._artifact
            got = provided() if provided is not None else None
            if got is None:
                self.work_dir = Path(tempfile.mkdtemp(prefix=f"flux-{self.name}-build-"))
                got = self.build(self.work_dir)
            self._artifact = got
            return got

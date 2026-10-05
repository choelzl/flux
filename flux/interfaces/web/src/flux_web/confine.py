"""What the server reads and writes of a loop stays inside the loop (D852).

A run writes some of the folders the server later reads -- the loop's `runs/` (its answer), `out/`
(the record and its run pointer) and its cache (the journal, the transcript). A run can therefore
leave a symbolic link there pointing anywhere this server's account can read or write, or a run
pointer naming another directory. So every host-side read, append or replace of a loop's file goes
through here:

- `within(path, *roots)`: the path with every link followed must lie inside one of `roots` -- the
  loop's folder, or its own cache; else `Escape`.
- `open_read` / `append`: open that resolved path with O_NOFOLLOW, so a link swapped in after the
  check is refused rather than followed.
- `replace`: a new file written beside, then renamed over the old one -- never written through a
  link, and an imported (hardlinked) document is replaced, not changed in its source.

A parent folder swapped for a link between the check and the open is not covered (the window is
small and the folders are the loop's own); the final component is.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import IO

__all__ = ["Escape", "append", "open_read", "replace", "within"]


class Escape(ValueError):
    """A path that leads outside the loop's own folders."""


def within(path: str | os.PathLike, *roots: str | os.PathLike | None) -> Path:
    """`path` resolved (links followed), when inside one of `roots`; else `Escape`."""
    real = Path(os.path.realpath(path))
    for r in roots:
        if not r:
            continue
        rr = Path(os.path.realpath(r))
        if real == rr or rr in real.parents:
            return real
    raise Escape(f"{path} leads outside the loop")


def open_read(path: str | os.PathLike, *roots: str | os.PathLike | None, text: bool = False) -> IO:
    """The file opened for reading, confined to `roots`; a link at the last step refused."""
    real = within(path, *roots)
    fd = os.open(real, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    return os.fdopen(fd, "r" if text else "rb")


def append(path: str | os.PathLike, data: str, *roots: str | os.PathLike | None) -> None:
    """`data` appended to the file (made when missing), confined to `roots`."""
    real = within(path, *roots)
    fd = os.open(real, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0), 0o644)
    with os.fdopen(fd, "a") as fh:
        fh.write(data)


def replace(path: str | os.PathLike, data: bytes | str, *roots: str | os.PathLike | None) -> Path:
    """The file's content replaced by `data`: written beside it and renamed over it, so neither a
    symbolic link nor a hardlink at `path` is written through. Its folder must lie in `roots`."""
    path = Path(path)
    folder = within(path.parent, *roots)
    target = folder / path.name
    if target.is_dir() and not target.is_symlink():
        raise Escape(f"{path} is a folder")
    mode = 0o644
    try:
        st = os.lstat(target)
        if not os.path.islink(target):
            mode = st.st_mode & 0o777
    except OSError:
        pass
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=f".{path.name}.", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data.encode() if isinstance(data, str) else data)
        os.chmod(tmp, mode)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return target

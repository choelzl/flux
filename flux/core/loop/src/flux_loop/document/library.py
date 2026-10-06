"""Knowledge files and the loop's own `library/` (D586, D648, D735, D791)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from .spec import TaskSpec


def read_input(path: Path) -> str:
    """A file's text for a model to read (D586): a PDF through `pdftotext -layout`, anything
    else as UTF-8; non-text bytes are reported as such."""
    if path.suffix.lower() == ".pdf":
        if shutil.which("pdftotext") is None:
            return f"({path.name}: a PDF, and pdftotext is not on PATH to read it)"
        import subprocess

        r = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, timeout=120)
        return r.stdout if r.returncode == 0 else f"({path.name}: pdftotext failed: {r.stderr.strip()[:200]})"
    try:
        return path.read_text()
    except UnicodeDecodeError:
        return f"({path.name}: {path.stat().st_size} bytes of binary, not text)"


#: D735, D791: a loop's own papers and references, read without a word in the document: its
#: `library/` (and `flux ask` puts its attachments there).
LIBRARY_FOLDER = "library"


def library_folders(task: "TaskSpec") -> tuple[str, ...]:
    """The folder a document's library adds to the shared one (D648, D791): its `library/`."""
    lib = Path(task.home) / LIBRARY_FOLDER if task.home else None
    return (str(lib.resolve()),) if lib is not None and lib.is_dir() else ()


def own_library(task: "TaskSpec") -> tuple[str, ...]:
    """The loop's own `library/`, when it holds a document (D753): what its Setup digests first."""
    from flux_knowledge.connectors.text import library_files as walk

    return tuple(f for f in library_folders(task) if any(True for _ in walk(Path(f))))


def library_on(task: "TaskSpec") -> bool:
    """Whether the library reaches this document's prompts and agents: always, unless
    `flow.knowledge` says `none` (D648)."""
    return "none" not in (task.flow.get("knowledge") or ())

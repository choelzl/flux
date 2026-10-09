"""Test-only loop documents, independent of the installed CLI."""

from pathlib import Path
import re


def loop_files(name: str, kind: str) -> list[tuple[str, str]]:
    source = Path(__file__).parent / "fixtures/loops" / kind
    module = re.sub(r"[^A-Za-z0-9_]", "_", name)
    if not re.match(r"[A-Za-z_]", module):
        module = "_" + module
    return [(str(f.relative_to(source)), f.read_text().replace("__NAME__", name).replace("__MODULE__", module))
            for f in sorted(source.rglob("*")) if f.is_file() and "__pycache__" not in f.parts] \
        + [("README.md", f"# {name}\nTest fixture.\n")]


def write_loop(kind: str, name: str, root: Path) -> Path:
    target = root / name
    target.mkdir(parents=True, exist_ok=True)       # a test may write the same loop again, changed
    for rel, text in loop_files(name, kind):
        (target / rel).parent.mkdir(parents=True, exist_ok=True)
        (target / rel).write_text(text)
    return target

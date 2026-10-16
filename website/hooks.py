"""MkDocs hook (D1166): the loop crafter page runs the web app's own configurator. Its script
(docs/assets/crafter-app.js) imports the app's modules from `assets/app/`; this hook publishes there the
files of flux/interfaces/web/src/flux_web/static it needs -- its imports followed, module by module, the
app's stylesheet and the vendored libraries' licences -- straight from the app's folder, so the site
carries no copy to keep in step. `mkdocs serve` watches that folder too."""

from __future__ import annotations

import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATIC = HERE.parent / "flux/interfaces/web/src/flux_web/static"
ENTRY = HERE / "docs/assets/crafter-app.js"
#: where the app's files are published, beside the page's script
PREFIX = "assets/app/"
#: besides the modules: the stylesheet the page reads into its shadow root, and the vendored code's licences
ALSO = ("flux.css", "vendor/LICENSE.preact", "vendor/LICENSE.htm")
_IMPORT = re.compile(r"""(?:\bfrom|\bimport)\s*\(?\s*["'](\.{1,2}/[^"']+)["']""")


def app_files(entry: Path = ENTRY, static: Path = STATIC) -> list[str]:
    """The app's files the page needs, as paths under static/: what `entry` imports from `./app/`, followed."""
    todo = [m[len("./app/"):] for m in _IMPORT.findall(entry.read_text()) if m.startswith("./app/")]
    seen: set[str] = set()
    while todo:
        rel = todo.pop()
        if rel in seen:
            continue
        path = static / rel
        if not path.is_file():
            raise FileNotFoundError(f"{entry.name} needs {rel}, which static/ does not have")
        seen.add(rel)
        for m in _IMPORT.findall(path.read_text()):
            todo.append(_join(rel, m))
    return sorted(seen | set(ALSO))


def _join(rel: str, spec: str) -> str:
    """`spec` (./x.js, ../y/z.js) as a path under static/, from the module at `rel`."""
    parts = rel.split("/")[:-1]
    for piece in spec.split("/"):
        if piece == "..":
            if not parts:
                raise ValueError(f"{rel} imports {spec}, outside static/")
            parts.pop()
        elif piece != ".":
            parts.append(piece)
    return "/".join(parts)


def on_files(files, config):
    from mkdocs.structure.files import File

    for rel in app_files():
        files.append(File.generated(config, PREFIX + rel, abs_src_path=str(STATIC / rel)))
    return files


def on_serve(server, config, builder):
    server.watch(str(STATIC))
    return server

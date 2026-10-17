"""MkDocs hook (D1166): the docs' pages that run the web app's own code. The loop crafter page's script
(docs/assets/crafter-app.js) imports the app's configurator from `assets/app/`; the Preview app (docs/preview/app.html,
over docs/assets/preview/mock.js) runs the whole app from there. This hook publishes there the files of
flux/interfaces/web/src/flux_web/static they need -- the imports followed, module by module, from each entry (the
crafter's script, the app's app.js, the preview's mock), the app's stylesheet and logo, the vendored libraries'
licences -- straight from the app's folder, so the site carries no copy to keep in step. `mkdocs serve` watches that
folder too."""

from __future__ import annotations

import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATIC = HERE.parent / "flux/interfaces/web/src/flux_web/static"
ENTRY = HERE / "docs/assets/crafter-app.js"
#: the Preview app's mock (beside it at assets/preview/: the app's modules are `../app/`)
MOCK = HERE / "docs/assets/preview/mock.js"
#: the whole app, as its index.html loads it
APP = ("app.js",)
#: where the app's files are published, beside the pages' scripts
PREFIX = "assets/app/"
#: besides the modules: the stylesheet, the logo, and the vendored code's licences
ALSO = ("flux.css", "logo.svg", "vendor/LICENSE.preact", "vendor/LICENSE.htm")
_IMPORT = re.compile(r"""(?:\bfrom|\bimport)\s*\(?\s*["'](\.{1,2}/[^"']+)["']""")


def app_files(entry: Path = ENTRY, static: Path = STATIC, *, whole: bool = True, mock: Path | None = MOCK) -> list[str]:
    """The app's files the pages need, as paths under static/: what `entry` imports from `./app/`, what `mock` imports
    from `../app/` and, `whole`, the app's app.js -- each followed. `whole=False, mock=None`: the crafter's alone."""
    todo = [m[len("./app/"):] for m in _IMPORT.findall(entry.read_text()) if m.startswith("./app/")]
    if mock is not None and mock.is_file():
        todo += [m[len("../app/"):] for m in _IMPORT.findall(mock.read_text()) if m.startswith("../app/")]
    if whole:
        todo += list(APP)
    seen: set[str] = set()
    while todo:
        rel = todo.pop()
        if rel in seen:
            continue
        path = static / rel
        if not path.is_file():
            raise FileNotFoundError(f"the docs need {rel}, which static/ does not have")
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

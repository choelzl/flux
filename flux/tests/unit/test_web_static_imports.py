"""Every name a page module imports from another is exported by it: one missing export stops the
whole page at "Loading..." (D1001 found one only in the browser)."""

from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).parents[2] / "interfaces/web/src/flux_web/static"


def _exports(text: str) -> set[str]:
    names = {x.strip().split(" as ")[-1].strip() for m in re.finditer(r"export\s*\{([^}]*)\}", text) for x in m.group(1).split(",")}
    names |= set(re.findall(r"export\s+(?:async\s+)?(?:function\*?|const|let|class)\s+(\w+)", text))
    return {n for n in names if n}


def test_every_imported_name_is_exported():
    mods = {p.name: p.read_text() for p in STATIC.glob("*.js")}
    exported = {name: _exports(text) for name, text in mods.items()}
    missing = [f"{name}: {n} from {target}"
               for name, text in mods.items()
               for names, target in re.findall(r'import\s*\{([^}]*)\}\s*from\s*"\./([^"]+)"', text)
               if target in exported
               for n in (x.strip().split(" as ")[0].strip() for x in names.split(","))
               if n and n not in exported[target]]
    assert not missing, missing

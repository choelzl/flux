"""Building a C++ L2 prefetcher header into a private ChampSim binary, cached by content.

The header holds one `class X : public Prefetcher` with every method inline. The rest is
mechanical: a `.cc` giving the vtable a home, a branch in `multi.l2c_pref` making `x` selectable
with `--l2c_prefetcher_types=x`, and every `knob::NAME` the header declares added to `knobs.cc`
so an `.ini` can set it. Defaults come from a `KNOBS: name=value, ...` line in the header, else 4.

Every build happens in a scratch copy of the tree, keyed by sha256(header + tree fingerprint),
so a second call with the same header reuses the binary and parallel builds never collide.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .binary import resolve_source_tree

#: The slots the prebuilt `pythia` uses (`build_champsim.sh multi multi no 1`), so a built binary
#: is that one plus a prefetcher and its baseline is comparable.
SLOTS = {
    "branch/perceptron.bpred": "branch/branch_predictor.cc",
    "prefetcher/multi.l1d_pref": "prefetcher/l1d_prefetcher.cc",
    "prefetcher/multi.l2c_pref": "prefetcher/l2c_prefetcher.cc",
    "prefetcher/no.llc_pref": "prefetcher/llc_prefetcher.cc",
    "replacement/ship.llc_repl": "replacement/llc_replacement.cc",
}
BINARY_NAME = "perceptron-multi-multi-no-ship-1core"
DEFAULT_KNOB = 4
_SKIP = {".git", "bin", "obj", "results", "experiments", "docs", "scripts", "tracer"}
_CLASS = re.compile(r"\bclass\s+([A-Za-z_]\w*)\s*(?:final\s*)?:\s*(?:public\s+)?Prefetcher\b")
_NAME = re.compile(r"^[a-z][a-z0-9_]{1,31}$")


class BuildError(RuntimeError):
    """The header cannot be installed (no class, a name clash, a tree without the anchors)."""


@dataclass
class BuildResult:
    ok: bool
    binary: Path | None
    name: str                  # the selector: `--l2c_prefetcher_types=<name>`
    errors: str                # the compiler's own words
    elapsed_s: float
    cached: bool = False

    @property
    def first_error(self) -> str:
        """The first real diagnostic; g++'s cascade after it buries the cause."""
        for line in self.errors.splitlines():
            if ": error:" in line or ": fatal error:" in line or "undefined reference" in line:
                return line.strip()
        return self.errors.strip().splitlines()[0] if self.errors.strip() else ""


def class_of(header: str) -> str:
    m = _CLASS.search(header)
    if m is None:
        raise BuildError("no `class X : public Prefetcher` in the header")
    return m.group(1)


def name_of(cls: str) -> str:
    """`ExstridePrefetcher` -> `exstride`, `MyPF` -> `my_pf`: the run-time selector."""
    stem = cls[: -len("Prefetcher")] if cls.endswith("Prefetcher") and cls != "Prefetcher" else cls
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", stem).lower()
    if not _NAME.match(name):
        raise BuildError(f"class {cls} gives the selector {name!r}; it must be [a-z][a-z0-9_]{{1,31}}")
    return name


def knobs_of(header: str) -> dict[str, int]:
    """Every `extern uint32_t NAME;` and `knob::NAME` the header uses, with its default."""
    given: dict[str, int] = {}
    m = re.search(r"KNOBS:\s*(.+)$", header, re.M)
    if m:
        for pair in m.group(1).split(","):
            k, _, v = pair.partition("=")
            if re.fullmatch(r"\s*[a-z]\w*\s*", k) and re.fullmatch(r"\s*\d{1,9}\s*", v):
                given[k.strip()] = int(v)
    used = re.findall(r"extern\s+uint32_t\s+([a-z]\w*)\s*;", header) + re.findall(r"knob::([a-z]\w*)", header)
    return {k: given.get(k, DEFAULT_KNOB) for k in dict.fromkeys(used)}


#: Standard-library symbols and the header each needs: a missing include is the most common
#: compile failure and says nothing about the design, so it is fixed mechanically.
_NEEDS: dict[str, tuple[str, ...]] = {
    "algorithm": ("std::find", "std::sort", "std::max_element", "std::min_element", "std::fill",
                  "std::count", "std::lower_bound", "std::upper_bound", "std::swap", "std::remove"),
    "unordered_map": ("std::unordered_map",),
    "unordered_set": ("std::unordered_set",),
    "map": ("std::map",),
    "set": ("std::set",),
    "deque": ("std::deque",),
    "list": ("std::list",),
    "array": ("std::array",),
    "vector": ("std::vector",),
    "cstdint": ("uint64_t", "uint32_t", "int64_t", "int32_t", "uint8_t", "uint16_t"),
    "cstring": ("std::memset", "std::memcpy"),
    "cmath": ("std::abs", "std::log2", "std::sqrt", "std::floor", "std::ceil"),
    "iostream": ("std::cout", "std::endl", "std::cerr"),
    "utility": ("std::pair", "std::make_pair"),
    "limits": ("std::numeric_limits",),
}


def ensure_includes(header: str) -> str:
    """Add each standard header the code uses but does not include, after the guard."""
    missing = [n for n, syms in _NEEDS.items() if f"#include <{n}>" not in header and any(s in header for s in syms)]
    if not missing:
        return header
    lines = header.splitlines(keepends=True)
    at = 0
    for i, line in enumerate(lines):
        if line.lstrip().startswith("#define") and i and "ifndef" in lines[i - 1]:
            at = i + 1
            break
        if line.lstrip().startswith("#include"):
            at = i
            break
    return "".join(lines[:at]) + "".join(f"#include <{n}>\n" for n in sorted(missing)) + "".join(lines[at:])


def stage_tree(source: Path, into: Path) -> Path:
    """A private, writable copy of the tree (the nix one is read-only), without build output."""
    shutil.copytree(source, into, ignore=lambda d, names: [n for n in names if d == str(source) and n in _SKIP],
                    symlinks=False, dirs_exist_ok=True)
    for dirpath, _, files in os.walk(into):
        os.chmod(dirpath, os.stat(dirpath).st_mode | stat.S_IWUSR)
        for f in files:
            p = os.path.join(dirpath, f)
            os.chmod(p, os.stat(p).st_mode | stat.S_IWUSR)
    (into / "bin").mkdir(exist_ok=True)
    return into


def install(header: str, tree: Path) -> str:
    """Write the header into `tree` and wire it in; returns the selector name."""
    cls = class_of(header)
    name = name_of(cls)
    multi = tree / "prefetcher" / "multi.l2c_pref"
    text = multi.read_text()
    if f'compare("{name}")' in text:
        raise BuildError(f"`{name}` is already a prefetcher in this ChampSim; rename class {cls}")
    (tree / "inc" / f"flux_{name}.h").write_text(ensure_includes(header))
    (tree / "prefetcher" / f"flux_{name}.cc").write_text(f'#include "flux_{name}.h"\n')   # the vtable's home
    _declare_knobs(tree / "src" / "knobs.cc", knobs_of(header))
    first = 'if(!knob::l2c_prefetcher_types[index].compare("none"))'
    if '#include "prefetcher.h"' not in text or first not in text:
        raise BuildError("multi.l2c_pref lacks the `prefetcher.h` include or the `none` branch to anchor on")
    branch = (f'if(!knob::l2c_prefetcher_types[index].compare("{name}"))\n'
              f'\t\t{{\n\t\t\tcout << "adding L2C_PREFETCHER: {cls}" << endl;\n'
              f'\t\t\tprefetchers.push_back(new {cls}(knob::l2c_prefetcher_types[index]));\n\t\t}}\n\t\telse ')
    text = text.replace('#include "prefetcher.h"', f'#include "prefetcher.h"\n#include "flux_{name}.h"', 1)
    multi.write_text(text.replace(first, branch + first, 1))
    return name


def _declare_knobs(knobs_cc: Path, knobs: dict[str, int]) -> None:
    """`uint32_t NAME = DEFAULT;` in `namespace knob`, and an ini-parse branch, per new knob."""
    text = knobs_cc.read_text()
    new = {k: v for k, v in knobs.items() if not re.search(rf"\b{k}\s*=", text)}
    if not new:
        return
    decl = re.search(r"\n\s*uint64_t\s+simulation_instructions\s*=[^;]*;\n", text)
    parse = re.search(r'\n\s*else if\s*\(MATCH\("",\s*"simulation_instructions"\)\)\s*\{[^}]*\}', text)
    if decl is None or parse is None:
        raise BuildError("knobs.cc lacks the simulation_instructions declaration or parse branch to anchor on")
    text = (text[:decl.end()] + "".join(f"\tuint32_t {k} = {v};\n" for k, v in new.items())
            + text[decl.end():parse.end()]
            + "".join(f'\n    else if (MATCH("", "{k}"))\n    {{\n\t\tknob::{k} = atoi(value);\n    }}' for k in new)
            + text[parse.end():])
    knobs_cc.write_text(text)


def build(tree: Path, *, jobs: int | None = None, timeout_s: float = 900) -> tuple[bool, str, Path]:
    """Select the slots and `make` in `tree`: (ok, output, binary path)."""
    for src, dst in SLOTS.items():
        shutil.copyfile(tree / src, tree / dst)
    out = tree / "bin" / BINARY_NAME
    try:
        proc = subprocess.run(["make", f"-j{jobs or os.cpu_count() or 4}"], cwd=tree,
                              capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return False, f"build exceeded {timeout_s:.0f}s", out
    made = tree / "bin" / "champsim"
    if proc.returncode == 0 and made.is_file():
        made.replace(out)
        return True, "", out
    return False, (proc.stdout + proc.stderr)[-6000:], out


def tree_fingerprint(tree: Path) -> str:
    """sha256 over the sources a build reads (not docs or build output)."""
    h = hashlib.sha256()
    for sub in ("Makefile", "inc", "src", "prefetcher", "branch", "replacement", "libbf"):
        root = tree / sub
        files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
        for p in files:
            h.update(str(p.relative_to(tree)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def cache_root() -> Path:
    base = os.environ.get("FLUX_TMPDIR") or tempfile.gettempdir()
    return Path(base) / "champsim"


_TREE_FP: dict[str, str] = {}


def build_header(header: str, *, source: str | Path | None = None, jobs: int | None = None,
                 timeout_s: float = 900) -> BuildResult:
    """Build `header` into a binary, or reuse the one built before for the same header and tree.

    A compile failure is cached too (it is deterministic); a timeout is not.
    """
    started = time.monotonic()
    name = name_of(class_of(header))
    src = resolve_source_tree(source)
    if str(src) not in _TREE_FP:
        _TREE_FP[str(src)] = tree_fingerprint(src)
    key = hashlib.sha256((header + "\0" + _TREE_FP[str(src)] + "\0" + BINARY_NAME).encode()).hexdigest()[:24]
    home = cache_root() / key
    home.mkdir(parents=True, exist_ok=True)
    binary, failed = home / BINARY_NAME, home / "error.txt"
    with open(home / ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)          # one build per key, however many workers ask
        if binary.is_file():
            return BuildResult(True, binary, name, "", time.monotonic() - started, cached=True)
        if failed.is_file():
            return BuildResult(False, None, name, failed.read_text(), time.monotonic() - started, cached=True)
        tree = home / "tree"
        shutil.rmtree(tree, ignore_errors=True)
        try:
            install(header, stage_tree(src, tree))
            ok, errors, made = build(tree, jobs=jobs, timeout_s=timeout_s)
        except BuildError as exc:
            ok, errors, made = False, str(exc), tree
        if ok:
            made.replace(binary)
            shutil.rmtree(tree, ignore_errors=True)
            return BuildResult(True, binary, name, "", time.monotonic() - started)
        if not errors.startswith("build exceeded"):
            failed.write_text(errors)
        return BuildResult(False, None, name, errors, time.monotonic() - started)

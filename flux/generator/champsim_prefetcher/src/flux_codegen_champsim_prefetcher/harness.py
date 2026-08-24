"""Compile a generated L2 prefetcher into a real ChampSim binary, and say precisely why not.

THE ECONOMICS ARE UNUSUAL AND THEY SHAPE EVERYTHING HERE. A full ChampSim rebuild takes about
seven seconds; one evaluation of the result is three simulations of about six minutes. So the
compiler is not the expensive step — it is essentially free feedback, and a generate-build-repair
loop can afford many attempts per candidate that ever reaches measurement.

WHAT IS GENERATED AND WHAT IS NOT. A model writes ONE header containing the complete prefetcher
class. Everything else is mechanical: the `.cc` that gives the vtable a home, the `else if` branch
in `multi.l2c_pref` that makes the name selectable at run time, and the knob declarations in
`knobs.cc`. That split is D48's rule for RTL applied unchanged — generated leaves, deterministically
wired — and it matters more here than there, because a model editing a 300-line dispatch it did not
write is a model with many ways to break every OTHER prefetcher in the build.

EVERY BUILD HAPPENS IN A SCRATCH COPY. Never the project tree. Candidates are built in parallel and
a failed one leaves a half-patched dispatch behind; a shared tree would carry that into the next
candidate and report a compile error nobody caused.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tarfile
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

#: What `build_champsim.sh no multi no 1` produces: the `multi` L2 slot is what makes a
#: prefetcher selectable by name at run time, which is the whole reason a generated one can be
#: measured without a second binary.
BUILD_ARGS = ("no", "multi", "no", "1")
BINARY_NAME = "perceptron-no-multi-no-ship-1core"

#: A generated name must be a plain C identifier: it becomes a class name, a file name, a knob
#: prefix and a run-time selector string, and anything else breaks at least one of those.
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,23}$")


class InvalidPrefetcherName(ValueError):
    """The name cannot be used as a class, a file, a knob prefix and a selector at once."""


@dataclass
class BuildResult:
    """What happened when a generated prefetcher met a real compiler."""

    ok: bool
    binary: Path | None
    errors: str                       # the compiler's own words, for the repair prompt
    elapsed_s: float
    tree: Path | None = None
    knobs: dict[str, int] = field(default_factory=dict)

    @property
    def first_error(self) -> str:
        """The first real diagnostic, which is usually the only one that matters.

        g++ reports a cascade after the first structural mistake; feeding a model four hundred
        lines of consequences buries the cause it needs to fix.
        """
        for line in self.errors.splitlines():
            if ": error:" in line or ": fatal error:" in line:
                return line.strip()
        return self.errors.strip().splitlines()[0] if self.errors.strip() else ""


def check_name(name: str) -> None:
    if not NAME_RE.match(name):
        raise InvalidPrefetcherName(
            f"{name!r} must be lowercase, start with a letter, and be 3-24 characters of "
            "[a-z0-9_] — it becomes a class name, a filename, a knob prefix and a selector")


def class_name_for(name: str) -> str:
    """`my_pref` -> `MyPrefPrefetcher`. Deterministic, so the dispatch patch can predict it."""
    return "".join(part.capitalize() for part in name.split("_")) + "Prefetcher"


#: Standard-library symbols and the header each one needs. Not exhaustive — just the ones a
#: prefetcher actually reaches for.
_NEEDS: dict[str, tuple[str, ...]] = {
    "algorithm": ("std::find", "std::sort", "std::max_element", "std::min_element",
                  "std::fill", "std::count", "std::lower_bound", "std::upper_bound",
                  "std::swap", "std::remove"),
    "unordered_map": ("std::unordered_map",),
    "unordered_set": ("std::unordered_set",),
    "map": ("std::map",),
    "set": ("std::set",),
    "deque": ("std::deque",),
    "list": ("std::list",),
    "array": ("std::array",),
    "vector": ("std::vector",),
    # `std::string` is deliberately absent: every constructor takes one and `prefetcher.h`
    # already includes <string>, so adding it would fire on every single header.
    "cstdint": ("uint64_t", "uint32_t", "int64_t", "int32_t", "uint8_t", "uint16_t"),
    "cstring": ("std::memset", "std::memcpy"),
    "cmath": ("std::abs", "std::log2", "std::sqrt", "std::floor", "std::ceil"),
    "iostream": ("std::cout", "std::endl", "std::cerr"),
    "utility": ("std::pair", "std::make_pair"),
    "limits": ("std::numeric_limits",),
}


def ensure_includes(header: str) -> str:
    """Add any standard header the code uses but did not include.

    MECHANICAL, for the same reason registration is: a missing `#include <algorithm>` for
    `std::find` is the single most common way generated C++ fails to compile, it says nothing
    about whether the DESIGN is any good, and a model handed its own diagnostic reproduced the
    identical mistake on the repair attempt. Spending a round of the loop on it buys nothing.

    Inserted after the include guard so the guard still opens the file, and only when the symbol
    genuinely appears — adding headers nobody uses is noise in a file a human may read.
    """
    missing = [name for name, symbols in _NEEDS.items()
               if f"#include <{name}>" not in header
               and any(symbol in header for symbol in symbols)]
    if not missing:
        return header

    lines = header.splitlines(keepends=True)
    insert_at = 0
    for index, line in enumerate(lines):
        if line.lstrip().startswith("#define") and index and "ifndef" in lines[index - 1]:
            insert_at = index + 1
            break
        if line.lstrip().startswith("#include"):
            insert_at = index
            break
    added = "".join(f"#include <{name}>\n" for name in sorted(missing))
    return "".join(lines[:insert_at]) + added + "".join(lines[insert_at:])


def stage_tree(source_root: Path, into: Path, *, warm: bool = True) -> Path:
    """A private copy of the ChampSim tree, ready to build.

    `warm=True` COPIES `obj/` so the build is incremental, and that is the difference between a
    seven-second candidate and a sixty-two-second one — measured, both. Only the generated header,
    its stub `.cc`, `knobs.cc` and `multi.l2c_pref` change, so `make` recompiles four files and
    relinks; a cold tree recompiles all sixty. At nine times per candidate this is the single
    biggest lever on how many designs a loop can afford to try.
    A warm tree is safe here because every build uses the same `BUILD_ARGS`, so no object was
    compiled under a different configuration. `warm=False` for a first build or after changing them.
    """
    if not (source_root / "build_champsim.sh").is_file():
        raise FileNotFoundError(
            f"{source_root} is not a ChampSim tree (no build_champsim.sh). The project tree is "
            "gitignored and may simply be absent — point at a checkout, or install the nix "
            "package once it exists.")
    into.mkdir(parents=True, exist_ok=True)
    skip = {".git", "bin", "results", "experiments"} | (set() if warm else {"obj"})
    with tempfile.TemporaryFile() as spool:
        with tarfile.open(fileobj=spool, mode="w") as tar:
            for entry in source_root.iterdir():
                if entry.name not in skip:
                    tar.add(entry, arcname=entry.name)
        spool.seek(0)
        with tarfile.open(fileobj=spool, mode="r") as tar:
            tar.extractall(into, filter="data")
    (into / "obj").mkdir(exist_ok=True)
    (into / "bin").mkdir(exist_ok=True)
    # libbf is a prebuilt static archive the Makefile links; without it every build fails at link
    # time with an error that says nothing about the generated prefetcher.
    lib = source_root / "libbf" / "build" / "lib" / "libbf.a"
    if lib.is_file():
        target = into / "libbf" / "build" / "lib"
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(lib, target / "libbf.a")
    return into


def install(name: str, header_source: str, knobs: dict[str, int], tree: Path) -> None:
    """Write the generated header and wire it in. Everything but the header is mechanical.

    `knobs` are the prefetcher's own parameters with their defaults. They are declared in
    `knobs.cc` and parsed from the `.ini`, so the search can tune a generated prefetcher exactly
    as it tunes `sms` — a generated design that could not be tuned would be measured once, at
    whatever constants the model happened to pick.
    """
    check_name(name)
    cls = class_name_for(name)

    (tree / "inc" / f"{name}.h").write_text(ensure_includes(header_source))
    # The vtable needs a translation unit. The Makefile globs prefetcher/*.cc, so this is also
    # what makes the file part of the build at all.
    (tree / "prefetcher" / f"{name}.cc").write_text(
        f'#include "{name}.h"\n\n/* Generated: the class is defined inline in the header; this\n'
        f'   translation unit gives its vtable a home and puts it in the Makefile\'s glob. */\n')

    _declare_knobs(tree / "src" / "knobs.cc", knobs)
    _register(tree / "prefetcher" / "multi.l2c_pref", name, cls)


def _declare_knobs(knobs_cc: Path, knobs: dict[str, int]) -> None:
    """Add `uint32_t NAME = DEFAULT;` and an ini-parse branch for each knob.

    Anchored on an existing knob rather than on a line number: `knobs.cc` is 900 lines and its
    layout is upstream's business, but `stride_num_trackers` has to exist for `stride` to work.
    """
    text = knobs_cc.read_text()
    anchor_decl = "\tuint32_t stride_num_trackers"
    if anchor_decl not in text:
        raise RuntimeError("knobs.cc has no stride_num_trackers declaration to anchor on")
    decls = "".join(f"\tuint32_t {k} = {v};\n" for k, v in knobs.items() if k not in text)
    text = text.replace(anchor_decl, decls + anchor_decl, 1)

    match = re.search(
        r'\n(\s*)else if\s*\(MATCH\("",\s*"stride_num_trackers"\)\)\s*\n\s*\{[^}]*\}', text)
    if match is None:
        raise RuntimeError("knobs.cc has no stride_num_trackers parse branch to anchor on")
    branches = "".join(
        f'\n    else if (MATCH("", "{k}"))\n    {{\n\t\tknob::{k} = atoi(value);\n    }}'
        for k in knobs if f'"{k}"' not in text)
    text = text.replace(match.group(0), match.group(0) + branches, 1)
    knobs_cc.write_text(text)


def _register(multi: Path, name: str, cls: str) -> None:
    """Make the name selectable via `--l2c_prefetcher_types=NAME`."""
    text = multi.read_text()
    if f'#include "{name}.h"' not in text:
        text = text.replace('#include "bingo.h"', f'#include "bingo.h"\n#include "{name}.h"', 1)
    anchor = '\t\telse if(!knob::l2c_prefetcher_types[index].compare("stride"))'
    if anchor not in text:
        raise RuntimeError("multi.l2c_pref has no stride branch to anchor on")
    branch = (f'\t\telse if(!knob::l2c_prefetcher_types[index].compare("{name}"))\n'
              f'\t\t{{\n'
              f'\t\t\tcout << "adding L2C_PREFETCHER: {cls}" << endl;\n'
              f'\t\t\t{cls} *pref_{name} = new {cls}(knob::l2c_prefetcher_types[index]);\n'
              f'\t\t\tprefetchers.push_back(pref_{name});\n'
              f'\t\t}}\n')
    multi.write_text(text.replace(anchor, branch + anchor, 1))


def build(tree: Path, *, timeout_s: int = 600) -> BuildResult:
    """Run the real build. Seven seconds when it works, and precise when it does not."""
    started = time.monotonic()
    try:
        proc = subprocess.run(
            ["./build_champsim.sh", *BUILD_ARGS], cwd=tree, capture_output=True, text=True,
            timeout=timeout_s, env={"PATH": __import__("os").environ.get("PATH", ""),
                                    "PYTHIA_HOME": str(tree),
                                    "HOME": __import__("os").environ.get("HOME", "/tmp")})
    except subprocess.TimeoutExpired:
        return BuildResult(False, None, f"build exceeded {timeout_s}s", time.monotonic() - started)
    elapsed = time.monotonic() - started
    binary = tree / "bin" / BINARY_NAME
    if proc.returncode == 0 and binary.is_file():
        return BuildResult(True, binary, "", elapsed, tree=tree)
    return BuildResult(False, None, (proc.stdout + proc.stderr)[-4000:], elapsed, tree=tree)

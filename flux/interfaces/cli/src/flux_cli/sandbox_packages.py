"""Extra sandbox tools, selected by attributes from Flux's locked nixpkgs and nixchip inputs."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterator


PACKAGE_ATTRIBUTE = r"^[A-Za-z0-9_+-]+(?:\.[A-Za-z0-9_+-]+)*$"
PACKAGE_ENV = "FLUX_SANDBOX_NIX_PACKAGES"


def package_spec(raw: str) -> dict[str, list[str]]:
    try:
        spec = json.loads(raw)
    except ValueError as exc:
        raise ValueError("sandbox Nix packages must be a JSON object with nixpkgs and nixchip lists") from exc
    if not isinstance(spec, dict) or set(spec) - {"nixpkgs", "nixchip"}:
        raise ValueError("sandbox Nix packages use only nixpkgs and nixchip lists")
    result = {}
    for source in ("nixpkgs", "nixchip"):
        names = spec.get(source, [])
        if not isinstance(names, list) or len(names) > 64:
            raise ValueError(f"{source}: at most 64 package attributes")
        if any(not isinstance(n, str) or len(n) > 120 or not re.fullmatch(PACKAGE_ATTRIBUTE, n) for n in names):
            raise ValueError(f"{source}: use package attributes such as jq or python3Packages.numpy")
        result[source] = list(dict.fromkeys(names))
    return result


def package_root(spec: dict[str, list[str]], cache: Path) -> Path:
    """Build once per package list and lock revision; the out-link keeps its closure alive."""
    root = Path(os.environ.get("FLUX_SANDBOX_NIX_FLAKE") or os.environ.get("FLUX_ROOT")
                or Path(__file__).resolve().parents[4]).resolve()
    if not (root / "flake.nix").is_file() or not (root / "flake.lock").is_file():
        raise RuntimeError("sandbox Nix packages need Flux's flake.nix and flake.lock; set FLUX_ROOT to the Flux checkout")
    encoded = json.dumps(spec, sort_keys=True)
    key = hashlib.sha256(str(root).encode() + (root / "flake.nix").read_bytes()
                         + (root / "flake.lock").read_bytes() + Path(__file__).with_suffix(".nix").read_bytes()
                         + encoded.encode() + platform.machine().encode()).hexdigest()
    profile = cache / "nix-packages" / key
    if profile.is_dir() and (profile / "flux-package-env.json").is_file():
        return profile.resolve()
    nix = shutil.which("nix")
    if not nix:
        raise RuntimeError("sandbox Nix packages need nix installed on the server")
    profile.parent.mkdir(parents=True, exist_ok=True)
    said = ", ".join(f"{source}:{name}" for source, names in spec.items() for name in names)
    print(f"flux sandbox: preparing Nix packages: {said}", file=sys.stderr, flush=True)
    subprocess.run([nix, "--extra-experimental-features", "nix-command flakes", "build", "--impure",
                    "--out-link", str(profile), "--file", str(Path(__file__).with_suffix(".nix")),
                    "--argstr", "flakePath", str(root), "--argstr", "packageSpec", encoded],
                   check=True, stdout=subprocess.DEVNULL)
    return profile.resolve()


@contextlib.contextmanager
def package_environment(cache: Path) -> Iterator[None]:
    """Only this launch gets the extra executables, headers and library search paths."""
    spec = package_spec(os.environ.get(PACKAGE_ENV) or "{}")
    if not any(spec.values()):
        yield
        return
    root = package_root(spec, cache)
    env = json.loads((root / "flux-package-env.json").read_text())
    paths = {"PATH": [root / "bin"], "CPATH": [root / "include"], "LIBRARY_PATH": [root / "lib"],
             "PKG_CONFIG_PATH": [root / "lib/pkgconfig", root / "share/pkgconfig"],
             "PYTHONPATH": [root / f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"]}
    for name, dirs in paths.items():
        found = [str(p) for p in dirs if p.is_dir()]
        if found:
            env[name] = os.pathsep.join([*found, *([os.environ[name]] if os.environ.get(name) else [])])
    # Nix binaries carry their loader and runtime dependencies. Do not add lib/ to
    # LD_LIBRARY_PATH: it would mix Nix libraries with the host's glibc for host binaries.
    before = {name: os.environ.get(name) for name in env}
    for name, value in env.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    try:
        yield
    finally:
        for name, value in before.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

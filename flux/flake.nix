{
  description = ''
    Flux dev environment. No venv, no pip install step: `nix develop` alone gives a working
    environment. Two shells:
      - `python` (fast): Python/Rust/Docker base — enough for ir/, evaluators/abi/,
        evaluators/zigzag/, evaluators/timeloop/ (docs/05.md Phase 1).
      - `default` (full): adds Verilator + Yosys + GTKWave, cherry-picked as standalone nixchip
        packages — needed by evaluators/rtl's real Verilator-simulation integration tests
        (docs/05.md Phase 2's first deliverable, an RTL-sim adapter).

    Design: third-party Python deps (jsonschema, pyyaml, onnx, pytest, ... and zigzag-dse, which
    nixpkgs doesn't package — built here from PyPI as a real derivation, see `zigzagDse` below)
    are real nix derivations via `python312.withPackages`, not pip-installed. The 15 local
    `flux-*` packages under ir/, evaluators/, stores/, flows/cli/, frontends/onnx/, calibration/,
    knowledge/, search/exhaustive/, search/annealing/, search/architecture/, flows/chia_nodes/
    are deliberately NOT
    built as nix derivations and pip-installed instead — they're this repo's actively-edited
    code; packaging them immutably would mean a full flake rebuild after
    every source edit before a test could see the change. Instead the shellHook puts each
    package's src/ directly on PYTHONPATH — equivalent to an editable install, but it's nix
    (an env var nix's shellHook sets), not a venv/pip install step.

    Not provided here, deliberately: Timeloop+Accelergy — run via the Docker image documented in
    evaluators/timeloop/README.md; nixchip doesn't package it and it isn't a Python dependency.

    `evaluators/systemc` needs SystemC (`.#default` now provides `pkgs.systemc` hermetically —
    originally relied on the system's `libsystemc-dev`, verified working outside this flake before
    a real nixpkgs derivation was wired in) and `evaluators/booksim` needs `flex`/`bison` to build
    Booksim2's config lexer/parser (also now in `.#default`; every other Booksim2 source file
    builds with plain g++, verified by actually building it, not assumed). Booksim2 itself isn't
    vendored or nix-derivation-ized — the adapter clones and builds it on first use, same
    "fetch an external resource once, cache it" pattern `evaluators/timeloop` already uses for its
    Docker image.

    Not provided here, undone (not deliberate): `flows/chia_nodes` needs real CHIA
    (`git+https://github.com/ucb-bar/chia.git`, not on PyPI) plus `ray[default]` and CHIA's other
    real dependencies (`mcp`, `pydantic==2.12.4`, `fastapi`, `google-genai`, `boto3`,
    `google-cloud-compute`) — none of that is nix-derivation-ized either; it currently needs a
    `pip install -e flows/chia_nodes` on top of a working `nix develop .#python` shell (or a plain
    venv, as verified during development). Fold it
    into a real devShell once they're load-bearing enough to be worth the derivation work.

    `default` deliberately does NOT use nixchip's own `simulation`/`asic`/`hardware` devShells:
    both bundle in `cryptominisat` (transitively, via formal-verification tools like
    sby/eqy bundled alongside Verilator in `simulation-tools`) whose CMake build tries to `git
    clone` the `cadical` solver via FetchContent at build time — Nix's sandboxed builder
    disallows network access mid-build, so that derivation is broken regardless of what pulls it
    in. `asic-tools` separately hits `or-tools` being flagged `broken` in this nixpkgs revision
    (a config-overridable evaluation block, unlike cryptominisat's genuine build failure).
    Verified empirically, not assumed: `nix build github:helcel-net/nixchip#verilator` and
    `#yosys` both succeed standalone — the breakage is specific to tools we don't need yet
    (formal verification, PnR), not to Verilator/Yosys themselves — so `default` cherry-picks
    the individual packages instead of the bundles. Re-check the bundled shells when EDA-tool
    needs grow past what's cherry-picked here; may already be fixed upstream by then.
  '';

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    nixchip.url = "github:helcel-net/nixchip";
    nixchip.inputs.nixpkgs.follows = "nixpkgs";
  };

  outputs = { self, nixpkgs, nixchip }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
    in
    {
      devShells = forAllSystems (system:
        let
          pkgs = import nixpkgs { inherit system; };
          # Cherry-picked, individually-verified-buildable nixchip packages — see the
          # description above for why not nixchip.devShells.${system}.
          chipPkgs = nixchip.packages.${system};
          py = pkgs.python312Packages;

          # Not in nixpkgs: a zigzag-dse runtime dep (PyPI metadata says `install_requires =
          # ["dill"]`; nothing else).
          multiprocessingOnDill = py.buildPythonPackage {
            pname = "multiprocessing_on_dill";
            version = "3.5.0a4";
            format = "setuptools";
            src = pkgs.fetchurl {
              url = "https://files.pythonhosted.org/packages/86/4d/4b135e2e5cd0194eb29f2ed36e9a77a07596787a9a8ac2279bd4445398f2/multiprocessing_on_dill-3.5.0a4.tar.gz";
              sha256 = "d6d50c300ff4bd408bb71eb78725e60231039ee9b3d0d9bb7697b9d0e15045e7";
            };
            propagatedBuildInputs = [ py.dill ];
            doCheck = false;
          };

          # Not in nixpkgs (niche academic tool, KU Leuven MICAS). Built from the real PyPI
          # wheel, pinned to the exact version this repo's adapters and tests are verified
          # against (flux/evaluators/zigzag/pyproject.toml). Runtime deps per its own wheel
          # METADATA (`Requires-Dist`), all present in nixpkgs except multiprocessing_on_dill.
          zigzagDse = py.buildPythonPackage {
            pname = "zigzag-dse";
            version = "3.8.5";
            format = "wheel";
            src = pkgs.fetchurl {
              url = "https://files.pythonhosted.org/packages/71/13/2c0799ca0c2ae83a49cf770ebe95ab4eb93db8de148b7977eb3c8753a38d/zigzag_dse-3.8.5-py3-none-any.whl";
              sha256 = "7e06c0b75a720e7a252cb8b0821503ad425d1fe558c789165d2059afcf7b27e0";
            };
            propagatedBuildInputs = [
              py.numpy
              py.networkx
              py.sympy
              py.matplotlib
              py.onnx
              py.tqdm
              multiprocessingOnDill
              py.pyyaml
              py.cerberus
              py.seaborn
              py.typeguard
            ];
            doCheck = false;
          };

          # Third-party deps for the local flux-* packages (see the description above for why
          # the local packages themselves aren't built as derivations).
          pythonEnv = pkgs.python312.withPackages (ps: [
            ps.pytest
            ps.jsonschema
            ps.pyyaml
            ps.onnx
            zigzagDse
          ]);

          # manylinux wheels built into pythonEnv (numpy, onnx, ...) dlopen libstdc++/zlib from
          # the standard dynamic linker path at import time; nixpkgs' Python doesn't put them
          # there like a distro Python would, so they fail with "libstdc++.so.6: cannot open
          # shared object file" unless we hand them an explicit LD_LIBRARY_PATH.
          nativeLibPath = pkgs.lib.makeLibraryPath [ pkgs.stdenv.cc.cc.lib pkgs.zlib ];

          # The 15 local flux-* packages, src/-only (each is a PEP 420 namespace-free package
          # rooted at src/<module>/) — equivalent to `pip install -e` for all of them at once,
          # with no install step at all.
          localSrcDirs = [
            "ir/src"
            "evaluators/abi/src"
            "evaluators/zigzag/src"
            "evaluators/timeloop/src"
            "stores/src"
            "flows/cli/src"
            "frontends/onnx/src"
            "calibration/src"
            "evaluators/rtl/src"
            "evaluators/systemc/src"
            "knowledge/src"
            "search/exhaustive/src"
            "search/annealing/src"
            "search/architecture/src"
            "flows/chia_nodes/src"
          ];

          shellHook = ''
            export PYTHONPATH="${pkgs.lib.concatStringsSep ":" (map (d: "$PWD/${d}") localSrcDirs)}:$PYTHONPATH"
            mkdir -p .nix-bin
            printf '#!/usr/bin/env bash\nexec python3 -c "from flux_cli.main import main; main()" "$@"\n' > .nix-bin/flux
            chmod +x .nix-bin/flux
            export PATH="$PWD/.nix-bin:$PATH"
            echo "flux dev shell: python $(python3 --version), no venv/pip install needed"
            echo "  python -m pytest -q     # run tests directly"
            echo "  flux --help              # the flux-cli console script (wrapper, see flake.nix)"
          '';
        in
        {
          python = pkgs.mkShell {
            name = "flux-dev-python";
            packages = [ pythonEnv pkgs.rustc pkgs.cargo pkgs.docker-client ];
            LD_LIBRARY_PATH = nativeLibPath;
            inherit shellHook;
          };

          default = pkgs.mkShell {
            name = "flux-dev-full";
            packages = [
              pythonEnv pkgs.rustc pkgs.cargo pkgs.docker-client
              chipPkgs.verilator chipPkgs.yosys chipPkgs.gtkwave
              # evaluators/systemc: real nixpkgs systemc, closing the gap this file used to
              # document (previously relied on system libsystemc-dev, verified working but not
              # hermetic). evaluators/booksim: flex+bison, needed only to build Booksim2's own
              # config.l/config.y lexer/parser — verified here (not assumed) to be the one real
              # missing piece; every other Booksim2 source file builds with plain g++.
              pkgs.systemc pkgs.flex pkgs.bison
            ];
            LD_LIBRARY_PATH = nativeLibPath;
            shellHook = ''
              echo "flux dev shell (full: python + rust + Verilator/Yosys/GTKWave/SystemC/flex+bison)"
              echo "For Phase 1 work only, prefer: nix develop .#python (faster, smaller closure)"
            '' + shellHook;
          };
        });
    };
}

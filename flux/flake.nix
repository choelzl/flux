{
  description = ''
    Flux dev environment. No venv, no pip install step: `nix develop` alone works. Shells:
      - `default`: Python, the EDA tools (OpenROAD on linux) and prebuilt simulators the adapters need.
      - `timeloop` (linux): hermetic Timeloop v4 + Accelergy.

    Almost everything third-party comes prebuilt from nixchip — the DSE Python stack
    (zigzag-dse), Timeloop/Accelergy, Pythia/ChampSim and the EDA tools (Verilator,
    Yosys, OpenROAD). `nixpkgs`
    follows nixchip's pin, so binaries substitute from the nixchip0-3 Cachix caches and
    cache.nixos.org; run nix with `--accept-flake-config`.

    The local `flux-*` packages are deliberately NOT derivations: they are actively edited,
    and packaging them immutably would force a flake rebuild before every test run. The
    shellHook puts each `src/` on PYTHONPATH instead — editable-install equivalent, without
    pip. `localSrcDirs` is the authoritative list;
    `tests/unit/test_flake_local_packages.py` checks it against the filesystem.

    The Timeloop adapter defaults to Docker regardless of shell — `FLUX_TIMELOOP_LOCAL=1`
    opts into the hermetic path, which reproduces the pinned Docker energy numbers (D206).

    `default` cherry-picks Verilator/Yosys rather than using nixchip's `simulation`/`asic`
    bundles: both pull in `cryptominisat`, whose build git-clones `cadical` at build time
    and so cannot work in nix's sandbox.
  '';

  # nixchip declares these in ITS flake, but a flake's `nixConfig` applies only when it is the
  # TOP-LEVEL flake — never when it is an input. Consuming nixchip without repeating them here
  # means its cache is silently never consulted, and `openroad-unstable` is compiled from source
  # over several hours. That is the cost the `nixpkgs.follows` below exists to avoid, and without
  # this block it is paid anyway.
  #
  # Requires `accept-flake-config = true` in nix.conf, or `--accept-flake-config` on the command
  # line; nix ignores a flake's substituters otherwise, and does so quietly.
  nixConfig = {
    extra-substituters = [ "https://nixchip0.cachix.org" ];
    extra-trusted-public-keys = [
      "nixchip0.cachix.org-1:nT5gEHc4661JFHoDukEnF1NFQ0XvS0TE7P370HLm4Ng="
    ];
  };

  inputs = {
    # nixchip is PINNED and both halves of the pin matter.
    #
    # `zigzag-dse` comes from nixchip rather than being built here from PyPI, but nixchip only
    # began exporting it after 179b4402 — the rev this repo used to pin, where the shell fails
    # with "attribute 'zigzag-dse' missing".
    #
    # nixpkgs follows nixchip rather than nixos-unstable, and that is not interchangeable:
    # nixchip's own pin is the interpreter its packages are actually built against.
    #
    # Cost, measured rather than assumed: `openroad-unstable` is a nixchip derivation and is in no
    # binary cache — nixchip0.cachix.org and cache.nixos.org both 404 on its output path — so
    # moving either pin means compiling OpenROAD from source. See `nixConfig` above: nixchip's
    # cachix does cover other packages, and is only consulted because it is repeated there.
    nixchip.url = "github:helcel-net/nixchip/f4fddde1616926a11cec12d325cf321536b4fc60";
    nixpkgs.follows = "nixchip/nixpkgs";
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
          chipPkgs = nixchip.packages.${system};

          # Shared by pythonEnv and the timeloop shell's env. `import onnx` comes via
          # zigzag-dse's propagated PyPI-wheel onnx; do NOT add ps.onnx alongside — the
          # nixpkgs build's libprotobuf clashes with ortools' vendored one and SIGSEGVs (D80).
          basePythonPackages = ps: [
            ps.pytest
            ps.pytest-xdist       # the unit core on every core (D531)
            ps.jsonschema
            ps.pyyaml
            # The bank-mapping study (applications/bankmap): z3 searches XOR-fold matrices
            # under conflict-freeness constraints; numpy is the exhaustive checker.
            ps.z3-solver
            ps.numpy
            chipPkgs.zigzag-dse
            # `flux serve`, the web interface (D683): the API, its server, form uploads,
            # and httpx for FastAPI's test client
            ps.fastapi
            ps.uvicorn
            ps.python-multipart
            ps.httpx
            ps.cryptography       # users' model keys, encrypted at rest (Fernet)
          ];
          pythonEnv = pkgs.python3.withPackages basePythonPackages;

          # manylinux wheels (numpy, onnx, ...) dlopen libstdc++/zlib at import time;
          # nixpkgs' Python doesn't put them on the default linker path.
          nativeLibPath = pkgs.lib.makeLibraryPath [ pkgs.stdenv.cc.cc.lib pkgs.zlib ];

          # The local flux-* packages, src/-only — `pip install -e` equivalent for all of
          # them at once, adapters included: PYTHONPATH costs nothing until imported (D123).
          localSrcDirs = [
            "core/ir/src"
            "evaluator/abi/src"
            "evaluator/zigzag/src"
            "evaluator/timeloop/src"
            "core/stores/src"
            "interfaces/cli/src"
            "interfaces/web/src"
            "evaluator/calibration/src"
            "evaluator/rtl/src"
            "mentor/knowledge/src"
            "mentor/records/src"
            "core/loop/src"
            "mentor/feedback/src"
            "core/llm/src"
            "core/profile/src"
            "core/tui/src"
            "core/frontier/src"
            "evaluator/cache/src"
            "evaluator/champsim/src"
            "applications/bankmap/lib/src"
            "applications/macarray/lib/src"
            "applications/interconnect_mapping/lib/src"
            "evaluator/openroad/src"
            "generator/harness_spec/src"
            "generator/harness_rtl/src"
            "evaluator/redaction/src"
          ];

          shellHook = ''
            # the flake's own directory, wherever the shell is entered from (a subfolder once
            # got a PYTHONPATH of paths that do not exist and a stray .nix-bin of its own)
            # (D598) walking up from where the shell is entered finds the checkout when you are in
            # it; from anywhere else (`nix develop /path/to/flux` in your own project) it is the
            # flake's own source -- `FLUX_ROOT=/path/to/flux` points it at a checkout to edit
            if [ -z "''${FLUX_ROOT:-}" ] || [ ! -d "$FLUX_ROOT/core/loop/src" ]; then
              FLUX_ROOT="$PWD"
              while [ "$FLUX_ROOT" != / ] && ! { [ -f "$FLUX_ROOT/flake.nix" ] && [ -d "$FLUX_ROOT/core/loop/src" ]; }; do
                FLUX_ROOT="$(dirname "$FLUX_ROOT")"
              done
              [ -d "$FLUX_ROOT/core/loop/src" ] || FLUX_ROOT="${./.}"
            fi
            export FLUX_ROOT
            export PYTHONPATH="${pkgs.lib.concatStringsSep ":" (map (d: "$FLUX_ROOT/${d}") localSrcDirs)}:$PYTHONPATH"
            # scratch: yours when you set FLUX_TMPDIR (a big local disk), else the user cache
            export FLUX_TMPDIR="''${FLUX_TMPDIR:-''${XDG_CACHE_HOME:-$HOME/.cache}/flux/tmp}"
            if [ -n "$FLUX_TMPDIR" ]; then
              mkdir -p "$FLUX_TMPDIR" && export TMPDIR="$FLUX_TMPDIR" \
                && export TMP="$FLUX_TMPDIR" && export TEMP="$FLUX_TMPDIR" \
                && echo "flux dev shell: scratch in $TMPDIR ($(df -h "$TMPDIR" | tail -1 | awk '{print $4}') free)"
            fi
            FLUX_BIN="$FLUX_ROOT/.nix-bin"                  # the store copy is read-only: the user cache then
            mkdir -p "$FLUX_BIN" 2>/dev/null && [ -w "$FLUX_BIN" ] || FLUX_BIN="''${XDG_CACHE_HOME:-$HOME/.cache}/flux/bin"
            mkdir -p "$FLUX_BIN"
            printf '#!/usr/bin/env bash\nexec python3 -c "import sys; from flux_cli.main import main; sys.exit(main())" "$@"\n' > "$FLUX_BIN/flux"
            chmod +x "$FLUX_BIN/flux"
            export PATH="$FLUX_BIN:$PATH"
            echo "flux dev shell: flux from $FLUX_ROOT, python $(python3 --version), no venv/pip install needed"
            echo "  python -m pytest -q     # run tests directly"
            echo "  flux --help              # the flux-cli console script (wrapper, see flake.nix)"
          '';
        in
        # Timeloop is linux-only; its shell exists only on linux.
        pkgs.lib.optionalAttrs pkgs.stdenv.isLinux {
          timeloop = pkgs.mkShell {
            name = "flux-dev-timeloop";
            packages = [
              (pkgs.python3.withPackages (ps: basePythonPackages ps ++ [
                chipPkgs.timeloopfe chipPkgs.accelergy
                chipPkgs.accelergy-library-plug-in chipPkgs.accelergy-cacti-plug-in
              ]))
              chipPkgs.timeloop pkgs.docker-client
            ];
            LD_LIBRARY_PATH = nativeLibPath;
            shellHook = ''
              echo "flux dev shell (timeloop: hermetic Timeloop v4 + Accelergy, no Docker)"
              echo "  FLUX_TIMELOOP_LOCAL=1   # opt in; the adapter defaults to Docker regardless"
            '' + shellHook;
          };
        }
        // (let
          default = pkgs.mkShell {
            name = "flux-dev-full";
            packages = [
              pythonEnv pkgs.docker-client
              pkgs.ruff        # the lint CI runs: `ruff check` (pyflakes rules; honours noqa)
              chipPkgs.verilator chipPkgs.sv-lang chipPkgs.yosys
              chipPkgs.iverilog  # Icarus Verilog: event-driven simulation beside Verilator
              # CMU-SAFARI/Pythia: ChampSim, with its source tree under
              # $out/share/pythia so `flux champsim build` can rebuild it.
              chipPkgs.pythia
              pkgs.systemc     # a SystemC prototype's testbench links it (D635)
              pkgs.hyperfine   # `flux prog time` (D661)
              pkgs.tini        # PID 1 of the run's sandbox: reaps the tools' processes, forwards signals (D680)
            ]
            # Physical design (OpenROAD, yosys-slang), linux-only.
            ++ pkgs.lib.optionals pkgs.stdenv.isLinux [
              chipPkgs.openroad chipPkgs.yosys-slang
              pkgs.valgrind    # `flux prog count`: cachegrind (D661)
            ];
            LD_LIBRARY_PATH = nativeLibPath;
            SYSTEMC_HOME = "${pkgs.systemc}";
            # Yosys only finds plugins under its own share/yosys/plugins. Exported rather
            # than hard-coded so the flow falls back to the built-in reader when absent.
            YOSYS_SLANG_PLUGIN = pkgs.lib.optionalString pkgs.stdenv.isLinux
              "${chipPkgs.yosys-slang}/share/yosys/plugins/slang.so";
            shellHook = ''
              echo "flux dev shell: python + Verilator/Yosys/OpenROAD, Pythia/ChampSim, SystemC"
              echo "  .#systemc adds ICSC (SystemC -> SV); .#timeloop is separate"
            '' + shellHook;
          };
        in
        { inherit default; }
        # the default shell plus nixchip's ICSC (SystemC -> SystemVerilog, D645, D656)
        // pkgs.lib.optionalAttrs pkgs.stdenv.isLinux {
          systemc = default.overrideAttrs (old: {
            name = "flux-dev-systemc";
            nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [ chipPkgs.icsc ];
            ICSC_HOME = "${chipPkgs.icsc}";
          });
        }));
    };
}

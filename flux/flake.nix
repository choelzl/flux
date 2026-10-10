{
  description = ''
    Flux dev environment. No venv, no pip install step: `nix develop` alone works. One shell:
      Python, the EDA tools and prebuilt simulators the applications need, and on linux OpenROAD
      and ICSC (SystemC -> SV) -- `flux serve` runs every task, so every task's tools are in the
      one shell it is started from.

    Almost everything third-party comes prebuilt from nixchip — Pythia/ChampSim and the EDA
    tools (Verilator, Yosys, OpenROAD and its flow scripts). `nixpkgs`
    follows nixchip's pin, so binaries substitute from the nixchip0-3 Cachix caches and
    cache.nixos.org; run nix with `--accept-flake-config`.

    The local `flux-*` packages are deliberately NOT derivations: they are actively edited,
    and packaging them immutably would force a flake rebuild before every test run. The
    shellHook puts each `src/` on PYTHONPATH instead — editable-install equivalent, without
    pip. `localSrcDirs` is the authoritative list;
    `tests/unit/test_flake_local_packages.py` checks it against the filesystem.

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

          basePythonPackages = ps: [
            ps.pytest
            ps.pytest-xdist       # the unit core on every core (D531)
            ps.jsonschema
            ps.pyyaml
            # The bank-mapping study (applications/bankmap): z3 searches XOR-fold matrices
            # under conflict-freeness constraints; numpy is the exhaustive checker.
            ps.z3-solver
            ps.numpy
            # `flux serve`, the web interface (D683): the API, its server, form uploads,
            # and httpx for FastAPI's test client
            ps.fastapi
            ps.uvicorn
            ps.python-multipart
            ps.httpx
            ps.cryptography       # users' model keys, encrypted at rest (Fernet)
          ];
          pythonEnv = pkgs.python3.withPackages basePythonPackages;

          # The nixchip tools the shell carries; nixchip's hook exports <NAME>_{HOME,BIN,LIB,
          # INCLUDE} for each. Only these, not all of pkgs.nixchip: the hook's paths are the
          # shell's inputs, so the whole set would build every nixchip tool on entry, the
          # cryptominisat that cannot build in the sandbox among them (see the description).
          chipTools = {
            inherit (chipPkgs) verilator sv-lang yosys iverilog pythia;
          } // pkgs.lib.optionalAttrs pkgs.stdenv.isLinux {
            # OpenROAD-flow-scripts over this OpenROAD and Yosys (`orfs`, below); KLayout for its GDS
            # step, not the host's (D947)
            inherit (chipPkgs) openroad openroad-flow-scripts klayout yosys-slang icsc;
          };

          # manylinux wheels (numpy, onnx, ...) dlopen libstdc++/zlib at import time;
          # nixpkgs' Python doesn't put them on the default linker path.
          nativeLibPath = pkgs.lib.makeLibraryPath [ pkgs.stdenv.cc.cc.lib pkgs.zlib ];

          # The local flux-* packages, src/-only — `pip install -e` equivalent for all of
          # them at once, adapters included: PYTHONPATH costs nothing until imported (D123).
          localSrcDirs = [
            "evaluator/abi/src"
            "core/stores/src"
            "interfaces/cli/src"
            "interfaces/web/src"
            "mentor/knowledge/src"
            "mentor/records/src"
            "core/loop/src"
            "mentor/feedback/src"
            "core/llm/src"
            "core/profile/src"
            "core/tui/src"
            "core/frontier/src"
            "evaluator/cache/src"
            "applications/bankmap/lib/src"
            "applications/macarray/lib/src"
            "applications/interconnect_mapping/lib/src"
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
            # D947: `orfs <target> DESIGN_CONFIG=...` in a copy made by `openroad-flow-scripts-init <dir>`:
            # its make run serially -- a -j (this machine's profile sets MAKEFLAGS=-j32) stops it at the
            # first .sdc a step writes beside its .odb; each step uses every core itself -- and its
            # report's images drawn offscreen (QT_QPA_PLATFORM=xcb for its gui_ targets)
            printf '#!/usr/bin/env bash\nMAKEFLAGS= QT_QPA_PLATFORM="''${QT_QPA_PLATFORM:-offscreen}" exec openroad-flow-scripts-make "$@"\n' > "$FLUX_BIN/orfs"
            chmod +x "$FLUX_BIN/orfs"
            echo "flux dev shell: flux from $FLUX_ROOT, python $(python3 --version), no venv/pip install needed"
            echo "  python -m pytest -q     # run tests directly"
            echo "  flux --help              # the flux-cli console script (wrapper, see flake.nix)"
          '';
        in
        {
          default = pkgs.mkShell {
            name = "flux-dev";
            packages = [
              pythonEnv pkgs.docker-client
              # System Ninja loads Nix libstdc++ via LD_LIBRARY_PATH, mixing Nix libm
              # with the host libc. Use Ninja built against the same Nix runtime.
              pkgs.ninja
              pkgs.ruff        # the lint CI runs: `ruff check` (pyflakes rules; honours noqa)
              pkgs.systemc     # a SystemC prototype's testbench links it (D635)
              pkgs.hyperfine   # `flux prog time` (D661)
              pkgs.tini        # PID 1 of the run's sandbox: reaps the tools' processes, forwards signals (D680)
              pkgs.ripgrep     # `rg`: the coding agents search with it first (Codex) -- in the sandbox via the PATH (D848)
            ]
            # Verilator, Yosys, Icarus, sv-lang; CMU-SAFARI/Pythia: ChampSim, with its source
            # tree under $out/share/pythia so an app's `champsim.py build` can rebuild it; on linux
            # OpenROAD + yosys-slang, ICSC (SystemC -> SystemVerilog, D645, D656)
            ++ builtins.attrValues chipTools
            ++ pkgs.lib.optionals pkgs.stdenv.isLinux [
              pkgs.valgrind    # `flux prog count`: cachegrind (D661)
            ];
            LD_LIBRARY_PATH = nativeLibPath;
            SYSTEMC_HOME = "${pkgs.systemc}";
            # Yosys only finds plugins under its own share/yosys/plugins. Exported rather
            # than hard-coded so the flow falls back to the built-in reader when absent.
            YOSYS_SLANG_PLUGIN = pkgs.lib.optionalString pkgs.stdenv.isLinux
              "${chipPkgs.yosys-slang}/share/yosys/plugins/slang.so";
            shellHook = nixchip.lib.mkNixchipVarsHook chipTools + "\n" + ''
              # D944: the hook's VERILATOR_BIN is its bin/ folder, but Verilator's script reads that
              # variable as the NAME of its binary and execs it -- every compile failed "Permission
              # denied". Unset, the script finds its own verilator_bin.
              unset VERILATOR_BIN
              echo "flux dev shell: python + Verilator/Yosys/OpenROAD/ORFS, Pythia/ChampSim, SystemC/ICSC"
            '' + shellHook;
          };
        });
    };
}

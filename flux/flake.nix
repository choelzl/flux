{
  description = ''
    Flux dev environment. No venv, no pip install step: `nix develop` alone works. Shells:
      - `python` (fast): Python/Rust/Docker base.
      - `default` (full): adds the EDA tools and prebuilt simulators the adapters need.
      - `timeloop` (linux): hermetic Timeloop v4 + Accelergy.
      - `physical` (linux): the OpenROAD place-and-route rung.

    Almost everything third-party comes prebuilt from nixchip — the DSE Python stack
    (zigzag-dse, stream-dse), CHIA, Timeloop/Accelergy, the simulators (Booksim2, Noxim,
    3D-ICE, gem5, CACTI, DRAMsim3) and the EDA tools (Verilator, Yosys, OpenROAD). `nixpkgs`
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
    # `zigzag-dse` and `stream-dse` come from nixchip rather than being built here from PyPI, but
    # nixchip only began exporting them after 179b4402 — the rev this repo used to pin, where the
    # shell fails with "attribute 'zigzag-dse' missing".
    #
    # nixpkgs follows nixchip rather than nixos-unstable, and that is not interchangeable: at
    # nixos-unstable 1559d3da the default interpreter is Python 3.14 and nixchip's stream-dse has
    # no ortools wheel pinned for it ("add it to ortoolsWheels in pkgs/stream-dse"). nixchip's own
    # pin is the interpreter its packages are actually built against.
    #
    # Cost, measured rather than assumed: `openroad-unstable` is a nixchip derivation and is in no
    # binary cache — nixchip0.cachix.org and cache.nixos.org both 404 on its output path — so
    # moving either pin means compiling OpenROAD from source. See `nixConfig` above: nixchip's
    # cachix does cover other packages, and is only consulted because it is repeated there.
    nixchip.url = "github:helcel-net/nixchip/243ae7e3e598e17345e846cf4493c7477f691550";
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
            ps.jsonschema
            ps.pyyaml
            # The only linter here, and it earns its place on one check: F821, a name used and
            # never bound. Three crashes in a single session were exactly that — functions
            # deleted by a slice edit while still called, and two names read above the line that
            # assigns them. Every one crashed the demo on its first step with the whole suite
            # green, because the code lived inside `main()` and nothing runs `main()`. A static
            # check for it was hand-rolled twice and was wrong both times (D333); this is the
            # tool that does it properly, and it is a pure-Python package with no build cost.
            ps.pyflakes
            # The bank-mapping study (applications/bankmap): z3 searches XOR-fold matrices
            # under conflict-freeness constraints; numpy is the exhaustive checker.
            ps.z3-solver
            ps.numpy
            chipPkgs.zigzag-dse
            chipPkgs.stream-dse
            chipPkgs.chia # propagates ray's [default] extras
            # Direct deps of flows/chia_nodes, flows/mcp, search/agentic.
            ps.openai
            ps.uvicorn
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
            "evaluator/stream/src"
            "core/stores/src"
            "interfaces/cli/src"
            "core/frontends/onnx/src"
            "evaluator/calibration/src"
            "evaluator/validity/src"
            "evaluator/rtl/src"
            "evaluator/systemc/src"
            "mentor/knowledge/src"
            "mentor/records/src"
            "mentor/extract/src"
            "orchestrator/decide/src"
            "core/report/src"
            "mentor/knowledge/mining/src"
            "mentor/feedback/src"
            "core/llm/src"
            "core/profile/src"
            "core/tui/src"
            "orchestrator/frontier/src"
            "evaluator/cache/src"
            "evaluator/champsim_bingo/src"
            "generator/champsim_prefetcher/src"
            "applications/prefetcher/lib/src"
            "applications/bankmap/lib/src"
            "applications/macarray/lib/src"
            "applications/omni/lib/src"
            "applications/interconnect_mapping/lib/src"
            "applications/nlu/lib/src"
            "mentor/protocols/src"
            "orchestrator/exhaustive/src"
            "orchestrator/annealing/src"
            "orchestrator/agentic/src"
            "orchestrator/architecture/src"
            "orchestrator/campaign/src"
            "orchestrator/directed/src"
            "evaluator/openroad/src"
            "evaluator/interconnect_struct/src"
            "evaluator/interconnect_phys/src"
            "applications/interconnect/lib/src"
            "interfaces/chia_nodes/src"
            "interfaces/mcp/src"
            "generator/harness_systemc/src"
            "generator/harness_rtl/src"
            "core/workload_dynamism/src"
            "generator/design/src"
            "evaluator/redaction/src"
            "evaluator/thermal/src"
            "evaluator/dramsim3/src"
            "evaluator/native/src"
            "evaluator/booksim/src"
            "evaluator/noxim/src"
            "evaluator/cacti/src"
            "evaluator/gem5/src"
          ];

          shellHook = ''
            export PYTHONPATH="${pkgs.lib.concatStringsSep ":" (map (d: "$PWD/${d}") localSrcDirs)}:$PYTHONPATH"
            export FLUX_TMPDIR=/home/shared/$USER/flux-tmp
            if [ -n "$FLUX_TMPDIR" ]; then
              mkdir -p "$FLUX_TMPDIR" && export TMPDIR="$FLUX_TMPDIR" \
                && export TMP="$FLUX_TMPDIR" && export TEMP="$FLUX_TMPDIR" \
                && echo "flux dev shell: scratch in $TMPDIR ($(df -h "$TMPDIR" | tail -1 | awk '{print $4}') free)"
            fi
            mkdir -p .nix-bin
            printf '#!/usr/bin/env bash\nexec python3 -c "from flux_cli.main import main; main()" "$@"\n' > .nix-bin/flux
            chmod +x .nix-bin/flux
            export PATH="$PWD/.nix-bin:$PATH"
            # Prebuilt CACTI/DRAMsim3 (D146/D148). Adapters fall back to cloning when these
            # are unset, and a caller's own value wins.
            export CACTI_BIN="''${CACTI_BIN:-${chipPkgs.cacti}/bin}"
            export DRAMSIM3_BIN="''${DRAMSIM3_BIN:-${chipPkgs.dramsim3_}/bin}"
            echo "flux dev shell: python $(python3 --version), no venv/pip install needed"
            echo "  python -m pytest -q     # run tests directly"
            echo "  flux --help              # the flux-cli console script (wrapper, see flake.nix)"
          '';
        in
        {
          # `python` and `physical` were separate shells until their closures were measured:
          # 4.8 GB against 5.7 GB, on a 4.8 GB floor the Python environment sets by itself. A
          # 0.9 GB saving did not pay for four names people mistype -- twice in one afternoon,
          # each time producing a "no simulator found" error whose real cause was the shell.
          # They are aliases now, so every command anyone has already typed still works.
          python = self.devShells.${system}.default;
          physical = self.devShells.${system}.default;
        }
        # Timeloop and OpenROAD are linux-only; these shells exist only on linux.
        // pkgs.lib.optionalAttrs pkgs.stdenv.isLinux {
          timeloop = pkgs.mkShell {
            name = "flux-dev-timeloop";
            packages = [
              (pkgs.python3.withPackages (ps: basePythonPackages ps ++ [
                chipPkgs.timeloopfe chipPkgs.accelergy
                chipPkgs.accelergy-library-plug-in chipPkgs.accelergy-cacti-plug-in
              ]))
              chipPkgs.timeloop pkgs.rustc pkgs.cargo pkgs.docker-client
            ];
            LD_LIBRARY_PATH = nativeLibPath;
            shellHook = ''
              echo "flux dev shell (timeloop: hermetic Timeloop v4 + Accelergy, no Docker)"
              echo "  FLUX_TIMELOOP_LOCAL=1   # opt in; the adapter defaults to Docker regardless"
            '' + shellHook;
          };

          # The OpenROAD place-and-route rung (D225), with Verilator for the
          # composition-frontier tests (D246), yosys-slang as SV front end (D276), and
          # OpenRAM as the CACTI cross-check (D260).
        }
        // {
          default = pkgs.mkShell {
            name = "flux-dev-full";
            packages = [
              pythonEnv pkgs.rustc pkgs.cargo pkgs.docker-client
              chipPkgs.verilator chipPkgs.yosys chipPkgs.gtkwave
              # Prebuilt so the adapters skip clone-and-build; nixchip names DRAMsim3
              # `dramsim3_` and 3D-ICE `threed-ice`.
              chipPkgs.cacti chipPkgs.dramsim3_
              # CMU-SAFARI/Pythia: the prefetcher study's ChampSim, with its whole
              # source tree under $out/share/pythia so the generated-prefetcher
              # loop can rebuild it.
              chipPkgs.pythia
            ]
            # gem5 and 3D-ICE are linux-only; referencing them on darwin fails evaluation.
            ++ pkgs.lib.optionals pkgs.stdenv.isLinux [
              chipPkgs.booksim2 chipPkgs.noxim chipPkgs.threed-ice chipPkgs.gem5
            ]
            ++ [
              # systemc replaces a system libsystemc-dev (D31/D39); flex/bison/cmake/unzip/
              # openblasCompat serve only the adapters' clone-and-build fallback, used when
              # the *_BIN overrides are unset (D25/D32/D64).
              pkgs.systemc pkgs.flex pkgs.bison pkgs.cmake pkgs.unzip pkgs.openblasCompat
            ]
            # Physical design, merged in when `physical` stopped being its own shell.
            ++ pkgs.lib.optionals pkgs.stdenv.isLinux [
              chipPkgs.openroad chipPkgs.yosys-slang chipPkgs.openram-wrapper
            ];
            LD_LIBRARY_PATH = nativeLibPath;
            # Yosys only finds plugins under its own share/yosys/plugins. Exported rather
            # than hard-coded so the flow falls back to the built-in reader when absent.
            YOSYS_SLANG_PLUGIN = pkgs.lib.optionalString pkgs.stdenv.isLinux
              "${chipPkgs.yosys-slang}/share/yosys/plugins/slang.so";
            shellHook = pkgs.lib.optionalString pkgs.stdenv.isLinux ''
              # Same defensive pattern as CACTI_BIN: adapters skip clone-and-build, and a
              # caller's own value wins.
              export BOOKSIM_BIN="''${BOOKSIM_BIN:-${chipPkgs.booksim2}/bin}"
              export NOXIM_BIN="''${NOXIM_BIN:-${chipPkgs.noxim}/bin}"
              export NOXIM_SHARE="''${NOXIM_SHARE:-${chipPkgs.noxim}/share/noxim}"
              export THREED_ICE_BIN="''${THREED_ICE_BIN:-${chipPkgs.threed-ice}/bin}"
              export GEM5_BIN="''${GEM5_BIN:-${chipPkgs.gem5}/bin}"
            '' + ''
              echo "flux dev shell: python + rust + Verilator/Yosys/OpenROAD/GTKWave/SystemC,"
              echo "  simulators (gem5, BookSim, Noxim, DRAMsim3, CACTI, 3D-ICE, Pythia/ChampSim)"
              echo "  .#python and .#physical are aliases of this shell; .#timeloop is separate"
            '' + shellHook;
          };
        });
    };
}

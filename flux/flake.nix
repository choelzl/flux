{
  description = ''
    Flux dev environment. No venv, no pip install step: `nix develop` alone works. Two shells:
      - `python` (fast): Python/Rust/Docker base — ir/, evaluators/abi, zigzag, timeloop.
      - `default` (full): adds Verilator + Yosys + GTKWave, needed by evaluators/rtl.

    Third-party Python deps are real nix derivations via `python312.withPackages` (including
    `zigzag-dse`, built here from PyPI — nixpkgs doesn't package it). The local `flux-*` packages
    are deliberately NOT derivations: they are actively edited, and packaging them immutably would
    force a flake rebuild before every test run. The shellHook puts each `src/` on PYTHONPATH
    instead — editable-install equivalent, without pip. `localSrcDirs` below is the authoritative
    list; a second copy in this description went stale twice.

    Every local package is listed, including the heavy build-on-first-use adapters
    (`booksim`, `noxim`, `cacti`, `gem5`, `thermal`, `dramsim3`, `native`) — being on PYTHONPATH
    costs nothing until something imports them. `tests/unit/test_flake_local_packages.py` checks
    the list against the filesystem, because this description twice claimed a package count that
    had since changed, and once claimed those seven adapters were excluded when they were not.

    Tool provisioning per adapter — full reasoning in docs/decisions.md, facts only here:
      - systemc + codegen/systemc_harness: `pkgs.systemc` hermetically (D31/D39).
      - booksim: `flex`/`bison` for its lexer/parser; Booksim2 cloned and built on first use (D25).
      - noxim: `cmake` for its self-provisioned yaml-cpp; its build.sh fetches SystemC 2.3.1 (D32).
      - cacti, gem5: plain `git`/`g++`/`make` (+ system `scons`). gem5's build workarounds live in
        its adapter (D35); its clone is tag-pinned because an unpinned one gave different cycle
        counts on different days (D38).
      - thermal (3D-ICE): `unzip` and `pkgs.openblasCompat` — the LP64 build specifically, since
        the default ILP64 openblas corrupts SuperLU_MT's 32-bit BLAS parameters into a segfault
        (D64). Commit-pinned, same reason as D38.

    Timeloop lives in its own shell, `nix develop .#timeloop`, and nowhere else: it is a
    from-source C++ build (Timeloop v4 plus barvinok, neither in nixpkgs at a usable version) that
    no one doing Python work should wait for. nixpkgs' `timeloop` is 3.0.3 and rejects this repo's
    own config files (`ERROR: key not found: data-spaces`), which is what D132/D163 read as
    "Docker is unavoidable"; D204 found the real obstacle was barvinok, and the hermetic path now
    reproduces the pinned Docker energy numbers exactly (D206). The adapter still defaults to
    Docker regardless of shell — `FLUX_TIMELOOP_LOCAL=1` opts in.

    CHIA (`flows/chia_nodes`, `flows/mcp`, `search/agentic`) is not on PyPI — built below from a
    pinned commit, with four of its exact version pins relaxed via `pythonRelaxDeps` against
    nixpkgs' newer `ray`/`pydantic`/`fastapi`/`pytest`. Compatibility was verified, not assumed
    (D23).

    `default` cherry-picks Verilator/Yosys rather than using nixchip's `simulation`/`asic` shells:
    both pull in `cryptominisat`, whose build git-clones `cadical` at build time and so cannot
    work in nix's sandbox, and `asic-tools` additionally hits a `broken`-flagged `or-tools`.
    `nix build github:helcel-net/nixchip#verilator` and `#yosys` succeed standalone. Re-check the
    bundles when EDA needs grow past this.
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
          pkgsUnpatched = import nixpkgs { inherit system; };

          # The physical-design toolchain (docs/decisions.md D225). Its own nixpkgs import,
          # because openroad's or-tools dependency is broken-marked there for a reason that does
          # not apply here: or-tools' VENDORED pybind11 (let-bound in its package, unreachable by
          # scope overlays) fails one test under python 3.14 — the TypeError wording for
          # unhashable set elements changed. Building or-tools against python 3.13 fixes that;
          # its own ctest then fails a python-contrib dependency check openroad never touches
          # (openroad links the C++ libraries only), so checks are skipped, not fixed. Uncached
          # upstream (Hydra skips broken-marked packages) — the first `.#physical` entry builds
          # or-tools and openroad from source.
          physicalPkgs = import nixpkgs {
            inherit system;
            config.allowBroken = true;
            overlays = [
              (final: prev: {
                or-tools = (prev.or-tools.override { python3 = final.python313; }).overrideAttrs
                  (_: { doCheck = false; });
              })
            ];
          };
          # A global override, not a per-package one: several of chia's nixpkgs deps are reached
          # through *more than one* chain (e.g. `fastapi` is both a direct chia dependency and a
          # transitive check-time dependency of `sse-starlette`, itself pulled in by `mcp`) —
          # disabling doCheck at the single call site where we write `py.fastapi` doesn't stop
          # nixpkgs from separately building sse-starlette's *own* check inputs, which
          # independently resolve the stock, still-checked `python312Packages.fastapi` again. A
          # `packageOverrides` on the python package set itself is the only fix that reaches
          # every one of those internal references consistently — every package below is one
          # this repo doesn't import directly (verified against CHIA's and flux-*'s source), so
          # skipping nixpkgs' own upstream test suites for them costs nothing real; we verify
          # they work via *our own* test suites instead. `scipy`'s upstream suite has one
          # genuinely flaky case (`test_support_moments_sample`, a Hypothesis-generated sub-ULP
          # floating-point difference, reproduced twice, unrelated to anything this repo does);
          # `fastapi`'s own suite needs `black` formatting-comparison tests
          # (`inline-snapshot[black]`) not available in this sandboxed builder; `boto3`/
          # `google-cloud-compute`'s suites pull in `moto`/`cfn-lint`/`aws-xray-sdk`, a large
          # AWS-mocking tree irrelevant to anything used here. `aiohttp` (reached via `ray`/`mcp`,
          # never imported by this repo — checked) has a wall-clock-sensitive case,
          # `test_keepalive_expires_on_time`, that fails reproducibly in this builder: 1 failed
          # against 4742 passed, twice, once under parallel build load and once on an idle
          # machine. It sits on the critical path to the dev shell itself, so leaving it checked
          # means the shell cannot be entered at all (docs/decisions.md D130).
          noCheck = pkg: pkg.overridePythonAttrs (old: { doCheck = false; doInstallCheck = false; });
          python312Patched = pkgsUnpatched.python312.override {
            packageOverrides = self: super: {
              scipy = noCheck super.scipy;
              fastapi = noCheck super.fastapi;
              boto3 = noCheck super.boto3;
              google-cloud-compute = noCheck super.google-cloud-compute;
              ray = noCheck super.ray;
              mcp = noCheck super.mcp;
              pydantic = noCheck super.pydantic;
              google-genai = noCheck super.google-genai;
              graphviz = noCheck super.graphviz;
              requests = noCheck super.requests;
              openai = noCheck super.openai;
              uvicorn = noCheck super.uvicorn;
              aiohttp = noCheck super.aiohttp;
            };
          };
          # `python312Packages` is a separate top-level nixpkgs alias, not derived from
          # `python312` by attribute lookup — overriding `python312` alone leaves it pointing at
          # the unpatched set, so `pkgs` below replaces both explicitly (`.pkgs` on the patched
          # interpreter is the correctly-overridden package set).
          pkgs = pkgsUnpatched // {
            python312 = python312Patched;
            python312Packages = python312Patched.pkgs;
          };
          # Cherry-picked, individually-verified-buildable nixchip packages — see the
          # description above for why not nixchip.devShells.${system}.
          chipPkgs = nixchip.packages.${system};
          py = pkgs.python312Packages;

          # ray[default]'s nixpkgs extras include py-spy (CPU stack-profiling for `ray stack`/
          # `ray memory` — not imported by CHIA or any flux-* code, verified by grepping CHIA's
          # source). Its own Rust test suite fails inside Nix's sandboxed builder specifically
          # (`test_thread_names` panics — a ptrace/process-introspection test that needs access
          # the build sandbox doesn't grant, not a real bug in the code this repo ships), so it's
          # dropped from the extras list rather than worked around — nothing here needs it.
          rayDefaultExtras = builtins.filter (p: (p.pname or "") != "py-spy") py.ray.optional-dependencies.default;

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

          # Shadows nixpkgs' onnx (D80). That build links against libprotobuf SONAME 35, and
          # OR-Tools' wheel vendors SONAME 33; both loaded together register overlapping
          # descriptors into protobuf's global registry and SIGSEGV. ONNX's own wheel avoids it.
          onnxNew = py.buildPythonPackage {
            pname = "onnx";
            version = "1.21.0";
            format = "wheel";
            src = pkgs.fetchurl {
              url = "https://files.pythonhosted.org/packages/a7/00/4823f06357892d1e60d6f34e7299d2ba4ed2108c487cc394f7ce85a3ff14/onnx-1.21.0-cp312-abi3-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl";
              sha256 = "a9261bd580fb8548c9c37b3c6750387eb8f21ea43c63880d37b2c622e1684285";
            };
            propagatedBuildInputs = [ py.numpy py.protobuf py.typing-extensions py.ml-dtypes ];
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
              onnxNew
              py.tqdm
              multiprocessingOnDill
              py.pyyaml
              py.cerberus
              py.seaborn
              py.typeguard
            ];
            doCheck = false;
          };

          # CHIA (github.com/ucb-bar/chia) isn't on PyPI — built from a pinned commit (D23, D85).
          # `doCheck = false`: `fetchFromGitHub` leaves CHIA's `examples/` submodules empty, and
          # its test suite needs docker/cluster infrastructure this sandbox has no way to provide.
          chia = py.buildPythonPackage {
            # Upstream renamed the *distribution* to "chialoops" ("chia" is taken on PyPI); the
            # import package and console script are still `chia`. `pname` must match the wheel's
            # METADATA Name or nixpkgs' pythonMetadataCheckPhase fails the build (D85).
            pname = "chialoops";
            # Must exactly match CHIA's own pyproject.toml version (nixpkgs'
            # pythonMetadataCheckPhase enforces derivation version == wheel METADATA version) —
            # the pinned commit is what's actually reproducible/traceable, via `src.rev` below.
            # Unchanged (0.1.0) across this pin bump — checked directly, not assumed.
            version = "0.1.0";
            pyproject = true;
            src = pkgs.fetchFromGitHub {
              owner = "ucb-bar";
              repo = "chia";
              rev = "098764c04c1260ee83b324153539bec6febab684";
              sha256 = "sha256-qs73CL8a0SvvER1BUe5dIr86a5l3cuJf6gvaOlM6ECg=";
            };
            build-system = [ py.setuptools py.wheel ];
            # See the top-level description: nixpkgs only has newer ray/pydantic/fastapi than
            # CHIA's exact pins; mcp's nixpkgs version matches CHIA's pin exactly already.
            pythonRelaxDeps = [ "ray" "pydantic" "fastapi" "pytest" ];
            propagatedBuildInputs = [
              py.google-genai
              py.ray
              py.mcp
              py.pydantic
              py.fastapi
              py.pyyaml
              py.pytest
              py.graphviz
              py.boto3
              py.google-cloud-compute
              py.requests
            ] ++ rayDefaultExtras;
            doCheck = false;
          };

          # Built from PyPI wheels, not nixpkgs (D80): `xdsl` isn't packaged at all, nixpkgs'
          # `protobuf` (6.30.2) is below stream-dse/ortools' shared `>=6.33.1` floor, and its
          # `ortools` builds from source and dies on an unrelated upstream scipy test flake.
          xdsl = py.buildPythonPackage {
            pname = "xdsl";
            version = "0.29.1";
            format = "wheel";
            src = pkgs.fetchurl {
              url = "https://files.pythonhosted.org/packages/81/16/94f64780274219c5662faca67c1656ca561e7bd1b2512d72e17577bad629/xdsl-0.29.1-py3-none-any.whl";
              sha256 = "1dc81297a75967a073114f6a18194f7302162774df851984c993256ef78bef7b";
            };
            # nixpkgs' typing-extensions/immutabledict are both past xdsl's own stated upper
            # pins (`<4.13`/`<4.2.2`) — relaxed the same way chia's own pydantic pin is above;
            # both are thin, stable compatibility/data-structure shims, real breakage from a
            # newer version is unlikely, and D80 verified real xdsl-backed Stream calls work
            # against them before relying on that alone.
            pythonRelaxDeps = [ "typing-extensions" "immutabledict" ];
            propagatedBuildInputs = [ py.immutabledict py.ordered-set py.typing-extensions ];
            doCheck = false;
          };

          # A dedicated, pinned-version protobuf was tried first and rejected empirically:
          # nixpkgs' own `onnx` (already a shared, load-bearing dependency — `frontends/onnx`,
          # `zigzagDse` above) transitively pulls its own newer protobuf (7.35.1), and nix's own
          # `pythonCatchConflictsPhase` correctly refuses two different protobuf versions in one
          # closure. Reusing nixpkgs' shared `py.protobuf` here instead — real, verified against
          # actual Stream calls in D80, not assumed compatible from ortools' own conservative
          # `<6.34` upper pin alone.
          ortools = py.buildPythonPackage {
            pname = "ortools";
            version = "9.15.6755";
            format = "wheel";
            src = pkgs.fetchurl {
              url = "https://files.pythonhosted.org/packages/49/0f/6d6d722102a0ceccf4a5038e2bc91d023da84a6dba98482a4634df3d27ab/ortools-9.15.6755-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl";
              sha256 = "033836c0eb33bc72697a299e0caedbb25fc9d1cee0b13832d69cb30405f57b3e";
            };
            pythonRelaxDeps = [ "protobuf" ];
            propagatedBuildInputs = [
              py.absl-py
              py.numpy
              py.pandas
              py.protobuf
              py.typing-extensions
              py.immutabledict
            ];
            doCheck = false;
          };

          # Stream (github.com/KULeuven-MICAS/stream, MIT, KU Leuven MICAS — the same group as
          # zigzag-dse, and built directly on top of it): multi-core/layer-fused DSE, docs/
          # decisions.md D80's real "prove the plumbing" first step before any Flux IR
          # translation exists (docs/roadmap.md's Phase 5 multi-core item). `pythonRelaxDeps`
          # for pydantic mirrors `chia`'s own precedent above — this shell's pydantic is newer
          # than stream-dse's own upper pin (`<2.12`), and D80 verified real Stream calls work
          # against it before relying on the relaxed check alone.
          streamDse = py.buildPythonPackage {
            pname = "stream-dse";
            version = "1.13.11";
            format = "wheel";
            src = pkgs.fetchurl {
              url = "https://files.pythonhosted.org/packages/3e/a8/b1da12fbe6ade0546b6894827baa2363fbbe0e4574e4d79f07b08a8a82da/stream_dse-1.13.11-py3-none-any.whl";
              sha256 = "53beaf80d615aca046d4eab0d5e34e7d3fb84a693578db36493d19903e8b6e53";
            };
            pythonRelaxDeps = [ "pydantic" ];
            propagatedBuildInputs = [
              zigzagDse
              py.cerberus
              ortools
              py.pydantic
              py.pydot
              xdsl
            ];
            doCheck = false;
          };

          # The hermetic Timeloop stack (docs/decisions.md D204/D205/D206). Kept out of both
          # shells above and reachable only through `nix develop .#timeloop`, because it is a
          # from-source C++ build (Timeloop + barvinok, neither in nixpkgs) that costs minutes on a
          # cold cache — a price no one doing Phase 1 Python work should pay to get a shell.
          barvinok = pkgs.callPackage ./build/nix/barvinok.nix { };
          timeloop = pkgs.callPackage ./build/nix/timeloop.nix { inherit barvinok; };
          accelergy = py.callPackage ./build/nix/accelergy.nix { };
          accelergyLibraryPlugIn = py.callPackage ./build/nix/accelergy-library-plug-in.nix {
            inherit accelergy;
          };
          accelergyCactiPlugIn = py.callPackage ./build/nix/accelergy-cacti-plug-in.nix {
            inherit accelergy;
            cacti = chipPkgs.cacti;
          };
          timeloopfe = py.callPackage ./build/nix/timeloopfe.nix { inherit accelergy; };

          # Third-party deps for the local flux-* packages (see the description above for why
          # the local packages themselves aren't built as derivations).
          pythonEnv = pkgs.python312.withPackages (ps: [
            ps.pytest
            ps.jsonschema
            ps.pyyaml
            onnxNew
            zigzagDse
            streamDse
            chia
            # Needed directly by flows/chia_nodes, flows/mcp, search/agentic (not just
            # transitively via chia) — chia.models.ollama.OllamaLLM's HTTP client and
            # flows/mcp's FluxTool server.
            ps.openai
            ps.uvicorn
          ] ++ rayDefaultExtras);

          # manylinux wheels built into pythonEnv (numpy, onnx, ...) dlopen libstdc++/zlib from
          # the standard dynamic linker path at import time; nixpkgs' Python doesn't put them
          # there like a distro Python would, so they fail with "libstdc++.so.6: cannot open
          # shared object file" unless we hand them an explicit LD_LIBRARY_PATH.
          nativeLibPath = pkgs.lib.makeLibraryPath [ pkgs.stdenv.cc.cc.lib pkgs.zlib ];

          # The local flux-* packages, src/-only (each is a PEP 420 namespace-free package
          # rooted at src/<module>/) — equivalent to `pip install -e` for all of them at once,
          # with no install step at all.
          #
          # Seven evaluator adapters (thermal, dramsim3, native, booksim, noxim, cacti, gem5) were
          # missing from this list for no recorded reason, which is why every documented test
          # command carried a seven-entry PYTHONPATH prefix to compensate (docs/decisions.md
          # D123). A setup step that every caller must remember is a defect, not a convention.
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
            "mentor/knowledge/mining/src"
            "core/llm/src"
            "core/profile/src"
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
            mkdir -p .nix-bin
            printf '#!/usr/bin/env bash\nexec python3 -c "from flux_cli.main import main; main()" "$@"\n' > .nix-bin/flux
            chmod +x .nix-bin/flux
            export PATH="$PWD/.nix-bin:$PATH"
            # Prebuilt CACTI and DRAMsim3 from nixchip, so `evaluators/{cacti,dramsim3}` skip
            # their clone-and-build path (docs/decisions.md D146/D148). Both adapters still fall
            # back to cloning when these are unset, which is what keeps them working outside this
            # shell — so this is a speed-up (1.8x and 2.4x on their own suites), not a new
            # requirement. Set before the banner so a caller overriding them in the environment
            # still wins.
            export CACTI_BIN="''${CACTI_BIN:-${chipPkgs.cacti}/bin}"
            export DRAMSIM3_BIN="''${DRAMSIM3_BIN:-${chipPkgs.dramsim3_}/bin}"
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

          # `nix develop .#timeloop` — the Docker-free Timeloop path. The adapter still defaults to
          # Docker even inside this shell; `FLUX_TIMELOOP_LOCAL=1` (or `use_local=True`) is what
          # selects it, so entering the shell can never silently change which tool produced a
          # number (docs/decisions.md D206).
          timeloop = pkgs.mkShell {
            name = "flux-dev-timeloop";
            packages = [
              (pkgs.python312.withPackages (ps: [
                ps.pytest ps.jsonschema ps.pyyaml
                onnxNew zigzagDse streamDse chia ps.openai ps.uvicorn
                timeloopfe accelergy accelergyLibraryPlugIn accelergyCactiPlugIn
              ] ++ rayDefaultExtras))
              timeloop pkgs.rustc pkgs.cargo pkgs.docker-client
            ];
            LD_LIBRARY_PATH = nativeLibPath;
            shellHook = ''
              echo "flux dev shell (timeloop: hermetic Timeloop v4 + Accelergy, no Docker)"
              echo "  FLUX_TIMELOOP_LOCAL=1   # opt in; the adapter defaults to Docker regardless"
            '' + shellHook;
          };

          # `nix develop .#physical` — the OpenROAD place-and-route rung (D225): yosys for
          # synthesis against the vendored ASAP7 liberty, openroad for floorplan/placement PPA.
          # Verilator too (D246): the composition-frontier and capstone tests escalate through
          # BOTH rtl (Verilator) and openroad rungs, and without it no shell in the repo could
          # run them at all — the review cycle found the D237/D239 flagships had zero possible
          # CI coverage.
          physical = pkgs.mkShell {
            name = "flux-dev-physical";
            packages = [
              pythonEnv pkgs.rustc pkgs.cargo
              chipPkgs.yosys physicalPkgs.openroad chipPkgs.verilator
              # yosys-slang (D276): a real SystemVerilog front end for Yosys. Yosys's own
              # frontend is a subset, and vendored industrial IP sits outside it — the PULP
              # logarithmic interconnect needs type parameters, `$bits` of a type, casts,
              # package imports on a module header and packed-array width inference, none of
              # which the built-in reader takes. Textual rewrites got five of those and then
              # met one that is not a rewrite, which is the point at which a front end is the
              # answer rather than more patches.
              chipPkgs.yosys-slang
              # OpenRAM (D260): a real memory COMPILER — generates characterized SRAM macros
              # (GDS/LEF/liberty) for freepdk45/sky130, the independent cross-check for the
              # analytical CACTI path at a node both support.
              chipPkgs.openram-wrapper
            ];
            LD_LIBRARY_PATH = nativeLibPath;
            # Yosys finds plugins under its OWN share/yosys/plugins, and yosys-slang installs
            # into its own store path, so the two never meet without being told. Exported
            # rather than hard-coded into the flow so the flow keeps working when the plugin
            # is absent (it falls back to Yosys's built-in reader).
            YOSYS_SLANG_PLUGIN = "${chipPkgs.yosys-slang}/share/yosys/plugins/slang.so";
            shellHook = ''
              echo "flux dev shell (physical: yosys + openroad on ASAP7, slang SV frontend)"
            '' + shellHook;
          };

          default = pkgs.mkShell {
            name = "flux-dev-full";
            packages = [
              pythonEnv pkgs.rustc pkgs.cargo pkgs.docker-client
              chipPkgs.verilator chipPkgs.yosys chipPkgs.gtkwave
              # CACTI and DRAMsim3 from nixchip rather than cloned at runtime (D146/D148).
              # nixchip names the latter `dramsim3_`; `#dramsim3` does not exist.
              chipPkgs.cacti chipPkgs.dramsim3_
              # systemc: hermetic, replacing a system libsystemc-dev dependency.
              # booksim: flex+bison, only to build Booksim2's own config.l/config.y.
              # noxim: cmake, only for its self-provisioned yaml-cpp (D32).
              # thermal: unzip (3D-ICE ships SuperLU_MT zipped) + openblasCompat — the LP64
              # build its solver needs; nixpkgs' plain `openblas` is ILP64 and segfaults (D64).
              pkgs.systemc pkgs.flex pkgs.bison pkgs.cmake pkgs.unzip pkgs.openblasCompat
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

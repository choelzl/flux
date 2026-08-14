# Timeloop v4, built from source (docs/decisions.md D204) — the piece D163 left open when it closed
# the hermetic-Timeloop question as "keep Docker".
#
# Why not nixpkgs' `timeloop`: that is 3.0.3, and this repo's adapter drives the v4 Python front-end
# (`timeloopfe`), whose config format the v3 binaries reject — `ERROR: key not found: data-spaces`
# against this repo's own reference YAML (D132). The four front-end Python packages already build
# here (`accelergy`, `timeloopfe`, and two plug-ins, D151/D158/D160/D161); this is the binary half.
#
# `barvinok` is the dependency that made D163 stop: `src/SConscript` links it unconditionally
# (`LIBS = ['barvinok', 'isl', 'ntl', 'pthread', 'polylibgmp', 'gmp']`) and nixpkgs does not package
# it. `nix/barvinok.nix` supplies it, along with the bundled isl and polylib it ships.
{ lib
, stdenv
, fetchFromGitHub
, scons
, barvinok
, boost
, gmp
, libconfig
, ncurses
, ntl
, yaml-cpp
}:

stdenv.mkDerivation rec {
  pname = "timeloop";
  version = "4.0-unstable-2025-06-09";

  # NVlabs/timeloop, not Accelergy-Project/timeloop — this is the repo the working Docker image
  # actually builds from, pinned to the exact submodule commit
  # `accelergy-timeloop-infrastructure` records. The Accelergy-Project fork's master is 18 months
  # older and still spells 8 config keys `data-spaces` where the front-end emits `data_spaces`,
  # which is precisely the `ERROR: key not found: data-spaces` that D132 attributed to the binary
  # being v3 (docs/decisions.md D204).
  src = fetchFromGitHub {
    owner = "NVlabs";
    repo = "timeloop";
    rev = "32370826fdf1aa3c8deb0c93e6b2a2fc7cf053aa";
    hash = "sha256-1TD+qkjDx3gf0z62m/OEFEVh1KqW53xg03VY8xsP6ZE=";
  };

  nativeBuildInputs = [ scons ];
  buildInputs = [ barvinok boost gmp libconfig ncurses ntl yaml-cpp ];

  # Timeloop's SConstruct reads its dependency locations from the environment rather than
  # pkg-config, so each is pointed at explicitly instead of relying on a global include path.
  BOOSTDIR = boost;
  LIBCONFIGPATH = libconfig;
  YAMLCPPPATH = yaml-cpp;
  BARVINOKPATH = barvinok;
  NTLPATH = ntl;

  # GCC 13 stopped including <cstdint> transitively, and this source predates that: headers use
  # `std::uint64_t` without including it. Force-including once is less invasive than patching each
  # file, and touches nothing about the code's meaning.
  env.NIX_CFLAGS_COMPILE = "-include cstdint";

  # `src/pat` is a symlink the upstream build expects the user to create by hand (its README says
  # `ln -s pat-public/src/pat src/pat`); the repo ships `pat-public/` but not the link.
  postPatch = ''
    ln -sfn ../pat-public/src/pat src/pat
  '';

  buildPhase = ''
    runHook preBuild
    # `--accelergy` is what makes the binaries call out to the Accelergy front-end for energy
    # estimation, which is how this repo uses Timeloop at all (its ERT/ART come from Accelergy).
    # Dynamic linking (upstream's default): `--static` asks for `-lpthread`, which modern glibc
    # no longer ships as a separate archive, and for static copies of libconfig/yaml-cpp that
    # nixpkgs splits across outputs.
    scons -j''${NIX_BUILD_CORES:-4} --accelergy
    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall
    mkdir -p $out/bin $out/lib
    for f in build/timeloop-*; do
      [ -f "$f" ] && [ -x "$f" ] && install -Dm755 "$f" "$out/bin/$(basename "$f")"
    done
    for f in lib/*.a lib/*.so*; do
      [ -e "$f" ] && install -Dm644 "$f" "$out/lib/$(basename "$f")" || true
    done
    runHook postInstall
  '';

  # Both install loops tolerate an empty glob, so a change in upstream's build layout would
  # produce an empty-but-green package — same reason `accelergy-cacti-plug-in.nix` asserts on
  # `tech_params` (docs/decisions.md D204). Asserted on the two binaries this repo actually
  # drives (`timeloopfe`'s mapper and model calls), not on the full set of six, which upstream
  # may grow or shrink without it mattering here.
  postInstall = ''
    for bin in timeloop-mapper timeloop-model; do
      [ -x "$out/bin/$bin" ] || { echo "missing $bin — install globs matched nothing?" >&2; exit 1; }
    done
  '';

  meta = with lib; {
    description = "Timeloop v4 — accelerator mapping/model tool (the binaries timeloopfe drives)";
    homepage = "https://github.com/NVlabs/timeloop";
    license = licenses.bsd3;
    platforms = platforms.linux;
  };
}

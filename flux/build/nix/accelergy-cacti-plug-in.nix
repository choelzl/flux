# `accelergy-cacti-plug-in` — the `CactiSRAM`/`CactiDRAM` estimators (docs/decisions.md D161).
#
# The last of the four, and the awkward one: its `get_cacti_dir` searches for `cacti/cacti` or
# `cacti` relative to `os.path.dirname(__file__)`, so the binary must live *inside* the installed
# plug-in directory. On PATH is not enough — read from the wrapper's source, not assumed.
#
# nixchip's CACTI is what makes this work, and specifically the fix that patched its `tech_params`
# and `contention.dat` lookups to absolute store paths (D146): a binary that needed its data
# alongside it could not be symlinked into someone else's directory at all. Upstream's own bundled
# copy produces identical numbers (D150), so this substitution changes nothing measurable.
{ lib, buildPythonPackage, fetchFromGitHub, accelergy, pyyaml, cacti }:

buildPythonPackage rec {
  pname = "accelergy-cacti-plug-in";
  version = "0.1";
  format = "setuptools";

  src = fetchFromGitHub {
    owner = "Accelergy-Project";
    repo = "accelergy-cacti-plug-in";
    rev = "master";
    hash = "sha256-IPA5OLM9Srqh5d8j/QNauPOwaCE8ft0kzQ57Zz4+qDM=";
  };

  propagatedBuildInputs = [ accelergy pyyaml ];

  # Upstream's `setup.py` copies `cacti/cacti` as a data file: the plug-in is *meant* to ship a
  # CACTI binary, and the image's Dockerfile builds one into the source tree before installing.
  # A fresh checkout has no such file, so the build fails with "can't copy 'cacti/cacti'". Supply
  # nixchip's instead of building a second copy — D150 measured the two producing identical
  # numbers, so this is a substitution, not an approximation.
  preBuild = ''
    mkdir -p cacti
    cp ${cacti}/bin/cacti cacti/cacti
    chmod +w cacti/cacti
    # CACTI reads its technology tables relative to its own binary, so `tech_params/` and
    # `contention.dat` must travel with it (docs/decisions.md D204). Without them CACTI produces
    # nothing and Accelergy reports "Can not find an energy estimator for DRAM(...)" — which reads
    # as a missing plug-in rather than a plug-in whose data files are absent. This is D145's
    # finding recurring: that entry traced five wrong CACTI predictions to exactly these files.
    cp -r ${cacti}/share/cacti/tech_params cacti/
    cp ${cacti}/share/cacti/contention.dat cacti/
    chmod -R +w cacti
  '';

  postInstall = ''
    plugin_dir="$out/share/accelergy/estimation_plug_ins/accelergy-cacti-plug-in"
    if [ ! -d "$plugin_dir/tech_params" ]; then
      echo "CACTI tech_params did not land beside the binary; every estimate will fail" >&2
      exit 1
    fi
    if [ ! -x "$plugin_dir/cacti" ]; then
      echo "CACTI binary did not land beside the wrapper; get_cacti_dir() will not find it" >&2
      exit 1
    fi
  '';

  doCheck = false;
  # No import check: Accelergy loads this by scanning, and the meaningful check is `estimator:
  # CactiSRAM` in a generated ERT (D138) — which needs a full run, not an import.
  meta = {
    description = "Accelergy CACTI-backed energy estimation plug-in";
    homepage = "https://github.com/Accelergy-Project/accelergy-cacti-plug-in";
  };
}

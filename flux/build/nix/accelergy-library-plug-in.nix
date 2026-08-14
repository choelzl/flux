# `accelergy-library-plug-in` — the `Library` estimator (docs/decisions.md D149/D160).
#
# One of exactly two plug-ins this repo's reference architecture actually uses: D138's captured ERT
# shows `CactiSRAM`, `CactiDRAM` and `Library`, so porting the image's other four would be work
# nobody needs.
#
# Accelergy discovers plug-ins by scanning a share directory rather than by import, so installing
# the Python package is not enough — the estimator's `.estimator.yaml` and its data have to land
# where Accelergy looks. That is the part packaging gets wrong quietly (D145: CACTI installed
# cleanly and crashed on data it could not find).
{ lib, buildPythonPackage, fetchFromGitHub, accelergy, pyyaml }:

buildPythonPackage rec {
  pname = "accelergy-library-plug-in";
  version = "0.1";
  format = "setuptools";

  src = fetchFromGitHub {
    owner = "Accelergy-Project";
    repo = "accelergy-library-plug-in";
    rev = "main";
    hash = "sha256-RbeHQm46HdkGHob/Od8FCVkqP97WHrPHPRCPZ9jZ76c=";
  };

  propagatedBuildInputs = [ accelergy pyyaml ];

  doCheck = false;
  # Deliberately no `pythonImportsCheck` on a plug-in name: Accelergy loads these by scanning, not
  # by import, so an import check would prove something other than what matters. The real check is
  # whether `estimator: Library` appears in a generated ERT (D138), which needs a full run.
  meta = {
    description = "Accelergy library-based energy estimation plug-in";
    homepage = "https://github.com/Accelergy-Project/accelergy-library-plug-in";
  };
}

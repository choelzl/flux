# `timeloopfe` — Timeloop's v4 Python front-end (docs/decisions.md D149/D151).
#
# The adapter drives Timeloop through this, not through the `timeloop-mapper` binary directly:
# its whole driver is `Specification.from_yaml_files(...)` then `call_mapper(...)`, and the bare
# binary cannot read this repo's own config files (D132, measured: `ERROR: key not found:
# data-spaces`). So a hermetic Timeloop needs this package, and it is the easy one — pure Python,
# no `ext_modules` (checked in D133).
{ lib, buildPythonPackage, fetchFromGitHub, accelergy, ruamel-yaml, psutil, joblib }:

buildPythonPackage rec {
  pname = "timeloopfe";
  version = "0.4-unstable";
  format = "setuptools";

  src = fetchFromGitHub {
    owner = "Accelergy-Project";
    repo = "timeloopfe";
    # Pinned to the commit the working Docker image's submodule records, not `main`. A moving ref
    # with a fixed hash is a build that breaks the day upstream pushes — and worse here, the whole
    # point of this package is reproducing pinned energy numbers (docs/decisions.md D206). Verified
    # to be byte-identical to what `main` resolved to when this was written: the SRI hash below is
    # unchanged from the floating version.
    rev = "5603893c0ff75183b5ffd6839aba33774fc3b6fe";
    hash = "sha256-/HO6QOoB9sUv2WztH+54Y9EahpKRmvx1+dRFTT27kXQ=";
  };

  propagatedBuildInputs = [ accelergy ruamel-yaml psutil joblib ];

  # Upstream ships no test suite runnable without Timeloop's binaries; the real check is this
  # repo's own energy-equivalence baseline (D141), not an import smoke test pretending to be one.
  doCheck = false;
  pythonImportsCheck = [ "timeloopfe" "timeloopfe.v4" ];

  meta = {
    description = "Python front-end for Timeloop v4 specifications";
    homepage = "https://github.com/Accelergy-Project/timeloopfe";
  };
}

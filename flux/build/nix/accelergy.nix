# `accelergy` 0.4 — the energy-estimation framework Timeloop's front-end sits on
# (docs/decisions.md D151/D158).
#
# nixpkgs ships `0.1-unstable`, which `timeloopfe` cannot use: it imports `accelergy.utils.yaml`,
# and in 0.1 `accelergy.utils` is not a package (measured in D151). So this replaces the nixpkgs
# package rather than extending it — and it is the first of the four, because everything else in
# the port depends on it.
#
# Deps are the ones the working Docker image reports for 0.4 (`pip show accelergy`): deepdiff,
# Jinja2, pyfiglet, pyYAML, ruamel.yaml. Taken from the artifact that runs rather than from a
# README, for the same reason D149's plug-in list was.
{ lib, buildPythonPackage, fetchFromGitHub, deepdiff, jinja2, pyfiglet, pyyaml, ruamel-yaml }:

buildPythonPackage rec {
  pname = "accelergy";
  version = "0.4";
  format = "setuptools";

  src = fetchFromGitHub {
    owner = "Accelergy-Project";
    repo = "accelergy";
    rev = "master";
    hash = "sha256-YgJbmxJfuw7jk+Ssj5r3cmJYSSepf7aw+Ti3a9brm6o=";
  };

  propagatedBuildInputs = [ deepdiff jinja2 pyfiglet pyyaml ruamel-yaml ];

  # Upstream has no runnable test suite without estimation plug-ins installed; the real check is
  # this repo's energy-equivalence baseline (D141), not an import smoke test standing in for one.
  doCheck = false;
  pythonImportsCheck = [ "accelergy" "accelergy.utils" "accelergy.utils.yaml" ];

  meta = {
    description = "Architecture-level energy estimation framework";
    homepage = "https://github.com/Accelergy-Project/accelergy";
  };
}

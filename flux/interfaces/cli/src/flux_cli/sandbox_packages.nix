# The loop's extra tools use the same locked inputs as Flux's development shell.
{ flakePath, packageSpec }:
let
  flake = builtins.getFlake flakePath;
  system = builtins.currentSystem;
  pkgs = import flake.inputs.nixpkgs { inherit system; };
  lib = pkgs.lib;
  spec = builtins.fromJSON packageSpec;
  resolve = source: name:
    let pkg = lib.attrByPath (lib.splitString "." name)
      (throw "No sandbox package '${name}' in this package source") source;
    in if lib.isDerivation pkg then pkg else throw "Sandbox package '${name}' is not a package";
  chip = lib.genAttrs spec.nixchip (resolve flake.inputs.nixchip.packages.${system});
  packages = map (resolve pkgs) spec.nixpkgs ++ lib.attrValues chip;
  chipEnv = lib.listToAttrs (lib.concatLists (lib.mapAttrsToList (name: pkg:
    let prefix = lib.toUpper (lib.replaceStrings [ "-" "." "+" ] [ "_" "_" "_" ] name);
    in map (suffix: { name = "${prefix}_${suffix.name}";
      # As in Flux's dev shell (D944), Verilator must find its own executable.
      value = if prefix == "VERILATOR" && suffix.name == "BIN" then null else "${pkg}${suffix.path}";
    }) [
      { name = "HOME"; path = ""; }
      { name = "BIN"; path = "/bin"; }
      { name = "LIB"; path = "/lib"; }
      { name = "INCLUDE"; path = "/include"; }
    ]) chip));
in pkgs.buildEnv {
  name = "flux-sandbox-packages";
  paths = packages;
  extraOutputsToInstall = [ "dev" ];
  postBuild = ''
    cat > "$out/flux-package-env.json" <<'EOF'
    ${builtins.toJSON chipEnv}
    EOF
  '';
}

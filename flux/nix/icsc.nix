# Intel Compiler for SystemC (ICSC): synthesizable SystemC -> SystemVerilog (D636).
#
# ICSC has no standalone translator binary. Its build installs a patched SystemC 3.0.1
# (elaboration instrumentation), `libSCTool` (a Clang 18 tool) and the cmake helper
# `svc_target`; a design becomes SV by compiling a "unity" .cpp that includes
# <sc_tool/SCTool.h> plus the design and links libSCTool: `sc_start` is redirected to
# `runScElab`, which elaborates the live module tree and re-parses the unity file with Clang.
# `bin/icsc-sv` below is that `svc_target` recipe without cmake, with the Nix paths baked in.
#
# Upstream's install.sh builds its own LLVM/Clang 18.1.8 and protobuf 3.19; here they come
# from nixpkgs: llvmPackages_18 is exactly 18.1.8, and protobuf_21 is the last release
# without the abseil dependency ICSC's FindProtobuf-based cmake does not link.
{ lib, stdenv, fetchFromGitHub, cmake, python3, llvmPackages_18, protobuf_21, zlib }:

let
  llvm = llvmPackages_18;
in
stdenv.mkDerivation {
  pname = "icsc";
  version = "1.7.12-unstable-2026-09-25";

  src = fetchFromGitHub {
    owner = "intel";
    repo = "systemc-compiler";
    rev = "68e56ae742e662199b697f7535f761c8d9bfcb7b";
    hash = "sha256-melsgKUyiv19LNLbrtFag6NZ5CfQ1iBDm/hgU1g/MZw=";
  };

  nativeBuildInputs = [ cmake python3 protobuf_21 ];
  buildInputs = [ llvm.llvm llvm.libclang protobuf_21 zlib ];

  # nixpkgs' LLVM is one shared libLLVM (LLVM_LINK_LLVM_DYLIB); linking its static
  # component archives next to libclang-cpp (which links libLLVM) registers every
  # cl::opt twice and aborts at start-up -- link the dylib instead. The systemc.h
  # PCH only speeds up ICSC's in-tree tests and wants a clang++ in LLVM's bindir: dropped.
  postPatch = ''
    sed -i -e 's/^\(\s*\)LLVMSupport$/\1LLVM/' -e '/^\s*LLVM[A-Z][A-Za-z0-9]*$/d' \
      -e '/add_dependencies(SCTool systemcPCH)/d' sc_tool/CMakeLists.txt
  '';

  preConfigure = ''
    export ICSC_HOME=$out LLVM_VER=${llvm.llvm.version}
  '';

  cmakeFlags = [ "-DCMAKE_CXX_STANDARD=20" "-DCMAKE_BUILD_TYPE=Release" "-DENABLE_PTHREADS=ON" ];

  # The flags `svc_target` hands the Clang re-parse: the unity file parses with -nostdinc
  # against exactly this toolchain's libstdc++/libc headers and Clang 18's builtins, never
  # the host's /usr/include.
  postInstall = ''
    incs=$(NIX_CFLAGS_COMPILE= ${stdenv.cc}/bin/c++ -xc++ -E -v /dev/null 2>&1 \
      | sed -n '/#include <...> search starts here/,/End of search list/p' \
      | grep '^ ' | grep -v '/lib/gcc/' | sed 's/^ */-isystem /' | tr '\n' ' ')
    mkdir -p $out/bin
    cat > $out/bin/icsc-sv <<EOF
    #!${stdenv.shell}
    # icsc-sv DESIGN.cpp OUT.sv [sc_tool options...]
    # DESIGN.cpp holds the module(s) and an sc_main that instantiates the top and calls
    # sc_start(); OUT.sv receives the SystemVerilog. Builds and runs the elaborator in a
    # temp dir (svc_target without cmake).
    set -eu
    src=\$(realpath "\$1"); out=\$(realpath -m "\$2"); shift 2
    work=\$(mktemp -d); trap 'rm -rf "\$work"' EXIT
    icsc=$out
    args="\$work/unity.cpp -sv_out \$out \$* -- -D__SC_TOOL__ -D__SC_TOOL_ANALYZE__ -DNDEBUG \
      -DSC_ALLOW_DEPRECATED_IEEE_API -Wno-logical-op-parentheses -std=c++20 -nostdinc \
      -I\$icsc/include -I\$icsc/include/sctcommon -I\$(dirname "\$src") \
      $incs -isystem ${lib.getLib llvm.clang-unwrapped}/lib/clang/${lib.versions.major llvm.llvm.version}/include"
    printf '#include <sc_tool/SCTool.h>\nconst char* __sctool_args_str = R"(%s)";\n#include "%s"\n' \
      "\$args" "\$src" > "\$work/unity.cpp"
    # the dev shell's NIX_CFLAGS_COMPILE names pkgs.systemc's headers: ICSC's patched ones only
    unset NIX_CFLAGS_COMPILE NIX_LDFLAGS
    ${stdenv.cc}/bin/c++ -std=c++20 -O1 -D__SC_TOOL__ -DSC_ALLOW_DEPRECATED_IEEE_API \
      -I\$icsc/include -I\$icsc/include/sctcommon -I\$(dirname "\$src") \
      "\$work/unity.cpp" -o "\$work/sctool" \
      -L\$icsc/lib -Wl,-rpath,\$icsc/lib -lSCTool -lSysCRTTI -lsc_elab_proto -lsystemc \
      -L${lib.getLib protobuf_21}/lib -Wl,-rpath,${lib.getLib protobuf_21}/lib -lprotobuf -lpthread
    cd "\$work" && ./sctool
    EOF
    chmod +x $out/bin/icsc-sv
  '';

  meta = {
    description = "Intel Compiler for SystemC: synthesizable SystemC to SystemVerilog";
    homepage = "https://github.com/intel/systemc-compiler";
    license = lib.licenses.asl20;  # Apache-2.0 WITH LLVM-exception
    platforms = lib.platforms.linux;
  };
}

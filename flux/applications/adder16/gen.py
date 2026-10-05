"""The adder generator the document sweeps: `gen.py <out> <arch>` writes one 16-bit adder.
`carry_select_<block>` names the carry-select block size (2, 4 or 8): six architectures, one
knob (D866)."""

import sys

N = 16
HEAD = f"module adder16(input [{N - 1}:0] a, input [{N - 1}:0] b, output [{N}:0] s);\n"


def behavioral() -> list[str]:
    return ["  assign s = a + b;"]


def ripple() -> list[str]:
    out = [f"  wire [{N}:0] c;", "  assign c[0] = 1'b0;"]
    for i in range(N):
        out.append(f"  assign s[{i}] = a[{i}] ^ b[{i}] ^ c[{i}];")
        out.append(f"  assign c[{i + 1}] = (a[{i}] & b[{i}]) | (c[{i}] & (a[{i}] ^ b[{i}]));")
    out.append(f"  assign s[{N}] = c[{N}];")
    return out


def carry_select(block: int) -> list[str]:
    """Each block adds twice (carry-in 0 and 1); the real carry picks one."""
    out = [f"  wire [{N // block}:0] c;", "  assign c[0] = 1'b0;"]
    for k, lo in enumerate(range(0, N, block)):
        hi = lo + block - 1
        out += [f"  wire [{block}:0] r0_{k}, r1_{k};",
                f"  assign r0_{k} = a[{hi}:{lo}] + b[{hi}:{lo}];",
                f"  assign r1_{k} = a[{hi}:{lo}] + b[{hi}:{lo}] + 1'b1;",
                f"  assign s[{hi}:{lo}] = c[{k}] ? r1_{k}[{block - 1}:0] : r0_{k}[{block - 1}:0];",
                f"  assign c[{k + 1}] = c[{k}] ? r1_{k}[{block}] : r0_{k}[{block}];"]
    out.append(f"  assign s[{N}] = c[{N // block}];")
    return out


def kogge_stone() -> list[str]:
    """Parallel prefix: log2(N) levels of (generate, propagate) pairs at doubling distance."""
    out = [f"  wire [{N - 1}:0] g0 = a & b;", f"  wire [{N - 1}:0] p0 = a ^ b;"]
    level, d = 0, 1
    while d < N:
        g, p, G, P = f"g{level}", f"p{level}", f"g{level + 1}", f"p{level + 1}"
        out.append(f"  wire [{N - 1}:0] {G}, {P};")
        for i in range(N):
            if i >= d:
                out.append(f"  assign {G}[{i}] = {g}[{i}] | ({p}[{i}] & {g}[{i - d}]);")
                out.append(f"  assign {P}[{i}] = {p}[{i}] & {p}[{i - d}];")
            else:
                out.append(f"  assign {G}[{i}] = {g}[{i}];")
                out.append(f"  assign {P}[{i}] = {p}[{i}];")
        level, d = level + 1, d * 2
    out.append("  assign s[0] = p0[0];")
    out.append(f"  assign s[{N - 1}:1] = p0[{N - 1}:1] ^ g{level}[{N - 2}:0];")
    out.append(f"  assign s[{N}] = g{level}[{N - 1}];")
    return out


ARCHS = {"behavioral": behavioral, "ripple": ripple, "carry_select_2": lambda: carry_select(2),
         "carry_select_4": lambda: carry_select(4), "carry_select_8": lambda: carry_select(8),
         "kogge_stone": kogge_stone}


if __name__ == "__main__":
    path, arch = sys.argv[1], sys.argv[2]
    if arch not in ARCHS:
        sys.exit(f"gen.py: no architecture {arch!r}; one of {', '.join(ARCHS)}")
    body = ARCHS[arch]()
    with open(path, "w") as f:
        f.write(HEAD + "\n".join(body) + "\nendmodule\n")

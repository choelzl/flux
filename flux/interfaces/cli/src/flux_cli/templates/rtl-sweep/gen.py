"""The generator of __NAME__: `gen.py <out> <arch> <chunk>` spells one 16-bit popcount per point
of the document's `flow.dse.space`. Add an architecture here and its name to the space to try it."""

import sys

N = 16
HEAD = f"module __NAME__(input logic [{N - 1}:0] a, output logic [4:0] y);\n"


def behavioral(chunk: int) -> list[str]:
    """One sum of every bit; synthesis builds the adders."""
    return ["  assign y = " + " + ".join(f"5'(a[{i}])" for i in range(N)) + ";"]


def tree(chunk: int) -> list[str]:
    """A pairwise adder tree: bits, pairs, nibbles, bytes, the whole."""
    out, level, width, n = [], 0, 1, N
    out.append(f"  logic [{width - 1}:0] l0 [{n}];")
    out += [f"  assign l0[{i}] = a[{i}];" for i in range(n)]
    while n > 1:
        level, width, n = level + 1, width + 1, n // 2
        out.append(f"  logic [{width - 1}:0] l{level} [{n}];")
        out += [f"  assign l{level}[{i}] = {{1'b0, l{level - 1}[{2 * i}]}} + {{1'b0, l{level - 1}[{2 * i + 1}]}};"
                for i in range(n)]
    out.append(f"  assign y = l{level}[0];")
    return out


def lut(chunk: int) -> list[str]:
    """Each `chunk` bits counted by a table (a case), then the counts summed."""
    groups, bits = N // chunk, chunk.bit_length()
    out = [f"  logic [{bits - 1}:0] c [{groups}];"]
    for g in range(groups):
        out.append(f"  always_comb case (a[{g * chunk + chunk - 1}:{g * chunk}])")
        out += [f"    {chunk}'d{v}: c[{g}] = {bits}'d{bin(v).count('1')};" for v in range(1 << chunk)]
        out.append(f"    default: c[{g}] = '0;\n  endcase")
    out.append("  assign y = " + " + ".join(f"5'(c[{g}])" for g in range(groups)) + ";")
    return out


if __name__ == "__main__":
    path, arch, chunk = sys.argv[1], sys.argv[2], int(sys.argv[3])
    body = {"behavioral": behavioral, "tree": tree, "lut": lut}[arch](chunk)
    with open(path, "w") as f:
        f.write(HEAD + "\n".join(body) + "\nendmodule\n")

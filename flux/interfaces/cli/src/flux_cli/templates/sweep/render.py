"""The generator of __NAME__: `render.py <out> <algorithm> <wheel>` writes one candidate for a
point of the document's `flow.dse.space`. Add an algorithm here and a value to the space to try it."""

import sys

out, algorithm, wheel = sys.argv[1], sys.argv[2], int(sys.argv[3])

BODIES = {
    # a list of booleans, crossed out one multiple at a time
    "list_sieve": """
def count_primes(n):
    if n < 3:
        return 0
    is_p = [True] * n
    is_p[0] = is_p[1] = False
    for i in range(2, int(n ** 0.5) + 1):
        if is_p[i]:
            for j in range(i * i, n, i):
                is_p[j] = False
    return sum(is_p)
""",
    # a bytearray, crossed out a whole slice at a time
    "slice_sieve": """
def count_primes(n):
    if n < 3:
        return 0
    s = bytearray([1]) * n
    s[0:2] = b"\\0\\0"
    for i in range(2, int(n ** 0.5) + 1):
        if s[i]:
            s[i * i::i] = bytearray(len(range(i * i, n, i)))
    return sum(s)
""",
    # odd numbers only: half the memory and half the crossing out
    "odd_sieve": """
def count_primes(n):
    if n < 3:
        return 0
    half = n // 2
    s = bytearray([1]) * half
    s[0] = 0
    i = 1
    while (2 * i + 1) ** 2 < n:
        if s[i]:
            p = 2 * i + 1
            s[p * p // 2::p] = bytearray(len(range(p * p // 2, half, p)))
        i += 1
    return sum(s) + 1
""",
}
body = BODIES[algorithm]
if wheel and algorithm == "list_sieve":
    body = body.replace("for i in range(2,", "for i in [2] + list(range(3,").replace("int(n ** 0.5) + 1):", "int(n ** 0.5) + 1, 2)):")
open(out, "w").write(f"# {algorithm}, wheel={wheel}\n" + body)

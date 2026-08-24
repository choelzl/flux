"""The gate of __NAME__: run the candidate on known cases and print `N failing of M`.
Exit 3 means the candidate could not even be loaded (a build failure to the loop)."""

import importlib.util
import sys


def reference(n: int) -> int:
    if n < 3:
        return 0
    sieve = bytearray([1]) * n
    sieve[0:2] = b"\0\0"
    for i in range(2, int(n ** 0.5) + 1):
        if sieve[i]:
            sieve[i * i::i] = bytearray(len(range(i * i, n, i)))
    return sum(sieve)


CASES = [0, 1, 2, 3, 4, 10, 11, 100, 1000, 7919, 10007, 100_000, 1_000_000]

spec = importlib.util.spec_from_file_location("candidate", sys.argv[1])
try:
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fn = mod.count_primes
except Exception as exc:  # noqa: BLE001 -- anything that stops the module loading
    print(f"did not load: {type(exc).__name__}: {exc}")
    print(f"{len(CASES)} failing of {len(CASES)}")
    sys.exit(3)
failing = 0
for n in CASES:
    want = reference(n)
    try:
        got = fn(n)
    except Exception as exc:  # noqa: BLE001
        got = f"{type(exc).__name__}: {exc}"
    if got != want:
        failing += 1
        print(f"FAIL count_primes({n}) = {got!r}, expected {want}")
print(f"{failing} failing of {len(CASES)}")
sys.exit(1 if failing else 0)

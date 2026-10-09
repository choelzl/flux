"""The stage of primes: time the candidate on the workload and print `time_ms=<median of 5>` and
`time_spread_ms=<slowest - fastest>`.

Each run is a fresh process on an n drawn near 2,000,000, and its answer is checked against the
reference, so a cache (`functools.lru_cache`) or a wrong answer cannot score (D874)."""

import importlib.util
import random
import statistics
import subprocess
import sys
import time
from pathlib import Path

N = 2_000_000
RUNS = 5


def once(artifact: str, n: int) -> None:
    """One timed call in this process: print its seconds, or say why it does not count."""
    spec = importlib.util.spec_from_file_location("candidate", artifact)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    t0 = time.perf_counter()
    got = mod.count_primes(n)
    took = time.perf_counter() - t0
    check = importlib.util.spec_from_file_location("check", Path(__file__).with_name("check.py"))
    ref = importlib.util.module_from_spec(check)
    check.loader.exec_module(ref)
    want = ref.reference(n)
    if type(got) is not int or got != want:
        sys.exit(f"count_primes({n}) = {got!r}, expected {want}")
    print(took)


def main() -> int:
    if sys.argv[1] == "--once":
        once(sys.argv[2], int(sys.argv[3]))
        return 0
    times = []
    for _ in range(RUNS):
        n = N - random.randrange(1000)
        try:
            proc = subprocess.run([sys.executable, __file__, "--once", sys.argv[1], str(n)],
                                  capture_output=True, text=True, timeout=25)
        except subprocess.TimeoutExpired:
            print(f"run failed: count_primes({n}) took over 25 s")
            return 1
        if proc.returncode != 0:
            print(f"run failed: {(proc.stderr.strip() or proc.stdout.strip())[-400:]}")
            return 1
        times.append(float(proc.stdout.split()[-1]))
    print(f"time_ms={statistics.median(times) * 1000:.3f}")
    print(f"time_spread_ms={(max(times) - min(times)) * 1000:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

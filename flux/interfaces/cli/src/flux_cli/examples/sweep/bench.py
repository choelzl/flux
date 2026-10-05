"""The stage of __NAME__: time the candidate on the workload and print `time_ms=<best of 3>`."""

import importlib.util
import sys
import time

N = 2_000_000

spec = importlib.util.spec_from_file_location("candidate", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
best = float("inf")
for _ in range(3):
    t0 = time.perf_counter()
    mod.count_primes(N)
    best = min(best, time.perf_counter() - t0)
print(f"time_ms={best * 1000:.3f}")

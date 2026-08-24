"""The stage of __NAME__: measure the setting, print `name=value`. Usage: bench.py <block> <order>"""

import os
import sys
import time

# one BLAS thread: a timing that depends on how many cores the library grabbed is not repeatable
for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(var, "1")

import numpy as np  # noqa: E402 -- after the thread settings

from workload import matmul  # noqa: E402

block, order = int(sys.argv[1]), sys.argv[2]
n = 512
rng = np.random.default_rng(1)
a, b = rng.standard_normal((n, n)), rng.standard_normal((n, n))
best = float("inf")
for _ in range(3):
    t0 = time.perf_counter()
    matmul(a, b, block, order)
    best = min(best, time.perf_counter() - t0)
print(f"time_ms={best * 1000:.2f}")

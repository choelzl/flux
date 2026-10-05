"""The gate of __NAME__: is the setting still correct? Runs the workload small and compares it with
a reference; prints `N failing`. Usage: check.py <block> <order>"""

import sys

import numpy as np

from workload import matmul

block, order = int(sys.argv[1]), sys.argv[2]
rng = np.random.default_rng(0)
failing = 0
for n in (1, 7, 64, 100):
    a, b = rng.standard_normal((n, n)), rng.standard_normal((n, n))
    if not np.allclose(matmul(a, b, block, order), a @ b):
        failing += 1
        print(f"FAIL n={n}")
print(f"{failing} failing")

"""The program __NAME__ tunes: a blocked matrix multiply whose block size and loop order are the
knobs. Replace it with yours -- a build, a solver, a kernel, a model's training script -- and
keep the contract: check.py says whether a setting is still correct, bench.py measures it."""

import numpy as np


def matmul(a: np.ndarray, b: np.ndarray, block: int, order: str) -> np.ndarray:
    n = a.shape[0]
    c = np.zeros((n, n), dtype=a.dtype)
    ranges = {"i": range(0, n, block), "j": range(0, n, block), "k": range(0, n, block)}
    loops = [ranges[ch] for ch in order]
    for x in loops[0]:
        for y in loops[1]:
            for z in loops[2]:
                idx = dict(zip(order, (x, y, z)))
                i, j, k = idx["i"], idx["j"], idx["k"]
                c[i:i + block, j:j + block] += a[i:i + block, k:k + block] @ b[k:k + block, j:j + block]
    return c

"""D862: the NLU's goldens give what their contracts say on every one of the 65,536 inputs. An
audit against a 60-digit reference found two that did not: rsqrt gave +Inf for -0 (the contract:
-Inf), and sigmoid gave +Inf for x in [-745, -710) (exp(-x) overflowed; the answer is 0)."""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import numpy as np

OPS = Path(__file__).resolve().parents[2] / "applications" / "nlu" / "ops"


def _golden(op):
    spec = importlib.util.spec_from_file_location(f"golden_{op}", OPS / op / "golden.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.golden


def _f16(bits):
    return float(np.uint16(bits).view(np.float16))


def test_rsqrt_keeps_the_sign_of_zero():
    g = _golden("rsqrt")
    assert g(0x0000)["y"] == 0x7C00 and g(0x8000)["y"] == 0xFC00
    assert math.isnan(_f16(g(0xBC00)["y"])) and g(0x7C00)["y"] == 0x0000




def test_sigmoid_is_between_zero_and_one_on_every_input():
    g = _golden("sigmoid")
    for bits in range(1 << 16):
        x, y = _f16(bits), _f16(g(bits)["y"])
        if math.isnan(x):
            assert math.isnan(y)
            continue
        assert 0.0 <= y <= 1.0, (hex(bits), x, y)
    assert g(0xFC00)["y"] == 0x0000 and g(0x7C00)["y"] == 0x3C00 and g(0xE18C)["y"] == 0x0000   # -Inf, +Inf, -710

"""D874: primes cannot be gamed. The bench timed count_primes(2,000,000) three times in one process
keeping the best, so `functools.lru_cache` scored 0.000 ms; the check stopped at 1,000,000 and the
bench never checked its answer, so a table of the 13 known answers, or 0 above 1,000,000, passed
with time 0; and `True` passed for 1."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "applications" / "primes"

SIEVE = '''
def count_primes(n):
    if n < 3:
        return 0
    s = bytearray([1]) * n
    s[0:2] = b"\\0\\0"
    for i in range(2, int(n ** 0.5) + 1):
        if s[i]:
            s[i * i::i] = bytearray(len(range(i * i, n, i)))
    return sum(s)
'''

#: The 13 old cases' answers and the workload's, as a lookup table.
TABLE = {0: 0, 1: 0, 2: 0, 3: 1, 4: 2, 10: 4, 11: 4, 100: 25, 1000: 168, 7919: 999, 10007: 1229,
         100_000: 9592, 1_000_000: 78498, 2_000_000: 148933}

CHEATS = {
    "cached": "import functools\n" + SIEVE.replace("def count_primes", "@functools.lru_cache(None)\ndef count_primes"),
    "table": f"TABLE = {TABLE!r}\ndef count_primes(n):\n    return TABLE.get(n, 0)\n",
    "zero_above_1m": SIEVE.replace("def count_primes(n):", "def count_primes(n):\n    if n > 1_000_000:\n        return 0"),
    "bool": SIEVE.replace("    return sum(s)", "    c = sum(s)\n    return True if c == 1 else c"),
}


def _run(script: str, artifact: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(APP / script), str(artifact)], capture_output=True, text=True, timeout=300)


def test_the_check_refuses_a_table_a_cutoff_and_a_bool(tmp_path):
    for name in ("table", "zero_above_1m", "bool"):
        (tmp_path / f"{name}.py").write_text(CHEATS[name])
        got = _run("check.py", tmp_path / f"{name}.py")
        assert got.returncode == 1 and "FAIL" in got.stdout, (name, got.stdout)
    (tmp_path / "ok.py").write_text(SIEVE)
    got = _run("check.py", tmp_path / "ok.py")
    assert got.returncode == 0 and got.stdout.strip().endswith("0 failing of 18"), got.stdout


def test_the_bench_times_fresh_processes_and_checks_the_answer(tmp_path):
    (tmp_path / "ok.py").write_text(SIEVE)
    (tmp_path / "cached.py").write_text(CHEATS["cached"])
    (tmp_path / "table.py").write_text(CHEATS["table"])
    honest, cached = _run("bench.py", tmp_path / "ok.py"), _run("bench.py", tmp_path / "cached.py")
    ms = [float(next(line for line in p.stdout.splitlines() if line.startswith("time_ms=")).split("=")[1])
          for p in (honest, cached)]
    assert all(m > 1.0 for m in ms), ("a cache must not score ~0 ms", ms)
    assert "time_spread_ms=" in honest.stdout
    wrong = _run("bench.py", tmp_path / "table.py")
    assert wrong.returncode == 1 and "time_ms=" not in wrong.stdout and "expected" in wrong.stdout

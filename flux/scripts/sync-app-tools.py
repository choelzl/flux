#!/usr/bin/env python3
"""Sync the one-file RTL tool (rtl.py) bundled with self-contained applications; --check reports drift.

Edit applications/mul8/rtl.py first, then run this script. Each RTL application (and the
application libraries that check RTL in Python, as rtl_check.py) carries its own copy, so uploading
or copying one never needs another application's folder; the NLU's operators run the NLU's own
copy through a four-line launcher (D956).
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil

FLUX = Path(__file__).resolve().parents[1]
SOURCE = FLUX / "applications/mul8/rtl.py"


def targets() -> list[Path]:
    apps = FLUX / "applications"
    out = [apps / name / "rtl.py" for name in ("adder16", "macarray", "nlu")]
    out += [FLUX.parent / "docs/tutorial/isqrt/rtl.py"]
    out += [FLUX / "tests/fixtures/loops" / kind / "rtl.py" for kind in ("rtl", "rtl-sweep")]
    out += [apps / "bankmap/lib/src/flux_bankmap/rtl_check.py", apps / "macarray/lib/src/flux_macarray/rtl_check.py"]
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    expected = SOURCE.read_bytes()
    different = [t for t in targets() if not t.is_file() or t.read_bytes() != expected]
    if args.check:
        for t in different:
            print(f"out of sync: {t.relative_to(FLUX.parent)}")
        return int(bool(different))
    for t in targets():
        t.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SOURCE, t)
    print(f"Synced {len(targets())} copies of rtl.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

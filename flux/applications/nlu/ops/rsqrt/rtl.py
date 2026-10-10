"""This operator's checks and measurements: the NLU's own rtl.py (two folders up), shared by
every operator so the NLU carries one copy (D956)."""

import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).resolve().parents[2] / "rtl.py"), run_name="__main__")

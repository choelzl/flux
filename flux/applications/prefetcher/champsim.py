"""This application's ChampSim checks and measurements; run with --help for options."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))

from champsim_tools.commands import main


if __name__ == "__main__":
    raise SystemExit(main())

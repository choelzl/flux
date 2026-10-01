"""A run's log with the time of each line (D732): `python -m flux_web.stamp <log> -- <argv...>`
starts the run, reads its output (stderr joined) and appends each line to the log as
`YYYY-MM-DD HH:MM:SS.mmm <line>`. It outlives the server as the run does (one process group),
ignores the group's SIGINT itself -- the run gets it and ends, and every line it says on the way
out is still written -- passes a SIGTERM on, and exits with the run's code. The page shows or
hides the stamp; a line without one (written before, or by the server) is shown as it is."""

from __future__ import annotations

import signal
import subprocess
import sys
import time

__all__ = ["STAMP_RE", "main", "stamp"]

#: What a stamped line begins with; the page's own pattern is the same.
STAMP_RE = r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3} "


def stamp(t: float | None = None) -> str:
    t = time.time() if t is None else t
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)) + f".{int(t * 1000) % 1000:03d} "


def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[1] != "--":
        print("usage: python -m flux_web.stamp <log> -- <argv...>", file=sys.stderr)
        return 2
    log, cmd = argv[0], argv[2:]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    # after the run started, so it does not inherit the ignore: a Stop now reaches it, not us
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, lambda *_: proc.send_signal(signal.SIGTERM))
    with open(log, "ab", buffering=0) as out:
        for line in iter(proc.stdout.readline, b""):
            out.write(stamp().encode() + line if line.endswith(b"\n") else stamp().encode() + line + b"\n")
    return proc.wait()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

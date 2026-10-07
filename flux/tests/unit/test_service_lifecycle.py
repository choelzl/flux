"""The installed service's stop policy must preserve loop sessions across updates."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid

import pytest


def test_a_user_service_restart_keeps_existing_loop_sessions_alive(tmp_path):
    if not all(shutil.which(cmd) for cmd in ("systemd-run", "systemctl", "nix", "git")):
        pytest.skip("the installer and lifecycle check need Nix, Git and a systemd user manager")
    if subprocess.run(["systemctl", "--user", "show-environment"], capture_output=True, timeout=5).returncode:
        pytest.skip("no systemd user manager available")
    installer = Path(__file__).resolve().parents[2] / "scripts/install-service.py"
    subprocess.run([sys.executable, str(installer), "--output", str(tmp_path / "units")], check=True, capture_output=True, timeout=10)
    unit = (tmp_path / "units/flux.service").read_text()
    mode = re.search(r"^KillMode=(.+)$", unit, re.M).group(1)
    # The same process structure as RunManager: a server and a loop in its own session.
    loop = tmp_path / "loop.py"
    loop.write_text("import os, sys, time\nfrom pathlib import Path\n"
                    "p = Path(sys.argv[1]) / f'loop-{os.getpid()}.ticks'\n"
                    "while True:\n"
                    "    with p.open('a') as f: f.write('working\\n')\n"
                    "    time.sleep(0.1)\n")
    server = tmp_path / "server.py"
    server.write_text("import subprocess, sys, time\nfrom pathlib import Path\n"
                      "p = subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2]], start_new_session=True)\n"
                      "with (Path(sys.argv[2]) / 'loops').open('a') as f: f.write(str(p.pid) + '\\n')\n"
                      "while True: time.sleep(1)\n")
    name = "flux-lifecycle-test-" + uuid.uuid4().hex[:12]
    started = False

    def wait_for(check):
        end = time.monotonic() + 5
        while time.monotonic() < end:
            if check():
                return True
            time.sleep(0.05)
        return False

    def pids():
        file = tmp_path / "loops"
        return [int(p) for p in file.read_text().splitlines()] if file.exists() else []

    try:
        subprocess.run(["systemd-run", "--user", "--quiet", "--unit", name, "--property", f"KillMode={mode}",
                        "--property", "TimeoutStopSec=3", sys.executable, str(server), str(loop), str(tmp_path)],
                       check=True, capture_output=True, timeout=10)
        started = True
        assert wait_for(lambda: bool(pids())), "the server must start its loop"
        pid = pids()[0]
        ticks = tmp_path / f"loop-{pid}.ticks"
        assert wait_for(ticks.exists)
        before = ticks.stat().st_size
        subprocess.run(["systemctl", "--user", "restart", name], check=True, capture_output=True, timeout=10)
        assert wait_for(lambda: len(pids()) == 2), "the replacement server must start"
        assert wait_for(lambda: ticks.stat().st_size > before + 16), "an update must leave the old loop doing work"
    finally:
        if started:
            subprocess.run(["systemctl", "--user", "stop", name], capture_output=True, timeout=10)
        for pid in pids():
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        subprocess.run(["systemctl", "--user", "reset-failed", name], capture_output=True, timeout=10)

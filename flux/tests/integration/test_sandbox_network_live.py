"""Real containers: permitted native TCP/UDP works, denied traffic hits the firewall."""

import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
from types import SimpleNamespace
import uuid

import pytest

from flux_cli import sandbox
from flux_cli.sandbox_proxy import AllowProxy


def test_native_tcp_udp_and_dns_respect_the_allowlist_without_task_capabilities(tmp_path, monkeypatch):
    eng = sandbox.engine()
    why = sandbox._engine_ok(eng)
    if why or not shutil.which("nft"):
        pytest.skip(why or "needs host nftables")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
        try:
            route.connect(("192.0.2.1", 9))
            peer = route.getsockname()[0]
        except OSError:
            pytest.skip("needs a host IPv4 route")
    tcp = socket.socket()
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tcp.bind((peer, 0))
    udp.bind((peer, 0))
    tcp.listen(8)
    tcp.settimeout(0.2)
    udp.settimeout(0.2)
    ended = threading.Event()

    def echo_tcp():
        while not ended.is_set():
            try:
                client, _ = tcp.accept()
                with client:
                    client.sendall(client.recv(128))
            except (OSError, TimeoutError):
                continue

    def echo_udp():
        while not ended.is_set():
            try:
                data, address = udp.recvfrom(128)
                udp.sendto(data, address)
            except (OSError, TimeoutError):
                continue

    for worker in (echo_tcp, echo_udp):
        threading.Thread(target=worker, daemon=True).start()
    original = socket.getaddrinfo
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *a, **kw:
                        original(peer if host == "allowed.flux.test" else host, *a, **kw))
    doc = tmp_path / "probe.problem.yaml"
    doc.write_text("statement: probe\n")
    args = SimpleNamespace(file=str(doc), db=str(tmp_path / "out" / "probe.db"), out=None, json=None,
                           plan=None, replies=None, skill=[], no_sandbox=False)
    name = "flux-netlive-" + uuid.uuid4().hex[:8]
    mine = sandbox.run_dir(name)
    proxy = AllowProxy(str(mine / "proxy.sock"), ["allowed.flux.test"], proxies={},
                       network_path=str(sandbox.run_dir(name + "-network") / "network.sock"))
    proxy.start()
    try:
        helper = sandbox.container_argv([sys.executable, "-I", str(Path(sandbox.__file__).with_name("sandbox_network.py"))],
                                         args, "task run", name + "-network", str(mine), eng,
                                         network="private" if eng == "podman" else "bridge", guard=True)
        start = subprocess.run(helper, capture_output=True, text=True, timeout=60)
        assert start.returncode == 0, start.stderr
        assert proxy.network_ready.wait(20), subprocess.run([*sandbox.engine_cli(eng), "logs", name + "-network"],
                                                           capture_output=True, text=True).stderr
        probe = tmp_path / "probe.py"
        probe.write_text(f'''import socket, subprocess
tcp = socket.create_connection(("allowed.flux.test", {tcp.getsockname()[1]}), 3)
tcp.sendall(b"tcp works")
assert tcp.recv(128) == b"tcp works"
tcp.close()
udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
udp.settimeout(3)
udp.sendto(b"udp works", ("allowed.flux.test", {udp.getsockname()[1]}))
assert udp.recv(128) == b"udp works"
try:
    socket.getaddrinfo("denied.flux.test", 9)
    raise AssertionError("denied DNS name resolved")
except socket.gaierror:
    pass
try:
    socket.create_connection(("192.0.2.1", 9), 0.3)
    raise AssertionError("denied TCP connected")
except OSError:
    pass
try:
    udp.sendto(b"denied udp", ("192.0.2.1", 9))
except OSError:
    pass
udp.sendto(b"still running", ("allowed.flux.test", {udp.getsockname()[1]}))
assert udp.recv(128) == b"still running", "a blocked destination must not stop later work"
assert subprocess.run([{str(Path(shutil.which("nft")).resolve())!r}, "flush", "ruleset"], capture_output=True).returncode != 0
assert int(next(line.split()[1] for line in open("/proc/self/status") if line.startswith("CapEff:")), 16) == 0
print("native TCP/UDP and domain DNS work; denied traffic and firewall edits are blocked")
''')
        task = sandbox.container_argv([sys.executable, str(probe)], args, "task run", name, str(mine), eng,
                                      network="container:" + name + "-network")
        result = subprocess.run(task, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        counters = subprocess.run([*sandbox.engine_cli(eng), "exec", name + "-network", shutil.which("nft"),
                                   "-j", "list", "chain", "inet", "flux_network", "output"],
                                  capture_output=True, text=True, check=True, timeout=15)
        dropped = [expr["counter"]["packets"] for entry in json.loads(counters.stdout)["nftables"]
                   for expr in entry.get("rule", {}).get("expr", []) if "counter" in expr]
        assert sum(dropped) >= 2, "both denied TCP and UDP reached the firewall's drop rule"
    finally:
        ended.set()
        tcp.close()
        udp.close()
        subprocess.run([*sandbox.engine_cli(eng), "rm", "-f", name, name + "-network"], capture_output=True, timeout=30)
        proxy.stop()
        shutil.rmtree(mine, ignore_errors=True)
        shutil.rmtree(sandbox.run_dir(name + "-network"), ignore_errors=True)

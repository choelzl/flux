"""The allowlist proxy of a sandboxed run (D680), on the host: the container has no network, and
its HTTP(S)_PROXY reaches this proxy through a Unix socket. It forwards to the hosts the
allowlist names -- a domain (and its subdomains), an IP, a CIDR, `localhost` -- and refuses the
rest with 403, said on stderr once per host and, when `flux serve` names a file for it
(`FLUX_SANDBOX_REFUSALS`), written there as a JSON line for the admin's audit (D708). HTTPS goes through CONNECT; plain HTTP is forwarded
with its absolute URL made relative."""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import sys
import threading
import time
from urllib.parse import urlsplit

__all__ = ["AllowProxy", "allowed", "permitted"]


def allowed(host: str, allow: list[str]) -> bool:
    host = host.strip("[]").lower().rstrip(".")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    for rule in allow:
        rule = rule.lower().strip().rstrip(".")
        if not rule:
            continue
        if ip is not None:
            try:
                if ip in ipaddress.ip_network(rule, strict=False):
                    return True
            except ValueError:
                pass
            if rule == "localhost" and ip.is_loopback:
                return True
            continue
        if host == rule or host.endswith("." + rule.lstrip("*.")) or (rule.startswith("*.") and host.endswith(rule[1:])):
            return True
    return False


def permitted(host: str, port: int, allow: list[str]) -> str | None:
    """Where to connect for `host`, or None when the allowlist refuses it (D698). A name the
    rules name goes as it is. Any other name is resolved, and passes when one of its addresses
    falls in an IP or CIDR rule -- that address is the one connected to, so the name cannot
    resolve elsewhere between the check and the connection."""
    if allowed(host, allow):
        return host.strip("[]")
    try:
        ipaddress.ip_address(host.strip("[]"))
        return None                                   # a bare IP no rule names
    except ValueError:
        pass
    if not any(_is_net(r) for r in allow):
        return None
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        return None
    for info in infos:
        addr = info[4][0]
        if allowed(addr, allow):
            return addr
    return None


def _is_net(rule: str) -> bool:
    try:
        ipaddress.ip_network(rule.strip(), strict=False)
        return True
    except ValueError:
        return False


def _pipe(a: socket.socket, b: socket.socket) -> None:
    try:
        while data := a.recv(65536):
            b.sendall(data)
    except OSError:
        pass
    finally:
        for s in (a, b):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


class AllowProxy:
    def __init__(self, path: str, allow: list[str], log: str | None = None, about: dict[str, str] | None = None) -> None:
        """`log`: a file each refused host is appended to, once per host and port, as JSON with
        `about` (the loop, the command, the container)."""
        self.path, self.allow = path, list(allow)
        self.refused: set[str] = set()
        self.log, self.about = log, dict(about or {})
        self._logged: set[tuple[str, int]] = set()
        self._srv: socket.socket | None = None

    def start(self) -> None:
        try:
            os.unlink(self.path)
        except OSError:
            pass
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(self.path)
        os.chmod(self.path, 0o600)
        srv.listen(64)
        self._srv = srv
        threading.Thread(target=self._serve, daemon=True, name="flux-sandbox-proxy").start()

    def stop(self) -> None:
        if self._srv is not None:
            try:
                self._srv.close()
            except OSError:
                pass

    def _serve(self) -> None:
        while True:
            try:
                client, _ = self._srv.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def _record(self, host: str, port: int) -> None:
        if not self.log or (host, port) in self._logged:
            return
        self._logged.add((host, port))
        line = json.dumps({"t": time.time(), "host": host, "port": port, **self.about}) + "\n"
        try:                                              # one write, appended: lines of runs at once do not mix
            fd = os.open(self.log, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, line.encode())
            finally:
                os.close(fd)
        except OSError:
            pass

    def _handle(self, client: socket.socket) -> None:
        try:
            head = b""
            while b"\r\n\r\n" not in head and len(head) < 65536:
                chunk = client.recv(4096)
                if not chunk:
                    client.close()
                    return
                head += chunk
            line, _, rest = head.partition(b"\r\n")
            method, target, version = line.decode("latin-1").split(" ", 2)
            if method.upper() == "CONNECT":
                host, _, port = target.rpartition(":")
                port_n = int(port or 443)
            else:
                u = urlsplit(target)
                host, port_n = u.hostname or "", u.port or (443 if u.scheme == "https" else 80)
            to = permitted(host, port_n, self.allow)
            if to is None:
                if host not in self.refused:
                    self.refused.add(host)
                    print(f"flux sandbox: refused {host} (not in FLUX_SANDBOX_ALLOW)", file=sys.stderr, flush=True)
                self._record(host, port_n)
                client.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                client.close()
                return
            up = socket.create_connection((to, port_n), timeout=30)
            up.settimeout(None)
            if method.upper() == "CONNECT":
                client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            else:
                u = urlsplit(target)
                path = (u.path or "/") + (f"?{u.query}" if u.query else "")
                up.sendall(f"{method} {path} {version}\r\n".encode("latin-1") + rest)
            for a, b in ((client, up), (up, client)):
                threading.Thread(target=_pipe, args=(a, b), daemon=True).start()
        except (OSError, ValueError):
            try:
                client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                client.close()
            except OSError:
                pass

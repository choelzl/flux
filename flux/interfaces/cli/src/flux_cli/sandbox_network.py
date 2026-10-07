"""A separate container owns the allowlisted network namespace and its firewall.

The task shares the network, without NET_ADMIN. DNS resolves on the host through the
read-only proxy socket; addresses enter the TCP/UDP firewall before the DNS reply is sent.
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import struct
import subprocess
import threading
import time

TTL = 60


def request(path: str, method: str, target: str = "rules") -> dict:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(15)
        client.connect(path)
        client.sendall(f"{method} {target} HTTP/1.1\r\n\r\n".encode())
        with client.makefile("rb") as reader:
            status = reader.readline(4096)
            length = 0
            while line := reader.readline(4096):
                if line == b"\r\n":
                    break
                if line.lower().startswith(b"content-length:"):
                    length = int(line.split(b":", 1)[1])
            if not 0 <= length <= 1048576:
                raise ValueError("network response too large")
            if b" 403 " in status:
                raise PermissionError("destination is not on the network allowlist")
            if b" 200 " not in status:
                raise OSError("network policy did not answer")
            return json.loads(reader.read(length))


def ruleset(networks: list[str]) -> str:
    sets = []
    for version in (4, 6):
        nets = list(ipaddress.collapse_addresses(ipaddress.ip_network(n) for n in networks
                                                if ipaddress.ip_network(n).version == version))
        elements = "elements = { " + ", ".join(map(str, nets)) + " };" if nets else ""
        sets.append(f"set allowed{version} {{ type ipv{version}_addr; flags interval; {elements} }}")
        sets.append(f"set resolved{version} {{ type ipv{version}_addr; flags timeout; }}")
    # Replies and IPv6 neighbour discovery work, but new non-TCP/UDP traffic cannot leave.
    neighbour = "meta l4proto ipv6-icmp icmpv6 type { nd-neighbor-solicit, nd-neighbor-advert, nd-router-solicit, nd-router-advert } accept"
    return "\n".join([
        "table inet flux_network {", *sets,
        "chain input { type filter hook input priority filter; policy drop;",
        'iifname "lo" accept', "ct state established,related accept", neighbour, "}",
        "chain output { type filter hook output priority filter; policy drop;",
        'oifname "lo" accept', "ct state established,related accept", neighbour,
        "meta l4proto { tcp, udp } ip daddr @allowed4 accept",
        "meta l4proto { tcp, udp } ip6 daddr @allowed6 accept",
        "meta l4proto { tcp, udp } ip daddr @resolved4 accept",
        "meta l4proto { tcp, udp } ip6 daddr @resolved6 accept", "counter drop", "}", "}",
    ]) + "\n"


class Firewall:
    def __init__(self, nft: str, networks: list[str]) -> None:
        self.nft = nft
        self.lock = threading.Lock()
        self.addresses: dict[str, float] = {}
        self.apply(ruleset(networks))

    def apply(self, script: str) -> None:
        subprocess.run([self.nft, "-f", "-"], input=script, text=True, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=15)

    def admit(self, addresses: list[str]) -> None:
        with self.lock:
            now = time.monotonic()
            for address in addresses:
                self.addresses[str(ipaddress.ip_address(address))] = now + 2 * TTL
            self.addresses = {a: end for a, end in self.addresses.items() if end > now}
            lines = []
            for version in (4, 6):
                lines.append(f"flush set inet flux_network resolved{version}")
                elements = [f"{a} timeout {max(1, int(end - now))}s" for a, end in self.addresses.items()
                            if ipaddress.ip_address(a).version == version]
                if elements:
                    lines.append(f"add element inet flux_network resolved{version} {{ {', '.join(elements)} }}")
            self.apply("\n".join(lines) + "\n")


def question(packet: bytes) -> tuple[str, int, bytes] | None:
    """A single uncompressed IN question; reject malformed packets rather than echo them."""
    if len(packet) < 17 or packet[2] & 0x80 or packet[4:6] != b"\x00\x01":
        return None
    labels, at = [], 12
    try:
        while packet[at]:
            size = packet[at]
            if size > 63 or at + size + 1 >= len(packet):
                return None
            labels.append(packet[at + 1:at + 1 + size].decode("ascii"))
            at += size + 1
        kind, cls = struct.unpack("!HH", packet[at + 1:at + 5])
    except (IndexError, UnicodeError, struct.error):
        return None
    name = ".".join(labels).lower()
    if cls != 1 or not name or len(name) > 253 or any(c.isspace() for c in name):
        return None
    return name, kind, packet[12:at + 5]


def answer(packet: bytes, proxy: str, firewall: Firewall) -> bytes:
    parsed = question(packet)
    if parsed is None:
        return packet[:2].ljust(2, b"\x00") + struct.pack("!HHHHH", 0x8081, 0, 0, 0, 0)
    name, kind, query = parsed
    records, rcode = [], 0
    try:
        addresses = request(proxy, "FLUX-RESOLVE", name)["addresses"]
        firewall.admit(addresses)                  # update succeeds before exposing the address
        if not addresses:
            rcode = 2
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if (kind == 1 and ip.version == 4) or (kind == 28 and ip.version == 6):
                records.append(b"\xc0\x0c" + struct.pack("!HHIH", kind, 1, TTL, len(ip.packed)) + ip.packed)
    except PermissionError:
        rcode = 5
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        rcode = 2
    flags = 0x8080 | ((packet[2] & 1) << 8) | rcode
    return packet[:2] + struct.pack("!HHHHH", flags, 1, len(records), 0, 0) + query + b"".join(records)


def main() -> None:
    proxy = os.environ["FLUX_SANDBOX_PROXY"]
    config = request(proxy, "FLUX-NETWORK")
    firewall = Firewall(os.environ["FLUX_SANDBOX_NFT"], config["networks"])
    firewall.admit(config["addresses"])
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    udp.bind(("127.0.0.1", 53))
    tcp.bind(("127.0.0.1", 53))
    tcp.listen(32)

    def datagrams() -> None:
        while True:
            data, addr = udp.recvfrom(4096)
            udp.sendto(answer(data, proxy, firewall), addr)

    def stream(client: socket.socket) -> None:
        with client:
            client.settimeout(15)
            with client.makefile("rb") as reader:
                while size := reader.read(2):
                    if len(size) != 2:
                        return
                    data = reader.read(struct.unpack("!H", size)[0])
                    reply = answer(data, proxy, firewall)
                    client.sendall(struct.pack("!H", len(reply)) + reply)

    def streams() -> None:
        while True:
            client, _ = tcp.accept()
            threading.Thread(target=stream, args=(client,), daemon=True).start()

    def refresh() -> None:
        while True:
            try:
                firewall.admit(request(proxy, "FLUX-NETWORK", "refresh")["addresses"])
            except (OSError, ValueError, KeyError, subprocess.SubprocessError):
                pass                             # expired addresses remain blocked after a failure
            time.sleep(TTL)

    for worker in (datagrams, streams, refresh):
        threading.Thread(target=worker, daemon=True).start()
    request(proxy, "FLUX-NETWORK-READY")
    threading.Event().wait()


if __name__ == "__main__":
    main()

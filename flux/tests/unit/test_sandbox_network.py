"""Native TCP/UDP uses the allowlist without giving the task firewall capabilities."""

import json
import socket
import struct
from types import SimpleNamespace

import pytest

from flux_cli import sandbox, sandbox_network as network
from flux_cli.sandbox_proxy import AllowProxy, resolve


def query(name, kind=1):
    return struct.pack("!HHHHHH", 7, 0x0100, 1, 0, 0, 0) + b"".join(
        bytes([len(label)]) + label.encode() for label in name.split(".")) + b"\0" + struct.pack("!HH", kind, 1)


def test_resolved_domain_addresses_and_cidrs_share_the_tcp_udp_policy(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_DGRAM, 17, "", ("192.0.2.7", 0)),
        (socket.AF_INET6, socket.SOCK_DGRAM, 17, "", ("2001:db8::7", 0, 0, 0)),
    ])
    assert resolve("api.example.test", ["example.test"]) == ["192.0.2.7", "2001:db8::7"]
    assert resolve("api.example.test", ["192.0.2.0/24"]) == ["192.0.2.7"]
    assert resolve("evil.test", ["example.test"]) == []
    rules = network.ruleset(["192.0.2.0/24", "192.0.2.7", "2001:db8::/32"])
    assert "policy drop" in rules and "tcp, udp" in rules
    assert "192.0.2.0/24" in rules and "192.0.2.7" not in rules, "overlapping IP rules are collapsed"
    assert "ip6 daddr @allowed6" in rules and "ip daddr @resolved4" in rules


@pytest.mark.parametrize("kind, address", [(1, "192.0.2.7"), (28, "2001:db8::7")])
def test_dns_exposes_addresses_only_after_the_firewall_admits_them(monkeypatch, kind, address):
    seen = []
    monkeypatch.setattr(network, "request", lambda *args: {"addresses": [address]})
    firewall = SimpleNamespace(admit=lambda addresses: seen.extend(addresses))
    reply = network.answer(query("api.example.test", kind), "socket", firewall)
    assert seen == [address] and reply[6:8] == b"\0\1"
    assert reply.endswith(socket.inet_pton(socket.AF_INET if kind == 1 else socket.AF_INET6, address))


def test_dns_refuses_unknown_names_and_withholds_addresses_if_firewall_update_fails(monkeypatch):
    firewall = SimpleNamespace(admit=lambda _: None)

    def denied(*args):
        raise PermissionError("not allowed")

    monkeypatch.setattr(network, "request", denied)
    assert network.answer(query("evil.test"), "socket", firewall)[3] & 15 == 5
    monkeypatch.setattr(network, "request", lambda *args: {"addresses": ["192.0.2.7"]})
    firewall.admit = lambda _: (_ for _ in ()).throw(OSError("firewall unavailable"))
    reply = network.answer(query("api.example.test"), "socket", firewall)
    assert reply[3] & 15 == 2 and reply[6:8] == b"\0\0"
    assert network.question(query("bad\nname.test")) is None
    assert network.question(b"short") is None


def test_an_unreachable_allowlisted_domain_cannot_prevent_network_startup(monkeypatch):
    def failed_lookup(*args, **kwargs):
        raise socket.gaierror("resolver cannot reach the domain")

    monkeypatch.setattr(socket, "getaddrinfo", failed_lookup)
    proxy = AllowProxy("unused.sock", ["unreachable.test", "192.0.2.0/24"])
    assert proxy.network_rules() == {"networks": ["192.0.2.0/24"], "addresses": []}
    assert proxy.network_rules(resolve_names=True)["addresses"] == []


def test_native_name_lookup_uses_the_host_policy_and_audits_refusals(tmp_path, monkeypatch):
    log = tmp_path / "refused.jsonl"
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_DGRAM, 17, "", ("192.0.2.7", 0)),
    ])
    proxy = AllowProxy(str(tmp_path / "proxy.sock"), ["example.test", "2001:db8::/32"], log=str(log), proxies={},
                       network_path=str(tmp_path / "network.sock"))
    proxy.start()
    try:
        assert network.request(proxy.path, "FLUX-RESOLVE", "api.example.test") == {"addresses": ["192.0.2.7"]}
        assert network.request(proxy.network_path, "FLUX-NETWORK")["networks"] == ["2001:db8::/32"]
        with pytest.raises(PermissionError):
            network.request(proxy.path, "FLUX-NETWORK")
        with pytest.raises(PermissionError):
            network.request(proxy.path, "FLUX-RESOLVE", "evil.test")
        assert json.loads(log.read_text())["how"] == "lookup"
    finally:
        proxy.stop()


def args(tmp_path):
    doc = tmp_path / "p.problem.yaml"
    doc.write_text("statement: s\n")
    return SimpleNamespace(file=str(doc), db=str(tmp_path / "out" / "p.db"), out=None, json=None,
                           plan=None, replies=None, skill=[], no_sandbox=False)


def test_only_the_separate_helper_gets_firewall_control_and_the_task_joins_after_readiness(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("FLUX_SANDBOX_ALLOW", "example.test")
    monkeypatch.setenv("FLUX_SANDBOX_RAW_NETWORK", "1")
    monkeypatch.setattr(sandbox, "_engine_ok", lambda _: "")
    monkeypatch.setattr(AllowProxy, "start", lambda self: self.network_ready.set())
    seen = []
    environments = []
    monkeypatch.setattr(sandbox.subprocess, "run", lambda cmd, **kw: seen.append(cmd) or SimpleNamespace(returncode=0, stderr="", stdout=""))

    def run_task(cmd, **kw):
        seen.append(cmd)
        environments.append(sandbox.container_env(cmd))
        return 7

    monkeypatch.setattr(sandbox.subprocess, "call", run_task)
    assert sandbox.launch(["task", "run", "p"], args(tmp_path), "task run") == 7
    guard, task = seen[:2]
    assert guard[guard.index("--cap-add") + 1] == "NET_ADMIN"
    assert "--cap-add" not in task and task[task.index("--cap-drop") + 1] == "ALL"
    assert task[task.index("--network") + 1] == "container:" + guard[guard.index("--name") + 1]
    assert "--sysctl" not in task and environments[0]["FLUX_SANDBOX_NETWORK"] == "1"
    proxy = environments[0]["FLUX_SANDBOX_PROXY"]
    parent = proxy.rsplit("/", 1)[0]
    assert f"{parent}:{parent}:ro" in task, "the task cannot replace the host's policy socket"
    assert all("network.sock" not in value for value in environments[0].values()), "the task cannot access the helper's policy socket"
    assert "FLUX_SANDBOX_RAW_NETWORK" not in environments[0], "the raw-network switch is host-side launch configuration"
    assert "rm" in seen[-1], "the helper is removed after the task finishes"


def test_helper_failure_continues_the_pass_through_the_isolated_http_relay(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("FLUX_SANDBOX_ALLOW", "example.test")
    monkeypatch.setenv("FLUX_SANDBOX_RAW_NETWORK", "1")
    monkeypatch.setattr(sandbox, "_engine_ok", lambda _: "")
    monkeypatch.setattr(AllowProxy, "start", lambda _: None)
    monkeypatch.setattr(sandbox.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stderr="helper failed", stdout=""))
    launched = []
    monkeypatch.setattr(sandbox.subprocess, "call", lambda cmd, **kw: launched.append(cmd) or 0)
    assert sandbox.launch(["task", "run", "p"], args(tmp_path), "task run") == 0
    assert len(launched) == 1 and launched[0][launched[0].index("--network") + 1] == "none"
    assert "--cap-add" not in launched[0]


@pytest.mark.parametrize("eng", ["docker", "podman"])
@pytest.mark.parametrize("raw, allow, mode, net", [
    (None, "example.test", "allowlist", "none"),
    ("0", "example.test", "allowlist", "none"),
    ("1", "", "allowlist", "none"),
    ("1", "", "open", "host"),
])
def test_a_network_helper_is_unnecessary_without_raw_access_and_allowed_destinations(tmp_path, monkeypatch, eng, raw, allow, mode, net):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("FLUX_SANDBOX_ALLOW", allow)
    monkeypatch.setenv("FLUX_SANDBOX_NET", mode)
    monkeypatch.delenv("FLUX_SANDBOX_RAW_NETWORK", raising=False)
    if raw is not None:
        monkeypatch.setenv("FLUX_SANDBOX_RAW_NETWORK", raw)
    monkeypatch.setattr(sandbox, "engine", lambda: eng)
    monkeypatch.setattr(sandbox, "_engine_ok", lambda _: "")
    proxies, containers, operations, environments = [], [], [], []
    monkeypatch.setattr(AllowProxy, "start", lambda self: proxies.append(self))
    monkeypatch.setattr(sandbox.subprocess, "run", lambda cmd, **kw: operations.append(cmd))
    def run_task(cmd, **kw):
        containers.append(cmd)
        environments.append(sandbox.container_env(cmd))
        return 0

    monkeypatch.setattr(sandbox.subprocess, "call", run_task)
    assert sandbox.launch(["task", "run", "p"], args(tmp_path), "task run") == 0
    assert len(containers) == 1 and not operations, "only the task starts; there is no helper startup or removal"
    task = containers[0]
    assert task[task.index("--network") + 1] == net and "--cap-add" not in task
    if net == "none":
        assert len(proxies) == 1 and proxies[0].network_path is None
        env = environments[0]
        assert env["HTTPS_PROXY"] == "http://127.0.0.1:18080"
        assert "FLUX_SANDBOX_NETWORK" not in env
    else:
        assert not proxies


def test_a_blocked_destination_does_not_abort_the_design_loop(tmp_path):
    from flux_llm import Reply
    from flux_loop import LoopRequest, PromptProblem, TaskSpec, run_loop

    proxy = AllowProxy(str(tmp_path / "proxy.sock"), [], proxies={})
    proxy.start()

    class Proposer:
        calls = 0

        def propose(self, prompt, **kwargs):
            self.calls += 1
            if self.calls == 1:
                network.request(proxy.path, "FLUX-RESOLVE", "denied.test")
            return Reply.of(json.dumps({"artifact": "a candidate after the blocked attempt"}))

    proposer = Proposer()
    problem = PromptProblem(TaskSpec.from_dict({"id": "network-recovery", "statement": "Keep experimenting.",
                                                "flow": {"test": {"check": "true"}}}))
    try:
        result = run_loop(problem, LoopRequest(prototype=False, steps=3, db=""), proposer=proposer, log=lambda _: None)
        assert result.admitted and proposer.calls == 2
        assert any("allowlist" in reason for _, reason in result.refused)
    finally:
        proxy.stop()

"""Does the sandbox actually hold a rogue loop? (D851) A loop's commands are arbitrary -- a
malicious one would write outside its folders, read another user's or the host's files, keep
privileges, or spawn without end. These run real containers through the same `container_argv` the
loop uses and assert each attempt is REFUSED, and that the one thing a loop may do (write its own
`out/` and `workbench/`) still works, so the test proves containment, not a broken runner.

Needs rootless Podman or Docker (`nix develop`). Skipped without one.
"""

from __future__ import annotations

import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

from flux_cli import sandbox

def _engine_runs() -> str:
    """"" when this machine can start the sandbox's container, else why not (D858): a CI runner may
    have the engine installed but no rootless setup for it -- an environment's limit, not a breach."""
    import shutil
    import tempfile

    if not (shutil.which("podman") or shutil.which("docker")):
        return "needs Podman or Docker for a real container"
    try:
        with tempfile.TemporaryDirectory() as d:
            rc, said = _run(Path(d), "echo ok")
    except Exception as exc:  # noqa: BLE001
        return f"the container engine cannot start the sandbox here: {exc!s:.200}"
    return "" if rc == 0 and "ok" in said else f"the container engine cannot start the sandbox here: {said.strip()[-200:]}"


def _args(tmp_path: Path) -> types.SimpleNamespace:
    doc = tmp_path / "loop" / "x.problem.yaml"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text("statement: s\n")
    (doc.parent / "secret-in-loop.txt").write_text("a design file the loop may read\n")
    return types.SimpleNamespace(file=str(doc), db=str(tmp_path / "loop" / "out" / "x.db"),
                                 out=None, json=None, plan=None, replies=None, skill=[], no_sandbox=False)


def _run(tmp_path: Path, script: str, env: dict[str, str] | None = None, proxy_dir: str | None = None,
         timeout: int = 120) -> tuple[int, str]:
    """A `/bin/sh -lc <script>` in the loop's sandbox, as a loop's own command runs (D680). Returns
    (exit code, stdout+stderr). `env` also steers how the container is built (e.g. FLUX_SANDBOX_HOME,
    which `container_argv` reads in this process), so it is set for the argv too, then restored.
    `proxy_dir` set gives the container `--network none` (its only route the proxy, as an allowlist
    run has)."""
    args = _args(tmp_path)
    for d in ("out", "workbench"):
        (tmp_path / "loop" / d).mkdir(parents=True, exist_ok=True)
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    name = f"flux-cntmt-{os.getpid()}-{abs(hash(script)) % 100000}"
    if proxy_dir:
        Path(proxy_dir).mkdir(parents=True, exist_ok=True)
    saved = {k: os.environ.get(k) for k in (env or {})}
    os.environ.update(env or {})
    try:
        cmd = sandbox.container_argv(["/bin/sh", "-lc", script], args, "task run", name, proxy_dir)
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=dict(os.environ),
                           stdin=subprocess.DEVNULL)
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        eng = sandbox.engine_cli(sandbox.engine())
        subprocess.run([*eng, "rm", "-f", name], capture_output=True, timeout=30)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _python(tmp_path: Path, program: str) -> tuple[int, str]:
    """Use the launcher's interpreter directly, preserving the container environment."""
    args = _args(tmp_path)
    name = f"flux-python-{os.getpid()}-{abs(hash(program)) % 100000}"
    cmd = sandbox.container_argv([sys.executable, "-c", program], args, "task run", name, None)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    finally:
        subprocess.run([*sandbox.engine_cli(sandbox.engine()), "rm", "-f", name], capture_output=True, timeout=30)
    return result.returncode, result.stdout + result.stderr


@pytest.fixture(autouse=True, scope="module")
def _engine():
    why = _engine_runs()
    if why:
        pytest.skip(why)


def test_a_loop_may_write_its_own_out_and_workbench(tmp_path):
    """The positive control: the one place a loop writes works, so a refusal below is the sandbox,
    not a container that runs nothing."""
    out = tmp_path / "loop" / "out"
    wb = tmp_path / "loop" / "workbench"
    rc, said = _run(tmp_path, f"echo mine > {out}/design.v && echo ok > {wb}/note.txt && echo WROTE")
    assert rc == 0 and "WROTE" in said, said
    assert (out / "design.v").read_text().strip() == "mine", "the loop's out/ is its own to write"
    assert (wb / "note.txt").exists()


def test_flux_paths_and_tracebacks_use_the_container_checkout(tmp_path, monkeypatch):
    """Actual imports (including cached bytecode) and executable lookup use the source alias."""
    monkeypatch.delenv("FLUX_ROOT", raising=False)
    program = (
        "import os, shutil, traceback\n"
        "import flux_loop.document.gate as gate\n"
        "print('ROOT=' + os.environ['FLUX_ROOT'])\n"
        "print('BIN=' + str(shutil.which('flux')))\n"
        "print('SOURCE=' + gate.__file__)\n"
        "print('CODE=' + gate._gate.__code__.co_filename)\n"
        "print('PYTHONPATH=' + os.environ['PYTHONPATH'])\n"
        "try: gate._gate(42)\n"
        "except Exception: print(traceback.format_exc())\n")
    rc, said = _python(tmp_path, program)
    assert rc == 0, said
    assert "ROOT=/flux" in said and "BIN=/flux/.nix-bin/flux" in said, said
    assert "SOURCE=/flux/core/loop/src/flux_loop/document/gate.py" in said, said
    assert "CODE=/flux/core/loop/src/flux_loop/document/gate.py" in said, said
    assert 'File "/flux/core/loop/src/flux_loop/document/gate.py"' in said, said
    assert str(sandbox._source_root()) not in said, said


def test_flux_wrapper_executes_from_the_canonical_path(tmp_path):
    rc, said = _python(tmp_path,
        "import shutil, subprocess\n"
        "binary = shutil.which('flux')\n"
        "print('BIN=' + str(binary))\n"
        "result = subprocess.run([binary, '--help'], capture_output=True, text=True, timeout=30)\n"
        "print(result.stdout + result.stderr)\n"
        "raise SystemExit(result.returncode)\n")
    assert rc == 0 and "BIN=/flux/.nix-bin/flux" in said, said
    assert "task" in said, said
    assert str(sandbox._source_root()) not in said, said


def test_flux_runtime_is_read_only_while_canonical_loop_outputs_are_writable(tmp_path):
    rc, said = _python(tmp_path,
        "import errno, pathlib\n"
        "for path in ('/flux/flake.nix', '/flux/core/loop/src/flux_loop/document/gate.py'):\n"
        "    assert pathlib.Path(path).read_text(), path\n"
        "    try:\n"
        "        with open(path, 'r+'): pass\n"
        "    except OSError as exc:\n"
        "        assert exc.errno in (errno.EROFS, errno.EACCES, errno.EPERM), exc\n"
        "    else: raise AssertionError('runtime is writable: ' + path)\n"
        "pathlib.Path('/sandbox/loop/out/canonical.txt').write_text('mine')\n"
        "pathlib.Path('/sandbox/loop/workbench/canonical.txt').write_text('mine')\n"
        "print('READ_ONLY_RUNTIME_WRITABLE_OUTPUTS')\n")
    assert rc == 0 and "READ_ONLY_RUNTIME_WRITABLE_OUTPUTS" in said, said
    assert (tmp_path / "loop/out/canonical.txt").read_text() == "mine"
    assert (tmp_path / "loop/workbench/canonical.txt").read_text() == "mine"


def test_cached_host_bytecode_traceback_is_relocated_to_flux(tmp_path, monkeypatch):
    import py_compile

    root = tmp_path / "checkout/flux"
    source = root / "core/loop/src"
    source.mkdir(parents=True)
    module = source / "path_probe.py"
    module.write_text("def fail():\n    raise RuntimeError('bytecode')\n")
    before = module.stat()
    legacy = "/previous-machine/checkout/flux/core/loop/src/path_probe.py"
    py_compile.compile(str(module), dfile=legacy, doraise=True, invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP)
    # Keep the timestamp and size valid for the cache, with a different source exception:
    # seeing "bytecode" proves that the import actually exercised the relocated .pyc.
    module.write_text(module.read_text().replace("bytecode", "source__"))
    os.utime(module, ns=(before.st_atime_ns, before.st_mtime_ns))
    monkeypatch.setattr(sandbox, "_source_root", lambda: root)
    monkeypatch.setenv("FLUX_ROOT", str(root))
    monkeypatch.setenv("PYTHONPATH", str(source))
    monkeypatch.setattr(sys, "path", [str(source), *sys.path])
    rc, said = _python(tmp_path,
        "import path_probe, traceback\n"
        "print('SOURCE=' + path_probe.__file__)\n"
        "print('CODE=' + path_probe.fail.__code__.co_filename)\n"
        "try: path_probe.fail()\n"
        "except RuntimeError: print(traceback.format_exc())\n")
    assert rc == 0, said
    assert "SOURCE=/flux/core/loop/src/path_probe.py" in said, said
    assert "CODE=/flux/core/loop/src/path_probe.py" in said, said
    assert 'File "/flux/core/loop/src/path_probe.py"' in said and "RuntimeError: bytecode" in said, said
    assert legacy not in said and str(root) not in said, said


def test_it_cannot_write_outside_its_folders(tmp_path):
    """Everything but out/, workbench/, the record and the loop's own home is read-only -- the root
    filesystem, the problem's own folder, /etc, any other host path. A write there fails; nothing
    lands on the host."""
    doc_dir = tmp_path / "loop"
    beyond = tmp_path / "beyond-the-loop"
    beyond.mkdir()
    for target in ("/etc/pwned", "/usr/pwned", "/nix/store/pwned", f"{doc_dir}/pwned", f"{beyond}/pwned",
                   f"{doc_dir}/x.problem.yaml"):
        rc, said = _run(tmp_path, f"echo PWNED > {target} && echo WROTE-{target}")
        assert rc != 0 and "WROTE-" not in said, f"wrote {target}: {said}"
    assert not (doc_dir / "pwned").exists() and not (beyond / "pwned").exists()
    assert "s\n" in (doc_dir / "x.problem.yaml").read_text(), "the document is untouched on the host"


def test_the_loops_home_is_its_own_not_the_hosts(tmp_path):
    """A loop may write its HOME (/home/flux): agent logins and caches live there. That home is a
    Flux home of its own -- never the host's shell home, and under `flux serve` a home per user, so
    one loop's writes reach no other user and no host dotfile."""
    host_home = Path.home()
    marker = host_home / ".bashrc-flux-containment-probe"
    if marker.exists():
        marker.unlink()
    mine = tmp_path / "home-a"
    theirs = tmp_path / "home-b"
    rc, said = _run(tmp_path, "echo PWNED > $HOME/.bashrc-flux-containment-probe && echo HOME=$HOME && echo WROTE",
                    env={"FLUX_SANDBOX_HOME": str(mine)})
    assert rc == 0 and "HOME=/home/flux" in said, said
    assert not marker.exists(), "a loop's $HOME write reached the host's real home"
    assert (mine / ".bashrc-flux-containment-probe").exists(), "it lands in the loop's own Flux home"
    # another user's home (a second FLUX_SANDBOX_HOME, as `flux serve` sets) does not see it
    rc, said = _run(tmp_path, "cat $HOME/.bashrc-flux-containment-probe 2>&1; echo DONE",
                    env={"FLUX_SANDBOX_HOME": str(theirs)})
    assert "PWNED" not in said and "DONE" in said, said


def test_it_cannot_read_what_is_not_mounted(tmp_path):
    """A loop sees its own folder and the system tools, not another path on the host: a secret the
    runner never mounted (another user's data, the operator's keys) is simply not there."""
    secret = tmp_path / "operator-keys" / "id_rsa"
    secret.parent.mkdir()
    secret.write_text("PRIVATE-KEY-MATERIAL\n")
    rc, said = _run(tmp_path, f"cat {secret} 2>&1; cat {Path.home()}/.ssh/id_rsa 2>&1; echo DONE")
    assert "PRIVATE-KEY-MATERIAL" not in said, said
    assert "DONE" in said and ("No such file" in said or "not found" in said or "cannot" in said.lower())
    # what the loop IS given, it reads -- its own design files
    rc, said = _run(tmp_path, f"cat {tmp_path}/loop/secret-in-loop.txt")
    assert rc == 0 and "a design file" in said, said


def test_it_has_no_privileges_and_cannot_regain_them(tmp_path):
    """--cap-drop ALL and no-new-privileges: no capabilities, and setuid cannot win any back."""
    rc, said = _run(tmp_path, "grep CapEff /proc/self/status; (chmod u+s /usr/bin/id 2>&1 || true); echo DONE")
    assert "DONE" in said, said
    cap = next((ln.split()[1] for ln in said.splitlines() if ln.startswith("CapEff")), "ffffffff")
    assert int(cap, 16) == 0, f"capabilities held: {cap}"
    # a host device node is not reachable (no --privileged, minimal /dev)
    rc, said = _run(tmp_path, "test -e /dev/sda && echo HAS-DISK || echo no-disk; test -e /dev/kmsg && echo HAS-KMSG || echo no-kmsg")
    assert "HAS-DISK" not in said and "HAS-KMSG" not in said, said


def test_with_an_allowlist_it_has_no_network_of_its_own(tmp_path):
    """An allowlist run gives the container `--network none` (D717): no route of its own at all --
    every connection must go through the proxy, which allows only the admin's hosts (the proxy's
    own filtering is `test_the_proxy_forwards_to_allowed_hosts_and_refuses_the_rest`). Here, with no
    relay running, a direct connection to a public address fails: the container cannot route out."""
    probe = tmp_path / "loop" / "out" / "probe.py"
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text("import socket\n"
                     "try:\n"
                     "    socket.create_connection(('1.1.1.1', 443), 3).close()\n"
                     "    print('REACHED')\n"
                     "except OSError as e:\n"
                     "    print('refused:', type(e).__name__)\n")
    rc, said = _run(tmp_path, f"python3 {probe} 2>&1 || true; echo DONE",
                    proxy_dir=str(tmp_path / "proxy"), timeout=60)
    assert "DONE" in said and "REACHED" not in said, said
    assert "refused:" in said, said


def test_the_docker_socket_is_not_reachable(tmp_path):
    """The engine's own socket would be a full escape (start a privileged container). It is never
    mounted, so a loop cannot reach it."""
    rc, said = _run(tmp_path, "ls -la /var/run/docker.sock /run/docker.sock /run/podman/podman.sock 2>&1; echo DONE")
    assert "DONE" in said and "PWN" not in said
    assert not any("srw" in ln for ln in said.splitlines()), f"a container socket is visible: {said}"


def test_an_admins_read_only_mount_refuses_a_write_and_a_read_write_one_takes_it(tmp_path):
    """D936: a loop's admin mounts, as the web hands them (FLUX_SANDBOX_MOUNTS): read-only is read,
    never written; read-write is written through to the host."""
    import json

    ro, rw = tmp_path / "datasets", tmp_path / "scratch"
    ro.mkdir()
    rw.mkdir()
    (ro / "seen.txt").write_text("FROM-THE-HOST\n")
    mounts = json.dumps([{"host": str(ro), "inside": "/mnt/datasets", "mode": "ro"},
                         {"host": str(rw), "inside": "/mnt/scratch", "mode": "rw"}])
    rc, said = _run(tmp_path, "cat /mnt/datasets/seen.txt; (echo X > /mnt/datasets/w.txt && echo RO-WROTE) 2>&1;"
                              " echo Y > /mnt/scratch/w.txt && echo RW-WROTE; echo DONE",
                    env={"FLUX_SANDBOX_MOUNTS": mounts})
    assert "DONE" in said and "FROM-THE-HOST" in said, said
    assert "RO-WROTE" not in said and not (ro / "w.txt").exists(), f"a read-only mount was written: {said}"
    assert "RW-WROTE" in said and (rw / "w.txt").read_text() == "Y\n", said

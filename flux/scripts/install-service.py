#!/usr/bin/env python3
"""Write Flux's systemd user units; activation is an explicit systemctl command."""

import argparse
import os
from pathlib import Path
import shutil
import subprocess


def quote(value):
    # systemd specifier and environment expansion must leave literal paths/arguments intact.
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$') + '"'


def unit_path(value):
    # Path directives do not use ExecStart's shell-like quoting.
    return str(value).replace('%', '%%')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'systemd/user')
    parser.add_argument('serve_args', nargs=argparse.REMAINDER, help='Flux serve arguments after --')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    nix = shutil.which('nix')
    systemctl = shutil.which('systemctl')
    if not nix or not systemctl:
        parser.error('nix and systemctl must be installed')
    def git_path(name):
        return subprocess.check_output(['git', '-C', str(root), 'rev-parse', '--path-format=absolute', '--git-path', name], text=True).strip()

    serve_args = args.serve_args
    if serve_args[:1] == ['--']:
        serve_args = serve_args[1:]
    if any('\n' in str(v) or '\r' in str(v) for v in [root, nix, systemctl, *serve_args]):
        parser.error('paths and arguments must not contain newlines')
    command = ' '.join(quote(v) for v in [nix, 'develop', '--accept-flake-config', str(root), '--command', 'flux', 'serve', *serve_args])
    units = {
        'flux.service': f'''[Unit]
Description=Flux web server in a fresh Nix development environment
StartLimitIntervalSec=0

[Service]
Type=simple
WorkingDirectory={unit_path(root)}
Environment={quote('FLUX_ROOT=' + str(root))}
Environment=PYTHONUNBUFFERED=1
UnsetEnvironment=LD_LIBRARY_PATH LD_PRELOAD PYTHONPATH
EnvironmentFile=-%h/.config/flux/service.env
ExecStart={command}
Restart=always
RestartSec=10
TimeoutStopSec=30
# Runs have their own sessions and outlive the web server; its next start finds them again.
# Stop only the server on updates. Killing the whole cgroup also SIGTERMs Podman/crun loops.
KillMode=process

[Install]
WantedBy=default.target
''',
        'flux-update.service': f'''[Unit]
Description=Restart Flux after a checkout or Nix environment update

[Service]
Type=oneshot
ExecStartPre=/usr/bin/sleep 3
ExecStart={quote(systemctl)} --user try-restart flux.service
''',
        'flux-update.path': f'''[Unit]
Description=Watch Flux checkout updates and Nix environment changes

[Path]
PathChanged={unit_path(git_path('logs/HEAD'))}
PathChanged={unit_path(git_path('HEAD'))}
PathChanged={unit_path(root / 'flake.nix')}
PathChanged={unit_path(root / 'flake.lock')}
Unit=flux-update.service

[Install]
WantedBy=default.target
''',
    }
    args.output.mkdir(parents=True, exist_ok=True)
    for name, content in units.items():
        (args.output / name).write_text(content)
        print(args.output / name)
    print('Activate: systemctl --user daemon-reload && systemctl --user enable --now flux.service flux-update.path')


if __name__ == '__main__':
    main()

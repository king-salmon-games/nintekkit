#!/usr/bin/env python3
"""SSH/SCP for this project; all command execution is explicitly remote."""
import argparse
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / '.local' / 'switch.json'


def settings():
    if not CONFIG.exists():
        raise SystemExit('Run configure first; machine-specific settings are ignored by Git.')
    cfg = json.loads(CONFIG.read_text(encoding='utf-8'))
    if not cfg['host'] or cfg['host'].startswith('-') or not cfg['user'].replace('_', '').replace('-', '').isalnum():
        raise SystemExit('Invalid SSH destination')
    return cfg


def ssh_args(cfg, scp=False):
    return [
        'scp' if scp else 'ssh', '-i', str(ROOT / cfg['key']),
        '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes',
        '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=yes',
        '-o', 'ForwardX11=no', '-o', 'ClearAllForwardings=yes',
    ]


def remote(cfg, command, payload=None):
    print(f'LOCAL={socket.gethostname()} REMOTE={cfg["user"]}@{cfg["host"]}', flush=True)
    destination = cfg['user'] + '@' + cfg['host']
    preflight = "printf 'REMOTE_MACHINE='; hostname; printf 'REMOTE_ARCH='; uname -m; test \"$(uname -m)\" = aarch64 || exit 70; "
    return subprocess.run(ssh_args(cfg) + [destination, preflight + command], input=payload).returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    conf = sub.add_parser('configure')
    conf.add_argument('--host', required=True)
    conf.add_argument('--user', required=True)
    conf.add_argument('--key', default='.local/ssh/id_ed25519')
    sub.add_parser('check')
    run = sub.add_parser('run')
    run.add_argument('script', type=Path)
    run.add_argument('args', nargs=argparse.REMAINDER)
    execute = sub.add_parser('exec')
    execute.add_argument('command', help='Linux shell command, executed only after AArch64 preflight')
    for action in ('put', 'get'):
        transfer = sub.add_parser(action)
        transfer.add_argument('source')
        transfer.add_argument('destination')
        transfer.add_argument('--recursive', action='store_true')
    args = parser.parse_args()
    if args.action == 'configure':
        if CONFIG.exists():
            raise SystemExit('Configuration already exists; edit ignored .local/switch.json deliberately.')
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(json.dumps({'host': args.host, 'user': args.user, 'key': args.key}, indent=2) + '\n')
        print('Wrote private configuration:', CONFIG)
        return 0
    cfg = settings()
    if args.action == 'check':
        return remote(cfg, 'id; uname -a')
    if args.action == 'exec':
        return remote(cfg, args.command)
    if args.action == 'run':
        script = args.script.read_bytes().replace(b'\r\n', b'\n')
        return remote(cfg, 'bash -s -- ' + shlex.join(args.args), script)
    # Verify the endpoint before transferring. SCP itself never executes local shell text.
    rc = remote(cfg, 'true')
    if rc:
        return rc
    destination = cfg['user'] + '@' + cfg['host'] + ':'
    command = ssh_args(cfg, scp=True) + (['-r'] if args.recursive else [])
    if args.action == 'put':
        if not args.destination.startswith('/'):
            raise SystemExit('SCP remote destination must be an absolute Linux path')
        command += [str(Path(args.source).resolve()), destination + args.destination]
    else:
        if not args.source.startswith('/'):
            raise SystemExit('SCP remote source must be an absolute Linux path')
        command += [destination + args.source, str(Path(args.destination).resolve())]
    return subprocess.run(command).returncode


if __name__ == '__main__':
    sys.exit(main())

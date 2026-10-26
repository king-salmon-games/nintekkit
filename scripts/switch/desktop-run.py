#!/usr/bin/env python3
"""Execute in the existing local user's graphical session, never a forwarded X server."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--display', help='Select an already discovered local X display if multiple exist')
parser.add_argument('command', nargs=argparse.REMAINDER)
args = parser.parse_args()
if args.command and args.command[0] == '--':
    args.command.pop(0)
if not args.command:
    parser.error('a command is required')
allowed = {'DISPLAY', 'XAUTHORITY', 'WAYLAND_DISPLAY', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS', 'XDG_SESSION_TYPE', 'XDG_CURRENT_DESKTOP'}
sessions = {}
for proc in Path('/proc').iterdir():
    try:
        if not proc.name.isdigit() or proc.stat().st_uid != os.getuid():
            continue
        name = (proc / 'comm').read_text().strip()
        if name not in {'gnome-shell', 'gnome-session-b', 'xfce4-session', 'mate-session', 'cinnamon', 'plasmashell', 'lxqt-session', 'lxsession'}:
            continue
        env = {}
        for entry in (proc / 'environ').read_bytes().split(b'\0'):
            key, sep, value = entry.partition(b'=')
            key = key.decode(errors='replace')
            if sep and key in allowed:
                env[key] = value.decode(errors='replace')
        display = env.get('DISPLAY', '')
        if display.startswith(':') and (not args.display or args.display == display):
            sessions[display] = env
    except (OSError, ValueError):
        pass
if len(sessions) != 1:
    raise SystemExit('Expected one actual local graphical session; found ' + repr(list(sessions)) + '. Select with --display; do not guess DISPLAY or use SSH X forwarding.')
display, session = next(iter(sessions.items()))
env = os.environ.copy()
for key in allowed:
    env.pop(key, None)
env.update(session)
for key in ('LIBGL_ALWAYS_SOFTWARE', 'MESA_GL_VERSION_OVERRIDE', 'MESA_GLSL_VERSION_OVERRIDE'):
    if env.get(key):
        raise SystemExit('Graphics override present: ' + key + '; inspect before launch')
print('ACTUAL_LOCAL_DISPLAY=' + display, flush=True)
sys.exit(subprocess.run(args.command, env=env).returncode)

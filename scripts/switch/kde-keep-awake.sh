#!/usr/bin/env bash
# Persistent, per-user KDE automatic-suspend inhibition; no sudo or system edits.
set -Eeuo pipefail
export LC_ALL=C
[[ $(uname -m) == aarch64 && $EUID -ne 0 ]] || { echo 'Run as the Switch desktop user'; exit 70; }
action=${1:-status}
unit=nintekkit-desktop-awake.service
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
destination="$HOME/.config/systemd/user/$unit"
state="$HOME/.local/state/nintekkit/desktop-awake"

status() {
    systemctl --user is-enabled "$unit" || return
    systemctl --user is-active "$unit" || return
    local main_pid
    main_pid=$(systemctl --user show "$unit" -p MainPID --value) || return
    [[ $main_pid =~ ^[1-9][0-9]*$ ]] || return 1
    python3 - "$main_pid" <<'PY'
import os, sys
import dbus
bus = dbus.bus.BusConnection('unix:path=/run/user/%d/bus' % os.getuid())
policy = dbus.Interface(bus.get_object('org.kde.Solid.PowerManagement',
                        '/org/kde/Solid/PowerManagement/PolicyAgent'),
                        'org.kde.Solid.PowerManagement.PolicyAgent')
application = 'Nintekkit setup (%s)' % sys.argv[1]
reason = 'Keep the existing KDE desktop awake during client setup'
if str(dict(policy.ListInhibitions()).get(application, '')) != reason:
    raise SystemExit('This service has not yet applied its KDE inhibitor')
print('VERIFIED_KDE_INHIBITOR=' + application)
PY
}

case "$action" in
    status) status; exit ;;
    stop) systemctl --user disable --now "$unit"; echo 'Desktop inhibitor stopped and startup disabled'; exit ;;
    install) ;;
    *) echo 'Usage: kde-keep-awake.sh install|status|stop'; exit 64 ;;
esac
test -f "$script_dir/kde-awake.py"
umask 077
mkdir -p "$HOME/.config/systemd/user" "$state"
journal="$state/$(date -u +%Y%m%dT%H%M%SZ)-$$"
mkdir "$journal"
systemctl --user show "$unit" -p LoadState -p ActiveState -p UnitFileState >"$journal/before.txt" || true
python3 - "$script_dir/kde-awake.py" "$journal/desired.service" "$state/service.log" <<'PY'
from pathlib import Path
import sys
helper, output, log = sys.argv[1:]
def quote(value):
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'
Path(output).write_text('[Unit]\nDescription=Prevent automatic sleep in the Nintekkit KDE desktop\n'
                       '[Service]\nType=exec\nExecStart=/usr/bin/python3 ' + quote(helper) + '\n'
                       'Restart=on-failure\nRestartSec=5\nStandardOutput=append:' + quote(log) + '\n'
                       'StandardError=append:' + quote(log) + '\n[Install]\nWantedBy=default.target\n')
PY
if [[ -e $destination || -L $destination ]]; then
    cp -a "$destination" "$journal/previous-unit"
    cmp -s "$destination" "$journal/desired.service" || { echo "Different existing unit preserved: $journal"; exit 2; }
else
    install -m 600 "$journal/desired.service" "$destination"
fi
systemctl --user daemon-reload
systemctl --user enable --now "$unit"
for attempt in {1..30}; do
    if status >"$journal/after.txt" 2>&1; then
        cat "$journal/after.txt"
        printf 'Starts with user login; audit: %s\nUndo: bash %q stop\n' "$journal" "${BASH_SOURCE[0]}"
        exit
    fi
    sleep 0.5
done
cat "$journal/after.txt"
echo 'Desktop inhibitor verification failed; inspect the service log.' >&2
exit 1

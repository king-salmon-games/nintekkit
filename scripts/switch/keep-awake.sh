#!/usr/bin/env bash
# A named, reversible inhibitor. No graphics/kernel/firmware or desktop policy edits.
set -Eeuo pipefail
export LC_ALL=C
unit=nintekkit-awake.service
destination=/etc/systemd/system/nintekkit-awake.service
source_unit="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/nintekkit-awake.service"
action=${1:-status}
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
case "$action" in install|status|stop) ;; *) die 'Usage: keep-awake.sh install|status|stop';; esac
printf 'HOST=%s ARCH=%s USER=%s\n' "$(hostname)" "$(uname -m)" "$(id -un)"
[[ $(uname -m) == aarch64 ]] || die 'Run on the Switch, not the Windows workstation.'
command -v systemctl >/dev/null || die 'systemctl is unavailable'
command -v systemd-inhibit >/dev/null || die 'systemd-inhibit is unavailable'

status() {
    systemctl is-enabled "$unit" || return
    systemctl is-active "$unit" || return
    local locks main_pid
    main_pid=$(systemctl show "$unit" -p MainPID --value) || return
    [[ $main_pid =~ ^[1-9][0-9]*$ ]] || return 1
    locks=$(systemd-inhibit --list --no-pager --no-legend) || return
    printf '%s\n' "$locks"
    # logind may canonicalize idle:sleep as sleep:idle. Verify both lock names.
    awk -v pid="$main_pid" '$1=="Nintekkit" && $4==pid && ($6=="idle:sleep" || $6=="sleep:idle") && $NF=="block" {found=1} END {exit !found}' <<<"$locks"
}

if [[ $action == status ]]; then status; exit; fi
[[ $EUID -eq 0 ]] || die 'Run install/stop with sudo in an authenticated Switch terminal.'
[[ -f $source_unit ]] || die 'Keep nintekkit-awake.service beside this script.'
state=/var/lib/nintekkit/power
umask 077
mkdir -p -- "$state"
journal="$state/$(date -u +%Y%m%dT%H%M%SZ)-$$"
mkdir -- "$journal"
systemctl show "$unit" -p LoadState -p ActiveState -p UnitFileState >"$journal/before.txt" || true
if [[ -e $destination || -L $destination ]]; then
    cp -a -- "$destination" "$journal/previous-unit"
    cmp -s -- "$source_unit" "$destination" || die "Different existing unit preserved; inspect $journal before replacing it."
elif [[ $action == stop ]]; then
    die 'No Nintekkit unit installed here; no change made.'
else
    load_state=$(systemctl show "$unit" -p LoadState --value 2>/dev/null || true)
    [[ -z $load_state || $load_state == not-found ]] || die 'An existing unit with this name is loaded elsewhere; preserve it for inspection.'
    temporary=$(mktemp /etc/systemd/system/.nintekkit-awake.XXXXXXXX)
    trap 'if [[ -n ${temporary:-} && -f $temporary ]]; then rm -f -- "$temporary"; fi' EXIT
    install -m 644 -- "$source_unit" "$temporary"
    mv -n -- "$temporary" "$destination"
    [[ ! -e $temporary ]] || die 'Destination appeared concurrently; existing file preserved.'
    temporary=
fi
if [[ $action == stop ]]; then
    systemctl disable --now "$unit"
    printf 'Nintekkit sleep inhibitor disabled. Unit retained for re-enabling. Audit: %s\n' "$journal"
    exit
fi
systemctl daemon-reload
systemctl enable --now "$unit"
for attempt in {1..25}; do
    if status >"$journal/after.txt" 2>&1; then
        cat "$journal/after.txt"
        printf 'Persistent Nintekkit idle/sleep inhibitor verified. Audit: %s\n' "$journal"
        printf 'Undo: sudo bash %q stop\n' "${BASH_SOURCE[0]}"
        exit
    fi
    sleep 0.2
done
cat "$journal/after.txt"
die "Service/inhibitor verification failed; inspect systemctl status $unit. Installation is not confirmed."

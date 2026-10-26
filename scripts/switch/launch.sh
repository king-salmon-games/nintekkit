#!/usr/bin/env bash
set -eu
controls=false
if test "$#" -gt 0; then
    test "$#" -eq 1 && test "$1" = --controls || { echo 'Usage: launch.sh [--controls]'; exit 64; }
    controls=true
fi
test "$(uname -m)" = aarch64 || { echo 'Run on the Switch'; exit 70; }
root="${NINTEKKIT_ROOT:-$HOME/.local/share/nintekkit}"
instance="$root/prism/instances/Nintekkit-1.2.4"
test -f "$instance/mmc-pack.json" || { echo "Instance missing: $instance"; exit 1; }
case "${DISPLAY:-}" in :*) ;; *) echo 'Launch in the existing local Switch desktop, or through desktop-run.py'; exit 1;; esac
command -v prismlauncher >/dev/null || { echo 'Prism is not installed'; exit 1; }
heap=$(awk -F= '$1=="MaxMemAlloc" {gsub(/\r/,"",$2); print $2}' "$instance/instance.cfg")
case "$heap" in ''|*[!0-9]*) echo 'Expected numeric MaxMemAlloc in isolated instance.cfg'; exit 1;; esac
available=$(awk '/MemAvailable:/ {print int($2/1024)}' /proc/meminfo)
printf 'LOCAL_SWITCH=%s DISPLAY=%s MemAvailable=%sMiB heap=%sMiB\n' "$(hostname)" "$DISPLAY" "$available" "$heap"
if test "$available" -lt "$((heap + 600))"; then
    echo 'Insufficient current available RAM for heap plus a provisional 600MiB native/desktop margin. Close unused apps or measure a smaller heap; no process was killed.'
    exit 2
fi
# Both routes watch actual Java for borderless presentation and scoped awake leases.
# Prism owns authentication; only the explicit controls route starts the mapper.
test -f "$root/bin/play_with_controls.py" || { echo 'Game lifecycle helper is not installed'; exit 1; }
if "$controls"; then
    exec python3 "$root/bin/play_with_controls.py" --root "$root"
fi
exec python3 "$root/bin/play_with_controls.py" --root "$root" --no-controls

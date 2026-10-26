#!/usr/bin/env bash
# Run THROUGH desktop-run.py; this queries the actual Switch display, not SSH X forwarding.
set -eu
printf 'HOST=%s DISPLAY=%s SESSION=%s\n' "$(hostname)" "${DISPLAY:-unset}" "${XDG_SESSION_TYPE:-unset}"
case "${DISPLAY:-}" in :*) ;; *) echo 'A local display is required'; exit 1;; esac
command -v glxinfo >/dev/null || { echo 'glxinfo missing (mesa-utils)'; exit 1; }
renderer=$(glxinfo -B)
printf '%s\n' "$renderer"
if printf '%s\n' "$renderer" | grep -Eiq 'llvmpipe|softpipe|swrast|software rasterizer'; then
    echo 'FAIL: software renderer'; exit 2
fi
printf '%s\n' "$renderer" | grep -q 'direct rendering: Yes' || { echo 'FAIL: direct rendering not confirmed'; exit 2; }
printf '%s\n' "$renderer" | grep -Eiq 'OpenGL renderer string:.*(NVIDIA|Tegra)' || { echo 'FAIL: expected Switch NVIDIA/Tegra renderer not confirmed'; exit 2; }
xrandr --current
xinput list || true
pactl info || true
pactl list short sinks || true
printf '\nHardware GL confirmed on this display; the real game must still verify its own renderer and performance.\n'

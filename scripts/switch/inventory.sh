#!/usr/bin/env bash
# Read-only inventory, intended for the Switch via scripts/switch.py run.
set -u
printf 'INVENTORY_UTC=%s\n' "$(date -u +%FT%TZ)"
printf 'HOST=%s ARCH=%s USER=%s\n' "$(hostname)" "$(uname -m)" "$(id -un)"
test "$(uname -m)" = aarch64 || { echo 'Refusing non-AArch64 host'; exit 70; }
uname -a
cat /etc/os-release
printf '\nHARDWARE\n'
for f in /proc/device-tree/model /proc/device-tree/compatible; do
    test ! -r "$f" || { printf '%s: ' "$f"; tr '\0' '\n' < "$f"; }
done
lscpu
free -m
cat /proc/swaps
df -hT "$HOME" / /tmp
lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINTS
printf '\nCPU_GPU_THERMAL_READONLY\n'
for f in /sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq /sys/class/devfreq/*/cur_freq /sys/class/thermal/thermal_zone*/type /sys/class/thermal/thermal_zone*/temp; do
    test ! -r "$f" || { printf '%s=' "$f"; cat "$f"; }
done
for c in tegrastats glxinfo xrandr xinput pactl aplay jstest antimicrox joycond prismlauncher java javac; do
    command -v "$c" || true
done
printf '\nPACKAGES\n'
dpkg-query -W -f='${binary:Package}\t${Version}\t${Architecture}\n' 2>/dev/null | awk '$1 ~ /nvidia|l4t|tegra|mesa|libgl|openjdk|prismlauncher|joycond|antimicro|xserver|libinput|openal|jinput|lwjgl/'
printf '\nDESKTOP_SESSIONS\n'
loginctl list-sessions --no-legend || true
for session in $(loginctl list-sessions --no-legend 2>/dev/null | awk -v u="$(id -un)" '$3==u {print $1}'); do
    loginctl show-session "$session" -p Name -p Type -p Remote -p Active -p State -p Display -p Desktop -p Leader || true
done
printf '\nDESKTOP_AND_INPUT_PROCESSES (names only)\n'
ps -u "$(id -u)" -o pid=,comm= | awk '$2 ~ /gnome|xfce|mate|cinnamon|Xorg|Xwayland|wayland|joycon|antimicro|steam|prism|java|pipewire|pulse|vnc|sunshine|obs|ffmpeg/'
printf '\nJAVA_RUNTIMES\n'
for j in /usr/lib/jvm/*/bin/java "$HOME"/.local/share/PrismLauncher/java/*/bin/java; do
    test ! -x "$j" || { printf '%s\n' "$j"; "$j" -version 2>&1; file "$(readlink -f "$j")"; }
done
printf '\nLAUNCHER_AND_INSTANCE_PATHS (no account files)\n'
for d in "$HOME/pi-apps" "$HOME/.local/share/PrismLauncher" "$HOME/.config/PrismLauncher" "$HOME/.var/app/org.prismlauncher.PrismLauncher" "$HOME/.minecraft" "$HOME/.technic" "$HOME/.local/share/multimc"; do
    if test -d "$d"; then
        du -sh "$d" 2>/dev/null
        find "$d" -maxdepth 5 -type f \( -name mmc-pack.json -o -name instance.cfg -o -name options.txt -o -name version.json \) -print 2>/dev/null | head -100
    fi
done
printf '\nINPUT_DEVICES\n'
cat /proc/bus/input/devices
ls -l /dev/input/by-id /dev/input/js* /dev/uinput 2>/dev/null || true
printf '\nPRIVILEGE_AVAILABILITY\n'
if sudo -n true 2>/dev/null; then echo 'sudo_without_prompt=yes'; else echo 'sudo_without_prompt=no'; fi

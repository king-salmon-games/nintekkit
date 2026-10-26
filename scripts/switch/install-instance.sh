#!/usr/bin/env bash
# Install an already prepared instance and scripts under the user's own data directory.
set -eu
test "$(uname -m)" = aarch64 || { echo 'Run on the Switch'; exit 70; }
test "$#" -eq 2 || { echo 'Usage: install-instance.sh /absolute/staged/instance /absolute/staged/scripts'; exit 64; }
source_instance=$(realpath "$1")
source_scripts=$(realpath "$2")
root="$HOME/.local/share/nintekkit"
target="$root/prism/instances/Nintekkit-1.2.4"
test -f "$source_instance/mmc-pack.json"
for f in launch.sh desktop-run.py display-check.sh inventory.sh verify-natives.sh ../verify_instance.py ../make_controls.py ../controller/nintekkit_controller.py ../controller/play_with_controls.py ../controller/enable_controls.py ../controller/screen_awake.py ../controller/window_presentation.py ../controller/on_screen_keyboard.py ../controller/onboard_stacking.py ../diagnostics/configure_server.py; do
    test -f "$source_scripts/$f" || { echo "Incomplete script transfer: $f"; exit 2; }
done
controller="$source_scripts/../controller/nintekkit_controller.py"
controller_helper="$source_scripts/../controller/play_with_controls.py"
controller_entry="$source_scripts/../controller/enable_controls.py"
screen_awake="$source_scripts/../controller/screen_awake.py"
window_presentation="$source_scripts/../controller/window_presentation.py"
server_bookmark="$source_scripts/../diagnostics/configure_server.py"
keyboard_helper="$source_scripts/../controller/on_screen_keyboard.py"
keyboard_stacking="$source_scripts/../controller/onboard_stacking.py"
# Parse the transferred standalone mapper before copying any application files.
# No imports, X connections, device floating or input events are performed here.
python3 - "$controller" "$controller_helper" "$controller_entry" "$screen_awake" "$window_presentation" "$server_bookmark" "$keyboard_helper" "$keyboard_stacking" <<'PY'
from pathlib import Path
import sys
for name in sys.argv[1:]:
    path = Path(name)
    compile(path.read_text(encoding='utf-8'), str(path), 'exec')
PY
verifier="$source_scripts/../verify_instance.py"
lock="$source_scripts/../../config/client-1.2.4.lock.json"
test -f "$verifier" || { echo 'Missing transferred scripts/verify_instance.py'; exit 2; }
test -f "$lock" || { echo 'Missing transferred config/client-1.2.4.lock.json'; exit 2; }
# Validate the fresh source before writing application files. Never trust a partial SCP.
python3 "$verifier" "$source_instance" --lock "$lock"
test ! -e "$target" || { echo 'Existing instance preserved. Back it up and choose an explicit migration before replacing it.'; exit 2; }
mkdir -p "$root/prism/instances" "$root/bin" "$root/backups" "$root/config" "$HOME/.local/share/applications"
chmod 700 "$root" "$root/backups"
stamp=$(date -u +%Y%m%dT%H%M%S)
temporary=$(mktemp -d "$root/prism/instances/.Nintekkit-1.2.4.XXXXXXXX")
trap 'if test -n "${temporary:-}" && test -d "$temporary"; then rm -rf -- "$temporary"; fi' EXIT
cp -a "$source_instance/." "$temporary/"
python3 "$verifier" "$temporary" --lock "$lock"
mv -T -n -- "$temporary" "$target"
test ! -d "$temporary" || { echo 'Target appeared during installation; preserving it'; exit 2; }
temporary=
for f in launch.sh desktop-run.py display-check.sh inventory.sh verify-natives.sh; do
    test ! -e "$root/bin/$f" || cp -p "$root/bin/$f" "$root/backups/$f.$stamp"
    install -m 700 "$source_scripts/$f" "$root/bin/$f"
done
for f in verify_instance.py make_controls.py; do
    test ! -e "$root/bin/$f" || cp -p "$root/bin/$f" "$root/backups/$f.$stamp"
    install -m 700 "$source_scripts/../$f" "$root/bin/$f"
done
test ! -e "$root/bin/nintekkit_controller.py" || cp -p "$root/bin/nintekkit_controller.py" "$root/backups/nintekkit_controller.py.$stamp"
install -m 700 "$controller" "$root/bin/nintekkit_controller.py"
cmp -s "$controller" "$root/bin/nintekkit_controller.py" || { echo 'Controller script copy verification failed'; exit 2; }
test ! -e "$root/bin/play_with_controls.py" || cp -p "$root/bin/play_with_controls.py" "$root/backups/play_with_controls.py.$stamp"
install -m 700 "$controller_helper" "$root/bin/play_with_controls.py"
cmp -s "$controller_helper" "$root/bin/play_with_controls.py" || { echo 'Controller helper copy verification failed'; exit 2; }
test ! -e "$root/bin/enable_controls.py" || cp -p "$root/bin/enable_controls.py" "$root/backups/enable_controls.py.$stamp"
install -m 700 "$controller_entry" "$root/bin/enable_controls.py"
cmp -s "$controller_entry" "$root/bin/enable_controls.py" || { echo 'Controller entry helper copy verification failed'; exit 2; }
test ! -e "$root/bin/screen_awake.py" || cp -p "$root/bin/screen_awake.py" "$root/backups/screen_awake.py.$stamp"
install -m 700 "$screen_awake" "$root/bin/screen_awake.py"
cmp -s "$screen_awake" "$root/bin/screen_awake.py" || { echo 'Screen-lock inhibitor helper copy verification failed'; exit 2; }
test ! -e "$root/bin/window_presentation.py" || cp -p "$root/bin/window_presentation.py" "$root/backups/window_presentation.py.$stamp"
install -m 700 "$window_presentation" "$root/bin/window_presentation.py"
cmp -s "$window_presentation" "$root/bin/window_presentation.py" || { echo 'Window presentation helper copy verification failed'; exit 2; }
test ! -e "$root/bin/configure_server.py" || cp -p "$root/bin/configure_server.py" "$root/backups/configure_server.py.$stamp"
install -m 700 "$server_bookmark" "$root/bin/configure_server.py"
cmp -s "$server_bookmark" "$root/bin/configure_server.py" || { echo 'Private server bookmark helper copy verification failed'; exit 2; }
test ! -e "$root/bin/on_screen_keyboard.py" || cp -p "$root/bin/on_screen_keyboard.py" "$root/backups/on_screen_keyboard.py.$stamp"
install -m 700 "$keyboard_helper" "$root/bin/on_screen_keyboard.py"
cmp -s "$keyboard_helper" "$root/bin/on_screen_keyboard.py" || { echo 'Onboard worker helper copy verification failed'; exit 2; }
test ! -e "$root/bin/onboard_stacking.py" || cp -p "$root/bin/onboard_stacking.py" "$root/backups/onboard_stacking.py.$stamp"
install -m 700 "$keyboard_stacking" "$root/bin/onboard_stacking.py"
cmp -s "$keyboard_stacking" "$root/bin/onboard_stacking.py" || { echo 'Onboard stacking helper copy verification failed'; exit 2; }
test ! -e "$root/config/client-1.2.4.lock.json" || cp -p "$root/config/client-1.2.4.lock.json" "$root/backups/client-1.2.4.lock.json.$stamp"
install -m 600 "$lock" "$root/config/client-1.2.4.lock.json"
desktop="$HOME/.local/share/applications/nintekkit.desktop"
test ! -e "$desktop" || cp -p "$desktop" "$root/backups/nintekkit.desktop.$stamp"
# Quote the absolute home path for Desktop Entry Exec syntax.
python3 - "$desktop" "$root/bin/launch.sh" <<'PY'
from pathlib import Path
import sys
path, launcher = sys.argv[1:]
quoted = '"' + launcher.replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$') + '"'
Path(path).write_text('[Desktop Entry]\nType=Application\nName=Nintekkit — Tekkit 2\nComment=Native Switch client, isolated Tekkit 2 1.2.4\nExec=' + quoted + '\nIcon=applications-games\nTerminal=false\nCategories=Game;\n')
PY
printf 'Installed candidate instance at %s\n' "$target"
printf 'Sign in using Prism locally: prismlauncher --dir "%s/prism"\n' "$root"
echo 'Controller mapper installed but inactive. Inspect it read-only, then enable manually only for the actual focused Java game PID.'
echo 'Installation alone does not validate renderer, server, controls or FPS.'

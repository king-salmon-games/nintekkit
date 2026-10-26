#!/usr/bin/env bash
# Install audited application packages only. Never change the L4T graphics stack.
set -Eeuo pipefail
export LC_ALL=C
umask 077

PRISM_VERSION=9.4-9
PRISM_SHA256=80f8f949d06c7ee6d633d5fc115786824808c11594e634cab36a6bcae4d15588
PRISM_URL="https://github.com/Pi-Apps-Coders/files/releases/download/large-files/prismlauncher_${PRISM_VERSION}_arm64.deb"
provided_deb=
launcher=${PRISM_LAUNCHER:-}
launcher_binary=${PRISM_BINARY:-}
plan_only=0

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
usage() {
    cat <<'EOF'
Usage: install-prism.sh [--plan-only] [--deb /path/to/pinned.deb]
                        [--launcher /path/to/prismlauncher]
                        [--launcher-binary /path/to/real/ARM64/ELF]

Run as the desktop user on the Switch. Reuses an existing ARM64 Prism Launcher.
Downloads the pinned Pi-Apps package only if no launcher is installed. Saves the
APT plan and package state under ~/.local/state/nintekkit/install/.
No apt update/upgrade, kernel/driver changes, or Prism configuration edits.
--plan-only downloads/verifies the package and simulates APT without installing.
The simulation needs no sudo; only the actual install requires authenticated sudo.
EOF
}
while (($#)); do
    case "$1" in
        --plan-only) plan_only=1; shift ;;
        --deb|--launcher|--launcher-binary)
            (($# >= 2)) || die "Missing value for $1"
            case "$1" in
                --deb) provided_deb=$2 ;;
                --launcher) launcher=$2 ;;
                --launcher-binary) launcher_binary=$2 ;;
            esac
            shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown argument: $1" ;;
    esac
done

printf 'Machine: %s; kernel architecture: %s; user: %s\n' "$(hostname)" "$(uname -m)" "$(id -un)"
[[ $(uname -m) == aarch64 ]] || die 'Run this script on the ARM64 Switch, not the Windows workstation.'
[[ $EUID -ne 0 ]] || die 'Run as the Switch desktop user, without sudo before the script name.'
for tool in apt-get dpkg dpkg-query dpkg-deb od sha256sum readlink awk; do
    command -v "$tool" >/dev/null || die "Required existing tool is unavailable: $tool"
done
[[ $(dpkg --print-architecture) == arm64 ]] || die 'Expected native arm64 Debian userspace.'
audit=$(dpkg --audit)
[[ -z $audit ]] || die "dpkg reports unfinished package work; resolve it before setup: $audit"

# Bootstrap architecture detection needs only coreutils, before file/binutils install.
is_arm64_elf() {
    local header
    [[ -f $1 ]] || return 1
    header=$(od -An -tx1 -N20 -- "$1" | tr -d ' \n')
    [[ ${header:0:12} == 7f454c460201 && ${header:36:4} == b700 ]]
}

if [[ -z $launcher ]]; then launcher=$(command -v prismlauncher || true); fi
if [[ -n $launcher ]]; then
    [[ -x $launcher ]] || die "Existing launcher is not executable: $launcher"
    launcher=$(readlink -f -- "$launcher")
    if [[ -z $launcher_binary ]] && is_arm64_elf "$launcher"; then
        launcher_binary=$launcher
    fi
    if [[ -z $launcher_binary ]]; then
        # Pi-Apps uses a shell wrapper. Only accept its known packaged binary.
        package_arch=$(dpkg-query -W -f='${Status} ${Architecture}' prismlauncher 2>/dev/null || true)
        if [[ $package_arch == 'install ok installed arm64' ]]; then
            for candidate in /usr/share/PrismLauncher/bin/prismlauncher /usr/lib/prismlauncher/prismlauncher; do
                if is_arm64_elf "$candidate"; then launcher_binary=$candidate; break; fi
            done
        fi
    fi
    [[ -n $launcher_binary ]] || die 'Cannot verify the existing launcher wrapper. Supply --launcher-binary with its actual ARM64 executable; setup will not overwrite it.'
    launcher_binary=$(readlink -f -- "$launcher_binary")
    is_arm64_elf "$launcher_binary" || die "Existing Prism binary is not little-endian ELF64 AArch64: $launcher_binary"
    printf 'Reusing launcher: %s\nVerified ARM64 binary: %s\n' "$launcher" "$launcher_binary"
else
    [[ -z $launcher_binary ]] || die '--launcher-binary requires --launcher.'
    if dpkg-query -W -f='${Status}' prismlauncher 2>/dev/null | grep -q '^install ok installed$'; then
        die 'A Prism package exists but no launcher was found. Inspect that installation before installing another.'
    fi
fi

state_base=${XDG_STATE_HOME:-$HOME/.local/state}/nintekkit/install
mkdir -p -- "$state_base"
journal="$state_base/$(date -u +%Y%m%dT%H%M%SZ)-$$"
mkdir -- "$journal"
printf 'Audit directory: %s\n' "$journal"
dpkg-query -W -f='${binary:Package}\t${Version}\t${Architecture}\t${Status}\n' >"$journal/packages-before.tsv"
printf '%s\n' "host=$(hostname)" "uname=$(uname -r)" "launcher=$launcher" "launcher_binary=$launcher_binary" >"$journal/context.txt"

packages=(openjdk-8-jre mesa-utils libxxf86vm1 libopenal1 libx11-6 libxext6 libxcursor1 libxrandr2 libxi6 file binutils)
if [[ -z $launcher ]]; then
    if [[ -n $provided_deb ]]; then
        deb=$(readlink -f -- "$provided_deb")
        [[ -f $deb ]] || die "Package does not exist: $provided_deb"
    else
        cache=${XDG_CACHE_HOME:-$HOME/.cache}/nintekkit/downloads
        mkdir -p -- "$cache"
        deb="$cache/prismlauncher_${PRISM_VERSION}_arm64.deb"
        if [[ ! -f $deb ]]; then
            temporary=$(mktemp "$cache/.prism-download.XXXXXXXX")
            trap 'if [[ -n ${temporary:-} ]]; then rm -f -- "$temporary"; fi' EXIT
            if command -v curl >/dev/null; then
                curl --fail --location --retry 2 --proto '=https' --tlsv1.2 "$PRISM_URL" --output "$temporary"
            elif command -v wget >/dev/null; then
                wget --https-only --output-document="$temporary" "$PRISM_URL"
            else
                die 'curl or wget is required to download Prism. Supply the audited package with --deb instead.'
            fi
            printf '%s  %s\n' "$PRISM_SHA256" "$temporary" | sha256sum --check --status || die 'Prism download SHA256 mismatch; no package was installed.'
            # A lock-free competing setup may have populated the cache. Do not overwrite it.
            [[ ! -e $deb ]] || die "Cache was populated concurrently; rerun to verify it: $deb"
            mv -n -- "$temporary" "$deb"
            temporary=
        fi
    fi
    printf '%s  %s\n' "$PRISM_SHA256" "$deb" | sha256sum --check --status || die "SHA256 mismatch; preserving untrusted file without installing: $deb"
    [[ $(dpkg-deb -f "$deb" Package) == prismlauncher ]] || die 'Unexpected package name.'
    [[ $(dpkg-deb -f "$deb" Architecture) == arm64 ]] || die 'Unexpected package architecture.'
    [[ $(dpkg-deb -f "$deb" Version) == "$PRISM_VERSION" ]] || die 'Unexpected package version.'
    dpkg-deb --info "$deb" >"$journal/prism-package.txt"
    printf '%s  %s\n' "$PRISM_SHA256" "$deb" >"$journal/prism-sha256.txt"
    for old_file in /usr/share/applications/org.prismlauncher.PrismLauncher.desktop /usr/bin/prismlauncher; do
        if [[ -e $old_file || -L $old_file ]]; then
            mkdir -p -- "$journal/existing-files"
            cp -aL -- "$old_file" "$journal/existing-files/"
        fi
    done
    packages+=("$deb")
fi

# Restrict package management to the requested userland components and dependencies.
apt_options=(--no-install-recommends --no-remove --no-upgrade -o APT::Install-Suggests=false -o Dpkg::Options::=--force-confold)
# APT simulation is read-only and can run as the desktop user without sudo.
# It disables package-manager locking: rerun/review before installing if other
# package-management work or index refreshes occur in the meantime.
if ! env LC_ALL=C DEBIAN_FRONTEND=noninteractive apt-get --simulate "${apt_options[@]}" install "${packages[@]}" >"$journal/apt-plan.txt" 2>&1; then
    cat "$journal/apt-plan.txt"
    die 'APT simulation failed; nothing installed. Check the saved plan and package availability.'
fi
cat "$journal/apt-plan.txt"

protected_package() {
    local package=${1%%:*}
    case "$package" in
        mesa-utils|mesa-utils-bin) return 1 ;;
        linux-*|nvidia*|libnvidia*|l4t*|libl4t*|switchroot*|tegra*|libtegra*|xserver-xorg*|mesa-*|*-mesa*|libgbm*|libdrm*|libglvnd*|libglx*|libegl*|libopengl*|libgl1|libgles*) return 0 ;;
        *) return 1 ;;
    esac
}
while read -r action package remainder; do
    case "$action" in
        Remv) die "APT would remove $package. Plan rejected; nothing installed." ;;
        Inst|Conf)
            if protected_package "$package"; then
                die "APT would change protected kernel/graphics package $package. Plan rejected; preserve the working L4T stack."
            fi ;;
    esac
done <"$journal/apt-plan.txt"
if grep -q 'The following packages will be REMOVED:' "$journal/apt-plan.txt"; then
    die 'APT proposed package removal. Plan rejected; nothing installed.'
fi
if ((plan_only)); then
    printf 'Plan passed the package guard. --plan-only: no packages installed.\n'
    exit 0
fi

command -v sudo >/dev/null || die 'The guarded plan passed, but sudo is unavailable; no packages installed.'
sudo -n true || die 'The guarded plan passed, but sudo authentication is unavailable for this session; no packages installed. Run sudo -v and this script in the SAME authenticated Switch terminal; a fresh SSH session may not share its sudo timestamp. The script never prompts for passwords.'
printf 'Installing the reviewed application transaction; existing config files are retained.\n'
if ! sudo -n env LC_ALL=C DEBIAN_FRONTEND=noninteractive apt-get --assume-yes "${apt_options[@]}" install "${packages[@]}" >"$journal/apt-install.txt" 2>&1; then
    cat "$journal/apt-install.txt"
    die "APT reported failure; some packages may have changed. Inspect $journal/apt-install.txt and dpkg --audit."
fi
cat "$journal/apt-install.txt"
dpkg-query -W -f='${binary:Package}\t${Version}\t${Architecture}\t${Status}\n' >"$journal/packages-after.tsv"
java8=/usr/lib/jvm/java-8-openjdk-arm64/jre/bin/java
[[ -x $java8 ]] || die "Packages installed, but expected Java 8 was not found at $java8. Inspect alternatives manually."
is_arm64_elf "$(readlink -f -- "$java8")" || die 'Installed Java launcher failed the AArch64 check.'
"$java8" -version
printf 'Application packages installed. Audit: %s\n' "$journal"
printf 'Next: verify the actual desktop renderer, then open Prism with its isolated Nintekkit --dir and sign in locally.\n'

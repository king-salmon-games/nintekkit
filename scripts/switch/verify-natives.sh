#!/usr/bin/env bash
# Audit the exact instance's JVM and extracted native directory; no game changes.
set -Eeuo pipefail
export LC_ALL=C
java=
natives=
game_pid=
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
usage() {
    cat <<'EOF'
Usage: verify-natives.sh --java /absolute/instance/java/bin/java
                         --natives /absolute/instance/extracted-natives
                         [--pid RUNNING_GAME_JAVA_PID]

Use the Java path selected by this Prism instance and its actual extracted native
directory. Checks ELF architecture, Java 8, DT_NEEDED and dependency resolution.
--pid additionally checks the running JVM and loaded LWJGL/OpenAL paths. It never
reads the JVM command line or account tokens. It does not benchmark FPS.
EOF
}
while (($#)); do
    case "$1" in
        --java|--natives|--pid)
            (($# >= 2)) || die "Missing value for $1"
            case "$1" in --java) java=$2 ;; --natives) natives=$2 ;; --pid) game_pid=$2 ;; esac
            shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown argument: $1" ;;
    esac
done
[[ $java == /* && $natives == /* ]] || die '--java and --natives must both be explicit absolute paths from the actual instance.'
printf 'Machine: %s; architecture: %s; user: %s\n' "$(hostname)" "$(uname -m)" "$(id -un)"
[[ $(uname -m) == aarch64 ]] || die 'Run on the ARM64 Switch.'
for tool in file readelf ldd readlink find awk; do command -v "$tool" >/dev/null || die "Missing diagnostic tool: $tool"; done
[[ -x $java ]] || die "Java is not executable: $java"
[[ -d $natives ]] || die "Native directory does not exist: $natives"
java=$(readlink -f -- "$java")
natives=$(readlink -f -- "$natives")

require_arm64() {
    local path=$1 header
    printf '\n%s\n' "$path"
    file -L -- "$path"
    header=$(readelf -h -- "$path") || die "Cannot read ELF header: $path"
    printf '%s\n' "$header" | awk '/Class:|Data:|Machine:/ {sub(/^[[:space:]]+/, ""); print "  " $0}'
    grep -q 'Class:.*ELF64' <<<"$header" || die "Not ELF64: $path"
    grep -q 'Machine:.*AArch64' <<<"$header" || die "Not AArch64: $path"
}
require_arm64 "$java"
properties=$("$java" -XshowSettings:properties -version 2>&1) || die 'The selected Java executable failed to start.'
printf '%s\n' "$properties" | awk '/^[[:space:]]*(java.home|java.version|os.arch) =/ || /^(openjdk|java|OpenJDK|Java HotSpot)/'
property() { awk -v key="$1" '$1 == key && $2 == "=" {sub(/^[^=]*=[[:space:]]*/, ""); print; exit}' <<<"$properties"; }
java_home=$(property java.home)
java_version=$(property java.version)
java_arch=$(property os.arch)
[[ $java_version == 1.8.* ]] || die "Expected instance Java 8; found $java_version"
[[ $java_arch == aarch64 || $java_arch == arm64 ]] || die "Java reports unexpected architecture: $java_arch"
[[ -d $java_home ]] || die "Java reports an invalid java.home: $java_home"

mapfile -d '' jawt_candidates < <(find -L "$java_home" -type f -name libjawt.so -print0)
mapfile -d '' jvm_candidates < <(find -L "$java_home" -type f -name libjvm.so -print0)
((${#jawt_candidates[@]} == 1)) || die 'Expected exactly one libjawt.so in the selected Java runtime; use the full Java 8 JRE.'
((${#jvm_candidates[@]} >= 1)) || die 'No libjvm.so in the selected runtime.'
require_arm64 "${jawt_candidates[0]}"
# Java loads JAWT/JVM itself. Include those directories in static ldd resolution.
java_library_path=$(dirname -- "${jawt_candidates[0]}")
for jvm in "${jvm_candidates[@]}"; do
    require_arm64 "$jvm"
    java_library_path+=":$(dirname -- "$jvm")"
done
if [[ -n ${LD_LIBRARY_PATH:-} ]]; then java_library_path+=":$LD_LIBRARY_PATH"; fi
printf '\nldd search includes the selected Java runtime native directories.\n'

[[ -f $natives/liblwjgl.so ]] || die 'Missing extracted ARM64 liblwjgl.so.'
[[ -f $natives/libopenal.so ]] || die 'Missing extracted ARM64 libopenal.so.'
[[ -f $natives/libjinput-linux.so || -f $natives/libjinput-linux64.so ]] || die 'Missing extracted JInput Linux native.'
mapfile -d '' native_files < <(find -L "$natives" -maxdepth 1 -type f -name '*.so*' -print0 | sort -z)
((${#native_files[@]} >= 3)) || die 'Expected LWJGL, OpenAL and JInput native files.'
for native in "${native_files[@]}"; do
    require_arm64 "$native"
    printf '  Direct DT_NEEDED entries:\n'
    readelf -d -- "$native" | awk '/\(NEEDED\)/ {print "    " $0}'
    if ! dependencies=$(LD_LIBRARY_PATH="$java_library_path" ldd -- "$native" 2>&1); then
        printf '%s\n' "$dependencies"
        die "Dependency inspection failed: $native"
    fi
    printf '%s\n' "$dependencies"
    if grep -q 'not found' <<<"$dependencies"; then die "Unresolved native dependency: $native"; fi
    # Check resolved library files too, not just the entry point JNI binary.
    while IFS= read -r dependency; do
        [[ -n $dependency ]] || continue
        dependency_header=$(readelf -h -- "$dependency") || die "Could not read resolved dependency: $dependency"
        grep -q 'Machine:.*AArch64' <<<"$dependency_header" || die "Resolved dependency is not AArch64: $dependency"
    done < <(awk '$2 == "=>" && $3 ~ /^\// {print $3} $1 ~ /^\// {print $1}' <<<"$dependencies" | sort -u)
done

if [[ -n $game_pid ]]; then
    [[ $game_pid =~ ^[1-9][0-9]*$ ]] || die '--pid must be a positive integer.'
    [[ -r /proc/$game_pid/maps ]] || die 'Cannot read running JVM maps; use the same desktop user and an active game PID.'
    running_java=$(readlink -f -- "/proc/$game_pid/exe") || die 'Cannot resolve the running JVM executable.'
    [[ $running_java == "$java" ]] || die "Running JVM differs from --java: $running_java"
    maps=$(cat "/proc/$game_pid/maps")
    printf '\nRunning JVM: %s; PID: %s\n' "$running_java" "$game_pid"
    for runtime_name in libjawt.so libjvm.so; do
        loaded=0
        while IFS= read -r loaded_path; do
            [[ -n $loaded_path ]] || continue
            printf 'Loaded %s: %s\n' "$runtime_name" "$loaded_path"
            [[ $loaded_path != *' (deleted)' ]] || die "Loaded $runtime_name was deleted after launch."
            runtime_path=$(readlink -f -- "$loaded_path")
            runtime_match=0
            for expected_runtime in "${jawt_candidates[@]}" "${jvm_candidates[@]}"; do
                if [[ $runtime_path == "$(readlink -f -- "$expected_runtime")" ]]; then runtime_match=1; break; fi
            done
            ((runtime_match)) || die "Running game loaded $runtime_name from outside the selected runtime."
            loaded=1
        done < <(awk -v name="$runtime_name" '$0 ~ "/" name "([[:space:]]|$)" {sub(/^[^/]*\//, "/"); print}' <<<"$maps" | sort -u)
        ((loaded)) || die "Running game has not loaded $runtime_name; complete game startup before checking."
    done
    for required in liblwjgl.so libopenal.so; do
        loaded=0
        while IFS= read -r loaded_path; do
            [[ -n $loaded_path ]] || continue
            printf 'Loaded %s: %s\n' "$required" "$loaded_path"
            [[ $loaded_path != *' (deleted)' ]] || die "Loaded $required was deleted after launch."
            [[ $(readlink -f -- "$loaded_path") == "$natives/$required" ]] || die "Running game loaded $required from another location."
            loaded=1
        done < <(awk -v name="$required" '$0 ~ "/" name "([[:space:]]|$)" {sub(/^[^/]*\//, "/"); print}' <<<"$maps" | sort -u)
        ((loaded)) || die "Running game has not loaded $required; finish startup and enable audio before checking."
    done
    if grep -q '/libjinput-linux' <<<"$maps"; then
        printf 'JInput loaded paths:\n'
        while IFS= read -r loaded_path; do
            printf '%s\n' "$loaded_path"
            [[ $loaded_path != *' (deleted)' ]] || die 'A loaded JInput library was deleted after launch.'
            resolved_input=$(readlink -f -- "$loaded_path")
            case "$resolved_input" in
                "$natives/libjinput-linux.so"|"$natives/libjinput-linux64.so") ;;
                *) die 'Running game loaded JInput from another location.' ;;
            esac
        done < <(awk '/\/libjinput-linux/ {sub(/^[^/]*\//, "/"); print}' <<<"$maps" | sort -u)
    else
        printf 'JInput files passed the audit; this JVM has not loaded JInput (controller use still needs testing).\n'
    fi
    printf '\nPASS: ARM64 Java 8/native dependencies and this running JVM\047s LWJGL/OpenAL paths match.\n'
else
    printf '\nPASS: static ARM64 Java 8/native dependency audit. Repeat with --pid after launch to verify loaded paths.\n'
fi
printf 'Hardware rendering, audible sound, controller behavior and frame times require separate in-game validation.\n'

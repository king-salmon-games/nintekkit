# Audited Switch userland setup

Run these scripts as the Switch desktop user in the existing L4T Ubuntu installation; their host banner identifies the executing machine. The actual application transaction described below has been installed and independently checked over SSH. Game native loading is a separate pending check.

## Install or reuse Prism

```bash
bash scripts/switch/install-prism.sh --plan-only
bash scripts/switch/install-prism.sh
```

The installer checks native ARM64 userspace, prerequisites, downloads and the guarded APT simulation without requiring sudo. `--plan-only` can therefore be used over the project SSH connection. Actual installation checks noninteractive `sudo` only after the reviewed plan passes. If authentication is missing, run `sudo -v` and the installer in the **same authenticated Switch terminal**. A new SSH session may not share that cached authentication. It never handles a password or changes sudo policy.

On the actual Switch, the plan passed with **7 new application packages, 0 upgrades and 0 removals**: Prism 9.4-9, ARM64 OpenJDK 8 full/headless components, Java certificate/common support, and the ATK Java wrapper. All requested X11/OpenAL/inspection libraries were already installed. The user applied that transaction with local sudo authentication. Fresh SSH verified Prism 9.4-9 and OpenJDK 8u504 (`8u504-ga-1ubuntu1~24.04.3`), with an ELF64 AArch64 executable at `/usr/lib/jvm/java-8-openjdk-arm64/jre/bin/java`, `os.arch=aarch64` and class version 52. No kernel or graphics package change was proposed.

The pinned Prism DEB's control archive contains only package metadata: no maintainer scripts, declared triggers or conffiles. Its payload is limited to the private launcher, its `/usr/bin` symlink and application metadata/icons; it contains no `/etc`, `/usr/lib`, `/boot`, kernel/modules or NVIDIA/L4T replacement destinations. Existing distro triggers can refresh desktop, icon and MIME caches. The actual installation audit is `~/.local/state/nintekkit/install/20261024T164342Z-12255`.

An existing ARM64 launcher is reused. For an unrecognized wrapper, supply its verified real binary explicitly:

```bash
bash scripts/switch/install-prism.sh --launcher /path/to/wrapper \
  --launcher-binary /path/to/real/ARM64/prismlauncher --plan-only
```

If Prism is absent, the installer downloads the exact Pi-Apps `9.4-9` ARM64 package and verifies the SHA256 recorded in [upstream.md](upstream.md). A locally transferred copy can be supplied with `--deb /absolute/path/prismlauncher_9.4-9_arm64.deb`; it must match the same checksum. The installer verifies package name, architecture and version before passing the file to APT.

Only Java 8, renderer inspection tools, X11/input/audio dependencies and native inspection tools are requested, with APT recommendations disabled. The script does not refresh package indexes or perform an OS upgrade. It saves the simulated transaction, rejects package removal, and rejects changes to kernel/L4T/NVIDIA packages or graphics driver libraries. If a required dependency would replace the working graphics stack, stop and inspect the saved plan; do not bypass the guard. Missing package candidates may require a separately reviewed `apt update` or a different supported Java source.

Audit files go under `${XDG_STATE_HOME:-$HOME/.local/state}/nintekkit/install/<UTC-time>-<PID>/`: package state before/after, APT plan/output, verified package metadata and backups of pre-existing global Prism launcher files when a package install is needed. Existing user Prism settings are not edited. The package action retains existing Debian configuration files. No automatic uninstall or package downgrade is attempted; the saved state supports a deliberate rollback after reviewing the actual transaction.

The APT simulation and install use the same arguments and unchanged local package indexes. Avoid running another package manager concurrently. `--no-remove` also applies during the real install. APT failure can leave a partial userland installation; inspect the saved output and `dpkg --audit` before retrying.

## Inspect the instance's Java and native libraries

After Prism prepares the instance, use the Java executable selected in that instance and its actual extracted native directory. Do not substitute the system's default `java` or assume a Windows native directory works.

```bash
bash scripts/switch/verify-natives.sh \
  --java /usr/lib/jvm/java-8-openjdk-arm64/jre/bin/java \
  --natives /absolute/path/to/this/instance/natives
```

The checker prints `file`/ELF architecture, Java version, direct dependencies and `ldd` resolution. It rejects a non-AArch64 entry point or resolved dependency, missing JAWT, a non-Java-8 runtime, missing LWJGL/OpenAL/JInput files, or any unresolved shared library. It includes the selected JVM's native directories when resolving JAWT statically. This does not change the launch environment.

Once the real game reaches a world, obtain its Java PID locally, then repeat with `--pid <PID>`. That check verifies `/proc/<PID>/exe`, requires JAWT/JVM mappings from the selected runtime and LWJGL/OpenAL mappings from the supplied native directory, and verifies JInput paths when loaded. It reads executable links and memory-map paths, never token-bearing JVM arguments or environment variables. The game may not load JInput until a mod uses its controller API, so file/dependency success still needs a separate control test.

These checks establish native compatibility only. Confirm the actual Switch display's NVIDIA renderer, audible sound, textures/machines/pipes and usable Joy-Con input in the real server world. Benchmarks require capture disabled, warm-up, and the specified base/traversal measurements.

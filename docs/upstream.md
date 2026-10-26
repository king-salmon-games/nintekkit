# Verified upstream route

Checked 2026-10-24 from the Windows workstation. These are source/package checks; they do not establish that the game runs, that the Switch display uses its GPU, or that the performance target is met.

## Launcher and Java

[Prism's Linux download page](https://prismlauncher.org/download/linux/) explicitly lists Pi-Apps for Debian/Ubuntu ARM. [Pi-Apps' supported systems](https://github.com/Botspot/pi-apps#supported-systems) explicitly include Switchroot L4T Ubuntu Noble 24.04 and Jammy 22.04. This is an appropriate starting route for the reported Switch installation.

Inspected Pi-Apps revision `ce6b850be82c9e85cb798a6fc8767ad77ea1b734`:

- [Prism ARM64 installer](https://github.com/Botspot/pi-apps/blob/ce6b850be82c9e85cb798a6fc8767ad77ea1b734/apps/Minecraft%20Java%20Prism%20Launcher/install-64): package `prismlauncher_9.4-9_arm64.deb`, `mesa-utils`, and Ubuntu OpenJDK 8/17/21 when available.
- [Pi-Apps installer](https://github.com/Botspot/pi-apps/blob/ce6b850be82c9e85cb798a6fc8767ad77ea1b734/install): performs `apt update`, installs its own GUI dependencies, creates launchers and an updater, and may re-clone an existing Pi-Apps directory. Running this whole installer is broader than installing the audited Prism package directly.
- [Package build instructions](https://github.com/Pi-Apps-Coders/files/blob/main/CompileCommands/Apps/Minecraft%20Java%20Prism%20Launcher.md): the community package bundles Qt 5 and several private compatibility libraries. Those instructions describe an older package revision and are build provenance guidance, not a byte-for-byte recipe for revision 9.

Audited package:

```text
URL: https://github.com/Pi-Apps-Coders/files/releases/download/large-files/prismlauncher_9.4-9_arm64.deb
SHA256: 80f8f949d06c7ee6d633d5fc115786824808c11594e634cab36a6bcae4d15588
dpkg Package: prismlauncher
dpkg Version: 9.4-9
dpkg Architecture: arm64
```

The checksum was calculated from the downloaded artifact; it is not an upstream signature. Keep the pin and stop if downloaded bytes change. This is the package selected by the current Pi-Apps script, not a claim that Prism 9.4 is the newest Prism release.

Before installing on the Switch, use `dpkg-deb --info` and `apt-get --simulate install ./prismlauncher_9.4-9_arm64.deb openjdk-8-jre`. Inspect the proposed transaction for removals or replacement of L4T graphics packages. Install only the named application dependencies, without a distribution upgrade. Java 8 must include AWT/JAWT; a headless-only runtime is insufficient for LWJGL 2.

The Pi-Apps application script adds `MESA_GL_VERSION_OVERRIDE=3.3` to its desktop entry if `glxinfo` reports less than 3.3 **or cannot be read**. Do not run that test in an SSH session without the existing desktop's display authorization, and do not adopt its GL override by default. Read the real NVIDIA renderer on the existing Switch display first. An override is not evidence of acceleration.

Use the isolated Prism application root with `prismlauncher --dir "$HOME/.local/share/nintekkit/prism"`. After legitimate Microsoft sign-in in that graphical session, a desktop entry can use `--launch Nintekkit`; the instance ID is its directory name. Prism documents [`--dir`, `--launch`, and `--server`](https://prismlauncher.org/wiki/getting-started/command-line-interface/). Account JSON and tokens stay outside version control and should never be copied into diagnostics.

## Minecraft 1.12.2 ARM64 natives

Prism already integrates the ARM64 classifiers. Do not replace the entire launcher metadata service or add an LWJGL 3 bridge for the baseline. The [live LWJGL 2 component](https://meta.prismlauncher.org/v1/org.lwjgl/2.9.4-nightly-20150209.json) lists `linux-arm64` classifiers for both native bundles. The [Prism metadata patch source](https://github.com/PrismLauncher/meta/blob/9cac2ca1cf410146d3a04fbd33752820fefdd70e/meta/common/mojang-library-patches.json) implements the mapping.

Downloaded native artifacts match Prism's SHA1 values. Inspected every `.so` in these two JARs: all are ELF64, `e_machine=183` (AArch64).

| Native bundle | Immutable download | SHA1 expected by Prism | SHA256 measured |
| --- | --- | --- | --- |
| LWJGL 2.9.4, including OpenAL | [LWJGL native JAR](https://raw.githubusercontent.com/theofficialgman/lwjgl3-binaries-arm64/9d58d749d73815b29150c84d28e73b5d7dae15ab/lwjgl-platform-2.9.4-nightly-20150209-natives-linux.jar) | `63ac7da0f4a4785c7eadc0f8edc1e9dcc4dd08cb` | `a0d5fc125b5c662d46e7af0987efe5cd30a4d123a73deb38b4676dca84a72837` |
| JInput platform 2.0.5 | [JInput native JAR](https://raw.githubusercontent.com/theofficialgman/lwjgl3-binaries-arm64/9d58d749d73815b29150c84d28e73b5d7dae15ab/jinput-platform-2.0.5-natives-linux.jar) | `42b388ccb7c63cec4e9f24f4dddef33325f8b212` | `71b9325a0cd4a16682e0c18d2ac388843202cfef0d9f1830cec48e15c8a9307f` |

ELF `DT_NEEDED` inspection found:

- `liblwjgl.so`: `libX11.so.6`, `libXext.so.6`, `libXcursor.so.1`, `libXrandr.so.2`, `libXxf86vm.so.1`, `libjawt.so`, plus libc/libm/libpthread and the AArch64 loader.
- `libopenal.so`: libc/libm/libpthread/libdl, `libstdc++.so.6`, `libgcc_s.so.1`, and the AArch64 loader.
- Both `libjinput-linux.so` and `libjinput-linux64.so`: libc and the AArch64 loader. Their names do not imply x86 in this bundle.

On Ubuntu these X11 dependencies correspond to `libx11-6 libxext6 libxcursor1 libxrandr2 libxxf86vm1`; `libjawt.so` comes from the full ARM64 Java runtime. Inspect with `file`, `readelf -h/-d`, and `ldd` on the Switch after Prism extracts the natives. If `ldd` cannot find `libjawt.so`, check using the actual JVM's `lib/aarch64` directory and inspect the running process maps. Verify successful loading in the real game before calling that resolved. LWJGL resolves GL functions and OpenAL may load audio backends dynamically: an empty `ldd` error list alone does not verify graphics or sound.

The port author's [manifest README](https://github.com/theofficialgman/piston-meta-arm64/blob/main/README.md) identifies LWJGL 2.9.4 and the JInput port, links [LWJGL source](https://github.com/theofficialgman/lwjgl) and [JInput 2.0.7 source](https://github.com/jinput/jinput/tree/2.0.7), and notes the absent ARM64 Mojang narrator native. Preserve those projects' license notices if redistributing rebuilt open-source native binaries. Minecraft authentication and game assets still come through the legitimate launcher.

## Exact Tekkit 2 version

The existing Windows client was identified separately as **Tekkit 2 1.2.4**, Minecraft **1.12.2**, Forge **14.23.5.2860**. Use 1.2.4 for the candidate matching instance; a successful join is still needed to establish what the existing server accepts.

Official machine-readable sources:

- [Pack/build list](https://solder.technicpack.net/api/modpack/tekkit-2).
- [Exact 1.2.4 manifest](https://solder.technicpack.net/api/modpack/tekkit-2/1.2.4).
- [Technic pack page](https://www.technicpack.net/modpack/tekkit-2/).

The 1.2.4 manifest reports Minecraft `1.12.2`, Java `1.8`, suggested memory `3072`, and 153 archive modules. The top-level `forge` field is null; the module named `forge` correctly specifies `14.23.5.2860`. Each module includes its version, download URL, byte count and MD5. Download exact versioned archives, verify MD5/size against this manifest and record SHA256 locally. MD5 provides compatibility/integrity checking, not a signature. Preserve configuration/resource modules and Forge integration rather than treating every module ZIP as a JAR to place in `mods/`.

The matching configuration archive is [z-tekkit2-configs-1.2.4.zip](https://mirror-mods.technicpack.net/mods/z-tekkit2-configs/z-tekkit2-configs-1.2.4.zip), MD5 `29813eae8b0d09b9a585b792951f262a`; the Forge module is [forge-14.23.5.2860.zip](https://mirror-mods.technicpack.net/mods/forge/forge-14.23.5.2860.zip), MD5 `945112aac6e0d70201f2af65f2a933d1`.

Existing performance-related modules in **1.2.4** include LoliASM `5.20`, VintageFix `0.5.1`, EntityCulling `6.4.3`, ParticleCulling `1.4.3`, RenderLib `1.3.5` and UniversalTweaks `1.12.1-686`. Inspect the installed configs and dependencies before considering additions. In particular, do not assume newer public pack manifests describe the user's installed optimization stack.

The official pack/build list currently calls **1.2.6** latest/recommended, and the public page agrees. That does not authorize upgrading the client or server. Keep the candidate instance on version 1.2.4. Keep all world data, credentials, private server addresses and local game artifacts out of version control.

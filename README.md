# Nintekkit — source-only alpha

Nintekkit prepares an isolated **Tekkit 2 1.2.4 / Minecraft 1.12.2 / Forge 14.23.5.2860** client that renders locally on a Switch's ARM64 CPU and NVIDIA Tegra GPU and connects to your matching server. It includes setup, landscape presentation, Joy-Con controls and measurement tools. No Minecraft assets, pack/mod binaries, accounts or worlds are distributed.

A user reported successful gameplay on October 25, 2026. **Sustained 30 FPS at 1280×720 has not been measured.** There are no qualifying ten-minute busy-base/traversal results or gameplay average, 1% low or p99 figures. The complete fresh installation flow below still needs acceptance testing. See [release notes](RELEASE_NOTES.md).

## Requirements

- An existing working **Switchroot L4T Ubuntu 24.04 ARM64, KDE/X11** desktop with hardware-accelerated NVIDIA graphics. Preserve its OS, kernel and graphics stack. This project does not install Switchroot or change firmware, partitions or clocks.
- A licensed Minecraft Java account, a legally acquired installed **Tekkit 2 1.2.4** client, and your own reachable matching server. Sign in legitimately on the Switch; do not copy account tokens.
- On Windows: **Python 3.10+**, OpenSSH/SCP and a **JDK 17** compiler for the original companion. Supply the exact **Forge 1.12.2-14.23.5.2860 universal JAR**, not its installer JAR, as a local compile dependency.
- On the Switch: a **full ARM64 Java 8** runtime and matching ARM64 LWJGL 2/input/audio natives. The guarded Prism installer checks application dependencies. Java 17 is a build tool here, not the game runtime.
- For controls: the existing readable Combined Joy-Con device/`joycond`, XTest/XInput/XRes, Python 3 with `dbus-python`, and the companion built below. Capture text entry additionally requires the existing **Onboard** keyboard. Other controller/display configurations are unvalidated.

Allow ample free storage and close unused applications. The pack can take roughly **ten minutes to start**. The initial 1792 MiB heap is provisional on a 4 GB Switch; startup has shown memory pressure and swap activity.

## 1. Prepare on Windows

Extract the source archive and run PowerShell from its root. Keep transferred shell scripts in Unix LF format. Replace placeholder paths below; use fresh output/staging directories rather than overwriting an existing instance.

```powershell
python scripts/import_client.py --source "$env:APPDATA\.technic\modpacks\tekkit-2" --output .local/staging/Nintekkit-1.2.4 --min-memory 512 --max-memory 1792 --java /usr/lib/jvm/java-8-openjdk-arm64/jre/bin/java
python scripts/verify_instance.py .local/staging/Nintekkit-1.2.4 --lock config/client-1.2.4.lock.json
python scripts/benchmark/build_probe.py --forge-jar "C:\path\to\forge-1.12.2-14.23.5.2860-universal.jar" --javac "C:\path\to\jdk-17\bin\javac.exe" --output .local/build/nintekkit-frameprobe-0.2.0.jar
```

The importer requires the reviewed 151-mod release, preserves required content/configuration and excludes credentials, worlds and saved server lists. The companion is original source compiled to Java 8 classes; it provides GUI/world context for controls and optional frame observation. It packages no Forge or Minecraft classes. Do not add it to staging before pristine verification. See [companion installation and rollback](docs/probe-overlay.md) and [benchmarking](docs/benchmark.md).

## 2. Establish SSH and transfer

Create or reuse your own dedicated SSH key. Do not overwrite an existing key. For a new key:

```powershell
New-Item -ItemType Directory -Force .local/ssh | Out-Null
ssh-keygen -t ed25519 -f .local/ssh/id_ed25519 -C nintekkit-switch
```

Verify the Switch SSH host fingerprint through its local terminal or another trusted connection before accepting it. Authorize **only your public key** in the Switch user's existing SSH setup, backing up `authorized_keys` first. Complete one normal SSH login to establish the verified known-host entry. If the key has a passphrase, make it available through your SSH agent. The wrapper uses batch authentication and strict host-key checking; `configure` does not authorize a key or trust a host.

Replace `SWITCH_ADDRESS` and `SWITCH_USER`, including the home directory placeholders. Choose a **fresh** absolute staging directory on the Switch. These Windows commands create its parent and copy each item into it:

```powershell
python scripts/switch.py configure --host SWITCH_ADDRESS --user SWITCH_USER --key .local/ssh/id_ed25519
python scripts/switch.py check
python scripts/switch.py exec "install -d -m 700 /home/SWITCH_USER/nintekkit-stage-alpha"
python scripts/switch.py put scripts /home/SWITCH_USER/nintekkit-stage-alpha/ --recursive
python scripts/switch.py put config /home/SWITCH_USER/nintekkit-stage-alpha/ --recursive
python scripts/switch.py put .local/staging/Nintekkit-1.2.4 /home/SWITCH_USER/nintekkit-stage-alpha/ --recursive
python scripts/switch.py put .local/build/nintekkit-frameprobe-0.2.0.jar /home/SWITCH_USER/nintekkit-stage-alpha/
```

The resulting directory must contain sibling `scripts`, `config`, `Nintekkit-1.2.4` and the companion JAR. Do not transfer the whole preparation directory, private SSH keys, accounts or worlds. Private wrapper configuration is stored locally and an existing configuration is preserved. Each remote command checks the target architecture and identifies its executing machine.

## 3. Install and sign in on the Switch

Run the following **in the Switch desktop user's terminal**, not Windows PowerShell. Substitute your actual staging directory. Review the application package plan before installation; the installer rejects protected kernel/graphics changes and removals and does not run an OS upgrade. Authenticate sudo in this same terminal:

```bash
cd /home/SWITCH_USER/nintekkit-stage-alpha
bash scripts/switch/install-prism.sh --plan-only
sudo -v
bash scripts/switch/install-prism.sh
python3 scripts/switch/desktop-run.py -- bash scripts/switch/display-check.sh
bash scripts/switch/install-instance.sh /home/SWITCH_USER/nintekkit-stage-alpha/Nintekkit-1.2.4 /home/SWITCH_USER/nintekkit-stage-alpha/scripts/switch
python3 scripts/switch/desktop-run.py -- prismlauncher --dir "$HOME/.local/share/nintekkit/prism"
```

The instance installer's **second argument is `scripts/switch`**, with the sibling directories preserved. It verifies the fresh copy and refuses to replace an installed instance. A failed graphics check must be resolved before play; software rendering is insufficient. See [installation details](docs/install-details.md) and [pinned launcher/natives](docs/upstream.md).

In this isolated Prism window, complete local Minecraft sign-in. Confirm the instance selects the full ARM64 Java 8 executable used during import. Keep its compatibility checks enabled. **Close every Prism process and the game before the next step**, including any background/tray launcher process.

## 4. Install the companion and Joy-Con entry

Still on the Switch, as the same desktop user and without sudo:

```bash
cd /home/SWITCH_USER/nintekkit-stage-alpha
python3 scripts/benchmark/install_probe.py --install \
  --instance "$HOME/.local/share/nintekkit/prism/instances/Nintekkit-1.2.4" \
  --jar /home/SWITCH_USER/nintekkit-stage-alpha/nintekkit-frameprobe-0.2.0.jar \
  --output-directory "$HOME/.local/share/nintekkit/benchmarks"
python3 "$HOME/.local/share/nintekkit/bin/desktop-run.py" -- \
  python3 "$HOME/.local/share/nintekkit/bin/nintekkit_controller.py" --inspect
python3 "$HOME/.local/share/nintekkit/bin/enable_controls.py" --install
```

The overlay installer requires stopped processes and preserves effective Java arguments, configuration and mod inventory in its transaction backup. Its output directory must be private, outside the instance, and an absolute ASCII path without whitespace. Existing overlays or journals cause refusal; follow [overlay instructions](docs/probe-overlay.md) rather than replacing them. Inspection does not inject input. Installation alone does not validate physical controls.

## 5. Launch and play

Open **Nintekkit Joy-Cons — Tekkit 2** from the application menu. Release both sticks/buttons while controls initialize, then keep the real game focused. This entry starts the game and its controller/presentation lifecycle; the plain **Nintekkit — Tekkit 2** entry omits controller input. Both refuse a pre-existing client rather than adopting or killing it.

Keep Minecraft **native fullscreen off** and **avoid F11**. The launcher applies borderless fullscreen to the verified Java-owned window. This corrects the tested rotated-panel portrait-dimension defect; direct Prism launch bypasses that supervisor. See [render investigation](docs/render-investigation.md).

Use Multiplayer to add or directly connect to your own server. Saved endpoints are intentionally not imported. For text, select the field and press **Capture** to toggle Onboard, then touch its keys or point with a stick and press **A**.

- **World:** left stick moves, right stick looks, A jumps, B holds crouch, Y opens inventory, X opens Java player crafting, ZR attacks/mines, ZL uses/places, L/R change hotbar slots.
- **GUI:** left/right sticks move the pointer coarsely/precisely; A or ZR clicks; B/Y go back. Hold **L3 + A** (left stick click + A) for Java Shift-click quick transfer. D-pad up/down perform JEI recipe/use lookup; Minus focuses JEI search.
- GUI X is unbound to avoid typing into fields. Java screens remain pointer-based; automatic slot navigation and Bedrock's separate crafting screen are not implemented. Focus/context loss releases input and requires neutral controls again.

Check camera feel, textures, machines, pipes, audio and all controls in your actual world. After first launch, audit extracted natives using the selected runtime:

```bash
bash "$HOME/.local/share/nintekkit/bin/verify-natives.sh" \
  --java /usr/lib/jvm/java-8-openjdk-arm64/jre/bin/java \
  --natives "$HOME/.local/share/nintekkit/prism/instances/Nintekkit-1.2.4/natives"
```

See [controls and recovery](docs/controls.md), [keep-awake behavior](docs/keep-awake.md) and [benchmark procedure](docs/benchmark.md). Game-scoped awake leases do not unlock an already locked desktop. Performance acceptance requires capture disabled, world warmup, uncapped headroom and separate ten-minute busy-base/traversal measurements; no FPS result is claimed by this alpha.

## Rollback and license

Quit the game normally to release controller holds, restore desktop input and release game-scoped leases. Remove the unchanged optional Joy-Con entry with `python3 "$HOME/.local/share/nintekkit/bin/enable_controls.py" --remove`. With the game **and every Prism process closed**, use the same paths and the guarded [companion rollback](docs/probe-overlay.md#rollback). Retain backups and inspect any refusal rather than overwriting changed files. [Installation details](docs/install-details.md) cover package audit/rollback; do not purge the L4T graphics stack. The original source client and server/world are not changed by these setup tools.

Original project code: **MIT License, copyright 2026 King Salmon Games**; see [LICENSE](LICENSE). Third-party software retains its own licenses.

**NOT AN OFFICIAL MINECRAFT PRODUCT. NOT APPROVED BY OR ASSOCIATED WITH MOJANG OR MICROSOFT.** Nintekkit is also independent of Nintendo and Technic and is not endorsed by them.

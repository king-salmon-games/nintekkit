# Original client-tools overlay

`scripts/benchmark/install_probe.py` installs the original MIT client-tools JAR **0.2.0** after the game and every Prism process have been closed. It does not stop either process. The 0.2.0 overlay was installed and loaded in the existing Switch client; a fresh installation still needs its own verification.

The 0.2.0 contract contains `FrameProbe`, `FrameRing`, `Recorder`, its shutdown-hook class, and `ControlsContext`, with Java class version 52. The helper requires exactly those class files and the original license/build/mod metadata; Minecraft/Forge classes, native libraries and unrelated payload files are rejected. The tools observe client frame callbacks and GUI context without installing server content. The existing ARM64 Java 8 client published a live GUI-context heartbeat and the recorder reached its ready state. Complete world-performance recordings remain outstanding.

After preparing the reviewed JAR, run **on the Switch as the instance owner**:

```bash
python3 /path/to/scripts/benchmark/install_probe.py --install \
  --instance "$HOME/.local/share/nintekkit/prism/instances/Nintekkit-1.2.4" \
  --jar /path/to/nintekkit-frameprobe-0.2.0.jar \
  --output-directory "$HOME/.local/share/nintekkit/benchmarks"
```

The output directory must be outside the instance, owned by the current user, and private if it already exists (mode 700). Use a plain ASCII absolute Unix path without whitespace, quotes or argument metacharacters. The helper checks process names and working directories without reading command lines, environments or account files. Any Prism process or Java process using this instance's `.minecraft` directory blocks installation and rollback.

Before writing runtime content, it stores the exact original and proposed `instance.cfg`, their SHA256 hashes, the installed JAR digest/build provenance, and the existing mod inventory in `<output-directory>/.nintekkit-probe-overlay/`. It adds only the client-tools JAR and changes only the `General` keys `OverrideJavaArgs` and `JvmArgs`. It retains other configuration bytes, including line endings and comments. Configuration writes and JAR publication are atomic, and an existing JAR is never overwritten.

## JVM arguments and QSettings

Prism 9.4 inherits global `JvmArgs` when `OverrideJavaArgs=false`. Enabling an instance override without carrying those effective arguments forward would change runtime behavior. The helper therefore preserves the effective global arguments in that case, or the current instance arguments when its override is already enabled; the full original flag/value remain in the backup. [Prism implementation](https://github.com/PrismLauncher/PrismLauncher/blob/9.4/launcher/minecraft/MinecraftInstance.cpp)

Qt INI serialization quotes strings containing `=` or commas and escapes embedded quotes/backslashes. Those outer INI quotes are distinct from Java argument quoting. The new argument is a plain `-Dnintekkit.frameprobe.dir=/absolute/path`; the whole `JvmArgs` value is serialized appropriately. The helper supports canonical ASCII scalar strings and refuses ambiguous list/variant encodings, mixed quoting, unsupported escapes or control/non-ASCII characters rather than guessing their meaning. [Qt 5.15 QSettings implementation](https://github.com/qt/qtbase/blob/5.15/src/corelib/io/qsettings.cpp)

An existing probe JAR or frame-probe property is preserved and causes refusal. Global launcher configuration is read only for the effective argument value and remains unchanged. Optional context configuration is not added: `ControlsContext` uses its default `.minecraft/nintekkit-controls/state.json`; the optional `nintekkit.controls.file` property is outside this installer's changes.

## Rollback

Close the game and Prism, then use the same instance/output paths:

```bash
python3 /path/to/scripts/benchmark/install_probe.py --rollback \
  --instance "$HOME/.local/share/nintekkit/prism/instances/Nintekkit-1.2.4" \
  --output-directory "$HOME/.local/share/nintekkit/benchmarks"
```

Rollback restores the exact original configuration only if the current bytes still match the installed version (or already match the backup), and removes only the matching tool JAR. If Prism/game settings or the JAR changed afterward, it refuses to overwrite them and reports that manual review/merge is required. Backups, captures and the transaction record are retained. A retained journal prevents a second install into that output directory; review/archive it deliberately or use a fresh private capture directory.

If installation is interrupted between JAR publication and configuration replacement, the same journal supports cleanup while preserving an unchanged original configuration. A partial transaction or any refusal is not evidence that instrumentation or controls work. Synthetic tests cover preservation, process refusal, JAR validation and rollback; fresh installations must still verify the reviewed JAR, live context, recorder status and real-pack behavior; the earlier live check does not establish their playability or performance.

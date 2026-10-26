# Benchmarking the real Switch client

There are no real Minecraft performance results in this document. A menu, an empty world, synthetic tests, or a successful build do not meet the acceptance test.

The original MIT-licensed client tools in `patches/frameprobe` observe Forge 1.12.2 `RenderTickEvent.END` and record `System.nanoTime()` for every observed callback. Version 0.2.0 also publishes GUI/world context for the Joy-Con mapper, through a private local file. Neither observer modifies rendering methods, registers game content, opens network channels, or changes the server. Forge's [RenderTickEvent definition](https://github.com/MinecraftForge/MinecraftForge/blob/3effde4f1fc9d14d6ed1dbf6bebc39c2b18780e1/src/main/java/net/minecraftforge/fml/common/gameevent/TickEvent.java) identifies a client render event, and [FMLCommonHandler](https://github.com/MinecraftForge/MinecraftForge/blob/3effde4f1fc9d14d6ed1dbf6bebc39c2b18780e1/src/main/java/net/minecraftforge/fml/common/FMLCommonHandler.java#L332) posts its END phase. The observers use Forge's [client-only and remote-version declarations](https://github.com/MinecraftForge/MinecraftForge/blob/3effde4f1fc9d14d6ed1dbf6bebc39c2b18780e1/src/main/java/net/minecraftforge/fml/common/Mod.java).

These are CPU render-loop intervals, including intervening game work, blocking, and frame limiting. They are not GPU execution times or display scan-out timestamps. Driver buffering, compositor presentation, and duplicated/dropped display frames remain outside this measurement. Verify the actual Switch display, accelerated renderer, focus, framebuffer size, and live callback rate separately. Report this limitation with the results.

## Build and install

Build on Windows or Linux with Python 3.10+ and a JDK. The game itself still needs the selected ARM64 Java 8 runtime and compatible ARM64 natives. The probe has no native libraries.

Use the isolated client's exact Forge universal JAR as the compile dependency:

```powershell
# Windows workstation, from the repository root
python scripts/benchmark/build_probe.py --forge-jar C:\path\to\forge-1.12.2-EXACT-universal.jar
```

The script compiles Java 8 class files without Minecraft classes or ForgeGradle, records source/dependency hashes and compiler version inside the JAR, and packages only the original probe classes, metadata, and license. ZIP entry ordering and timestamps are fixed. Byte-for-byte reproduction requires the same sources, Forge JAR, and JDK. It refuses to replace an existing output; back it up or choose `--output`.

Use the guarded [overlay installer](probe-overlay.md) while the game and Prism are closed. It preserves effective JVM arguments and backs up configuration before adding the original JAR and this instance-only argument:

```text
-Dnintekkit.frameprobe.dir=/home/SWITCH_USER/.local/share/nintekkit/benchmarks
```

Replace `SWITCH_USER` with the actual Linux username in this literal JVM argument; Prism does not expand `$HOME` or `~` inside saved Java arguments. Keep captures outside version control. Launch Minecraft in the Switch's existing graphical session with valid authentication. Frame recording is inactive without the property; the independent GUI observer still writes `.minecraft/nintekkit-controls/state.json` for controls. Its client-thread sampling and asynchronous private atomic writer are covered by real Java fixture tests. After initialization, `status.properties` should say `state=ready`. A pending command from a previous JVM is deliberately ignored. Do not run two clients against the same probe directory.

The actual 0.2.0 JAR built twice with JDK 17.0.14 and Forge 2860 was byte-identical: SHA256 `b60e3fcf20709e3823cbe49e17b118a241f10afa645ea9c5e4756e1e88cc2501`. Its five original Java 8 classes contain no Minecraft/Forge classes or native binaries. This overlay is separate from the pristine 151-mod client lock. It was installed on the Switch with the configuration journal at `~/.local/share/nintekkit/benchmarks/.nintekkit-probe-overlay/`; live recording remains to be validated.

## Controlled runs

Run the following commands **on the Switch over SSH**, using the deployed repository path. The controller refuses Windows because its capture guard inspects Linux `/proc`. It scans known recorder/sharing process names, including KDE `krfb`, `krfb-virtualmonitor` and `krdpserver`, with Linux's 15-byte command-name truncation. It still requires an operator attestation and cannot prove the absence of every capture service. Ordinary Plasma, KWin and PipeWire processes are not sufficient evidence of capture. The guard never stops processes itself.

Before each run:

1. Confirm the client joined the existing world's actual server. Check machines, pipes, textures, sound, Joy-Con camera/movement, inventory, hotbar, recipe lookup, crafting, and machine GUIs. Do not perform destructive server/world actions for a benchmark.
2. Set the actual framebuffer/display resolution to 1280×720. Record the pack release, Forge, complete mod/config hashes, Java and natives, heap, renderer, view distance, graphics settings, control profile, and repository revision in the run conditions or an adjacent manifest. Check that game focus and display state match normal handheld play.
3. Stop desktop sharing, video recording, screenshots, and other capture. Close overlays that materially alter rendering. Keep the measurement probe and read-only telemetry enabled consistently in all comparisons.
4. For initial headroom runs, disable VSync and set the Minecraft limiter to unlimited. `--uncapped` records an operator declaration; the probe does not change or verify the setting.
5. Warm up for at least two minutes **in the world**, longer if loading/compilation/temperature has not settled. Warm the busy base and traversal route separately. Do not call launch/menu time a world warmup.

```bash
# Switch; run from the deployed repository directory
python3 scripts/benchmark/control.py --directory "$HOME/.local/share/nintekkit/benchmarks" check-capture
python3 scripts/benchmark/control.py --directory "$HOME/.local/share/nintekkit/benchmarks" status

python3 scripts/benchmark/control.py --directory "$HOME/.local/share/nintekkit/benchmarks" start \
  --phase base --seconds 610 --warmup-seconds 120 --actual-server --capture-disabled \
  --uncapped --instance Nintekkit --server-alias existing-world --resolution 1280x720 \
  --conditions 'Record pack/mod/config revision, renderer, heap, graphics and route here'
```

The controller returns the unique run ID after the probe acknowledges it. Keep playing normally in the busy base. The worker finishes automatically after the requested duration; its deadline can overshoot by roughly its 200 ms polling interval. The default 610 seconds permits at least 600 seconds **between observed render endpoints**, and leaves a small margin for annotations. If longer exclusions are expected, extend the run. Then repeat `start` with `--phase traversal` and follow a representative reproducible route, including ordinary chunk loading. A single 20-minute mixed capture does not replace the two distinct scenarios.

Annotate exceptional events as they happen, from SSH so the game retains focus:

```bash
python3 scripts/benchmark/control.py --directory "$HOME/.local/share/nintekkit/benchmarks" mark --kind teleport --edge start --note 'portal'
python3 scripts/benchmark/control.py --directory "$HOME/.local/share/nintekkit/benchmarks" mark --kind teleport --edge end --note 'arrived'
```

Supported spans are `loading`, `teleport`, `menu`, and `disconnect`. A `note` with `--edge point` records contextual information without exclusions. Annotation timestamps are taken in the JVM when the worker receives the command, with roughly 200 ms polling uncertainty plus SSH scheduling. Start spans before a planned transition; a command arriving after a stall cannot retrospectively identify its beginning. Ordinary exploration/chunk-loading stalls belong in normal play. Do not remove them just because they hurt the result. Preserve raw measurements and explain every exclusion.

`stop` ends early with status `stopped`; such a run never passes the complete-capture guard. The recorder logs through an allocation-free frame callback and a bounded asynchronous ring. If the ring fills, it stops and marks the run invalid instead of silently discarding frames. A crash or forced kill can leave incomplete data; the analyzer rejects it as a valid acceptance capture.

## System telemetry

Identify the **game's** Java PID, not Prism's process, without copying authentication tokens from command lines. Run the sampler on the Switch before starting the frame capture, with an output path outside the repository:

```bash
python3 scripts/benchmark/sample_system.py --pid GAME_JAVA_PID --seconds 630 \
  --output "$HOME/.local/share/nintekkit/benchmarks/base-system.jsonl"
```

It samples aggregate/per-core CPU busy percentages, process CPU/RSS/swap, available memory, swap/major-fault counters, temperatures, readable frequency nodes, known Tegra GPU load nodes, boot ID/uptime and battery/USB power supplies. Battery numeric units are included in the header; signs and literal `Unknown` status are preserved. Device serials, manufacturers and identifying wireless supply names are withheld. GPU percentages use the known 0–1000 Tegra load scale and retain the source path/raw value. Missing nodes remain unavailable; absence is not zero load. Frequency values retain sysfs units: CPU `scaling_cur_freq` is normally kHz, devfreq `cur_freq` normally Hz; verify the installed driver. This script does not write clocks, power limits, fan policy, or thermal settings.

Use `start_epoch_ms` in probe metadata and telemetry UTC timestamps to align the runs approximately; inspect clock stability. Java and Python monotonic clocks are separately recorded and must not be assumed to share an epoch. Inspect per-core saturation, GPU load, RAM/swap growth, frequency reductions, and temperature over the whole ten minutes before naming a bottleneck. GPU load alone is insufficient evidence of GPU-bound frame time. Check CPU render work, driver waits, GC logs, and chunk generation/loading where appropriate. Run a capture-disabled comparison if measuring instrumentation overhead; synthetic queue tests do not establish negligible game overhead.

## Analyze and report

After `status` reports `complete`, analyze the run directory on either host:

```bash
python3 scripts/benchmark/analyze.py /absolute/path/to/benchmarks/RUN_ID \
  --output /absolute/path/to/RUN_ID-report.json
```

The report keeps all observed intervals in `raw_all_frames`, then separately reports `normal_play` after removing whole intervals overlapping explicitly annotated exceptional spans. Gaps are never compressed into contiguous time for sustained-dip detection. Unmatched spans, short runs, missing metadata, recorder overflow, incomplete files, missing attestations, and a large unobserved trailing gap prevent a valid CPU-target verdict. Operator declarations are evidence supplied by the operator, not automated proof of world/server/display state.

The definitions are explicit:

- Average FPS is observed interval count divided by summed measured interval seconds.
- 1% low FPS is the reciprocal of the mean duration of the slowest `ceil(1% × interval count)` frames. It differs from the reciprocal of p99.
- p99 frame time uses the nearest-rank 99th percentile.
- Stalls are intervals of at least 100 ms, with their elapsed timestamps and durations listed. The maximum is reported too.
- Sustained dips are at least three consecutive complete one-second windows below 30 FPS. Frames crossing window boundaries contribute their proportional overlap; windows with excluded gaps are omitted. The report also includes the worst one-second rate and counts of frames over the target budget.

Report each base/traversal run's average, 1% low, p99, maximum, stalls, sustained dips, total measured/normal-play duration, exclusions, thermal/memory behavior, and declared limiter. The optional `cpu_loop_target_met` requires 600 seconds of normal play, average ≥30 FPS, p99 ≤33.3 ms, no sustained dip, and the capture-quality guards. It establishes only the CPU interval target under declared conditions; it never establishes final GPU/display/playability success.

Change one meaningful variable at a time and retain paired results. Measure uncapped base and traversal headroom before selecting a cap. Repeat both scenarios with `--cap-fps 30` (or another chosen limiter) after setting it in game; do not pass both limiter options. Use longer runs if the final cap leaves too little thermal headroom or any sustained degradation appears. Preserve regressions as evidence, then restore the backed-up settings.

## Verification and rollback

```powershell
# Windows workstation; synthetic correctness tests only
python -m unittest discover -s tests/benchmark -v
```

These tests check tail/percentile definitions, sustained-dip boundaries and gaps, corruption/incomplete evidence rejection, annotation behavior, the capture-name guard, CPU accounting, concurrent ring ordering/overflow, and the real Java recorder/control/CSV path. They do not launch Forge, measure a GPU, or claim Switch performance. The source has compiled against official Forge 1.12.2-14.23.5.2860 with JDK 17 targeting class version 52; live acceptance against the selected pack remains required.

Remove the probe JAR and its JVM property to roll back instrumentation. It writes only the chosen local capture directory. No server installation, save conversion, kernel/graphics changes, overclock, or world modification is involved. Keep completed capture files as evidence outside Git; never add accounts, credentials, proprietary pack/game JARs, or world data.

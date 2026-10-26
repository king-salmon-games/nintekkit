# Joy-Con controls

The original mapper is `scripts/controller/nintekkit_controller.py`. It uses Python's standard library and the Switch's existing X11, XTest, XInput2, `joycond` and Combined Joy-Con device. The original client companion supplies GUI/world context through a private local JSON file. No AntiMicroX package, new driver or global Xorg configuration is needed. **The revised contextual layout needs physical validation on the actual running pack.** Passing synthetic tests does not establish playable controls.

The [live inventory](controller-inventory.md) confirmed a combined device with four axes and eighteen buttons, readable by the desktop user, and the required native libraries. Physical Nintendo labels below follow the installed `50-joystick.conf` and raw button numbering. The mapper does not depend on SDL's positional-label conventions.

The face-button actions follow the [official Minecraft controller guide](https://www.minecraft.net/en-us/article/minecraft-controls). Java 1.12.2 uses its existing mouse/keyboard screens: X and Y both open E's inventory/player-crafting screen, and X cannot create Bedrock's separate crafting interface. GUI sticks move a pointer; automatic slot selection is not implemented.

| Physical control | World | Menu, inventory or machine GUI |
| --- | --- | --- |
| Left stick | Held WASD movement, including diagonals | Coarse pointer movement |
| Right stick | Relative camera movement | Fine pointer movement |
| A | Jump, Space | Select/hold left mouse button |
| B | Hold crouch, Shift | Back/close, Escape |
| Y | Open inventory, E | Back/close, Escape |
| X | Open player crafting/inventory, E | Unmapped, so text fields receive no accidental E |
| ZR | Hold attack/mine | Hold left mouse button |
| ZL | Hold use/place | Hold right mouse button |
| L / R | Previous / next hotbar slot | One scroll step per press |
| D-pad up / down | Unmapped | JEI recipe / use lookup, R / U |
| D-pad left / right | Hotbar slot 1 / 9 | Java number-key slot action, 1 / 9 |
| Minus | Player list, Tab | JEI search focus, Ctrl+F |
| Plus | Pause, Escape | Back/close, Escape |
| Left stick click | Hold sprint, Ctrl | Hold Shift for quick transfer with a click |
| Right stick click | Pick block | Middle mouse button |
| Capture | Unmapped | Toggle existing Onboard keyboard |
| Home | Unmapped | Unmapped |

Use ZL to open a machine, left stick to move across its GUI, right stick for precise item selection, and A or ZR to click. Hold the left stick button with a click for Java shift transfers. Confirm the pack's actual E/R/U/Ctrl+F bindings. For text entry, select the field and press **Capture** (the small square button on the left Joy-Con). Tap Onboard's keys on the touchscreen or point and click with A. Press Capture again to hide it. Home remains unmapped.

The keyboard helper uses the installed Onboard session service asynchronously, with bounded calls and no text inspection. It verifies Onboard's service PID and XRes window owner, then temporarily sets only its matching dock's transient parent to the verified game window so it appears above borderless fullscreen. It restores the prior hint only while the current hint still belongs to this helper, preserves an existing Onboard process and hides only its own newly shown keyboard on context/focus loss or exit. Dry-run never opens a keyboard. Xlib initialization and error-handler lifetime are shared safely between the mapper and worker connections.

## Required client context

Install the original combined frame/context companion as described in [probe-overlay.md](probe-overlay.md), with the client closed first. It observes the actual client GUI and world on client ticks, publishes changes and a one-second heartbeat asynchronously, and changes no server protocol. Its default output is `nintekkit-controls/state.json` under the Java process's working directory, which must be this isolated instance's `.minecraft`. An absolute `-Dnintekkit.controls.file=...` property can select another private path.

The mapper requires an explicit `--context-file` and the actual Java `--pid`. It accepts schema 1, matching PID, monotonic sequence, boolean world/GUI flags, GUI class or null, and a client-tick timestamp no older than 2500 ms. The file must be an ordinary file owned by the current user with no group/other permissions; the producer uses a private directory and atomic replacement. Missing, malformed, stale, wrong-PID or unknown context releases all generated holds and suppresses input. A loading state with neither a world nor a GUI is unknown. Context changes, including changes between GUI classes, release holds and require neutral controls before rearming. Thus holding A while closing inventory cannot become a jump, and holding a GUI click cannot become mining in the world.

## Installation and read-only checks

Fresh instance installation copies the mapper, lifecycle helper, screen-lock inhibitor and desktop-entry helper under `$HOME/.local/share/nintekkit/bin` with mode 700, after a syntax preflight and before/after-copy checks. It does **not** automatically enable live input or install the companion. For an already installed client, copy updated scripts separately with backups; the instance installer deliberately refuses to replace an existing instance. Keep the source in Nintekkit; no pip installation is required. Screen-lock inhibition uses the Switch's existing `dbus-python` runtime.

Run in the Switch's existing graphical session. Over SSH, use the installed `desktop-run.py` to obtain its actual display authorization; do not guess DISPLAY or enable X forwarding. During setup, a diagnostics copy and a separate mapper staging directory may be used; the normal installed paths are shown here.

```bash
desktop="$HOME/.local/share/nintekkit/bin/desktop-run.py"
mapper="$HOME/.local/share/nintekkit/bin/nintekkit_controller.py"
context="$HOME/.local/share/nintekkit/prism/instances/Nintekkit-1.2.4/.minecraft/nintekkit-controls/state.json"
python3 "$desktop" -- python3 "$mapper" --inspect
# With the companion running, inspect its matching state or log intended actions:
python3 "$desktop" -- python3 "$mapper" --inspect --pid ACTUAL_GAME_JAVA_PID --context-file "$context"
python3 "$desktop" -- python3 "$mapper" --dry-run --seconds 10 --pid ACTUAL_GAME_JAVA_PID --context-file "$context"
```

`--inspect` discovers the exact combined joystick and both exact XInput names, checks four axes/eighteen buttons, and exercises read-only ARM64 X11/XTest-extension/XInput2 queries. With PID/context options it also reports the verified control context. It never floats devices or injects events. `--dry-run` reads raw events and prints intended actions through a logging backend; it also never floats or injects. Without verified context it remains inactive. The existing desktop mapper remains active during dry-run, so physical buttons retain their desktop actions.

Actual Switch read-only evidence: `--inspect` passed on ARM64 with both original X devices/masters and enabled driver flags, and a two-second neutral `--dry-run` exited successfully without intended actions. The earlier static layout received a live user test and prompted this revision. Those checks establish native query compatibility; they do not validate the revised context bridge, button behavior or camera feel.

The live XRes 1.2 query also resolved the actual **Tekkit 2** window to its Java PID and confirmed focused ownership while `_NET_WM_PID` was absent. This validates the ARM64 ownership-query ABI, not physical input. Switching the real game to fullscreen recreated its window; normal PID mode queries current ownership and does not cache the old window ID. A synthetic recreation regression covers that behavior. An explicitly pinned `--focus-window` must be refreshed after recreation.

## Explicit game-scoped use

Launch the legitimate isolated client first. Identify its **actual Java PID**, using executable paths and process state without copying token-bearing command lines. Focus the real Minecraft window, release both sticks and buttons, then run:

```bash
python3 "$desktop" -- python3 "$mapper" --run --pid ACTUAL_GAME_JAVA_PID --context-file "$context"
```

The default title expression accepts the observed exact title **Tekkit 2** and the normal Minecraft title prefix: `^(?:Tekkit 2$|Minecraft(?:\s|$))`. Injection additionally requires a focused window or ancestor owned by the watched Java PID. The mapper uses a valid `_NET_WM_PID` first, then the XRes 1.2 local-client PID API when that property is absent. The real LWJGL 2 window omits `_NET_WM_PID`; XRes avoids guessing ownership from a title. Its ABI was verified against the [official libXres 1.2.2 source](https://xorg.freedesktop.org/archive/individual/lib/libXres-1.2.2.tar.xz). Unknown/mismatched PID or focus fails closed. Explicit `--focus-window 0xOBSERVED_ID` further restricts the focused ancestor chain; Java PID and context ownership are still required. Focus loss releases generated holds; focus regain requires neutral controls before rearming.

The script waits for a complete initialization batch and 100 ms of neutral input before taking ownership. It snapshots the current XInput master attachments and driver flags into a private runtime JSON file, then floats **both** original combined pointer and keyboard. The raw joystick remains available. This prevents duplicate output from the existing desktop mapper, including its Capture-toggle behavior. The mapper never edits the system joystick file.

World movement uses a deadzone followed by consistently held keys, without the old Xorg driver's PWM pulses. Pointer/camera motion runs at up to sixty updates per second, with a nonlinear speed curve and no large catch-up motion after scheduling delays. Initial values are movement deadzone 8000, camera deadzone 6500, world camera speed 900, GUI left-stick speed 700 and GUI right-stick speed 200 pixels/second; these are unvalidated ergonomic defaults. `--camera-speed`, `--gui-speed` and `--gui-fine-speed` allow measured tuning. Check drift, diagonal movement, camera precision and GUI usability on the device. The script does not cap game FPS or start capture.

Ctrl+C, SIGTERM, SIGHUP, target-process exit, unplug, and errors release mapper-owned key/mouse holds and restore the saved X attachments/flags. It never kills the game. `--reconnect` additionally waits after unplug, then rediscovers the combined device and X identities, snapshots fresh desktop state and requires initialization/neutral input again. A protocol, ownership or restoration error stops rather than repeatedly taking control.

**Prism can dispatch a launch to an existing GUI and exit immediately**, so that command's exit does not identify the game's lifetime. Use the actual Java `--pid`; do not wrap `prismlauncher --launch` as a foreground process and assume its CLI remains alive.

## Optional launch lifecycle helper

`play_with_controls.py` provides the shared Prism/Java presentation and awake lifecycle. Plain `launch.sh` passes `--no-controls`; `launch.sh --controls` also starts the Joy-Con mapper. Fresh installation copies all helpers together. Physical controls and restoration require device validation.

The helper takes one supervisor lock, refuses pre-existing or ambiguous Java clients in the exact isolated `.minecraft` directory, dispatches Prism, and polls once per second for a new same-user Java executable with that exact working directory. It tracks PID, executable, working directory and process start time; it never reads command lines, environments or authentication tokens. A successful Prism CLI exit does not end the wait. The default five-minute deadline is for the Java process to appear, not for the pack to finish loading.

Once exactly one Java process appears, the helper starts the mapper with that actual `--pid` and the isolated `.minecraft/nintekkit-controls/state.json` path. The mapper still requires fresh context, verified window ownership, focus, initialization and neutral controls. Game exit, helper stop/SIGTERM, PID reuse or ambiguity ends the mapper session. Cleanup sends SIGTERM only to its mapper child and waits for restoration; it never kills Java or Prism. If mapper cleanup times out, it reports the PID/recovery requirement and does not escalate to SIGKILL.

During that Java lifetime the helper owns two game-scoped cookies on the existing user's session bus. `org.freedesktop.ScreenSaver.Inhibit` prevents automatic locking. A separate PowerDevil `AddInhibition(uint32(4), app, reason)` prevents automatic display dimming/blanking; policy 4 is `ChangeScreenSettings` and also implies session-interruption inhibition. This direct cookie has a distinct application label containing the Java PID and helper PID. It waits across regular polls for KDE's five-second enforcement delay, then requires its own application/reason in `ListInhibitions` plus `HasInhibition(4)` before logging `POWERDEVIL_DISPLAY_VERIFIED`. A returned cookie or someone else's inhibition does not satisfy verification. [KDE policy API](https://github.com/KDE/powerdevil/blob/72089c7129f4784b0100bf14847f6fbc93b97f74/daemon/powerdevilpolicyagent.h#L69-L98)

Each lease tracks its own service's unique owner, reacquires independently after that service restarts, and releases only its stored cookie on exit/signals/errors, including a mapper cleanup timeout. Owner lookups and display calls use two-second D-Bus deadlines; pending enforcement does not sleep inside a refresh. The screen-lock call retains its three-second deadline. Missing/failed verification reports the condition and retries while the game lives. The direct display lease covers a PowerDevil-only restart even when ScreenSaver's initial delegated display cookie is lost. The persistent KDE desktop sleep service remains separate from these game leases.

This helper changes no global power, lock or password settings and sends no simulated activity. Manual lock, explicit suspend/power requests and critical-battery actions retain their existing behavior; manually locking can still allow display blanking. It never unlocks a screen. The code and fake-bus tests do not establish the cause of a past shutdown or prove an uninterrupted real idle-timeout test.

For an already running client, a separate bounded lease can be started without restarting the game:

```bash
python3 "$desktop" -- python3 "$HOME/.local/share/nintekkit/bin/screen_awake.py" --pid ACTUAL_GAME_JAVA_PID
```

It watches only a same-user Java executable and its process start time, and releases both leases when that identity exits or on Ctrl+C/SIGTERM/SIGHUP. It reads no command line, environment or account tokens. An already locked desktop still requires the user's normal local unlock. Confirm the actual `POWERDEVIL_DISPLAY_VERIFIED` log and matching application/reason entry before treating automatic display protection as active, then validate it past the configured idle timeout on the Switch.

Read-only inspection and the future opt-in route are:

```bash
python3 "$desktop" -- python3 "$HOME/.local/share/nintekkit/bin/play_with_controls.py" --inspect
# Only after physical validation; starts a fresh client and its mapper lifecycle:
python3 "$desktop" -- bash "$HOME/.local/share/nintekkit/bin/launch.sh" --controls
```

An already running client must use the manual mapper command; the helper deliberately refuses to adopt it. No live helper dispatch, floating or injection was performed during helper implementation/tests.

The helper also accepts optional `--server HOST[:PORT]` for Prism's direct-join route. Before dispatch it creates one private Java 1.12.2 NBT bookmark named **Existing Tekkit world** only if the isolated list is absent. Existing lists are preserved without reading or rewriting them; strict generation refuses unsafe paths or active instance Java. The endpoint is never echoed or tracked in repository configuration. This option belongs to `play_with_controls.py`, not `launch.sh`.

For a click-to-launch entry, first validate the revised physical controls and restoration, then run as the Switch desktop user, without sudo:

```bash
python3 "$HOME/.local/share/nintekkit/bin/enable_controls.py" --install
# Undo only the unchanged generated Joy-Con entry:
python3 "$HOME/.local/share/nintekkit/bin/enable_controls.py" --remove
```

This adds **Nintekkit Joy-Cons — Tekkit 2** to the application menu with `launch.sh --controls`; the original **Nintekkit — Tekkit 2** entry stays available. It checks the installed scripts and expected 0.2.0 context companion, creates its separate file exclusively, and refuses to replace or remove foreign edits. Repeated installation is idempotent. Removal works after helpers or companion have been removed. It saves no server endpoint and creates no autostart service.

Its executable path uses the [Desktop Entry specification's quoting and literal-percent rules](https://specifications.freedesktop.org/desktop-entry/latest/exec-variables.html); unsupported control/non-ASCII characters and equals signs are refused.

## Recovery and validation

Snapshots remain in the user's runtime directory and their paths are printed before floating. A mapper lock prevents concurrent Nintekkit input owners. Normal exit handles restoration automatically. SIGKILL or loss of the X server cannot run Python cleanup. After an unclean termination, explicit recovery in the same user/display can restore the saved attachments and flags:

```bash
python3 "$desktop" -- python3 "$mapper" --run --restore-state /absolute/path/to/printed-snapshot.json
```

That command restores XInput state only. If XTest holds remain stuck after a forced kill, add `--release-stuck` while all physical controls/keys are released. This explicitly issues releases for the finite set of keys/buttons this mapper can generate; it does not press anything. It may release those same keys if held on another keyboard, so use it as deliberate recovery, never automatic startup. Recovery refuses to run while another mapper owns its lock and will not attach unrelated recycled IDs or changed masters.

Before accepting controls, physically verify forward/back/diagonal holds, simultaneous jump and movement, sprint, drift, camera precision, focus loss/regain, inventory, every hotbar slot, JEI recipe/use/search access, crafting, shift transfers, right-click quantities and actual machine GUIs. Test exit, SIGTERM and disconnect/reconnect restoration separately. Preserve the measured settings and device connection mode. Keep desktop/video capture off during FPS benchmarks.

Run synthetic tests from the repository on Windows or Linux:

```text
python -m unittest discover -s tests/controller -v
```

The tests use fake events, XTest outputs, focus windows, private context fixtures and XInput commands. They verify protocol parsing, neutral/resync behavior, held/edge actions, contextual A/B behavior, GUI pointer speeds, stale-state release, Java discovery, optional server argument construction, PID focus isolation and transactional restoration; they do not inject real input. The pre-revision mapper/helper/docs are preserved locally in `.local/backups/controller-switch-layout-before-20261024T170806Z`. Roll back by stopping the mapper normally and restoring the previous backed-up scripts, or removing its optional launcher integration. The existing controller drivers, pairing service and global desktop map remain the baseline.

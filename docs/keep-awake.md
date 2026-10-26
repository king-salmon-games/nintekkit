# Keep the Switch awake

**Applied and verified on the Switch:** `nintekkit-desktop-awake.service` is enabled in the desktop user's systemd manager, is active, and holds its own verified KDE PowerDevil inhibition. After the user restarted the Switch on 2026-10-24, the service automatically restarted with login and reacquired its own inhibition. The installed helper is under `~/.local/share/nintekkit/power-setup/`.

The current Switch uses Plasma 5.27 on X11. Direct non-root logind sleep inhibition was denied, so the installed solution uses the existing KDE session's supported inhibition interface. It does not need sudo or change global power settings.

Reproduce this user-level setup by keeping `kde-keep-awake.sh` and `kde-awake.py` together and running as the desktop user:

```bash
bash ~/.local/share/nintekkit/power-setup/kde-keep-awake.sh install
bash ~/.local/share/nintekkit/power-setup/kde-keep-awake.sh status
```

The script preserves a previous unit before any replacement, refuses a different existing unit, and verifies both enabled/active state and the running process's own application/reason in PowerDevil's inhibitor list. [PowerDevil 5.27](https://invent.kde.org/plasma/powerdevil/-/blob/Plasma/5.27/daemon/powerdevilpolicyagent.cpp) deliberately applies new inhibitors after five seconds; verification waits for that rather than treating the returned cookie as proof. Its [Freedesktop connector](https://github.com/KDE/powerdevil/blob/Plasma/5.27/daemon/powerdevilfdoconnector.cpp) applies the interruption policy and releases the lock when the owning DBus client exits. The helper keeps that connection alive and reacquires the inhibition after PowerDevil restarts.

Undo the installed desktop solution:

```bash
bash ~/.local/share/nintekkit/power-setup/kde-keep-awake.sh stop
```

This disables its login startup and releases only its own inhibition. Audit/backup files are under `~/.local/state/nintekkit/desktop-awake/`. The enabled user unit is `~/.config/systemd/user/nintekkit-desktop-awake.service`. It acts while this user's KDE session runs; it does not make a claim about an unattended login greeter or explicit manual suspend. It does not inhibit screen locking. Keep the device powered appropriately for extended awake operation.

The earlier temporary setup unit was stopped after the persistent unit's separate inhibitor was verified. Fresh SSH checks and the user's subsequent restart confirmed persistence. The exact former sleep timeout is unknown, so an extended idle-timeout test remains outstanding.

## Automatic locking during the game

The long real client initialization exposed a separate automatic screen lock while the verified PowerDevil sleep inhibitor remained active. A locked screen requires local user authentication; no helper unlocks it.

`scripts/controller/screen_awake.py` uses the existing `org.freedesktop.ScreenSaver.Inhibit` API while one same-user Java PID/executable/start-time identity lives. The Joy-Con launch supervisor acquires this lease as soon as the actual game process appears and releases it after the game or supervisor exits, including error/signal cleanup. It reacquires after service-owner changes. The persistent desktop power unit remains separate.

The game helper also owns a direct PowerDevil `ChangeScreenSettings` lease using mask **4**, independently of ScreenSaver's delegated request. It prevents ordinary automatic dimming/DPMS blanking and implies interruption inhibition. Its distinct application label contains game/helper PIDs. Verification waits nonblockingly across polls for KDE's five-second delay, then requires both that exact application/reason entry and `HasInhibition(4)` before logging `POWERDEVIL_DISPLAY_VERIFIED`. A PowerDevil-only restart reacquires this lease independently of the ScreenSaver owner. Each stored cookie is released only through its pinned service owner; global settings and the persistent service are unchanged. See [the exact policy enum and API](https://github.com/KDE/powerdevil/blob/72089c7129f4784b0100bf14847f6fbc93b97f74/daemon/powerdevilpolicyagent.h#L69-L98) and [controls](controls.md).

These leases preserve manual lock/power actions and critical-battery handling. The actual post-restart game supervisor logged `POWERDEVIL_DISPLAY_VERIFIED` with its own Java/helper application label after the enforcement delay. An extended idle-timeout test remains necessary; synthetic tests establish the helper's state transitions only.

For an already running game, the standalone helper can hold the same game-scoped lease:

```bash
python3 ~/.local/share/nintekkit/bin/screen_awake.py --pid ACTUAL_GAME_JAVA_PID
```

Ctrl+C releases its own cookies. It never calls `Lock`, `SetActive` or `SimulateUserActivity`, changes no password or locking configuration, and does not dismiss an existing lock. Manual locking remains available. This feature needs the already installed system `dbus-python`, without a pip install or sudo. The second launch used a separate transient user unit; the post-restart Joy-Con launch manages both leases within the supervisor itself.

## Power evidence after an outage

Run `scripts/diagnostics/power_snapshot.py` on the Switch as the desktop user. It reads boot identity, uptime, battery/USB fields, thermals, known service/inhibitor status and bounded current/previous-boot journal records. It emits only categorized events, not raw journal messages or account/process arguments. Missing journal history is reported as unavailable. The user's restart was confirmed by a changed boot ID, but the previous boot journal was not retained, so the cause of the earlier shutdown remains unknown.

The benchmark sampler also records boot ID/uptime and allowlisted battery/USB fields each second. Numeric values retain native sysfs units and signs, `Unknown` remains literal, and unavailable fields are not treated as zero. These readings establish observed power state, not a past shutdown cause. No global power policy or journal configuration is changed.

## Optional system-wide alternative (not installed)

The following root alternative is prepared for use if inhibition before desktop login is required. The earlier request to run this sudo command is no longer needed for the installed KDE solution.

Immediate protection in the already logged-in Switch shell:

```bash
sudo systemd-inhibit --what=idle:sleep --mode=block --who=Nintekkit --why="Minecraft setup" sleep infinity
```

Enter sudo authentication locally if requested. Leave the command running; Ctrl+C releases its inhibitor. This is temporary and should not be described as a permanent fix.

For a system-wide inhibitor, transfer `scripts/switch/keep-awake.sh` and its adjacent `nintekkit-awake.service`, then run on the Switch:

```bash
sudo bash /absolute/path/to/keep-awake.sh install
bash /absolute/path/to/keep-awake.sh status
```

The installer records state and backs up an existing same-named unit under `/var/lib/nintekkit/power/`, refuses to replace a different existing unit, and enables the dedicated system service for subsequent boots. It requires an active service, an enabled boot entry, and an actual **Nintekkit / idle + sleep / block** entry in logind's inhibitor list with the service's own main PID before reporting success. A separate temporary inhibitor cannot satisfy this check. A live SSH check beyond the former idle timeout remains necessary to validate the observed disconnect problem; an inhibitor cannot establish the reason for every network disconnect.

The [Ubuntu Noble manual](https://manpages.ubuntu.com/manpages/noble/man1/systemd-inhibit.1.html) documents the child-process lifetime and block semantics. The service survives SSH logout and starts again after reboot. It leaves screen blanking/locking, shutdown, kernel/graphics drivers, clocks and firmware alone. Privileged explicit suspend can override an inhibitor; see [systemd's inhibitor documentation](https://systemd.io/INHIBITOR_LOCKS/). Keep the device powered appropriately for extended awake operation.

Undo only this service's change:

```bash
sudo bash /absolute/path/to/keep-awake.sh stop
```

That stops the inhibitor, disables boot startup and retains the dormant unit/audit files. It does not unmask targets or change any previous desktop power setting. Do not restart logind or reboot merely to apply this service.

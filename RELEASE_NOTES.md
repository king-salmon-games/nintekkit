# Nintekkit — public alpha draft

Nintekkit provides source and setup tools for an isolated **Tekkit 2 1.2.4 / Minecraft 1.12.2 / Forge 14.23.5.2860** client on an existing Switchroot L4T Ubuntu ARM64 installation. The game runs locally on the Switch CPU and NVIDIA Tegra GPU and connects to a matching server. It is not a streamed desktop client.

This is a **source-only alpha**, not a preinstalled game image. Minecraft, Tekkit/mod JARs, launcher accounts, server addresses and world data are not included. The original Nintekkit code is MIT licensed; third-party software retains its own licenses.

## What is included

- An importer and verifier for the exact pack release, with a reviewed mod hash lock, isolated Prism instance and preserved pack content/configuration. Existing optimization mods remain in place.
- ARM64 Java 8 and LWJGL 2/native dependency checks, local NVIDIA renderer diagnostics and scripts for the existing graphical session.
- A landscape presentation correction. On the tested rotated display, native Minecraft fullscreen used portrait dimensions inside a landscape window. Nintekkit keeps native fullscreen off and requests borderless fullscreen for the verified game window. Live checks confirmed matching **1280×720** window, Minecraft framebuffer and OpenGL viewport dimensions.
- Optional Joy-Con controls with client-provided world/GUI context, plus a launcher that follows the actual Java process rather than Prism's short-lived dispatch command.
- Game-lifetime screen-lock/display inhibition, with cleanup when the game or supervisor exits. Manual locking, explicit power actions and critical-battery safeguards retain their behavior.
- Optional frame observation, telemetry and analysis tools. Recorded render-loop intervals describe CPU frame timing, not GPU execution or physical display presentation.

## Joy-Con controls

The mapper uses the existing Combined Joy-Con device and desktop drivers. It temporarily takes control during an explicit controller session and restores the saved desktop attachments and mapping flags on normal cleanup. Fresh installation leaves controller input opt-in; contextual input requires the original client companion.

| Control | In the world | In menus, inventory or machine screens |
| --- | --- | --- |
| Left / right stick | Movement / camera | Coarse / fine pointer movement |
| A | Jump | Select or hold left click |
| B | Hold crouch | Back/close |
| Y / X | Inventory / player crafting | Back/close / unbound |
| ZR / ZL | Attack or mine / use or place | Left / right click |
| L / R | Previous / next hotbar slot | Scroll |
| Left stick click | Hold sprint | Hold Shift; combine with A or ZR for quick transfer |
| D-pad up / down | Unbound | JEI recipe / use lookup |
| Minus | Player list | JEI search focus |
| Capture | Unbound | Toggle the existing Onboard keyboard |

Select a text field, then use Capture to show Onboard. Its verified dock is temporarily parented to the game so it can appear above borderless presentation without taking game focus. Use touch or pointer + A to select keys; Capture hides it again. The helper does not record entered text.

Input is released on focus loss, stale context or GUI/world transitions, and neutral controls are required before resuming. Java menus remain mouse-based: automatic slot navigation and Bedrock's separate crafting interface are not implemented. X opens Java's existing inventory/player-crafting screen.

## Prerequisites

- A working Switchroot L4T Ubuntu ARM64 desktop with hardware-accelerated NVIDIA graphics. The tested environment uses Ubuntu 24.04, KDE and X11; other desktop/display stacks are unvalidated.
- A licensed Minecraft Java account and a legally acquired **Tekkit 2 1.2.4** client. Complete legitimate sign-in locally in Prism. A reachable server must match the required pack and content.
- A full ARM64 Java 8 runtime, a compatible ARM64 Prism installation, and matching ARM64 LWJGL 2, input and audio natives. A JVM alone is insufficient.
- Python 3 and the documented preparation/transfer tools. The original companion requires a JDK to build. Joy-Con integration needs the existing readable combined device, XTest/XInput/XRes support and the companion; the keyboard and awake helpers also need the existing session services and `dbus-python`. Onboard is required for Capture text entry.

Setup assumes the existing OS, kernel and graphics stack already work. It makes no OS upgrade, kernel, firmware, partition, graphics-driver or clock changes, and does not modify the server or world. Follow the repository installation and rollback instructions; this alpha is not a guide to installing Switchroot itself.

## Validation and current limits

On **October 25, 2026**, a user reported: “It worked I played it.” This is a user report of gameplay, not independent acceptance of the intended server world, all controls or performance.

Live checks established native ARM64 libraries, local NVIDIA acceleration, the corrected landscape dimensions, and keyboard show/hide, focus retention and transient-parent restoration. Automated tests cover importer isolation, contextual button behavior, quick-transfer bindings, ownership checks and cleanup; these tests do not establish complete physical control usability.

The **sustained 30 FPS at 1280×720 target remains unmeasured**. There are no qualifying ten-minute busy-base and ten-minute traversal recordings, and no reported gameplay average FPS, 1% low, p99 frame time or sustained-dip result. Correct menu geometry and successful play do not establish this target. Uncapped headroom measurements and gameplay validation of textures, machines, pipes, audio and controls remain outstanding.

The pack is demanding on the tested 4 GB Switch. Startup has taken roughly ten minutes, and startup/menu telemetry showed memory pressure and swap activity. The conservative 1792 MiB maximum heap is provisional, not a proven budget for every world; increasing it can reduce memory available to Linux and graphics. Camera feel, GUI precision, quick transfers and text entry still need broader physical testing.

Keep Minecraft's native fullscreen setting off and use the Nintekkit launcher; F11 can reintroduce the portrait-dimension defect. Direct Prism launch bypasses the presentation/awake supervisor. The complete fresh installation path still needs acceptance testing; tested updates to an existing client do not prove every new installation. After an unclean mapper termination, follow the documented saved-state recovery procedure before re-enabling input.

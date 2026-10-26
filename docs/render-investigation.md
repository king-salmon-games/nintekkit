# Confirmed fullscreen dimension mismatch

Checked 2026-10-24. No Switch changes were performed by this source investigation. The main task performed the live comparisons and original diagnostic attachment below. This document distinguishes the confirmed rendering dimensions, an offline parser reproduction, and a proposed correction.

## Observed comparison

The real Tekkit 2 multiplayer GUI in the 1280×720 fullscreen X window rendered only through approximately x=720; the remaining right strip was black and the button group was centered near x=360. The screenshot is retained privately as `.local/evidence/reboot-game-5842.png`.

In the same running Java process, one guarded F11 toggle changed to a decorated **1280×648** window. The menu then filled the entire 1280-pixel width, with its heading and upper-right button visible. No pack restart or mod change intervened. Private evidence: `.local/evidence/reboot-game-5842-windowed.png`.

An original one-shot diagnostic then captured existing dimensions on Minecraft's scheduled main thread in both states. The [MIT diagnostic source](../patches/render-diagnostics/README.md), version **0.1.1**, was built with Java 8; its JAR SHA256 is `deaa3264134fa7d75d74f7e4bc8d07b0cbfbe86daca0dd86439067c4e3d6cf95`. Attachment loads the diagnostic into the JVM but does not transform classes, resize the game, alter fields or GL state, send packets, or inspect accounts. Its only GL call reads `GL_VIEWPORT`.

| Layer | Faulty Minecraft fullscreen | Working decorated window | Working WM fullscreen |
| --- | --- | --- | --- |
| Actual X window | **1280×720** | 1280×648 | **1280×720** |
| Minecraft display | **720×1280** | 1280×648 | **1280×720** |
| Current GUI | 360×640 | 640×324 | 640×360 |
| Framebuffer and its texture | **720×1280** | 1280×648 | **1280×720** |
| LWJGL width/height | **720×1280** | 1280×648 | **1280×720** |
| LWJGL fullscreen flag | true | false | **false** |
| LWJGL selected mode | 720×1280 | 1280×720 | 1280×720 |
| GL viewport | `[0,0,720,1280]` | `[0,0,1280,648]` | **`[0,0,1280,720]`** |

The live XRandR evidence independently shows logical output geometry **1280×720**, rotation **left**, and starred raw modeline **720×1280**. The mismatch therefore reaches LWJGL, Minecraft, GUI, framebuffer and GL viewport: the game renders a portrait image into a landscape window. A framebuffer-only or GUI-only stale size is ruled out by these readings. Private numeric evidence is under `.local/evidence/render-evidence-20261024T183525Z/`, including `render-fullscreen-final.json`, `render-windowed-final.json`, `render-borderless-final.json` and `xrandr.txt`.

The final one-variable comparison applied EWMH window-manager fullscreen while leaving Minecraft/LWJGL fullscreen **false**. The menu then visibly filled 1280×720 without titlebar or panel, and the diagnostic confirmed consistent dimensions in the last column. Screenshot: `.local/evidence/reboot-game-5842-borderless.png`. This establishes correct 720p menu presentation for that running client and mechanism; it does not establish the fresh offline candidate's launch integration, FPS, world compatibility or successful server login.

## Exact artifacts and provenance

Prism's [LWJGL 2 component](https://meta.prismlauncher.org/v1/org.lwjgl/2.9.4-nightly-20150209.json) uses the ordinary Mojang-hosted Java library alongside the ARM64 native classifier:

| Artifact | Verified identity |
| --- | --- |
| LWJGL Java JAR | `2.9.4-nightly-20150209`, SHA1 `697517568c68e78ae0b4544145af031c81082dfe` |
| ARM64 native JAR | SHA1 `63ac7da0f4a4785c7eadc0f8edc1e9dcc4dd08cb`; SHA256 `a0d5fc125b5c662d46e7af0987efe5cd30a4d123a73deb38b4676dca84a72837` |

The native bundle is pinned at binary repository commit `9d58d749d73815b29150c84d28e73b5d7dae15ab`; its LWJGL file was introduced by [commit `24e7a3e0…`](https://github.com/theofficialgman/lwjgl3-binaries-arm64/commit/24e7a3e0e3f27bab3894f0f591dda5c947ebfd83). The bundle manifest says `Created-By: 16.0.1 (Private Build)` and does not attest a source commit. The author's [source guidance](https://github.com/theofficialgman/piston-meta-arm64#sources) links the LWJGL fork. Its [February 9, 2015 commit `e4b098c5…`](https://github.com/theofficialgman/lwjgl/commit/e4b098c5e20b9f4465e3a5efc34cc40684df259d) is a date-matching source candidate, not a proven binary-source mapping. ARM64 native loading was separately verified; a dimensions bug would not contradict that result.

## Source path explaining the confirmed mismatch

The legacy [XRandR parser](https://github.com/theofficialgman/lwjgl/blob/e4b098c5e20b9f4465e3a5efc34cc40684df259d/src/java/org/lwjgl/opengl/XRandR.java#L63) takes current mode dimensions from the starred modeline. Its header parser retains position but discards the header's logical width/height, and `Screen` does not represent rotation. A rotated `720x1280` mode can consequently become a portrait Java display mode even if the desktop geometry is landscape `1280x720`. X.Org documents [rotation and output transforms separately from mode selection](https://xorg.freedesktop.org/archive/X11R7.5/doc/man/man1/xrandr.1.html).

[LinuxDisplay initialization](https://github.com/theofficialgman/lwjgl/blob/e4b098c5e20b9f4465e3a5efc34cc40684df259d/src/java/org/lwjgl/opengl/LinuxDisplay.java#L729) obtains its desktop mode through that parser. [Display](https://github.com/theofficialgman/lwjgl/blob/e4b098c5e20b9f4465e3a5efc34cc40684df259d/src/java/org/lwjgl/opengl/Display.java#L652) propagates native resize dimensions only while windowed; fullscreen `getWidth()`/`getHeight()` return the selected mode. This permits X-window and Java dimensions to disagree. The native [geometry getters](https://github.com/theofficialgman/lwjgl/blob/e4b098c5e20b9f4465e3a5efc34cc40684df259d/src/native/linux/opengl/org_lwjgl_opengl_Display.c#L342) use `XGetWindowAttributes`, so actual drawable geometry is available below that Java policy.

This path matches the live portrait dimensions at every Java/rendering layer and the corrected windowed dimensions. Ordinary GUI scale does not cause this mismatch. Local bytecode inspection also confirms that this Java library's Linux pixel-scale factor is constant 1; generic HiDPI environment flags are not a supported explanation or correction here. The exact native-source provenance limitation remains separate from the now-confirmed runtime dimensions.

## Offline reproduction and downstream paths

An original reflection fixture exercised the **exact SHA1-matched LWJGL Java JAR** on Windows Java 8 without initializing `Display`, loading native graphics, querying X, or launching Minecraft. It called only the parser functions on synthetic data:

| Header geometry | Starred modeline | Returned Java display mode |
| --- | --- | --- |
| 1280×720 | 1280×720 | 1280×720 |
| 1280×720 | 720×1280 | **720×1280** |
| 720×1280 | 720×1280 | 720×1280 |

All three assertions passed. Original fixture and output are private scratch under `.local/upstream/render-investigation/`; no proprietary game classes were added to the repository. This independently reproduces the parser behavior corresponding to the Switch's observed XRandR geometry.

Read-only bytecode inspection of the local Minecraft 1.12.2 JAR (SHA256 `8ada07da5ee77dad3527bd7278fbd05ee1fc8a597813b216a871a2d7d64cc64f`), interpreted with the [Forge-hosted MCP mappings](https://maven.minecraftforge.net/de/oceanlabs/mcp/mcp_config/1.12.2-20200226.224830/mcp_config-1.12.2-20200226.224830.zip), identified these downstream paths. Runtime coremods may alter them:

| Layer | Relevant fields/methods and behavior |
| --- | --- |
| Minecraft | `displayWidth`/`field_71443_c`, `displayHeight`/`field_71440_d`; fullscreen toggle `func_71352_k` copies selected mode dimensions. Resize checking `func_175604_i` skips fullscreen. |
| Resize/GUI | `resize`/`func_71370_a(int,int)` updates dimensions, current-screen resolution and framebuffer; `ScaledResolution` derives GUI size from Minecraft dimensions. Current GUI fields are `field_146294_l` and `field_146295_m`. |
| Framebuffer | `framebufferMc`/`field_147124_at`; texture size `field_147622_a`/`field_147620_b`, framebuffer size `field_147621_c`/`field_147618_d`. Binding with viewport update and final framebuffer render set viewport from their dimensions/arguments. |

The public Forge [Minecraft patch](https://github.com/MinecraftForge/MinecraftForge/blob/3effde4f1fc9d14d6ed1dbf6bebc39c2b18780e1/patches/minecraft/net/minecraft/client/Minecraft.java.patch) and [Framebuffer patch](https://github.com/MinecraftForge/MinecraftForge/blob/3effde4f1fc9d14d6ed1dbf6bebc39c2b18780e1/patches/minecraft/net/minecraft/client/shader/Framebuffer.java.patch) were inspected for integration context. This pinned branch head is not asserted to be the exact 2860 source release.

## Next build and validation

The live dimension comparison, including corrected 1280×720 borderless presentation, is complete. Preserve the numeric evidence when comparing the next candidate; viewport depends on render phase, and the diagnostic records one scheduled-main-thread instant rather than an entire rendered frame. A repeated snapshot and visible GUI comparison should confirm a fresh installed candidate before performance measurement.

The candidate now stages **Minecraft windowed at 1280×720**, with `fullscreen:false` and unchanged explicit width/height overrides; the launcher helper can request PID-verified window-manager fullscreen separately. This preserves the geometry path demonstrated above. An undecorated property is optional, not required by the successful live EWMH comparison; the [legacy Linux window-creation code](https://github.com/theofficialgman/lwjgl/blob/e4b098c5e20b9f4465e3a5efc34cc40684df259d/src/java/org/lwjgl/opengl/LinuxDisplay.java#L473) supports it. The existing driver/natives and desktop rotation remain in use. See [the offline candidate record](next-build.md) for exact content/hash and remaining integration checks.

If that candidate is insufficient, a focused client compatibility helper could reconcile a proven mismatch through Minecraft's existing resize path on its rendering thread, after stable validated drawable dimensions. Test repeated unchanged sizes, zero/minimized sizes, window recreation, GUI transitions and no repeated framebuffer allocation. Do not merely assign display fields or reset the viewport every frame: GUI/FBO state must stay consistent. No such corrective helper has been implemented or applied by this investigation.

A library fork would need to separate logical rotated geometry from raw mode dimensions; blindly swapping every `Screen.width/height` can make its `xrandr --mode` request invalid. Preserve the LWJGL license and establish source/build provenance before distributing a rebuilt artifact. `-DLWJGL_DISABLE_XRANDR=true` is only a secondary diagnostic: the source falls back to XF86VidMode, which can fail or report unsuitable modes. Neither native replacement nor a graphics/kernel upgrade is justified by the current evidence.

Acceptance still requires full-width 1280×720 rendering, accurate GUI pointer coordinates, textures/audio/machines on the actual server, and the requested uncapped headroom plus ten-minute base/traversal measurements. A correctly drawn menu cannot satisfy those checks.

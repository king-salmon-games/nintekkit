# One-shot render diagnostic

SPDX-License-Identifier: MIT. The full license is in [LICENSE](LICENSE) and is
included in the diagnostic JAR as `META-INF/LICENSE`.

Original MIT Java 8 attach agent. It reads existing Minecraft, framebuffer, LWJGL
display and OpenGL viewport dimensions on Minecraft's scheduled main thread.
It does not transform classes, change game fields, resize the display, make GL
state changes, send packets, or inspect account data. `glGetInteger(GL_VIEWPORT)`
is the only OpenGL call. Attaching itself loads this diagnostic agent into the JVM.

Build using an existing JDK (no Minecraft or Forge compile dependency):

```powershell
python scripts/diagnostics/build_render_probe.py --javac 'C:\Program Files\Java\jdk1.8.0_211\bin\javac.exe'
```

The script refuses an existing output. Repeating with a distinct `--output` using
the same compiler produces the same JAR hash. Build metadata contains source
hashes and compiler version. JARs are ignored by Git.

Version 0.1.1 compiled with `javac 1.8.0_211` has SHA-256
`deaa3264134fa7d75d74f7e4bc8d07b0cbfbe86daca0dd86439067c4e3d6cf95`.
Its agent class is `RenderSnapshot`; this distinct name permits collection after
an earlier diagnostic class was loaded, without replacing that class. The OpenGL
buffer has room for 16 integers as required by LWJGL 2's argument checks; only the
four viewport values are exported. A viewport query failure retains the other
geometry and reports only `gl_error_class`.

For a same-user Linux target, use an ARM64 JDK 8 `tools.jar` matching the target
JRE. The helper may be compiled with Windows JDK 8 against its `lib/tools.jar`,
then run with the Linux target JDK's tools.jar and ARM64 attach library:

```text
javac -source 8 -target 8 -cp /path/to/tools.jar -d /private/helper-classes patches/render-diagnostics/tools/AttachRender.java
java -cp /private/helper-classes:/path/to/linux/tools.jar AttachRender PID /absolute/agent.jar /private/evidence/render-unique.json
```

Before attaching, independently verify the exact target PID, process owner,
executable and start identity. The agent accepts a new absolute filename matching
`render-[A-Za-z0-9_-]{1,80}.json` only in an existing canonical, nonsymlinked 0700
directory owned by the home directory owner. It creates output as 0600 and
refuses an existing file. Missing classes, fields or methods fail with only a
safe exception class in the output, without messages or stack traces. An invalid
output path fails before scheduling. A blocked Minecraft main thread may never
produce output; there is no render-thread fallback or forced thread execution.

Scheduling succeeds asynchronously; helper success alone does not confirm a
completed diagnostic. Read and validate the resulting JSON separately. Dimensions
describe the instant at which the task ran and do not measure FPS or rendering
correctness.

## Live integration check

On 2026-10-24 the exact 0.1.1 JAR above attached to the existing ARM64 Java
8u504 Tekkit 2 process on the Switch. The output was created with mode 0600 in
the private evidence directory. The scheduled task completed in the actual
Minecraft client and read `GuiMultiplayer` without changing the screen.

| Mode selected separately by the operator | Minecraft / framebuffer / LWJGL dimensions | GL viewport | LWJGL fullscreen |
| --- | --- | --- | --- |
| Minecraft fullscreen | 720×1280 | `[0,0,720,1280]` | true |
| Decorated window | 1280×648 | `[0,0,1280,648]` | false |
| Window manager fullscreen | 1280×720 | `[0,0,1280,720]` | false |

The operator independently observed a 1280×720 X display and X window during
both fullscreen comparisons. The portrait dimensions explain why Minecraft's
fullscreen GUI used only part of the landscape screen. Window manager
fullscreen retained windowed LWJGL behavior and produced matching 1280×720
dimensions. Those state changes were performed outside this read-only agent.

These are instantaneous geometry checks in the multiplayer menu. They do not
measure swap or presentation timing, FPS, stalls, server connectivity, or world
performance. The private raw evidence remains excluded from version control;
see [the render investigation](../../docs/render-investigation.md) for the
dimension comparison and source analysis.

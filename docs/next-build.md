# Remaining alpha validation

The existing client has user-reported gameplay, verified ARM64 native loading,
and a live 1280×720 borderless rendering correction. The next validation work is:

1. Repeat the documented installation on a fresh supported system.
2. Physically check movement, camera, inventory, whole-stack transfers, recipe
   lookup, crafting, machine GUIs and Capture keyboard input.
3. Verify textures, machines, pipes and audio in a matching existing server world.
4. Measure uncapped headroom after world warmup: at least ten minutes at a busy
   base and ten minutes of traversal, with desktop/video capture disabled.
5. Report average FPS, 1% low, p99 frame time, stalls, sustained dips, CPU/GPU load,
   memory/swap and thermals. Separate exceptional loading/teleports explicitly.
6. Retain only measured improvements, then choose and validate a final frame cap.

The target is sustained ≥30 FPS at 1280×720, with p99 ≤33.3 ms and no sustained
dips below 30. **There are currently no qualifying measurements.**

Use the [benchmark procedure](benchmark.md). Preserve the working L4T graphics
stack and server compatibility. Native Minecraft fullscreen must remain disabled
on the tested rotated display; see [render investigation](render-investigation.md).

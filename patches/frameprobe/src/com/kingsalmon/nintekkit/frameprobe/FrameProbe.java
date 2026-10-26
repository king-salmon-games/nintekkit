/* SPDX-License-Identifier: MIT */
package com.kingsalmon.nintekkit.frameprobe;

import net.minecraftforge.common.MinecraftForge;
import net.minecraftforge.client.event.GuiOpenEvent;
import net.minecraftforge.fml.common.Mod;
import net.minecraftforge.fml.common.event.FMLInitializationEvent;
import net.minecraftforge.fml.common.eventhandler.SubscribeEvent;
import net.minecraftforge.fml.common.gameevent.TickEvent;

/** Client observers; no packets, game content, coremod or method transforms. */
@Mod(modid = "nintekkit_frameprobe", name = "Nintekkit Client Tools", version = "0.2.0",
     acceptedMinecraftVersions = "[1.12.2]", clientSideOnly = true,
     acceptableRemoteVersions = "*")
public final class FrameProbe {
    private Recorder recorder;
    private ControlsContext controls;

    @Mod.EventHandler
    public void initialize(FMLInitializationEvent event) {
        String directory = System.getProperty("nintekkit.frameprobe.dir");
        try {
            controls = new ControlsContext();
            controls.start();
        } catch (Exception exception) {
            System.err.println("Nintekkit controls context unavailable: " + exception.getClass().getName());
        }
        if (directory != null && !directory.isEmpty()) {
        try {
            recorder = new Recorder(directory);
            recorder.start();
        } catch (Exception exception) {
            System.err.println("Nintekkit Frame Probe disabled: " + exception);
        }
        }
        MinecraftForge.EVENT_BUS.register(this);
    }

    @SubscribeEvent
    public void render(TickEvent.RenderTickEvent event) {
        if (event.phase == TickEvent.Phase.END && recorder != null) {
            recorder.frame(System.nanoTime());
        }
    }

    @SubscribeEvent
    public void clientTick(TickEvent.ClientTickEvent event) {
        if (event.phase == TickEvent.Phase.END && controls != null) controls.tick();
    }

    @SubscribeEvent
    public void guiOpen(GuiOpenEvent event) {
        if (controls != null) controls.guiOpen(event);
    }
}

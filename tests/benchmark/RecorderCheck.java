/* SPDX-License-Identifier: MIT; exercises recorder IO only, never real Minecraft. */
package com.kingsalmon.nintekkit.frameprobe;

public final class RecorderCheck {
    public static void main(String[] args) throws Exception {
        Recorder recorder = new Recorder(args[0]);
        recorder.start();
        long end = System.nanoTime() + 6000000000L;
        while (System.nanoTime() < end) {
            recorder.frame(System.nanoTime());
            Thread.sleep(5);
        }
    }
}

/* SPDX-License-Identifier: MIT; synthetic queue tests, not benchmark results. */
package com.kingsalmon.nintekkit.frameprobe;

public final class FrameRingCheck {
    public static void main(String[] args) throws Exception {
        FrameRing small = new FrameRing(2);
        if (!small.offer(11) || !small.offer(22) || small.offer(33)) throw new AssertionError("overflow");
        if (small.poll() != 11 || small.poll() != 22 || small.poll() != Long.MIN_VALUE) {
            throw new AssertionError("order/empty");
        }
        final FrameRing ring = new FrameRing(32);
        final int count = 100000;
        Thread producer = new Thread(new Runnable() {
            public void run() {
                for (int i = 0; i < count; i++) while (!ring.offer(i)) Thread.yield();
            }
        });
        producer.start();
        for (int i = 0; i < count; i++) {
            long value;
            while ((value = ring.poll()) == Long.MIN_VALUE) Thread.yield();
            if (value != i) throw new AssertionError("Concurrent FIFO corruption at " + i + ": " + value);
        }
        producer.join();
        System.out.println("FrameRing checks passed");
    }
}

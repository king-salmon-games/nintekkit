/* SPDX-License-Identifier: MIT */
package com.kingsalmon.nintekkit.frameprobe;

import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicLongArray;

/** Single producer/single consumer; never overwrites unread measurements. */
final class FrameRing {
    private final AtomicLongArray values;
    private final AtomicLong produced = new AtomicLong();
    private final AtomicLong consumed = new AtomicLong();

    FrameRing(int capacity) { values = new AtomicLongArray(capacity); }

    boolean offer(long timestamp) {
        long sequence = produced.get();
        if (sequence - consumed.get() >= values.length()) return false;
        values.set((int) (sequence % values.length()), timestamp);
        produced.lazySet(sequence + 1);
        return true;
    }

    long poll() {
        long sequence = consumed.get();
        if (sequence >= produced.get()) return Long.MIN_VALUE;
        long value = values.get((int) (sequence % values.length()));
        consumed.lazySet(sequence + 1);
        return value;
    }

    void discard() { while (poll() != Long.MIN_VALUE) { } }
}

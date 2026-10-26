/* SPDX-License-Identifier: MIT */
package com.kingsalmon.nintekkit.frameprobe;

import java.io.BufferedWriter;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardCopyOption;
import java.nio.file.StandardOpenOption;
import java.util.Properties;

/** All filesystem work runs on the worker; frame callbacks allocate nothing. */
final class Recorder implements Runnable {
    private final Path directory;
    private final FrameRing ring = new FrameRing(32768);
    private volatile boolean recording;
    private volatile boolean overflow;
    private volatile boolean running = true;
    private volatile boolean producing;
    private Thread worker;
    private BufferedWriter frames;
    private BufferedWriter marks;
    private Path runDirectory;
    private String runId = "";
    private String lastCommand = "";
    private long startNs;
    private long deadlineNs;
    private long previousNs;
    private long lastNs;
    private long frameCount;
    private Properties metadata;

    Recorder(String path) throws Exception {
        directory = Paths.get(path).toAbsolutePath();
        Files.createDirectories(directory);
        // An old pending start must never start a new process's capture.
        Properties pending = readRequest();
        lastCommand = pending.getProperty("id", "");
    }

    void start() throws Exception {
        status("ready", "");
        worker = new Thread(this, "Nintekkit-frame-writer");
        worker.setDaemon(true);
        worker.start();
        Runtime.getRuntime().addShutdownHook(new Thread(new Runnable() {
            public void run() {
                running = false;
                try { worker.join(3000); } catch (InterruptedException ignored) { }
            }
        }, "Nintekkit-frame-close"));
    }

    void frame(long timestamp) {
        if (!recording) return;
        producing = true;
        // The second read makes stopping a capture race-safe.
        if (recording && timestamp >= startNs && !ring.offer(timestamp)) {
            overflow = true;
            recording = false;
        }
        producing = false;
    }

    public void run() {
        try {
            while (running) {
                command();
                drain();
                if (frames != null && (overflow || System.nanoTime() >= deadlineNs)) {
                    finish(overflow ? "invalid-overflow" : "complete");
                }
                Thread.sleep(200);
            }
            if (frames != null) finish("interrupted");
            status("closed", "");
        } catch (Throwable exception) {
            recording = false;
            System.err.println("Nintekkit Frame Probe error: " + exception);
            try {
                if (frames != null) finish("invalid-writer-error");
                status("error", exception.toString());
            } catch (Exception ignored) { }
        }
    }

    private Properties readRequest() throws Exception {
        Properties properties = new Properties();
        Path file = directory.resolve("request.properties");
        if (Files.isRegularFile(file)) {
            try (InputStream input = Files.newInputStream(file)) { properties.load(input); }
        }
        return properties;
    }

    private void command() throws Exception {
        Properties request = readRequest();
        String id = request.getProperty("id", "");
        if (id.isEmpty() || id.equals(lastCommand)) return;
        lastCommand = id;
        String action = request.getProperty("action", "");
        if ("start".equals(action)) {
            if (frames != null) { status("recording", "start rejected: capture already active"); return; }
            String candidate = request.getProperty("run_id", "");
            if (!candidate.matches("[A-Za-z0-9_-]{1,100}")) {
                status("ready", "start rejected: invalid run_id"); return;
            }
            double seconds;
            try { seconds = Double.parseDouble(request.getProperty("seconds", "610")); }
            catch (NumberFormatException invalid) { status("ready", "start rejected: invalid seconds"); return; }
            if (!Double.isFinite(seconds) || seconds <= 0 || seconds > 86400) {
                status("ready", "start rejected: seconds out of range"); return;
            }
            runId = candidate;
            runDirectory = directory.resolve(runId);
            Files.createDirectory(runDirectory); // Never replace an existing capture.
            metadata = new Properties();
            metadata.putAll(request);
            metadata.setProperty("measurement", "forge-render-tick-end-cpu-loop");
            metadata.setProperty("status", "recording");
            metadata.setProperty("java_version", System.getProperty("java.version", "unknown"));
            metadata.setProperty("os_arch", System.getProperty("os.arch", "unknown"));
            metadata.setProperty("start_epoch_ms", Long.toString(System.currentTimeMillis()));
            overflow = false;
            previousNs = 0;
            lastNs = 0;
            frameCount = 0;
            ring.discard();
            frames = Files.newBufferedWriter(runDirectory.resolve("frames.csv"), StandardCharsets.UTF_8,
                    StandardOpenOption.CREATE_NEW, StandardOpenOption.WRITE);
            frames.write("elapsed_ns,frame_ns\n");
            marks = Files.newBufferedWriter(runDirectory.resolve("annotations.csv"), StandardCharsets.UTF_8,
                    StandardOpenOption.CREATE_NEW, StandardOpenOption.WRITE);
            marks.write("elapsed_ns,kind,edge,note\n");
            startNs = System.nanoTime();
            deadlineNs = startNs + (long) (seconds * 1.0e9);
            metadata.setProperty("start_monotonic_ns", Long.toString(startNs));
            writeMetadata();
            recording = true;
            status("recording", "");
        } else if ("stop".equals(action) && frames != null) {
            finish("stopped");
        } else if ("mark".equals(action) && frames != null) {
            marks.write(Long.toString(System.nanoTime() - startNs));
            marks.write("," + csv(request.getProperty("kind", "note")));
            marks.write("," + csv(request.getProperty("edge", "point")));
            marks.write("," + csv(request.getProperty("note", "")) + "\n");
            marks.flush();
            status("recording", "");
        } else {
            status(frames == null ? "ready" : "recording", "command not applicable");
        }
    }

    private void drain() throws Exception {
        if (frames == null) return;
        long stamp;
        while ((stamp = ring.poll()) != Long.MIN_VALUE) {
            frames.write(Long.toString(stamp - startNs));
            frames.write(',');
            frames.write(Long.toString(previousNs == 0 ? 0 : stamp - previousNs));
            frames.write('\n');
            previousNs = stamp;
            lastNs = stamp;
            frameCount++;
        }
        frames.flush();
    }

    private void finish(String result) throws Exception {
        recording = false;
        // A callback that passed its first gate must finish before draining.
        while (producing) Thread.yield();
        drain();
        long endNs = System.nanoTime();
        frames.close();
        marks.close();
        frames = null;
        marks = null;
        metadata.setProperty("status", result);
        metadata.setProperty("frame_count", Long.toString(frameCount));
        metadata.setProperty("elapsed_ns", Long.toString(endNs - startNs));
        metadata.setProperty("trailing_gap_ns", Long.toString(lastNs == 0 ? endNs - startNs : endNs - lastNs));
        metadata.setProperty("overflow", Boolean.toString(overflow));
        writeMetadata();
        status(result, "");
    }

    private void writeMetadata() throws Exception {
        try (OutputStream output = Files.newOutputStream(runDirectory.resolve("metadata.properties"))) {
            metadata.store(output, "Nintekkit Frame Probe; no GPU completion or scan-out claim");
        }
    }

    private void status(String state, String message) throws Exception {
        Properties properties = new Properties();
        properties.setProperty("state", state);
        properties.setProperty("run_id", runId);
        properties.setProperty("command_id", lastCommand);
        properties.setProperty("message", message);
        Path temporary = directory.resolve("status.properties.tmp");
        try (OutputStream output = Files.newOutputStream(temporary)) {
            properties.store(output, "Nintekkit Frame Probe");
        }
        Files.move(temporary, directory.resolve("status.properties"), StandardCopyOption.REPLACE_EXISTING);
    }

    private static String csv(String value) {
        return "\"" + value.replace("\r", " ").replace("\n", " ").replace("\"", "\"\"") + "\"";
    }
}

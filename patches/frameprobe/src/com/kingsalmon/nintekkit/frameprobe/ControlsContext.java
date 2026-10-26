/* SPDX-License-Identifier: MIT */
package com.kingsalmon.nintekkit.frameprobe;

import java.lang.management.ManagementFactory;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardCopyOption;
import java.nio.file.attribute.PosixFilePermissions;

/** Original GUI/world observer. Reflection avoids bundling Minecraft classes. */
public final class ControlsContext implements Runnable {
    private final Object minecraft;
    private final Field screenField, worldField;
    private final Path output;
    private final long pid;
    private volatile String pending;
    private long sequence, lastPublished;
    private String lastClass;
    private boolean lastWorld, published;

    public ControlsContext() throws Exception {
        Class<?> type = Class.forName("net.minecraft.client.Minecraft");
        Method singleton = method(type, "func_71410_x", "getMinecraft");
        minecraft = singleton.invoke(null);
        if (minecraft == null) throw new IllegalStateException("Minecraft is unavailable");
        screenField = field(type, "field_71462_r", "currentScreen");
        worldField = field(type, "field_71441_e", "world");
        pid = Long.parseLong(ManagementFactory.getRuntimeMXBean().getName().split("@", 2)[0]);
        String configured = System.getProperty("nintekkit.controls.file");
        output = configured == null || configured.isEmpty()
                ? Paths.get(System.getProperty("user.dir"), "nintekkit-controls", "state.json").toAbsolutePath().normalize()
                : Paths.get(configured).normalize();
        if (!output.isAbsolute()) throw new IllegalArgumentException("Controls path must be absolute");
        Files.createDirectories(output.getParent());
        if (Files.isSymbolicLink(output.getParent()) || Files.isSymbolicLink(output)
                || !Files.isDirectory(output.getParent(), LinkOption.NOFOLLOW_LINKS)) {
            throw new IllegalArgumentException("Controls output must use an ordinary private directory");
        }
        privateMode(output.getParent(), true);
    }

    private static Method method(Class<?> type, String... names) throws Exception {
        for (String name : names) {
            try { Method value = type.getDeclaredMethod(name); value.setAccessible(true); return value; }
            catch (NoSuchMethodException ignored) { }
        }
        throw new NoSuchMethodException("Minecraft singleton");
    }

    private static Field field(Class<?> type, String... names) throws Exception {
        for (String name : names) {
            try { Field value = type.getDeclaredField(name); value.setAccessible(true); return value; }
            catch (NoSuchFieldException ignored) { }
        }
        throw new NoSuchFieldException("Minecraft context");
    }

    public void start() {
        Thread worker = new Thread(this, "Nintekkit-controls-context");
        worker.setDaemon(true);
        worker.start();
        tick();
    }

    /** Called on the client thread; disk I/O belongs to the daemon worker. */
    public void tick() {
        try { publish(screenField.get(minecraft), worldField.get(minecraft) != null); }
        catch (Exception ignored) { /* No heartbeat: consumer becomes stale and releases input. */ }
    }

    /** GuiOpenEvent is an early hint; ClientTick.END confirms actual state. */
    public void guiOpen(Object event) {
        try {
            Object screen = event.getClass().getMethod("getGui").invoke(event);
            publish(screen, worldField.get(minecraft) != null);
        } catch (Exception ignored) { }
    }

    private void publish(Object screen, boolean world) {
        long now = System.currentTimeMillis();
        String guiClass = screen == null ? null : screen.getClass().getName();
        if (published && world == lastWorld && equal(guiClass, lastClass)
                && now >= lastPublished && now - lastPublished < 1000) return;
        lastClass = guiClass;
        lastWorld = world;
        lastPublished = now;
        published = true;
        pending = json(pid, ++sequence, now, world, guiClass);
    }

    private static boolean equal(Object first, Object second) {
        return first == null ? second == null : first.equals(second);
    }

    static String json(long pid, long sequence, long timestamp, boolean world, String guiClass) {
        String safeClass = guiClass == null ? "null" : "\"" + escape(guiClass) + "\"";
        return "{\"schema\":1,\"pid\":" + pid + ",\"sequence\":" + sequence
                + ",\"updated_epoch_ms\":" + timestamp + ",\"world_loaded\":" + world
                + ",\"gui_open\":" + (guiClass != null) + ",\"gui_class\":" + safeClass + "}\n";
    }

    private static String escape(String value) {
        StringBuilder result = new StringBuilder();
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            if (c == '\\' || c == '"') result.append('\\').append(c);
            else if (c >= 32 && c <= 126) result.append(c);
            else result.append(String.format("\\u%04x", (int) c));
        }
        return result.toString();
    }

    private static void privateMode(Path path, boolean directory) throws Exception {
        try { Files.setPosixFilePermissions(path, PosixFilePermissions.fromString(directory ? "rwx------" : "rw-------")); }
        catch (UnsupportedOperationException ignored) { /* Windows build tests; Switch uses POSIX. */ }
    }

    @Override public void run() {
        String written = null;
        try {
            while (true) {
                String current = pending;
                if (current != null && !current.equals(written)) {
                    Path temporary = Files.createTempFile(output.getParent(), ".state-", ".tmp");
                    try {
                        privateMode(temporary, false);
                        Files.write(temporary, current.getBytes(StandardCharsets.UTF_8));
                        // Same-directory atomic replacement; an unsupported filesystem fails closed.
                        Files.move(temporary, output, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
                        written = current;
                    } finally { Files.deleteIfExists(temporary); }
                }
                Thread.sleep(25);
            }
        } catch (InterruptedException exception) {
            Thread.currentThread().interrupt();
        } catch (Exception exception) {
            System.err.println("Nintekkit controls context writer stopped: " + exception.getClass().getName());
        }
    }
}

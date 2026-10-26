package com.kingsalmon.nintekkit.diagnostics;

import java.lang.instrument.Instrumentation;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.IntBuffer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardOpenOption;
import java.nio.file.attribute.PosixFilePermissions;
import java.util.Map;
import java.util.LinkedHashMap;

/** One-shot, read-only main-thread render dimensions. No bytecode transformations. */
public final class RenderSnapshot {
    private RenderSnapshot() { }

    public static void agentmain(String argument, Instrumentation instrumentation) throws Exception {
        final Path output = safeOutput(argument);
        final Map<String, Class<?>> loaded = new LinkedHashMap<String, Class<?>>();
        for (Class<?> type : instrumentation.getAllLoadedClasses()) {
            String name = type.getName();
            if (name.equals("net.minecraft.client.Minecraft") ||
                name.equals("org.lwjgl.opengl.Display") || name.equals("org.lwjgl.opengl.GL11")) {
                if (loaded.put(name, type) != null) throw new IllegalStateException("AmbiguousClass");
            }
        }
        try {
            Class<?> minecraft = required(loaded, "net.minecraft.client.Minecraft");
            final Object game = method(minecraft, new String[] {"getMinecraft", "func_71410_x"}).invoke(null);
            final Class<?> display = required(loaded, "org.lwjgl.opengl.Display");
            final Class<?> gl = required(loaded, "org.lwjgl.opengl.GL11");
            Runnable task = new Runnable() {
                public void run() {
                    try { write(output, collect(game, display, gl)); }
                    catch (Throwable error) {
                        try { write(output, error(error)); } catch (Throwable ignored) { }
                    }
                }
            };
            method(minecraft, new String[] {"addScheduledTask", "func_152344_a"}, Runnable.class).invoke(game, task);
        } catch (Throwable failure) {
            write(output, error(failure));
        }
    }

    private static Path safeOutput(String value) throws Exception {
        if (value == null) throw new IllegalArgumentException("MissingPath");
        Path path = Paths.get(value);
        if (!path.isAbsolute() || !path.equals(path.normalize()) || path.getParent() == null ||
            !path.getFileName().toString().matches("render-[A-Za-z0-9_-]{1,80}\\.json"))
            throw new IllegalArgumentException("InvalidPath");
        Path parent = path.getParent();
        if (!parent.toRealPath().equals(parent) || !Files.isDirectory(parent, LinkOption.NOFOLLOW_LINKS) ||
            Files.exists(path, LinkOption.NOFOLLOW_LINKS)) throw new IllegalArgumentException("UnsafePath");
        if (!Files.getPosixFilePermissions(parent).equals(PosixFilePermissions.fromString("rwx------")) ||
            !Files.getOwner(parent).equals(Files.getOwner(Paths.get(System.getProperty("user.home")))))
            throw new IllegalArgumentException("PrivateDirectoryRequired");
        return path;
    }

    private static Class<?> required(Map<String, Class<?>> loaded, String name) {
        Class<?> type = loaded.get(name);
        if (type == null) throw new IllegalStateException("ClassNotLoaded");
        return type;
    }

    private static Method method(Class<?> type, String[] names, Class<?>... parameters) throws Exception {
        for (Class<?> current = type; current != null; current = current.getSuperclass()) {
            for (String name : names) {
                try {
                    Method result = current.getDeclaredMethod(name, parameters);
                    result.setAccessible(true);
                    return result;
                } catch (NoSuchMethodException ignored) { }
            }
        }
        throw new NoSuchMethodException("RequiredMethod");
    }

    private static Object field(Object instance, String... names) throws Exception {
        for (Class<?> current = instance.getClass(); current != null; current = current.getSuperclass()) {
            for (String name : names) {
                try {
                    Field result = current.getDeclaredField(name);
                    if (Modifier.isStatic(result.getModifiers())) throw new IllegalStateException("UnexpectedStatic");
                    result.setAccessible(true);
                    return result.get(instance);
                } catch (NoSuchFieldException ignored) { }
            }
        }
        throw new NoSuchFieldException("RequiredField");
    }

    private static String integer(Object value) {
        if (!(value instanceof Integer)) throw new IllegalStateException("ExpectedInteger");
        return value.toString();
    }

    private static String collect(Object game, Class<?> display, Class<?> gl) throws Exception {
        StringBuilder json = new StringBuilder("{\"schema\":1,\"minecraft\":{");
        json.append("\"display_width\":").append(integer(field(game, "displayWidth", "field_71443_c")));
        json.append(",\"display_height\":").append(integer(field(game, "displayHeight", "field_71440_d")));
        Object screen = field(game, "currentScreen", "field_71462_r");
        json.append("},\"screen\":");
        if (screen == null) json.append("null");
        else {
            String name = screen.getClass().getName();
            if (!name.matches("[A-Za-z0-9_.$]{1,256}")) throw new IllegalStateException("InvalidClassName");
            json.append("{\"class\":\"").append(name).append("\",\"width\":")
                .append(integer(field(screen, "width", "field_146294_l"))).append(",\"height\":")
                .append(integer(field(screen, "height", "field_146295_m"))).append('}');
        }
        Object framebuffer = field(game, "framebufferMc", "field_147124_at");
        json.append(",\"framebuffer\":");
        if (framebuffer == null) json.append("null");
        else json.append("{\"width\":").append(integer(field(framebuffer, "framebufferWidth", "field_147621_c")))
            .append(",\"height\":").append(integer(field(framebuffer, "framebufferHeight", "field_147618_d")))
            .append(",\"texture_width\":").append(integer(field(framebuffer, "framebufferTextureWidth", "field_147622_a")))
            .append(",\"texture_height\":").append(integer(field(framebuffer, "framebufferTextureHeight", "field_147620_b"))).append('}');
        json.append(",\"lwjgl\":{\"width\":").append(integer(display.getMethod("getWidth").invoke(null)))
            .append(",\"height\":").append(integer(display.getMethod("getHeight").invoke(null)));
        Object full = display.getMethod("isFullscreen").invoke(null);
        if (!(full instanceof Boolean)) throw new IllegalStateException("ExpectedBoolean");
        Object mode = display.getMethod("getDisplayMode").invoke(null);
        json.append(",\"fullscreen\":").append(full)
            .append(",\"mode_width\":").append(integer(mode.getClass().getMethod("getWidth").invoke(mode)))
            .append(",\"mode_height\":").append(integer(mode.getClass().getMethod("getHeight").invoke(mode))).append('}');
        try {
            // LWJGL 2 validates space for 16 integers even for the four-value viewport.
            IntBuffer viewport = ByteBuffer.allocateDirect(64).order(ByteOrder.nativeOrder()).asIntBuffer();
            gl.getMethod("glGetInteger", Integer.TYPE, IntBuffer.class).invoke(null, 0x0BA2, viewport);
            json.append(",\"gl_viewport\":[");
            for (int index = 0; index < 4; index++) {
                if (index != 0) json.append(',');
                json.append(viewport.get(index));
            }
            json.append(']');
        } catch (Throwable failure) {
            json.append(",\"gl_error_class\":\"").append(errorClass(failure)).append('"');
        }
        return json.append("}\n").toString();
    }

    private static String error(Throwable failure) {
        return "{\"schema\":1,\"error_class\":\"" + errorClass(failure) + "\"}\n";
    }

    private static String errorClass(Throwable failure) {
        while (failure instanceof java.lang.reflect.InvocationTargetException && failure.getCause() != null)
            failure = failure.getCause();
        String name = failure.getClass().getSimpleName();
        if (!name.matches("[A-Za-z0-9_$]{1,100}")) name = "DiagnosticError";
        return name;
    }

    private static void write(Path path, String text) throws Exception {
        // Revalidate at use time; CREATE_NEW refuses an existing file or symlink.
        safeOutput(path.toString());
        Files.createFile(path, PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------")));
        Files.write(path, text.getBytes(StandardCharsets.UTF_8), StandardOpenOption.WRITE);
    }
}

"""Exercise reflective MCP/SRG field reads without Minecraft dependencies."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("build_render_probe", ROOT / "scripts/diagnostics/build_render_probe.py")
BUILD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD)

HARNESS = r'''
import java.lang.reflect.Method;
import java.nio.IntBuffer;
public final class Harness {
  public static class Screen { public int width=640, height=360; }
  public static class Buffer { public int framebufferWidth=1280,framebufferHeight=720,framebufferTextureWidth=1280,framebufferTextureHeight=720; }
  public static class Game { public int displayWidth=1280,displayHeight=720; public Object currentScreen=new Screen(),framebufferMc=new Buffer(); }
  public static class SrgScreen { public int field_146294_l=640,field_146295_m=360; }
  public static class SrgBuffer { public int field_147621_c=1280,field_147618_d=720,field_147622_a=1280,field_147620_b=720; }
  public static class SrgGame { public int field_71443_c=1280,field_71440_d=720; public Object field_71462_r=new SrgScreen(),field_147124_at=new SrgBuffer(); }
  public static class Display {
    public static int getWidth(){return 1280;} public static int getHeight(){return 720;}
    public static boolean isFullscreen(){return true;} public static Mode getDisplayMode(){return new Mode();}
  }
  public static class Mode { public int getWidth(){return 1280;} public int getHeight(){return 720;} }
  public static class GL {
    public static void glGetInteger(int name,IntBuffer output) {
      if(name!=0x0BA2 || !output.isDirect() || output.remaining()<16)throw new IllegalArgumentException();
      output.put(0,0);output.put(1,0);output.put(2,1280);output.put(3,720);
    }
  }
  public static class BrokenGL {
    public static void glGetInteger(int name,IntBuffer output) {
      throw new IllegalArgumentException("Do not disclose exception messages");
    }
  }
  public static void main(String[] args) throws Exception {
    Class<?> probe=Class.forName("com.kingsalmon.nintekkit.diagnostics.RenderSnapshot");
    Method collect=probe.getDeclaredMethod("collect",Object.class,Class.class,Class.class);
    collect.setAccessible(true);
    System.out.print(collect.invoke(null,new Game(),Display.class,GL.class));
    System.out.print(collect.invoke(null,new SrgGame(),Display.class,GL.class));
    Game nulls=new Game();nulls.currentScreen=null;nulls.framebufferMc=null;
    System.out.print(collect.invoke(null,nulls,Display.class,GL.class));
    System.out.print(collect.invoke(null,new Game(),Display.class,BrokenGL.class));
    Method safe=probe.getDeclaredMethod("safeOutput",String.class);safe.setAccessible(true);
    try {safe.invoke(null,"relative.json");throw new AssertionError();}
    catch(java.lang.reflect.InvocationTargetException failure) {
      if(!(failure.getCause() instanceof IllegalArgumentException))throw failure;
    }
  }
}
'''


class RenderProbeTests(unittest.TestCase):
    def test_reproducible_java8_reflection_and_output(self):
        javac = shutil.which("javac")
        fallback = Path(r"C:\Program Files\Java\jdk1.8.0_211\bin\javac.exe")
        if javac is None and fallback.exists():
            javac = str(fallback)
        if javac is None:
            self.skipTest("An installed JDK is required")
        java = Path(javac).parent / ("java.exe" if javac.endswith(".exe") else "java")
        with tempfile.TemporaryDirectory(prefix="render-fixture-") as directory:
            base = Path(directory)
            first, second = base / "a.jar", base / "b.jar"
            a, b = BUILD.build(javac, first), BUILD.build(javac, second)
            self.assertEqual(a["jar_sha256"], b["jar_sha256"])
            with zipfile.ZipFile(first) as archive:
                classes = [name for name in archive.namelist() if name.endswith(".class")]
                self.assertEqual(len(classes), 2)
                for name in classes:
                    self.assertEqual(int.from_bytes(archive.read(name)[6:8], "big"), 52)
            source = base / "Harness.java"
            source.write_text(HARNESS, encoding="utf-8")
            subprocess.run([javac, "-cp", str(first), "-d", str(base), str(source)], check=True)
            import os
            output = subprocess.check_output([str(java), "-cp", os.pathsep.join([str(base), str(first)]), "Harness"], text=True)
            rows = [json.loads(line) for line in output.splitlines()]
            self.assertEqual(len(rows), 4)
            for row in rows:
                self.assertEqual(row["minecraft"], {"display_width": 1280, "display_height": 720})
                self.assertTrue(row["lwjgl"]["fullscreen"])
            for row in rows[:3]:
                self.assertEqual(row["gl_viewport"], [0, 0, 1280, 720])
            for row in rows[:2]:
                self.assertEqual(row["screen"]["width"], 640)
                self.assertEqual(row["framebuffer"], {"width": 1280, "height": 720, "texture_width": 1280, "texture_height": 720})
            self.assertIsNone(rows[2]["screen"])
            self.assertIsNone(rows[2]["framebuffer"])
            self.assertEqual(rows[3]["gl_error_class"], "IllegalArgumentException")
            self.assertEqual(rows[3]["framebuffer"]["width"], 1280)
            self.assertNotIn("Do not disclose", output)


if __name__ == "__main__":
    unittest.main()

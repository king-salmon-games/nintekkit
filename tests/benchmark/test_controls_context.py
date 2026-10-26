"""Exercise original Java GUI observer against tiny original reflection fixtures."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'patches/frameprobe/src/com/kingsalmon/nintekkit/frameprobe/ControlsContext.java'


@unittest.skipUnless(shutil.which('javac') and shutil.which('java'), 'JDK required')
class ControlsContextTests(unittest.TestCase):
    def run_fixture(self, srg):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stub = root / 'net/minecraft/client/Minecraft.java'
            stub.parent.mkdir(parents=True)
            screen, world, singleton = ('field_71462_r', 'field_71441_e', 'func_71410_x') if srg else ('currentScreen', 'world', 'getMinecraft')
            stub.write_text('package net.minecraft.client; public final class Minecraft { '
                            'private static final Minecraft INSTANCE = new Minecraft(); '
                            f'public Object {screen}, {world}; '
                            f'public static Minecraft {singleton}() {{ return INSTANCE; }} }}')
            harness = root / 'ContextHarness.java'
            harness.write_text('''
import com.kingsalmon.nintekkit.frameprobe.ControlsContext;
import net.minecraft.client.Minecraft;
import java.nio.file.*;
import java.nio.charset.StandardCharsets;
public class ContextHarness {
    public static class Menu { }
    public static class GuiEvent {
        public Object getGui() { return new Menu(); }
    }
    static String read(Path path) throws Exception {
        return new String(Files.readAllBytes(path), StandardCharsets.UTF_8).trim();
    }
    static String changed(Path path, String before) throws Exception {
        for (int i = 0; i < 200; i++) {
            if (Files.exists(path)) { String current = read(path); if (!current.equals(before)) return current; }
            Thread.sleep(10);
        }
        throw new AssertionError("Writer did not publish");
    }
    public static void main(String[] args) throws Exception {
        Path path = Paths.get(System.getProperty("user.dir"), "nintekkit-controls", "state.json");
        ControlsContext observer = new ControlsContext();
        observer.start();
        String initial = changed(path, ""); System.out.println(initial);
        Thread.sleep(1150);
        if (!read(path).equals(initial)) throw new AssertionError("Worker invented client heartbeat");
        Minecraft mc = Minecraft.SINGLETON();
        mc.WORLD = new Object(); mc.SCREEN = new Menu(); observer.tick();
        String gui = changed(path, initial); System.out.println(gui);
        mc.SCREEN = null; observer.tick();
        String world = changed(path, gui); System.out.println(world);
        observer.guiOpen(new GuiEvent());
        String early = changed(path, world); System.out.println(early);
        observer.tick();
        String confirmed = changed(path, early); System.out.println(confirmed);
        mc.WORLD = null; observer.tick();
        System.out.println(changed(path, confirmed));
    }
}
'''.replace('SINGLETON', singleton).replace('WORLD', world).replace('SCREEN', screen))
            classes = root / 'classes'
            classes.mkdir()
            version = subprocess.check_output(['javac', '-version'], stderr=subprocess.STDOUT, text=True)
            target = ['-source', '8', '-target', '8'] if 'javac 1.8.' in version else ['--release', '8']
            subprocess.run(['javac', *target, '-d', str(classes), str(SOURCE), str(stub), str(harness)], check=True, capture_output=True)
            result = subprocess.run(['java', '-cp', str(classes), 'ContextHarness'], cwd=root,
                                    text=True, capture_output=True, timeout=12)
            self.assertEqual(result.returncode, 0, result.stderr + '\n' + result.stdout)
            rows = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual([row['sequence'] for row in rows], list(range(1, 7)))
            self.assertEqual([(r['world_loaded'], r['gui_open']) for r in rows],
                             [(False, False), (True, True), (True, False), (True, True), (True, False), (False, False)])
            self.assertTrue(all(r['pid'] > 0 and r['schema'] == 1 for r in rows))
            self.assertTrue(rows[1]['gui_class'].endswith('$Menu'))
            self.assertGreaterEqual(rows[1]['updated_epoch_ms'] - rows[0]['updated_epoch_ms'], 1100)
            self.assertIsNone(rows[-1]['gui_class'])
            self.assertEqual(set(rows[0]), {'schema', 'pid', 'sequence', 'updated_epoch_ms', 'world_loaded', 'gui_open', 'gui_class'})
            self.assertFalse(list((root / 'nintekkit-controls').glob('*.tmp')))

    def test_srg_fields_real_async_writer_and_client_tick_heartbeat(self):
        self.run_fixture(True)

    def test_mcp_fields_gui_event_hint_and_authoritative_tick(self):
        self.run_fixture(False)


if __name__ == '__main__':
    unittest.main()

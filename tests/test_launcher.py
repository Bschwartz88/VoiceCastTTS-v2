import os
import struct
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICON = os.path.join(ROOT, "web", "voicecast.ico")


def read_icon_sizes(path):
    """Return the image sizes listed in an .ico file's header (256 is stored as 0)."""
    with open(path, "rb") as f:
        reserved, kind, count = struct.unpack("<HHH", f.read(6))
        if reserved != 0 or kind != 1:
            raise ValueError("not an icon file")
        sizes = []
        for _ in range(count):
            width, height = struct.unpack("<BB14x", f.read(16))
            sizes.append((width or 256, height or 256))
    return sizes


def read_text(name):
    with open(os.path.join(ROOT, name), "rb") as f:
        return f.read()


class IconTests(unittest.TestCase):
    def test_icon_has_every_windows_size(self):
        sizes = read_icon_sizes(ICON)
        for size in (16, 24, 32, 48, 256):
            self.assertIn((size, size), sizes)


class ShortcutScriptTests(unittest.TestCase):
    def test_shortcut_points_at_launcher_with_icon_minimized(self):
        script = read_text("Create_Desktop_Shortcut.bat").decode("ascii")
        self.assertIn("'Launch_VoiceCastTTS_v2.bat'", script)
        self.assertIn("web\\voicecast.ico", script)
        self.assertIn("WindowStyle = 7", script)  # 7 = start minimized
        self.assertIn("GetFolderPath('Desktop')", script)

    def test_batch_files_use_windows_line_endings(self):
        # Multi-line commands (^) in .bat files can break with Unix line endings.
        for name in ("Create_Desktop_Shortcut.bat", "Launch_VoiceCastTTS_v2.bat", "Install_Dependencies.bat"):
            data = read_text(name)
            self.assertEqual(data.count(b"\n"), data.count(b"\r\n"), name)


if __name__ == "__main__":
    unittest.main()

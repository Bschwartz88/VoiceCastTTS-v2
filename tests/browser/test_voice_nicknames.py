"""Browser tests for voice nicknames, the ✎ My names folder, voice search and speaker-name matching.
They don't render audio, so the Kokoro model files aren't needed.

    python -m unittest discover -s tests/browser -p "test_voice_nicknames.py" -v
"""

import json
import os
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

SCREENSHOTS = os.path.join(HERE, "screenshots")

try:
    from cdp import Browser, find_browser
    BROWSER = find_browser()
    SKIP_REASON = None if BROWSER else "Edge/Chrome not found (set VOICECAST_BROWSER)"
except ImportError as e:
    BROWSER, SKIP_REASON = None, f"missing dependency: {e.name} (pip install -r requirements-dev.txt)"

PANEL = "#voicePickerPanel"


@unittest.skipIf(SKIP_REASON, SKIP_REASON)
class VoiceNicknameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import web_server
        cls.web_server = web_server
        cls.real_settings_path = web_server.USER_SETTINGS_PATH
        cls.settings_dir = tempfile.TemporaryDirectory()
        web_server.USER_SETTINGS_PATH = os.path.join(cls.settings_dir.name, "user_settings.json")
        cls.server = web_server.StudioServer(("127.0.0.1", 0), web_server.StudioHandler)
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}/"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.b = Browser(BROWSER)
        cls.b.goto(cls.url)
        cls.b.wait_for("document.querySelectorAll('#characterCardsContainer .role-row').length >= 3")

    @classmethod
    def tearDownClass(cls):
        cls.b.close()
        cls.server.shutdown()
        cls.server.server_close()
        cls.web_server.USER_SETTINGS_PATH = cls.real_settings_path
        cls.settings_dir.cleanup()

    # Helpers

    def open_list(self):
        self.b.js("closeVoicePicker(); document.getElementById('voice_Narrator_picker').click(); true")
        self.b.wait_for(f"!!document.querySelector('{PANEL}')", timeout=5)

    def press(self, key, selector="document.activeElement"):
        self.b.js(f"{selector}.dispatchEvent(new KeyboardEvent('keydown', {{key: '{key}', bubbles: true}})); true")

    def start_edit(self, voice):
        self.b.js(f"document.querySelector('{PANEL} .vp-folder:not(.vp-folder-names) .vp-nick[data-voice=\"{voice}\"]').click(); true")

    def type_nickname(self, text, key="Enter"):
        self.b.js(f"document.activeElement.value = {json.dumps(text)}; true")
        self.press(key)

    def name(self, voice, text):
        """Gives voice a nickname through the list, as a user would."""
        self.open_list()
        self.start_edit(voice)
        self.type_nickname(text)

    def click_away(self):
        """The test browser never has window focus, so blur() fires no event: send it ourselves."""
        self.b.js("document.activeElement.dispatchEvent(new FocusEvent('blur')); true")

    def label(self, voice, part):
        return self.b.js(f"document.querySelector('{PANEL} .vp-folder:not(.vp-folder-names) "
                         f".vp-choose[data-voice=\"{voice}\"] .vp-{part}')?.textContent ?? null")

    def saved_nicknames(self):
        return self.b.js("fetch('/api/config').then(r => r.json()).then(d => d.user_settings.voice_nicknames)")

    def wait_saved(self, check):
        """Waits until the server's saved nicknames pass check (b.wait_for doesn't wait for promises)."""
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if check(self.saved_nicknames()):
                return
            time.sleep(0.2)
        self.fail(f"not saved as expected: {self.saved_nicknames()}")

    def names_folder(self):
        return self.b.js(f"[...document.querySelectorAll('{PANEL} .vp-folder-names .vp-choose')].map(b => b.dataset.voice)")

    def search(self, text):
        self.b.js(f"{{ const s = document.querySelector('{PANEL} .vp-search'); s.value = {json.dumps(text)}; "
                  "s.dispatchEvent(new Event('input', {bubbles: true})); } true")

    def shown_voices(self):
        return self.b.js(f"[...document.querySelectorAll('{PANEL} .vp-choose')].map(b => b.dataset.voice)")

    # Nicknames

    def test_01_give_a_nickname(self):
        self.b.js("document.getElementById('voice_Narrator').value = 'bm_george'; true")
        self.open_list()
        self.start_edit("bm_george")
        self.assertTrue(self.b.js("document.activeElement.classList.contains('vp-nick-input')"))
        self.type_nickname("  skipper   joe ")
        self.assertEqual(self.label("bm_george", "main"), "skipper joe")
        self.assertEqual(self.label("bm_george", "small"), "bm_george · British Male")
        self.assertIsNone(self.label("af_sky", "small"), "voices without a nickname look as before")
        self.assertEqual(self.b.js("document.querySelector('#voice_Narrator_picker .vp-main').textContent"), "skipper joe")
        self.wait_saved(lambda n: n.get("bm_george") == "skipper joe")

    def test_02_change_refuse_cancel_and_remove(self):
        self.name("bm_george", "Skipper")
        self.start_edit("af_sky")
        self.type_nickname("SKIPPER")
        self.assertEqual(self.b.js(f"document.querySelector('{PANEL} .vp-nick-note').textContent"),
                         "SKIPPER is already the name of bm_george.")
        self.assertTrue(self.b.js("document.activeElement.classList.contains('vp-nick-input')"), "the box stays open")
        self.click_away()  # clicking away doesn't save a duplicate
        self.wait_saved(lambda n: n.get("bm_george") == "Skipper" and "af_sky" not in n)

        self.open_list()
        self.start_edit("af_sky")
        self.type_nickname("Sky girl", key="Escape")
        self.assertTrue(self.b.js(f"!!document.querySelector('{PANEL}')"), "Esc cancels the edit, not the list")
        self.assertIsNone(self.label("af_sky", "small"))

        self.start_edit("bm_george")
        self.assertEqual(self.b.js("document.activeElement.value"), "Skipper")
        self.type_nickname("Skipper Joe")
        self.assertEqual(self.label("bm_george", "main"), "Skipper Joe")
        self.start_edit("bm_george")
        self.type_nickname("   ")
        self.assertIsNone(self.label("bm_george", "small"), "an empty box removes the nickname")
        self.wait_saved(lambda n: "bm_george" not in n)

    def test_03_my_names_folder(self):
        self.open_list()
        folders = self.b.js(f"[...document.querySelectorAll('{PANEL} .vp-folder-head')].map(h => h.dataset.folder)")
        self.assertEqual(folders[:2], ["★ Favorites", "✎ My names"])
        self.assertIn("Click the pencil", self.b.js(f"document.querySelector('{PANEL} .vp-folder-names .vp-empty').textContent"))
        self.name("bm_george", "Skipper")
        self.name("af_sky", "amy")
        self.b.js("userSettings.voice_nicknames.gone_voice = 'Ghost'; true")  # a deleted custom voice
        self.open_list()
        self.assertEqual(self.names_folder(), ["af_sky", "bm_george"], "sorted by nickname; missing voices left out")
        self.b.js("delete userSettings.voice_nicknames.gone_voice; true")
        self.b.js(f"document.getElementById('voicePickerPanel').scrollTop = 0; true")
        self.b.screenshot(os.path.join(SCREENSHOTS, "nicknames_list.png"))

    def test_04_fixing_a_taken_name_then_clicking_away_saves_it(self):
        self.name("bm_george", "Skipper")
        self.start_edit("am_adam")
        self.type_nickname("Skipper")
        self.assertIn("already the name of bm_george", self.b.js(f"document.querySelector('{PANEL} .vp-nick-note').textContent"))
        self.b.js("const i = document.activeElement; i.value = 'Uncle'; i.dispatchEvent(new Event('input', {bubbles: true})); true")
        self.click_away()
        self.wait_saved(lambda n: n.get("am_adam") == "Uncle" and n.get("bm_george") == "Skipper")
        self.name("am_adam", "")

    def test_09_nicknames_survive_a_reload(self):
        self.wait_saved(lambda n: n.get("af_sky") == "amy")
        self.b.goto(self.url)
        self.b.wait_for("document.querySelectorAll('#characterCardsContainer .role-row').length >= 3")
        self.open_list()
        self.assertEqual(self.names_folder(), ["af_sky", "bm_george"])

    # Search

    def test_10_search(self):
        self.name("bm_george", "Skipper")
        self.open_list()
        self.assertTrue(self.b.js("document.activeElement.classList.contains('vp-search')"), "ready to type")
        self.assertEqual(self.b.js(f"document.querySelector('{PANEL} .vp-search').placeholder"), "Find a voice…")
        self.search("SKIP")
        self.assertEqual(set(self.shown_voices()), {"bm_george"})
        self.b.screenshot(os.path.join(SCREENSHOTS, "nicknames_search.png"))
        self.search("british male")  # "male" must not match "Female"
        self.assertTrue(self.shown_voices())
        self.assertTrue(all(v.startswith("bm_") for v in self.shown_voices()), self.shown_voices())
        self.search("lew")  # the start of a word inside an id
        self.assertEqual(set(self.shown_voices()), {"bm_lewis"})
        self.search("scholarly")  # a word from bm_lewis's description
        self.assertEqual(set(self.shown_voices()), {"bm_lewis"})
        self.search("zzzz")
        self.assertEqual(self.shown_voices(), [])
        self.assertEqual(self.b.js(f"document.querySelector('{PANEL} .vp-empty').textContent"), 'No voices match "zzzz".')
        self.press("Escape", f"document.querySelector('{PANEL} .vp-search')")
        self.assertEqual(self.b.js(f"document.querySelector('{PANEL} .vp-search').value"), "")
        self.assertIn("af_sky", self.shown_voices())
        self.press("Escape", f"document.querySelector('{PANEL} .vp-search')")
        self.assertFalse(self.b.js(f"!!document.querySelector('{PANEL}')"), "Esc on an empty search closes the list")

    def test_11_search_looks_inside_closed_folders(self):
        self.open_list()
        self.b.js(f"document.querySelector('{PANEL} .vp-folder-head[data-folder=\"British Dialogue Voices\"]').click(); true")
        self.assertNotIn("bm_lewis", self.shown_voices())
        self.search("lewis")
        self.assertEqual(set(self.shown_voices()), {"bm_lewis"})
        self.assertEqual(self.b.js(f"document.querySelector('{PANEL} .vp-folder-head[data-folder=\"British Dialogue Voices\"] .vp-count').textContent"), "1")
        self.search("")
        self.assertNotIn("bm_lewis", self.shown_voices(), "the folder is still closed")
        self.b.js(f"document.querySelector('{PANEL} .vp-folder-head[data-folder=\"British Dialogue Voices\"]').click(); true")

    # Speaker names

    def voice_of(self, speaker):
        return self.b.js(f"characters.find(c => c.name === {json.dumps(speaker)})?.voice ?? null")

    def detect(self, script):
        self.b.js(f"document.getElementById('scriptInput').value = {json.dumps(script)}; autoDetectSpeakers(false); true")

    def test_20_new_speaker_named_like_a_nickname(self):
        self.detect("[Narrator] Night.\n[Owl] Hoo.")
        owl_before = self.voice_of("Owl")
        self.name("bm_george", "  skipper  joe ")
        self.name("bm_lewis", "Owl")
        self.detect("[Narrator] Night.\n[Owl] Hoo.\n[SKIPPER JOE] Hello.")
        self.assertEqual(self.voice_of("SKIPPER JOE"), "bm_george")
        self.assertEqual(self.voice_of("Owl"), owl_before, "speakers already in the cast keep their voice")
        card_select = self.b.js("`voice_${characters.find(c => c.name === 'SKIPPER JOE').id}`")
        self.assertEqual(self.b.js(f"document.getElementById('{card_select}_picker').querySelector('.vp-main').textContent"),
                         "skipper joe")

    def test_21_missing_voice_is_not_picked(self):
        self.b.js("userSettings.voice_nicknames.gone_voice = 'Ghost'; true")
        self.detect("[Narrator] Night.\n[Ghost] Boo.")
        self.assertNotEqual(self.voice_of("Ghost"), "gone_voice")
        self.b.js("delete userSettings.voice_nicknames.gone_voice; true")

    def test_99_no_javascript_errors(self):
        errors = self.b.messages("exception") + self.b.messages("error")
        self.assertEqual(errors, [])

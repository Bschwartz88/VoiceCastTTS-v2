"""Browser tests for Import: files, Google Docs links (against a fake Google), and placing the text.
They don't render audio, so the Kokoro model files aren't needed.

    python -m unittest tests.browser.test_import
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
sys.path.insert(0, os.path.join(ROOT, "tests"))

SCREENSHOTS = os.path.join(HERE, "screenshots")

try:
    from cdp import Browser, find_browser
    BROWSER = find_browser()
    SKIP_REASON = None if BROWSER else "Edge/Chrome not found (set VOICECAST_BROWSER)"
except ImportError as e:
    BROWSER, SKIP_REASON = None, f"missing dependency: {e.name} (pip install -r requirements-dev.txt)"


@unittest.skipIf(SKIP_REASON, SKIP_REASON)
class ImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import web_server, import_text, fake_google_docs
        cls.web_server, cls.import_text, cls.fake = web_server, import_text, fake_google_docs
        cls.real_settings_path = web_server.USER_SETTINGS_PATH
        cls.settings_dir = tempfile.TemporaryDirectory()
        web_server.USER_SETTINGS_PATH = os.path.join(cls.settings_dir.name, "user_settings.json")
        cls.google = fake_google_docs.FakeGoogle()
        cls.restore_google = cls.google.point(import_text)
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
        cls.restore_google()
        cls.google.close()
        cls.web_server.USER_SETTINGS_PATH = cls.real_settings_path
        cls.settings_dir.cleanup()

    def setUp(self):
        self.b.js("document.getElementById('importDialog').open && closeImport();"
                  "{ const t = document.getElementById('scriptInput'); t.value = '';"
                  "t.dispatchEvent(new Event('input', {bubbles: true})); } true")

    # Helpers

    def choose_file(self, name, text):
        """Imports a file the way the file picker would hand it over (the picker itself can't be driven)."""
        self.b.js("openImport(); true")
        self.b.js(f"onImportFileChosen({{files: [new File([{json.dumps(text)}], {json.dumps(name)})], value: ''}})")

    def script(self):
        return self.b.js("document.getElementById('scriptInput').value")

    def message(self):
        return self.b.js("document.getElementById('importMessage').innerText")

    # Tests

    def test_01_import_button_opens_the_window(self):
        self.b.js("document.getElementById('importBtn').click(); true")
        self.assertTrue(self.b.js("document.getElementById('importDialog').open"))
        os.makedirs(SCREENSHOTS, exist_ok=True)
        for theme in ("light", "dark"):
            self.b.js(f"window.applyTheme('{theme}'); true")
            self.b.screenshot(os.path.join(SCREENSHOTS, f"import_window_{theme}.png"))
        self.b.js("window.applyTheme('system'); closeImport(); true")

    def test_02_md_file_fills_an_empty_script_tidied(self):
        self.choose_file("Fox.md", "# Chapter 1\n[Fox] **Hello**, owl.")
        self.b.wait_for("document.getElementById('scriptInput').value !== ''", timeout=10)
        self.assertEqual(self.script(), "Chapter 1\n[Fox] Hello, owl.")
        self.assertFalse(self.b.js("document.getElementById('importDialog').open"))
        self.assertIn("Imported Fox.md: 5 words.", self.b.js("document.getElementById('progressTitle').innerText"))
        self.b.wait_for("[...document.querySelectorAll('#characterCardsContainer input')].some(i => i.value === 'Fox')", timeout=5)

    def test_03_windows_1252_text_keeps_its_accents(self):
        self.assertEqual(self.b.js("decodeImportBytes(new Uint8Array([0x93, 0x63, 0x61, 0x66, 0xe9, 0x94]).buffer)"), "“café”")
        self.assertEqual(self.b.js("decodeImportBytes(new TextEncoder().encode('café').buffer)"), "café")

    def test_04_replace_add_to_end_and_cancel(self):
        self.b.js("{ const t = document.getElementById('scriptInput'); t.value = '[Narrator] Old.'; } true")
        for choice, expected in (("cancel", "[Narrator] Old."), ("append", "[Narrator] Old.\nNew."), ("replace", "New.")):
            self.choose_file("a.txt", "New.")
            self.b.wait_for("!document.getElementById('importChoice').classList.contains('hidden')", timeout=10)
            self.b.js(f"document.querySelector('#importChoice [data-value=\"{choice}\"]').click(); true")
            self.assertEqual(self.script(), expected)
            self.b.js("{ const t = document.getElementById('scriptInput'); t.value = '[Narrator] Old.'; } true")

    def test_05_wrong_file_type_is_refused_in_the_window(self):
        self.choose_file("a.docx", "hi")
        self.assertEqual(self.message(), "Import opens .txt and .md files.")
        self.assertEqual(self.script(), "")

    def test_06_link_box_is_disabled_until_the_box_is_ticked(self):
        self.b.js("openImport(); true")
        self.assertTrue(self.b.js("document.getElementById('importLink').disabled && document.getElementById('importLinkBtn').disabled"))
        self.b.js("{ const c = document.getElementById('importLinksAllowed'); c.checked = true; c.dispatchEvent(new Event('change', {bubbles: true})); } true")
        self.assertFalse(self.b.js("document.getElementById('importLink').disabled"))
        # b.wait_for doesn't wait for promises, so poll the server's saved value.
        for _ in range(20):
            if self.b.js("fetch('/api/config').then(r => r.json()).then(d => d.user_settings.import_links_enabled)"):
                break
            time.sleep(0.25)
        else:
            self.fail("import_links_enabled was not saved")

    def test_07_link_import_against_the_fake_google(self):
        self.google.routes[self.fake.EXPORT_PATH] = (200, {"Content-Type": "text/markdown"}, b"## Two\nHello there")
        self.b.js("openImport(); { const c = document.getElementById('importLinksAllowed'); c.checked = true;"
                  "c.dispatchEvent(new Event('change', {bubbles: true})); }"
                  f"document.getElementById('importLink').value = {json.dumps(self.fake.LINK)};"
                  "document.getElementById('importLinkBtn').click(); true")
        self.b.wait_for("document.getElementById('scriptInput').value !== ''", timeout=10)
        self.assertEqual(self.script(), "Two\nHello there")

    def test_08_link_problem_is_shown_in_the_window(self):
        self.b.js("openImport(); { const c = document.getElementById('importLinksAllowed'); c.checked = true;"
                  "c.dispatchEvent(new Event('change', {bubbles: true})); }"
                  "document.getElementById('importLink').value = 'https://example.com/x';"
                  "document.getElementById('importLinkBtn').click(); true")
        self.b.wait_for("document.getElementById('importMessage').classList.contains('is-error')", timeout=10)
        self.assertTrue(self.message().startswith("That isn't a Google Docs link."))
        self.assertTrue(self.b.js("document.getElementById('importDialog').open"))

    def test_09_import_is_unavailable_while_the_script_is_locked(self):
        self.b.js("lockScript(true); true")
        self.assertTrue(self.b.js("document.getElementById('importBtn').disabled"))
        self.b.js("lockScript(false); true")
        self.assertFalse(self.b.js("document.getElementById('importBtn').disabled"))

    def test_10_render_and_lock_both_keep_import_off_until_both_end(self):
        importing = "document.getElementById('importBtn').disabled"
        self.b.js("lockScript(true); setImportRendering(true); setImportRendering(false); true")
        self.assertTrue(self.b.js(importing), "audio finished, but Continue is still writing")
        self.b.js("setImportRendering(true); lockScript(false); true")
        self.assertTrue(self.b.js(importing), "Continue finished, but audio is still being made")
        self.b.js("setImportRendering(false); true")
        self.assertFalse(self.b.js(importing))

    def test_11_a_window_left_open_cannot_change_a_locked_script(self):
        self.choose_file("a.txt", "Ready.")  # nothing locked yet: placed straight away
        self.b.wait_for("document.getElementById('scriptInput').value === 'Ready.'", timeout=10)
        self.b.js("{ const t = document.getElementById('scriptInput'); t.value = ''; } true")
        self.b.js("openImport(); lockScript(true); true")
        self.b.js("onImportFileChosen({files: [new File(['New.'], 'a.txt')], value: ''})")
        self.b.wait_for("document.getElementById('importMessage').classList.contains('is-error')", timeout=10)
        self.assertEqual(self.script(), "")
        self.b.js("lockScript(false); true")

    def test_12_closing_the_window_mid_import_drops_the_result(self):
        self.google.routes[self.fake.EXPORT_PATH] = (200, {"Content-Type": "text/markdown"}, b"Late text")
        self.google.delay = 1.5
        try:
            self.b.js("openImport(); { const c = document.getElementById('importLinksAllowed'); c.checked = true;"
                      "c.dispatchEvent(new Event('change', {bubbles: true})); }"
                      f"document.getElementById('importLink').value = {json.dumps(self.fake.LINK)};"
                      "document.getElementById('importLinkBtn').click(); true")
            self.b.wait_for("importBusy", timeout=5)
            self.b.js("document.getElementById('importDialog').close(); true")  # what Escape does
            self.b.js("openImport(); true")
            self.assertIn("Still", self.message(), "reopening while the last import is running says so")
            self.b.js("closeImport(); true")
            self.b.wait_for("!importBusy", timeout=10)
            self.assertEqual(self.script(), "", "a closed window doesn't place text later")
        finally:
            self.google.delay = 0

    def test_13_ctrl_enter_in_the_window_does_not_generate(self):
        self.b.js("window.realGenerate = window.generateAudio; window.generated = false;"
                  "window.generateAudio = () => { window.generated = true; }; openImport();"
                  "document.getElementById('importLink').dispatchEvent(new KeyboardEvent('keydown',"
                  " {key: 'Enter', ctrlKey: true, bubbles: true, cancelable: true})); true")
        try:
            self.assertFalse(self.b.js("window.generated"))
        finally:
            self.b.js("window.generateAudio = window.realGenerate; closeImport(); true")

    def test_14_a_large_accented_file_is_called_too_big(self):
        # 5 MB of Windows-1252 "é" is within the file limit but twice that once sent to the studio.
        self.b.js("openImport(); onImportFileChosen({files: [new File([new Uint8Array(5 * 1024 * 1024).fill(0xE9)], 'big.txt')], value: ''})")
        self.b.wait_for("document.getElementById('importMessage').classList.contains('is-error')", timeout=30)
        self.assertEqual(self.message(), "That is too big to import (the limit is 5 MB).")
        self.assertEqual(self.script(), "")


    # Send to VoiceCast (scripts sent from LM Studio through voicecast_mcp.py)

    def send(self, text, title=""):
        import voicecast_mcp
        voicecast_mcp.PORT = self.server.server_address[1]
        from unittest import mock
        with mock.patch.object(voicecast_mcp.session_access, "read_session", return_value=self.server.session_token):
            return voicecast_mcp.send_script(text, title)

    def set_inbox(self, on):
        self.b.js(f"openImport(); {{ const c = document.getElementById('inboxEnabled'); c.checked = {str(on).lower()};"
                  "c.dispatchEvent(new Event('change', {bubbles: true})); } closeImport(); settingsSaved()")
        for _ in range(20):
            if self.b.js("fetch('/api/config').then(r => r.json()).then(d => d.user_settings.inbox_enabled)") == on:
                return
            time.sleep(0.25)
        self.fail("inbox_enabled was not saved")

    def test_15_box_is_off_at_first_and_sending_is_refused(self):
        self.b.js("openImport(); true")
        self.assertFalse(self.b.js("document.getElementById('inboxEnabled').checked"))
        self.b.js("closeImport(); true")
        self.assertEqual(self.send("Hi."), (False, self.web_server.INBOX_OFF))

    def test_16_unlabelled_story_is_formatted_into_an_empty_script(self):
        self.set_inbox(True)
        self.assertTrue(self.send('The fox ran.\n"Run," said Rose.', "The Fox")[0])
        self.b.wait_for("document.getElementById('scriptInput').value !== ''", timeout=10)
        self.assertIn("[Rose]", self.script())  # Format Prose gives a quote with "said Rose" to Rose
        self.assertTrue(self.script().startswith("The fox ran."))  # untagged lines stay as they are (the Narrator reads them)
        self.assertIn('Received "The Fox" from LM Studio', self.b.js("document.getElementById('progressTitle').innerText"))
        self.assertFalse(self.b.js("document.getElementById('importDialog').open"))

    def test_17_labelled_script_is_kept_as_sent(self):
        self.set_inbox(True)
        self.send("[Narrator] Once.\n[Owl] Hoo.")
        self.b.wait_for("document.getElementById('scriptInput').value !== ''", timeout=10)
        self.assertEqual(self.script(), "[Narrator] Once.\n[Owl] Hoo.")
        self.assertIn("Received a script from LM Studio", self.b.js("document.getElementById('progressTitle').innerText"))

    def test_18_script_with_text_asks_replace_add_cancel(self):
        self.set_inbox(True)
        for choice, expected in (("cancel", "[Narrator] Old."), ("append", "[Narrator] Old.\n[Owl] New."), ("replace", "[Owl] New.")):
            self.b.js("{ const t = document.getElementById('scriptInput'); t.value = '[Narrator] Old.'; } true")
            self.send("[Owl] New.")
            self.b.wait_for("document.getElementById('importDialog').open"
                            " && !document.getElementById('importChoice').classList.contains('hidden')", timeout=10)
            self.b.js(f"document.querySelector('#importChoice [data-value=\"{choice}\"]').click(); true")
            self.assertEqual(self.script(), expected)
            self.b.js("closeImport(); true")

    def test_19_waits_while_busy(self):
        self.set_inbox(True)
        self.b.js("aiBusy = true; true")
        try:
            self.send("[Owl] Later.")
            time.sleep(3)  # longer than one 2-second check
            self.assertEqual(self.script(), "")
        finally:
            self.b.js("aiBusy = false; true")
        self.b.wait_for("document.getElementById('scriptInput').value !== ''", timeout=10)
        self.assertEqual(self.script(), "[Owl] Later.")

    def test_20_switching_off_stops_asking(self):
        self.set_inbox(True)
        self.assertTrue(self.b.js("inboxTimer !== null"))
        self.set_inbox(False)
        self.assertTrue(self.b.js("inboxTimer === null"))
        self.assertEqual(self.send("Hi."), (False, self.web_server.INBOX_OFF))

    def chats(self, *chats):
        """A temporary LM Studio chats folder for this test: (file, name, answer text, created) per chat."""
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        for file, name, answer, created in chats:
            data = {"name": name, "createdAt": created, "lastUsedModel": {"identifier": "fablewright-12b-v1.1"},
                    "messages": [{"role": "user", "content": "Write it."}, {"role": "assistant", "content": answer}]}
            with open(os.path.join(folder.name, file), "w", encoding="utf-8") as f:
                json.dump(data, f)
        real = self.web_server.lmstudio_chats.CHATS_DIR
        self.web_server.lmstudio_chats.CHATS_DIR = folder.name
        self.addCleanup(setattr, self.web_server.lmstudio_chats, "CHATS_DIR", real)

    def open_chats(self, count):
        self.b.js("openImport(); true")
        self.b.wait_for(f"document.getElementById('importChatList').options.length === {count}"
                        " && !document.getElementById('importChatList').dataset.loading", timeout=10)

    def test_21_saved_lm_studio_chats(self):
        self.chats(("1.conversation.json", "Owl script", "[Narrator] Once.\n[Owl] Hoo.", 1_760_000_000_000),
                   ("2.conversation.json", "Rose story", '"Run," said Rose.', 1_770_000_000_000))
        self.open_chats(2)
        self.assertEqual(self.b.js("[...document.getElementById('importChatList').options].map(o => o.text)"),
                         ["Rose story (fablewright-12b-v1.1)", "Owl script (fablewright-12b-v1.1)"], "newest first")
        self.assertEqual(self.b.js("document.getElementById('importChatWhich').value"), "last")
        self.b.js("document.getElementById('importChatList').scrollIntoView({block: 'center'}); true")
        self.b.screenshot(os.path.join(SCREENSHOTS, "import_lm_studio_chat.png"))
        self.b.js("document.getElementById('importChatList').value = '1.conversation.json';"
                  "document.getElementById('importChatBtn').click(); true")
        self.b.wait_for("document.getElementById('scriptInput').value !== ''", timeout=10)
        self.assertEqual(self.script(), "[Narrator] Once.\n[Owl] Hoo.")
        self.assertIn('Imported "Owl script"', self.b.js("document.getElementById('progressTitle').innerText"))

        self.setUp()  # an empty script again
        self.open_chats(2)
        self.b.js("document.getElementById('importChatBtn').click(); true")  # the newest: untagged prose
        self.b.wait_for("document.getElementById('scriptInput').value !== ''", timeout=10)
        self.assertEqual(self.script(), "[Rose] Run.", "a chat without speaker names goes through Format Prose")

    def test_22_no_saved_chats(self):
        self.chats()
        self.open_chats(0)
        self.assertTrue(self.b.js("document.getElementById('importChatBtn').disabled"))
        self.assertIn("No saved chats found in LM Studio.",
                      self.b.js("document.getElementById('importChatNote').innerText"))


if __name__ == "__main__":
    unittest.main()

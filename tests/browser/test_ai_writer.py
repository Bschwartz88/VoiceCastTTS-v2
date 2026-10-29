"""Browser tests for the Script Assistant panel, using a fake LM Studio so they always run.
They don't render audio, so the Kokoro model files aren't needed. One extra test
uses the real LM Studio and is skipped when it isn't running.

    python -m unittest discover -s tests/browser -p "test_ai_writer.py" -v
"""

import copy
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))  # tests/, for fake_lm_studio

SCREENSHOTS = os.path.join(HERE, "screenshots")

try:
    from cdp import Browser, find_browser
    BROWSER = find_browser()
    SKIP_REASON = None if BROWSER else "Edge/Chrome not found (set VOICECAST_BROWSER)"
except ImportError as e:
    BROWSER, SKIP_REASON = None, f"missing dependency: {e.name} (pip install -r requirements-dev.txt)"

import fake_lm_studio
from fake_lm_studio import Reply


def real_lm_studio_running() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:1234/api/v1/models", timeout=2) as r:
            return r.status == 200
    except OSError:
        return False


@unittest.skipIf(SKIP_REASON, SKIP_REASON)
class AiWriterPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import web_server
        cls.web_server = web_server
        cls.real_settings_path = web_server.USER_SETTINGS_PATH
        cls.settings_dir = tempfile.TemporaryDirectory()
        web_server.USER_SETTINGS_PATH = os.path.join(cls.settings_dir.name, "user_settings.json")
        cls.real_lm_settings_dir = getattr(web_server.lm_studio, "SETTINGS_DIR", None)
        web_server.lm_studio.SETTINGS_DIR = fake_lm_studio.write_settings_folder(
            os.path.join(cls.settings_dir.name, "lmstudio"))
        web_server.lm_studio.STEADY_LENGTHS = False  # tiny fake stories; test_30 turns it on on purpose
        cls.fake = fake_lm_studio.FakeLMStudio()
        cls.fake_url = cls.fake.start()
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
        cls.fake.stop()
        cls.web_server.USER_SETTINGS_PATH = cls.real_settings_path
        cls.web_server.lm_studio.SETTINGS_DIR = cls.real_lm_settings_dir
        cls.web_server.lm_studio.STEADY_LENGTHS = True
        cls.settings_dir.cleanup()

    def shown(self, element_id):
        return self.b.js(f"!document.getElementById('{element_id}').classList.contains('hidden')")

    def status(self):
        return self.b.js("document.getElementById('aiStatus').innerText")

    def saved_settings(self):
        with open(self.web_server.USER_SETTINGS_PATH, encoding="utf-8") as f:
            return json.load(f)

    def test_01_off_by_default(self):
        self.assertFalse(self.shown("aiPanel"), "a new download shows no AI panel")
        self.assertEqual(self.b.js("document.getElementById('aiToggle').getAttribute('aria-pressed')"), "false")
        self.assertEqual(self.fake.requests, [])
        self.assertEqual(self.b.js("document.getElementById('aiToggle').innerText.trim()"), "Assistant")
        self.assertIn("Script Assistant", self.b.js("document.getElementById('aiToggle').title"))
        self.assertTrue(self.b.js("document.querySelector('#aiPanel summary').innerText.trim().startsWith('Script Assistant')"))

    def test_02_turning_on_shows_the_panel_and_lists_models(self):
        # Point the studio at the fake LM Studio first (the address editor arrives in a later stage).
        self.b.js(f"userSettings.llm_base_url = '{self.fake_url}'; true")
        self.b.js("document.getElementById('aiToggle').click(); true")
        self.b.wait_for("!document.getElementById('aiPanel').classList.contains('hidden')", timeout=5)
        self.b.wait_for("!settingsSaving", timeout=5)
        self.assertTrue(self.saved_settings()["ai_writer_enabled"])
        self.b.js("document.getElementById('aiPanel').open = true; true")
        self.b.wait_for("document.getElementById('aiModel').options.length === 1", timeout=10)
        self.assertEqual(self.b.js("document.getElementById('aiModel').options[0].text"), "Fablewright 12B v1.1")
        self.assertIn("connected", self.status())
        self.assertFalse(self.b.js("document.getElementById('aiWriteBtn').disabled"))
        # The open panel must not push the editor out of its zone or under the Output zone.
        self.assertGreaterEqual(self.b.js("document.getElementById('scriptInput').getBoundingClientRect().height"), 150)
        self.assertTrue(self.b.js("""(() => { const z = document.querySelector('.zone-script');
            return z.scrollHeight <= z.clientHeight + 1 || getComputedStyle(z).overflowY === 'auto'; })()"""),
            "the Script zone scrolls instead of spilling over")
        self.assertLess(self.b.js("document.getElementById('aiRequest').getBoundingClientRect().height"), 120,
                        "the request box is not stretched like the editor")
        self.b.screenshot(os.path.join(SCREENSHOTS, "ai_panel_light.png"))
        self.b.js("window.applyTheme('dark'); true")
        self.b.screenshot(os.path.join(SCREENSHOTS, "ai_panel_dark.png"))
        self.b.js("window.applyTheme('light'); true")

    def test_03_no_model_loaded(self):
        self.fake.models = [copy.deepcopy(fake_lm_studio.UNLOADED)]
        self.b.js("refreshAiStatus(); true")
        self.b.wait_for("document.getElementById('aiStatus').innerText.includes('No model loaded')", timeout=10)
        self.assertTrue(self.b.js("document.getElementById('aiWriteBtn').disabled"))
        self.fake.models = [copy.deepcopy(fake_lm_studio.LOADED), copy.deepcopy(fake_lm_studio.UNLOADED)]
        self.b.js("refreshAiStatus(); true")
        self.b.wait_for("document.getElementById('aiModel').options.length === 1", timeout=10)

    def test_04_model_choice_is_remembered(self):
        self.fake.models.append(copy.deepcopy(fake_lm_studio.THINKER))
        self.b.js("refreshAiStatus(); true")
        self.b.wait_for("document.getElementById('aiModel').options.length === 2", timeout=10)
        self.b.js("""const s = document.getElementById('aiModel'); s.value = 'ponder-9b';
                     s.dispatchEvent(new Event('change', {bubbles: true})); true""")
        self.b.wait_for("!settingsSaving", timeout=5)
        self.assertEqual(self.saved_settings()["llm_model"], "ponder-9b")
        self.b.js("refreshAiStatus(); true")
        self.b.wait_for("document.getElementById('aiStatus').innerText.includes('Ponder 9B')", timeout=10)
        self.assertEqual(self.b.js("document.getElementById('aiModel').value"), "ponder-9b")

    def test_05_lm_studio_not_running(self):
        self.b.js(f"userSettings.llm_base_url = '{fake_lm_studio.free_port_url()}'; saveUserSettings(); true")
        self.b.wait_for("!settingsSaving", timeout=5)
        self.b.js("refreshAiStatus(); true")
        self.b.wait_for("document.getElementById('aiStatus').innerText.includes(\"isn't answering\")", timeout=10)
        self.assertTrue(self.b.js("document.getElementById('aiWriteBtn').disabled"))
        self.b.screenshot(os.path.join(SCREENSHOTS, "ai_not_running.png"))
        self.b.js(f"userSettings.llm_base_url = '{self.fake_url}'; saveUserSettings(); true")
        self.b.wait_for("!settingsSaving", timeout=5)

    def test_06_turning_off_hides_the_panel(self):
        self.b.js("document.getElementById('aiToggle').click(); true")
        self.b.wait_for("document.getElementById('aiPanel').classList.contains('hidden')", timeout=5)
        self.b.wait_for("!settingsSaving", timeout=5)
        self.assertFalse(self.saved_settings()["ai_writer_enabled"])
        self.b.js("document.getElementById('aiToggle').click(); true")  # back on for later steps
        self.b.wait_for("!settingsSaving", timeout=5)

    STORY = json.dumps({"title": "Fox", "cast": [{"name": "Fox", "voice_type": "Female"},
                                                  {"name": "Owl", "voice_type": "Male"}],
                        "lines": [{"speaker": "Narrator", "text": "Night fell."},
                                  {"speaker": "Fox", "text": "Hello, Owl."},
                                  {"speaker": "Owl", "text": "Hoo."}]})

    def script(self):
        return self.b.js("document.getElementById('scriptInput').value")

    def click_write(self):
        self.b.js("document.getElementById('aiWriteBtn').click(); true")

    def test_10_write_fills_an_empty_script(self):
        self.b.js("document.getElementById('scriptInput').value = ''; true")
        self.fake.replies.append(Reply(content=self.STORY))
        self.b.js("document.getElementById('aiRequest').value = 'A fox and an owl.'; true")
        self.click_write()
        self.b.wait_for("document.getElementById('scriptInput').value.includes('[Owl] Hoo.')", timeout=10)
        self.assertEqual(self.script(), "[Narrator] Night fell.\n[Fox] Hello, Owl.\n[Owl] Hoo.")
        self.b.wait_for("characters.some(c => c.name === 'Owl')", timeout=5)
        self.assertEqual(self.b.js("document.getElementById('aiWriteBtn').innerText"), "Write Script")
        self.assertTrue(self.b.js("document.getElementById('btnPlayMaster').disabled"), "no audio was made")

    def test_11_existing_script_asks_first(self):
        before = self.script()
        self.fake.replies.append(Reply(content=self.STORY))
        self.click_write()
        self.b.wait_for("!document.getElementById('aiChoice').classList.contains('hidden')", timeout=10)
        self.b.screenshot(os.path.join(SCREENSHOTS, "ai_replace_choice.png"))
        self.b.js("document.querySelector('[data-click=aiInsertChoice][data-value=cancel]').click(); true")
        self.assertEqual(self.script(), before, "Cancel leaves the script alone")
        self.fake.replies.append(Reply(content=self.STORY))
        self.click_write()
        self.b.wait_for("!document.getElementById('aiChoice').classList.contains('hidden')", timeout=10)
        self.b.js("document.querySelector('[data-click=aiInsertChoice][data-value=append]').click(); true")
        self.assertEqual(self.script(), before + "\n" + before)
        self.fake.replies.append(Reply(content=self.STORY))
        self.click_write()
        self.b.wait_for("!document.getElementById('aiChoice').classList.contains('hidden')", timeout=10)
        self.b.js("document.querySelector('[data-click=aiInsertChoice][data-value=replace]').click(); true")
        self.assertEqual(self.script(), before)

    def test_12_split_shows_word_differences(self):
        self.b.js("setAiMode('split'); document.getElementById('scriptInput').value = ''; true")
        self.assertEqual(self.b.js("document.getElementById('aiWriteBtn').innerText"), "Split into Speakers")
        self.b.js("document.getElementById('aiRequest').value = 'Night fell. Hello, Owl, said Fox. Hoo.'; true")
        self.fake.replies.append(Reply(content=self.STORY))
        self.click_write()
        self.b.wait_for("!document.getElementById('aiDiffs').classList.contains('hidden')", timeout=10)
        self.assertIn("1 change", self.b.js("document.getElementById('aiDiffSummary').innerText"))
        self.b.js("document.querySelector('[data-click=toggleAiDiffs]').click(); true")
        self.assertIn("said fox", self.b.js("document.getElementById('aiDiffList').innerText"))
        self.b.screenshot(os.path.join(SCREENSHOTS, "ai_word_check.png"))
        self.b.js("setAiMode('write'); true")

    def test_13_stop(self):
        self.fake.replies.append(Reply(content=self.STORY, delay=10))
        self.click_write()
        self.b.wait_for("document.getElementById('aiWriteBtn').innerText.includes('Stop')", timeout=5)
        self.b.js("document.getElementById('aiWriteBtn').click(); true")
        self.b.wait_for("document.getElementById('aiStatus').innerText === 'Stopped.'", timeout=5)
        self.assertEqual(self.b.js("document.getElementById('aiWriteBtn').innerText"), "Write Script")

    def test_14_live_counter(self):
        self.assertEqual(self.b.js("aiElapsedText(45)"), "45 s")
        self.assertEqual(self.b.js("aiElapsedText(130)"), "2 min 10 s")
        self.assertEqual(self.b.js("aiStartedAt = Date.now(); aiProgress = {words: 1, thinking: false}; aiBusyText()"),
                         "Writing… about 1 word · 0 s")
        self.b.js("document.getElementById('scriptInput').value = ''; true")
        self.fake.replies.append(Reply(content=self.STORY, pieces=12, chunk_delay=0.4))
        self.click_write()
        self.b.wait_for("/^Writing… about \\d+ words · \\d+ s$/.test(document.getElementById('aiStatus').innerText)",
                        timeout=10)
        self.b.js("document.getElementById('aiStatus').scrollIntoView({block: 'center'}); true")
        self.b.screenshot(os.path.join(SCREENSHOTS, "ai_counter.png"))
        self.b.wait_for("document.getElementById('scriptInput').value.includes('[Owl] Hoo.')", timeout=15)
        self.assertEqual(self.status(), "Script added. Check it, then press Generate.")

    def test_15_thinking(self):
        self.b.js("document.getElementById('scriptInput').value = ''; true")
        self.fake.replies.append(Reply(content=self.STORY, thinking=4, chunk_delay=0.5))
        self.click_write()
        self.b.wait_for("document.getElementById('aiStatus').innerText.startsWith('Thinking… ')", timeout=10)
        self.b.wait_for("document.getElementById('scriptInput').value.includes('[Owl] Hoo.')", timeout=15)

    def choose_model(self, key):
        self.b.js(f"""(() => {{ const s = document.getElementById('aiModel'); s.value = '{key}';
                      s.dispatchEvent(new Event('change', {{bubbles: true}})); return true; }})()""")

    def prompt_line(self):
        return self.b.js("document.getElementById('aiPromptText').innerText")

    def test_20_length_buttons(self):
        active = "document.querySelector('.ai-length.is-active').dataset.value"
        self.assertEqual(self.b.js(active), "medium")
        self.b.js("document.querySelector('[data-click=setAiLength][data-value=long]').click(); true")
        self.b.wait_for("!settingsSaving", timeout=5)
        self.assertEqual(self.saved_settings()["llm_length"], "long")
        self.assertEqual(self.b.js(active), "long")
        self.b.js("setAiMode('split'); true")
        self.assertFalse(self.shown("aiLengths"), "length is for Write new only")
        self.b.js("setAiMode('write'); true")
        self.assertTrue(self.shown("aiLengths"))

    def test_21_model_with_an_lm_studio_prompt(self):
        self.choose_model("fablewright-12b-v1.1")
        self.b.wait_for("document.getElementById('aiPromptText').innerText.includes('Using your LM Studio prompt')", timeout=10)
        self.assertEqual(self.prompt_line(), "Using your LM Studio prompt for Fablewright 12B v1.1 (Q6_K).")
        self.assertFalse(self.shown("aiOwnBox"))
        self.b.js("document.getElementById('scriptInput').value = ''; true")
        self.fake.replies.append(Reply(content=self.STORY))
        self.click_write()
        self.b.wait_for("document.getElementById('scriptInput').value.includes('[Owl] Hoo.')", timeout=10)
        sent = self.fake.requests[-1]
        self.assertTrue(sent["messages"][0]["content"].startswith(fake_lm_studio.FABLEWRIGHT_PROMPT))
        self.assertEqual((sent["temperature"], sent["max_tokens"]), (0.7, 5512), "Long from test_20")

    def test_22_optional_box_for_a_model_without_a_prompt(self):
        self.choose_model("ponder-9b")
        self.b.wait_for("document.getElementById('aiPromptText').innerText.includes('No LM Studio prompt')", timeout=10)
        self.assertEqual(self.prompt_line(),
                         "No LM Studio prompt for Ponder 9B (Q6_K). You can add your own instructions (optional).")
        self.assertTrue(self.shown("aiOwnBox"))
        self.b.screenshot(os.path.join(SCREENSHOTS, "ai_own_box.png"))
        self.b.js("document.getElementById('scriptInput').value = ''; true")
        self.fake.replies.append(Reply(content=self.STORY))
        # Type, then click Write straight away: the new text must be used.
        self.b.js("""const box = document.getElementById('aiOwnBox'); box.value = 'Make it rhyme.';
                     box.dispatchEvent(new Event('change', {bubbles: true}));
                     document.getElementById('aiWriteBtn').click(); true""")
        self.b.wait_for("document.getElementById('scriptInput').value.includes('[Owl] Hoo.')", timeout=10)
        self.assertTrue(self.fake.requests[-1]["messages"][0]["content"].startswith("Make it rhyme.\n\n"))
        self.b.wait_for("!settingsSaving", timeout=5)
        self.assertEqual(self.saved_settings()["llm_model_prompts"],
                         {"ponder-9b": {"text": "Make it rhyme.", "use_own": False}})
        self.choose_model("fablewright-12b-v1.1")
        self.b.wait_for("document.getElementById('aiPromptText').innerText.includes('Fablewright')", timeout=10)
        self.choose_model("ponder-9b")
        self.b.wait_for("document.getElementById('aiOwnBox').value === 'Make it rhyme.'", timeout=10)

    def test_23_split_explains_its_instructions(self):
        self.b.js("setAiMode('split'); true")
        self.assertEqual(self.prompt_line(), "Split uses VoiceCast's word-for-word instructions.")
        self.assertFalse(self.shown("aiOwnBox"))
        self.b.js("setAiMode('write'); true")
        self.b.wait_for("document.getElementById('aiPromptText').innerText.includes('Ponder 9B')", timeout=10)

    def open_settings(self):
        if not self.shown("aiSettings"):
            self.b.js("document.getElementById('aiSettingsLink').click(); true")
        self.b.wait_for("!document.getElementById('aiSettings').classList.contains('hidden')", timeout=5)

    def settings_status(self):
        return self.b.js("document.getElementById('aiSettingsStatus').innerText")

    def test_30_editor_shows_the_lm_studio_prompt_and_settings(self):
        self.choose_model("fablewright-12b-v1.1")
        self.b.wait_for("document.getElementById('aiPromptText').innerText.includes('Using your LM Studio prompt')", timeout=10)
        self.b.js("document.getElementById('aiPromptLink').click(); true")  # "View" opens the editor
        self.b.wait_for("!document.getElementById('aiSettings').classList.contains('hidden')", timeout=5)
        self.assertTrue(self.b.js("document.getElementById('aiUseLm').checked"))
        self.assertEqual(self.b.js("document.getElementById('aiLmPrompt').value"), fake_lm_studio.FABLEWRIGHT_PROMPT)
        params = self.b.js("document.getElementById('aiLmParams').innerText")
        for part in ("temperature 0.7", "top-p 0.95", "top-k 0", "repeat penalty 1.05", "1 stop word"):
            self.assertIn(part, params)
        self.b.screenshot(os.path.join(SCREENSHOTS, "ai_settings_light.png"))
        self.b.js("window.applyTheme('dark'); true")
        self.b.screenshot(os.path.join(SCREENSHOTS, "ai_settings_dark.png"))
        self.b.js("window.applyTheme('light'); true")

    def test_31_own_instructions_instead_of_lm_studio(self):
        self.open_settings()
        self.b.js("""document.getElementById('aiUseOwn').checked = true;
                     document.getElementById('aiOwnText').value = 'Short and sweet.';
                     document.querySelector('[data-click=saveAiWriteSettings]').click(); true""")
        self.b.wait_for("document.getElementById('aiPromptText').innerText.includes('Using your own instructions')", timeout=10)
        self.assertEqual(self.saved_settings()["llm_model_prompts"]["fablewright-12b-v1.1"],
                         {"text": "Short and sweet.", "use_own": True})
        self.assertEqual(self.settings_status(), "Saved.")
        self.b.js("""document.getElementById('aiUseLm').checked = true;
                     document.querySelector('[data-click=saveAiWriteSettings]').click(); true""")
        self.b.wait_for("document.getElementById('aiPromptText').innerText.includes('Using your LM Studio prompt')", timeout=10)
        self.assertEqual(self.saved_settings()["llm_model_prompts"]["fablewright-12b-v1.1"],
                         {"text": "Short and sweet.", "use_own": False})

    def test_32_split_instructions_save_and_reset(self):
        self.open_settings()
        self.assertTrue(self.b.js("document.getElementById('aiSplitText').value.startsWith('You prepare existing text')"))
        self.b.js("""document.getElementById('aiSplitText').value = 'Split carefully.';
                     document.querySelector('[data-click=saveAiSplitSettings]').click(); true""")
        self.b.wait_for("document.getElementById('aiSettingsStatus').innerText === 'Saved.'", timeout=5)
        self.assertEqual(self.saved_settings()["llm_instructions"], {"split": "Split carefully."})
        self.b.js("document.querySelector('[data-click=resetAiSplitSettings]').click(); true")
        self.b.wait_for("document.getElementById('aiSettingsStatus').innerText.startsWith('Reset')", timeout=5)
        self.assertEqual(self.saved_settings()["llm_instructions"], {})
        self.assertTrue(self.b.js("document.getElementById('aiSplitText').value.startsWith('You prepare existing text')"))

    def test_33_connection_settings(self):
        self.open_settings()
        self.assertIn("silent for", self.b.js("document.getElementById('aiTimeout').parentElement.innerText"))
        self.assertEqual(self.b.js("document.getElementById('aiTimeout').value"), "3", "3-minute default")
        self.assertEqual(self.b.js("document.getElementById('aiBaseUrl').value"), self.fake_url)
        self.b.js("""document.getElementById('aiBaseUrl').value = 'http://192.168.1.5:1234';
                     document.querySelector('[data-click=saveAiConnection]').click(); true""")
        self.b.wait_for("document.getElementById('aiSettingsStatus').innerText.includes('Only an address on this computer')", timeout=5)
        self.assertEqual(self.saved_settings()["llm_base_url"], self.fake_url)
        self.assertEqual(self.b.js("userSettings.llm_base_url"), self.fake_url, "the refused value is not kept")
        self.b.js(f"""document.getElementById('aiBaseUrl').value = '{self.fake_url}';
                      document.getElementById('aiTimeout').value = '2';
                      document.querySelector('[data-click=saveAiConnection]').click(); true""")
        self.b.wait_for("document.getElementById('aiSettingsStatus').innerText.startsWith('Saved. LM Studio connected')",
                        timeout=10)
        self.assertEqual(self.saved_settings()["llm_timeout_seconds"], 120)
        self.b.js("document.getElementById('aiTimeout').value = '45'; "
                  "document.querySelector('[data-click=saveAiConnection]').click(); true")
        self.assertIn("1 to 30 minutes", self.settings_status())
        self.b.js("document.getElementById('aiSettingsLink').click(); true")
        self.assertFalse(self.shown("aiSettings"))

    def choose_program(self, program, url=None, context=None):
        """Picks a program in the Connection settings and saves (url: the address to use)."""
        self.open_settings()
        js = f"document.getElementById('aiProgram').value = {json.dumps(program)}; onAiProgramChange();"
        if url:
            js += f"document.getElementById('aiBaseUrl').value = {json.dumps(url)};"
        if context:
            js += f"document.getElementById('aiContextLength').value = {json.dumps(str(context))};"
        self.b.js(js + "document.querySelector('[data-click=saveAiConnection]').click(); true")

    def test_34_choosing_the_ai_program(self):
        self.open_settings()
        self.assertEqual(self.b.js("[...document.getElementById('aiProgram').options].map(o => o.textContent)"),
                         ["LM Studio", "Ollama", "Jan", "KoboldCpp", "llama.cpp server", "Other (same request format)"])
        self.assertEqual(self.b.js("document.getElementById('aiProgram').value"), "lmstudio")
        self.assertFalse(self.shown("aiContextRow"), "LM Studio reports the text room itself")
        start = self.b.js("document.getElementById('aiBaseUrl').value")
        self.b.js("document.getElementById('aiBaseUrl').value = 'http://127.0.0.1:1234';"
                  "const sel = document.getElementById('aiProgram'); sel.value = 'ollama';"
                  "sel.dispatchEvent(new Event('change', { bubbles: true })); true")  # as a person's choice does
        self.assertEqual(self.b.js("document.getElementById('aiBaseUrl').value"), "http://127.0.0.1:11434")
        self.assertFalse(self.shown("aiContextRow"), "Ollama reports the text room itself")
        self.b.js("document.getElementById('aiProgram').value = 'jan'; onAiProgramChange(); true")
        self.assertEqual(self.b.js("document.getElementById('aiBaseUrl').value"), "http://127.0.0.1:1337")
        self.assertTrue(self.shown("aiContextRow"))
        self.b.js("document.getElementById('aiBaseUrl').value = 'http://127.0.0.1:4321';"
                  "document.getElementById('aiProgram').value = 'koboldcpp'; onAiProgramChange(); true")
        self.assertEqual(self.b.js("document.getElementById('aiBaseUrl').value"), "http://127.0.0.1:4321",
                         "a typed address is kept")
        self.b.js(f"document.getElementById('aiProgram').value = 'lmstudio'; onAiProgramChange();"
                  f"document.getElementById('aiBaseUrl').value = {json.dumps(start)}; true")
        self.assertIn("silent for", self.b.js("document.getElementById('aiTimeout').parentElement.innerText"))
        self.assertNotIn("LM Studio", self.b.js("document.getElementById('aiBaseUrl').parentElement.innerText"))

    def test_35_writing_with_ollama(self):
        import fake_ollama
        ollama = fake_ollama.FakeOllama()
        url = ollama.start()
        try:
            self.choose_program("ollama", url)
            self.b.wait_for("document.getElementById('aiStatus').innerText.startsWith('Ollama connected')", timeout=10)
            self.assertEqual(self.b.js("[...document.getElementById('aiModel').options].map(o => o.textContent)"),
                             ["llama3.2:3b (ready)", "qwen3:8b"])
            self.b.js("document.getElementById('aiModel').value = 'qwen3:8b'; onAiModelChange(); true")
            self.b.wait_for("document.getElementById('aiPromptText').innerText.includes(\"VoiceCast's instructions\")",
                            timeout=5)
            self.assertFalse(self.shown("aiLmPrompt"), "no LM Studio prompt box for Ollama")
            ollama.replies.append(Reply(content=self.STORY, delay=1.5))
            self.b.js("setAiMode('write'); document.getElementById('scriptInput').value = '';"
                      "document.getElementById('aiRequest').value = 'A fox and an owl.'; true")
            self.click_write()
            self.b.wait_for("document.getElementById('aiStatus').innerText.startsWith('Loading the model in Ollama')",
                            timeout=5)
            self.b.wait_for("document.getElementById('scriptInput').value.includes('[Narrator]')", timeout=10)
            self.assertEqual(ollama.requests[0]["model"], "qwen3:8b")
        finally:
            ollama.stop()
            self.choose_program("lmstudio", self.fake_url)
            self.b.wait_for("document.getElementById('aiStatus').innerText.startsWith('LM Studio connected')", timeout=10)

    def test_36_basic_mode_and_not_answering(self):
        self.choose_program("jan", fake_lm_studio.free_port_url(), context=8192)
        self.b.wait_for("document.getElementById('aiStatus').innerText.includes(\"Jan isn't answering\")", timeout=10)
        # the result is also shown beside the Save button, where the user is looking
        self.b.wait_for("document.getElementById('aiSettingsStatus').innerText.startsWith(\"Saved. Jan isn't answering\")",
                        timeout=10)
        self.assertTrue(self.b.js("document.getElementById('aiSettingsStatus').classList.contains('is-error')"))
        self.assertEqual(self.b.js("userSettings.llm_context_length"), 8192)
        self.choose_program("jan", self.fake_url)
        self.b.wait_for("document.getElementById('aiStatus').innerText.startsWith('Jan connected')", timeout=10)
        self.assertEqual(self.b.js("[...document.getElementById('aiModel').options].map(o => o.value)"),
                         ["story-model-7b", "helper-3b"])
        self.b.js("document.getElementById('aiContextLength').value = '100';"
                  "document.querySelector('[data-click=saveAiConnection]').click(); true")
        self.assertIn("2,048 to 262,144", self.settings_status())
        self.choose_program("lmstudio", self.fake_url)
        self.b.wait_for("document.getElementById('aiStatus').innerText.startsWith('LM Studio connected')", timeout=10)
        self.assertEqual(self.b.js("document.getElementById('aiModel').value"), "fablewright-12b-v1.1")

    def run_write_and_replace(self, reply):
        self.fake.replies.append(Reply(content=reply))
        self.b.js("setAiMode('write'); document.getElementById('aiRequest').value = 'A story.'; true")
        self.click_write()
        self.b.wait_for("!document.getElementById('aiChoice').classList.contains('hidden')", timeout=10)
        self.b.js("document.querySelector('[data-click=aiInsertChoice][data-value=replace]').click(); true")

    def cast(self):
        return self.b.js("Object.fromEntries(characters.map(c => [c.name, [c.gender, c.voice]]))")

    def test_50_new_speakers_get_voices_from_their_type(self):
        self.b.js("""document.getElementById('scriptInput').value = '[Speaker 1] Hi.';
                     autoDetectSpeakers(true);
                     characters.find(c => c.name === 'Speaker 1').voice = 'bm_george';
                     userSettings.voice_nicknames = { bm_lewis: 'Skipper' }; true""")
        reply = json.dumps({"title": "T",
                            "cast": [{"name": "Speaker 1", "voice_type": "Female"},
                                     {"name": "Maya", "voice_type": "Child / Youth"},
                                     {"name": "Ann", "voice_type": "Female"},
                                     {"name": "Bea", "voice_type": "Female"},
                                     {"name": "Skipper", "voice_type": "Male"},
                                     {"name": "Robot", "voice_type": "Neutral"}],
                            "lines": [{"speaker": n, "text": "Hi."}
                                      for n in ("Speaker 1", "Maya", "Ann", "Bea", "Skipper", "Robot")]})
        self.run_write_and_replace(reply)
        cast = self.cast()
        self.assertEqual(cast["Speaker 1"][1], "bm_george", "a speaker already in the cast keeps its voice")
        self.assertEqual(cast["Maya"][0], "Child")
        self.assertEqual(cast["Ann"][0], "Female")
        self.assertNotEqual(cast["Ann"][1], cast["Bea"][1], "two new women get different voices")
        self.assertEqual(cast["Skipper"][1], "bm_lewis", "a nickname-named speaker gets that voice")
        self.assertEqual(cast["Robot"][0], "Neutral")
        self.b.js("userSettings.voice_nicknames = {}; true")
        self.b.screenshot(os.path.join(SCREENSHOTS, "ai_cast_cards.png"), full_page=True)

    def test_51_no_voice_types_keeps_name_guesses(self):
        self.b.js("document.getElementById('scriptInput').value = '[Speaker 1] Hi.'; autoDetectSpeakers(true); true")
        # A plain-text answer: the server sends voice_type null for every speaker.
        self.fake.replies.append(Reply(content="not json"))
        self.fake.replies.append(Reply(content="[Narrator] Snow.\n[Grandma] Brr."))
        self.b.js("setAiMode('write'); document.getElementById('aiRequest').value = 'A story.'; true")
        self.click_write()
        self.b.wait_for("!document.getElementById('aiChoice').classList.contains('hidden')", timeout=10)
        self.b.js("document.querySelector('[data-click=aiInsertChoice][data-value=replace]').click(); true")
        guessed = self.b.js("guessGenderAndVoice('Grandma', 0).gender")
        self.assertEqual(self.cast()["Grandma"][0], guessed)

    CHAPTER = json.dumps({"title": "Fox 2", "cast": [{"name": "Fox", "voice_type": "Female"},
                                                     {"name": "Hen", "voice_type": "Female"}],
                          "lines": [{"speaker": "Narrator", "text": "Chapter 2."},
                                    {"speaker": "Fox", "text": "Morning, Hen."},
                                    {"speaker": "Hen", "text": "Cluck."}]})
    CHAPTER_ONE = "[Narrator] Chapter 1. Night fell.\n[Fox] Hello, Owl."

    def start_continue(self, request=""):
        self.b.js(f"""setAiMode('continue');
                      document.getElementById('scriptInput').value = {json.dumps(self.CHAPTER_ONE)};
                      autoDetectSpeakers(true);
                      document.getElementById('aiRequest').value = {json.dumps(request)}; true""")

    def test_60_continue_needs_a_story(self):
        self.b.js("setAiMode('continue'); document.getElementById('scriptInput').value = ''; true")
        self.assertEqual(self.b.js("document.getElementById('aiWriteBtn').innerText"), "Write Next Chapter")
        self.assertEqual(self.b.js("document.getElementById('aiRequest').placeholder"),
                         "What should happen next? (optional)")
        self.assertTrue(self.shown("aiLengths"))
        sent = len(self.fake.requests)
        self.click_write()
        self.assertEqual(self.status(), "Write or open a story first; Continue writes its next chapter.")
        self.assertEqual(len(self.fake.requests), sent)
        self.assertTrue(self.b.js("""(() => { const panel = document.getElementById('aiPanel').getBoundingClientRect();
            return [...document.querySelectorAll('.ai-mode, .ai-length')].every(b =>
                b.getBoundingClientRect().right <= panel.right + 1); })()"""), "the mode and length buttons fit")
        self.b.js("document.getElementById('aiPanel').scrollIntoView(); true")
        self.b.screenshot(os.path.join(SCREENSHOTS, "ai_continue_mode.png"))

    def test_61_unsaved_story_asks_first_and_cancel_leaves_it(self):
        self.start_continue()
        self.b.js("savedScriptText = null; true")
        sent = len(self.fake.requests)
        self.click_write()
        self.assertTrue(self.shown("aiSaveChoice"))
        self.assertEqual(len(self.fake.requests), sent, "nothing is sent before the choice")
        self.b.screenshot(os.path.join(SCREENSHOTS, "ai_continue_save_first.png"))
        self.b.js("document.querySelector('[data-click=aiSaveChoice][data-value=cancel]').click(); true")
        self.assertFalse(self.shown("aiSaveChoice"))
        self.assertEqual(self.script(), self.CHAPTER_ONE)
        self.assertEqual(self.status(), "Left your script as it was.")

    def test_62_save_and_continue_waits_for_the_save(self):
        self.start_continue("The owl finds a key.")
        self.b.js("""window._realSave = saveProject; savedScriptText = null;
                     characters.find(c => c.name === 'Fox').voice = 'bm_george';
                     saveProject = async () => false; true""")
        self.click_write()
        sent = len(self.fake.requests)
        self.b.js("document.querySelector('[data-click=aiSaveChoice][data-value=save]').click(); true")
        self.b.wait_for("true", timeout=1)
        self.assertTrue(self.shown("aiSaveChoice"), "a closed save dialog keeps the row")
        self.assertEqual(len(self.fake.requests), sent)
        self.fake.replies.append(Reply(content=self.CHAPTER))
        self.b.js("saveProject = async () => { markProjectSaved(); return true; }; true")
        self.b.js("document.querySelector('[data-click=aiSaveChoice][data-value=save]').click(); true")
        self.b.wait_for("document.getElementById('scriptInput').value.includes('[Hen] Cluck.')", timeout=10)
        self.b.js("saveProject = window._realSave; true")
        self.assertEqual(self.script(), "[Narrator] Chapter 2.\n[Fox] Morning, Hen.\n[Hen] Cluck.",
                         "the chapter replaces the story")
        self.assertFalse(self.shown("aiChoice"), "Continue doesn't ask Replace / Add / Cancel")
        user = self.fake.requests[-1]["messages"][1]["content"]
        self.assertIn(self.CHAPTER_ONE, user)
        self.assertIn('"Chapter 2."', user)
        self.assertIn("What should happen next: The owl finds a key.", user)
        self.assertEqual(self.cast()["Fox"][1], "bm_george", "the cast and voices stay")
        self.assertEqual(self.cast()["Hen"][0], "Female")

    def test_63_continue_without_saving_clears_audio_and_the_name(self):
        self.start_continue()
        self.b.js("""setProjectName('Story.voicecast.json'); markProjectSaved();
                     document.getElementById('mainAudio').setAttribute('src', 'data:audio/wav;base64,');
                     true""")
        self.click_write()
        self.assertTrue(self.shown("aiSaveChoice"), "audio in the Output zone asks first too")
        self.fake.replies.append(Reply(content=self.CHAPTER))
        self.b.js("document.querySelector('[data-click=aiSaveChoice][data-value=nosave]').click(); true")
        self.b.wait_for("document.getElementById('scriptInput').value.includes('[Hen] Cluck.')", timeout=10)
        self.assertFalse(self.b.js("!!document.getElementById('mainAudio').getAttribute('src')"))
        self.assertEqual(self.b.js("document.getElementById('projectName').innerText"), "Unsaved project")
        self.assertIsNone(self.b.js("projectFileHandle"))

    def test_64_saved_story_without_audio_goes_straight_on(self):
        self.start_continue()
        self.b.js("markProjectSaved(); true")
        self.fake.replies.append(Reply(content=self.CHAPTER))
        self.click_write()
        self.assertFalse(self.shown("aiSaveChoice"))
        self.b.wait_for("document.getElementById('scriptInput').value.includes('[Hen] Cluck.')", timeout=10)
        self.b.js("setAiMode('write'); true")

    def test_65_continue_waits_for_audio_being_made(self):
        self.start_continue()
        self.b.js("markProjectSaved(); document.getElementById('generateBtn').disabled = true; true")
        sent = len(self.fake.requests)
        try:
            self.click_write()
            self.assertEqual(self.status(), "Audio is being made for this story. Wait for it to finish, "
                                            "or press Stop, then continue.")
            self.assertFalse(self.shown("aiSaveChoice"))
            self.assertEqual(len(self.fake.requests), sent)
        finally:
            self.b.js("document.getElementById('generateBtn').disabled = false; setAiMode('write'); true")

    def test_66_save_and_continue_sends_once(self):
        self.start_continue()
        self.b.js("""window._realSave = saveProject; savedScriptText = null;
                     saveProject = () => new Promise(r => { window._finishSave = r; }); true""")
        self.click_write()
        sent = len(self.fake.requests)
        self.fake.replies.append(Reply(content=self.CHAPTER))
        self.b.js("document.querySelector('[data-click=aiSaveChoice][data-value=save]').click(); true")
        self.assertTrue(self.b.js("[...document.querySelectorAll('#aiSaveChoice button')].every(b => b.disabled)"),
                        "the row's buttons wait while the save window is open")
        self.b.js("document.querySelector('[data-click=aiSaveChoice][data-value=nosave]').click(); true")
        self.b.js("markProjectSaved(); window._finishSave(true); true")
        self.b.wait_for("document.getElementById('scriptInput').value.includes('[Hen] Cluck.')", timeout=10)
        self.b.js("saveProject = window._realSave; setAiMode('write'); true")
        self.assertEqual(len(self.fake.requests), sent + 1, "only one request")
        self.assertFalse(self.b.js("[...document.querySelectorAll('#aiSaveChoice button')].some(b => b.disabled)"))

    SCRIPT_BUTTONS = "#dynamicTagButtons button, #btnFormatProse, [data-click=loadSampleScript], [data-click=openProject]"

    def test_67_script_box_is_locked_while_continue_writes(self):
        self.start_continue()
        self.b.js("markProjectSaved(); true")
        self.fake.replies.append(Reply(content=self.CHAPTER, delay=1.5))
        self.click_write()
        self.b.wait_for("aiBusy", timeout=5)
        self.assertTrue(self.b.js("document.getElementById('scriptInput').readOnly"), "no edits that would be replaced")
        self.assertTrue(self.b.js(f"[...document.querySelectorAll({self.SCRIPT_BUTTONS!r})].every(b => b.disabled)"),
                        "nor buttons that change the script")
        self.b.wait_for("document.getElementById('scriptInput').value.includes('[Hen] Cluck.')", timeout=10)
        self.assertFalse(self.b.js("document.getElementById('scriptInput').readOnly"))
        self.assertFalse(self.b.js(f"[...document.querySelectorAll({self.SCRIPT_BUTTONS!r})].some(b => b.disabled)"))
        self.b.js("setAiMode('write'); true")

    def test_68_save_check_looks_at_the_script_only(self):
        self.start_continue()
        self.b.js("markProjectSaved(); characters.find(c => c.name === 'Fox').voice = 'bm_george';"
                  "characters.find(c => c.name === 'Fox').speed = 1.3; true")
        self.assertFalse(self.b.js("projectHasUnsavedScript()"), "voice and speed changes don't block Continue")
        self.b.js("document.getElementById('scriptInput').value += ' More.'; true")
        self.assertTrue(self.b.js("projectHasUnsavedScript()"))
        self.b.js("setAiMode('write'); true")

    def test_69_a_failed_save_does_not_continue(self):
        self.start_continue()
        self.b.js("""window._realPicker = window.showSaveFilePicker; window._downloads = 0;
                     window._realCreateUrl = URL.createObjectURL; URL.createObjectURL = b => { _downloads++; return 'blob:x'; };
                     window._aborted = 0; window._failWrite = true;
                     window.showSaveFilePicker = async () => ({ name: 'Story.json', createWritable: async () => ({
                       write: async () => { if (_failWrite) throw new DOMException('The disk is full.', 'QuotaExceededError'); },
                       abort: async () => { _aborted++; }, close: async () => {} }) });
                     projectFileHandle = null; savedScriptText = null; true""")
        try:
            self.click_write()
            self.b.js("document.querySelector('[data-click=aiSaveChoice][data-value=save]').click(); true")
            self.b.wait_for("document.getElementById('aiStatus').innerText.includes('could not be saved')", timeout=5)
            self.assertTrue(self.shown("aiSaveChoice"), "the row stays, nothing is sent")
            self.assertEqual(self.b.js("_downloads"), 0, "no download instead")
            self.assertEqual(self.b.js("_aborted"), 1, "the half-written file is thrown away")
            self.assertIn("could not be saved", self.b.js("document.getElementById('progressStatus').innerText"))
            self.b.js("_failWrite = false; true")
            self.assertTrue(self.b.js("saveProject(false)"))
            self.assertNotEqual(self.b.js("document.getElementById('progressTitle').innerText"), "Not saved",
                                "a save that works clears the old 'Not saved'")
        finally:
            self.b.js("window.showSaveFilePicker = window._realPicker; URL.createObjectURL = window._realCreateUrl;"
                      "projectFileHandle = null; setAiMode('write'); true")

    def test_52_speaker_with_a_type_does_not_take_a_guessed_voice(self):
        self.b.js("document.getElementById('scriptInput').value = '[Narrator] Old.'; autoDetectSpeakers(true); true")
        # Bella has no type from the model, so she keeps her name's guess (af_bella), the first voice for a new woman.
        reply = json.dumps({"title": "T", "cast": [{"name": "Ann", "voice_type": "Female"}],
                            "lines": [{"speaker": "Ann", "text": "Hello."}, {"speaker": "Bella", "text": "Hi."}]})
        self.run_write_and_replace(reply)
        cast = self.cast()
        self.assertEqual(cast["Bella"][1], "af_bella")
        self.assertNotEqual(cast["Ann"][1], "af_bella", "Ann gets a voice nobody uses")

    def stub_generate(self):
        self.b.js("""window._realGenerate = generateAudio; window.autoAudioCalls = 0;
                     generateAudio = async () => { autoAudioCalls++; return true; }; true""")

    def restore_generate(self):
        self.b.js("generateAudio = window._realGenerate; document.title = STUDIO_TITLE; true")

    def test_70_auto_audio_checkbox_is_remembered(self):
        self.assertFalse(self.b.js("document.getElementById('aiAutoAudio').checked"), "off by default")
        self.b.js("document.getElementById('aiAutoAudio').click(); true")
        self.b.wait_for("!settingsSaving", timeout=5)
        self.assertTrue(self.saved_settings()["llm_auto_audio"])

    def test_71_auto_audio_waits_for_the_choice(self):
        self.stub_generate()
        try:
            self.b.js("setAiMode('write'); document.getElementById('scriptInput').value = '[Narrator] Old.'; true")
            self.fake.replies.append(Reply(content=self.STORY))
            self.b.js("document.getElementById('aiRequest').value = 'A fox.'; true")
            self.click_write()
            self.b.wait_for("!document.getElementById('aiChoice').classList.contains('hidden')", timeout=10)
            self.assertEqual(self.b.js("autoAudioCalls"), 0, "no audio before the choice")
            self.b.js("document.querySelector('[data-click=aiInsertChoice][data-value=cancel]').click(); true")
            self.assertEqual(self.b.js("autoAudioCalls"), 0, "Cancel means no audio")
            self.fake.replies.append(Reply(content=self.STORY))
            self.click_write()
            self.b.wait_for("!document.getElementById('aiChoice').classList.contains('hidden')", timeout=10)
            self.b.js("document.querySelector('[data-click=aiInsertChoice][data-value=replace]').click(); true")
            self.b.wait_for("autoAudioCalls === 1", timeout=5)
            self.b.wait_for("document.title.startsWith('✓ Audio ready · ')", timeout=5)
        finally:
            self.restore_generate()

    def test_72_auto_audio_not_twice(self):
        self.stub_generate()
        try:
            self.b.js("document.getElementById('scriptInput').value = ''; document.getElementById('generateBtn').disabled = true; true")
            self.fake.replies.append(Reply(content=self.STORY))
            self.click_write()
            self.b.wait_for("document.getElementById('scriptInput').value.includes('[Owl] Hoo.')", timeout=10)
            self.assertEqual(self.b.js("autoAudioCalls"), 0)
            self.assertEqual(self.status(), "Audio is already being made; press Generate when it's done.")
        finally:
            self.b.js("document.getElementById('generateBtn').disabled = false; true")
            self.restore_generate()
            self.b.js("document.getElementById('aiAutoAudio').click(); true")  # back off for later tests
            self.b.wait_for("!settingsSaving", timeout=5)

    MIXED = json.dumps({"title": "Twins", "cast": [{"name": "Speaker 2", "voice_type": "Female"}],
                        "lines": [{"speaker": "Narrator", "text": "Night fell."},
                                  {"speaker": "Speaker 2", "text": "Rose grinned. 'Let's play, Lily.'"}]})
    MIXED_AS_SENT = "[Narrator] Night fell.\n[Speaker 2] Rose grinned. 'Let's play, Lily.'"
    MIXED_SPLIT = "[Narrator] Night fell.\n[Narrator] Rose grinned.\n[Speaker 2] Let's play, Lily."

    def undo_split(self):
        self.b.js("document.querySelector('[data-click=undoAiSplit]').click(); true")

    def test_73_auto_split_checkbox_is_remembered(self):
        self.assertFalse(self.b.js("document.getElementById('aiAutoSplit').checked"), "off by default")
        self.assertFalse(self.shown("aiSplitNote"))
        self.b.js("document.getElementById('aiAutoSplit').click(); true")
        self.b.wait_for("!settingsSaving", timeout=5)
        self.assertTrue(self.saved_settings()["llm_auto_split"])

    def test_74_auto_split_and_undo(self):
        self.b.js("setAiMode('write'); document.getElementById('scriptInput').value = '';"
                  "document.getElementById('aiRequest').value = 'Twins.'; true")
        self.fake.replies.append(Reply(content=self.MIXED))
        self.click_write()
        self.b.wait_for("document.getElementById('scriptInput').value.includes('Lily')", timeout=10)
        self.assertEqual(self.script(), self.MIXED_SPLIT)
        self.assertTrue(self.shown("aiSplitNote"))
        self.assertIn("1 line", self.b.js("document.getElementById('aiSplitNote').innerText"))
        self.b.js("document.getElementById('aiSplitNote').scrollIntoView({block: 'center'}); true")
        self.b.screenshot(os.path.join(SCREENSHOTS, "ai_auto_split.png"))
        self.undo_split()
        self.assertEqual(self.script(), self.MIXED_AS_SENT, "Undo puts back what the AI wrote")
        self.assertFalse(self.shown("aiSplitNote"))
        self.assertIn("as the AI wrote it", self.status())

    def test_75_auto_split_only_changes_the_new_text(self):
        own = '[Ann] "Hi," she said.'
        self.b.js(f"document.getElementById('scriptInput').value = {json.dumps(own)}; true")
        self.fake.replies.append(Reply(content=self.MIXED))
        self.click_write()
        self.b.wait_for("!document.getElementById('aiChoice').classList.contains('hidden')", timeout=10)
        self.b.js("document.querySelector('[data-click=aiInsertChoice][data-value=append]').click(); true")
        self.assertEqual(self.script(), f"{own}\n{self.MIXED_SPLIT}", "the user's own line is left alone")
        # edited after the split: Undo asks first, and No keeps the edits
        self.b.js("document.getElementById('scriptInput').value += '\\n[Ann] Bye.'; window.confirm = () => false; true")
        self.undo_split()
        self.assertTrue(self.script().endswith("[Ann] Bye."))
        self.assertTrue(self.shown("aiSplitNote"))
        self.b.js("window.confirm = () => true; true")
        self.undo_split()
        self.assertEqual(self.script(), f"{own}\n{self.MIXED_AS_SENT}")

    def test_76_auto_split_leaves_split_my_text_alone(self):
        text = "Rose grinned. 'Let's play, Lily.'"
        self.b.js(f"setAiMode('split'); document.getElementById('scriptInput').value = '';"
                  f"document.getElementById('aiRequest').value = {json.dumps(text)}; true")
        self.fake.replies.append(Reply(content=json.dumps(
            {"title": "", "cast": [], "lines": [{"speaker": "Speaker 2", "text": text}]})))
        self.click_write()
        self.b.wait_for("document.getElementById('scriptInput').value.includes('Lily')", timeout=10)
        self.assertEqual(self.script(), f"[Speaker 2] {text}", "Split my text never changes the words")
        self.assertFalse(self.shown("aiSplitNote"))
        self.b.js("setAiMode('write'); document.getElementById('aiAutoSplit').click(); true")  # back off for later tests
        self.b.wait_for("!settingsSaving", timeout=5)

    def test_77_steadier_lengths(self):
        self.assertEqual(self.b.js("aiStartedAt = Date.now(); aiMode = 'write';"
                                   "aiProgress = {words: 900, thinking: false, phase: 'more', round: 2, rounds: 3};"
                                   "aiBusyText()"), "Writing… about 900 words · adding more (2 of 3) · 0 s")
        self.assertEqual(self.b.js("aiProgress = {words: 900, thinking: false, phase: 'ending'}; aiBusyText()"),
                         "Writing… about 900 words · adding an ending · 0 s")
        before = self.b.js("userSettings.llm_length")
        self.web_server.lm_studio.STEADY_LENGTHS = True
        try:
            def words(n):
                return json.dumps({"title": "T", "cast": [],
                                   "lines": [{"speaker": "Narrator", "text": " ".join(["word"] * n)}]})
            self.fake.replies += [Reply(content=words(100)), Reply(content=words(250))]
            self.b.js("document.getElementById('scriptInput').value = ''; setAiLength('short'); true")
            self.click_write()
            self.b.wait_for("document.getElementById('aiStatus').innerText.includes('Topped up once')", timeout=15)
            self.assertEqual(self.status(),
                             "Script added. Topped up once to reach 350 words. Check it, then press Generate.")
        finally:
            self.web_server.lm_studio.STEADY_LENGTHS = False
            self.b.js(f"setAiLength({json.dumps(before)}); true")

        # Review fix: Stop while adding more keeps the story but never starts the audio by itself.
        made = self.b.js("const realAuto = autoMakeAudio, realSetting = userSettings.llm_auto_audio; let made = 0;"
                         "autoMakeAudio = () => { made++; }; userSettings.llm_auto_audio = true;"
                         "insertAiScript({script: '[Narrator] Kept.', cast: [], stopped_early: true,"
                         " length_note: 'Stopped while adding more: kept 2 words.'}, 'replace');"
                         "insertAiScript({script: '[Narrator] Done.', cast: []}, 'replace');"
                         "autoMakeAudio = realAuto; userSettings.llm_auto_audio = realSetting; made")
        self.assertEqual(made, 1, "only the finished script starts the audio")

    def test_78_story_presets_removed(self):
        self.b.js("setAiMode('write'); true")
        self.assertTrue(self.b.js("document.getElementById('aiPresetRow') === null"))
        self.assertTrue(self.b.js("document.getElementById('aiPreset') === null"))
        self.assertNotIn("llm_preset", self.saved_settings())

    def test_79_long_text_counts_the_parts(self):
        self.assertEqual(self.b.js("aiStartedAt = Date.now(); aiMode = 'split';"
                                   "aiProgress = {words: 900, thinking: false, piece: 2, pieces: 5}; aiBusyText()"),
                         "Splitting… about 900 words · part 2 of 5 · 0 s")
        self.assertEqual(self.b.js("aiProgress = {words: 0, thinking: true, piece: 3, pieces: 5}; aiBusyText()"),
                         "Thinking… · part 3 of 5 0 s")
        self.b.js("aiMode = 'write'; setAiMode('write'); true")

    def test_99_no_javascript_errors(self):
        errors = self.b.messages("exception") + self.b.messages("error")
        self.assertEqual(errors, [])


@unittest.skipIf(SKIP_REASON or not real_lm_studio_running(), "LM Studio isn't running on 127.0.0.1:1234")
class RealLMStudioTests(unittest.TestCase):
    def test_real_lm_studio_status(self):
        import lm_studio
        status = lm_studio.get_status("http://127.0.0.1:1234")
        self.assertIsInstance(status["models"], list)


def real_ollama_running() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=2) as r:
            return r.status == 200
    except OSError:
        return False


@unittest.skipIf(SKIP_REASON or not real_ollama_running(), "Ollama isn't running on 127.0.0.1:11434")
class RealOllamaTests(unittest.TestCase):
    def test_real_ollama_models(self):
        import ai_programs
        for model in ai_programs.list_models("ollama", "http://127.0.0.1:11434"):
            self.assertGreater(model["context_length"], 0)

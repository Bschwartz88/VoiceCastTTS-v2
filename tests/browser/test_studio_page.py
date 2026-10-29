"""End-to-end tests of the studio page in headless Edge, using the real Kokoro model.

These are not part of the quick test run. Run them with:

    python -m unittest discover -s tests/browser -v

They need Microsoft Edge (or Chrome; set VOICECAST_BROWSER to its path), the
websocket-client package (requirements-dev.txt) and the model files, and take
about a minute. The steps run in order and build on each other.
Screenshots are saved to tests/browser/screenshots/ for a visual check.
"""

import json
import os
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

SCREENSHOTS = os.path.join(HERE, "screenshots")
# Emoji and symbols the redesign replaced with drawn icons (none may remain on controls).
REPLACED_SYMBOLS = "▶■⏸⇄↺↻★☆✎▾▸⬇📦🔁🔊✨🔍📂💾♀♂🧒⚪🎙"

try:
    from cdp import Browser, find_browser
    BROWSER = find_browser()
    SKIP_REASON = None if BROWSER else "Edge/Chrome not found (set VOICECAST_BROWSER)"
except ImportError as e:
    BROWSER, SKIP_REASON = None, f"missing dependency: {e.name} (pip install -r requirements-dev.txt)"

if not SKIP_REASON and not all(os.path.exists(os.path.join(ROOT, f)) for f in ("kokoro-v1.0.onnx", "voices-v1.0.bin")):
    SKIP_REASON = "model files not downloaded yet (launch the studio once)"

LONG_SCRIPT = "\n".join(
    f"[Narrator] This is test sentence number {i} for the stop button, and it is fairly long so it takes a moment."
    for i in range(1, 13))

PROJECT = {
    "format": "VoiceCastTTS project", "version": 1,
    "script": "[Narrator] Once upon a time.\n[Maya] Hello there!\n[Skipper] Well, hello.",
    "narrator": {"voice": "bm_george", "speed": 0.9},
    "characters": [{"name": "Maya", "gender": "Child", "voice": "af_sky", "speed": 1.1},
                   {"name": "Skipper", "gender": "Male", "voice": "no_such_voice", "speed": 0.85}],
    "pacing": {"dialogue_turn_pause": 0.5, "narrator_transition_pause": 0.9},
    "pronunciations": [{"word": "Maya", "say": "My uh"}],
    "evenVolume": False,
}


@unittest.skipIf(SKIP_REASON, SKIP_REASON)
class StudioPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import web_server
        # Keep the user's real favorites untouched: save test settings to a temporary file.
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

    def text(self, element_id):
        return self.b.js(f"document.getElementById('{element_id}').innerText")

    def generate_and_wait(self, expected_title="Synthesis Complete"):
        self.b.js("generateAudio(); true", await_promise=False)
        self.b.wait_for("getComputedStyle(document.getElementById('stopBtn')).display !== 'none'", timeout=10)
        self.b.wait_for(f"document.getElementById('progressTitle').innerText === '{expected_title}'", timeout=600)

    def speaker_names(self):
        return self.b.js("characters.map(c => getSpeakerName(c.id))")

    # Steps, in order

    def test_01_initial_state(self):
        self.assertEqual(self.text("projectName"), "Unsaved project")
        self.assertEqual(self.b.js("getComputedStyle(document.getElementById('stopBtn')).display"), "none")
        self.assertTrue(self.b.js("document.getElementById('transcriptSection').classList.contains('hidden')"))
        self.assertFalse(self.b.js("document.getElementById('outputEmpty').classList.contains('hidden')"), "empty state shows")
        self.assertTrue(self.b.js("document.getElementById('btnPlayMaster').disabled"), "nothing to play yet")
        self.assertEqual(self.b.js("document.getElementById('scriptInput').value"), "", "a new session starts empty")
        self.assertEqual(self.b.js("[...document.querySelectorAll('#dynamicTagButtons .tag-label')].map(t => t.innerText)"),
                         ["[Narrator]", "[Speaker 1]", "[Speaker 2]", "[Speaker 3]"], "tag buttons are ready to use")
        self.b.js("document.getElementById('generateBtn').click(); true")
        self.assertEqual(self.text("progressTitle"), "Nothing to generate yet")
        self.assertEqual(self.b.js("document.activeElement.id"), "scriptInput")
        self.assertEqual(self.b.messages("dialog"), [], "no pop-up, just a message")
        self.b.js("loadSampleScript(); true")  # the remaining steps use the demo script
        self.assertEqual(self.speaker_names(), ["Speaker 1", "Speaker 2", "Speaker 3"])

    def test_02_render_shows_transcript(self):
        self.b.js("generateAudio(); true", await_promise=False)
        self.b.wait_for("getComputedStyle(document.getElementById('stopBtn')).display !== 'none'", timeout=10)
        self.assertTrue(self.b.js("document.getElementById('importBtn').disabled"), "no Import while rendering")
        self.b.wait_for("document.getElementById('progressTitle').innerText === 'Synthesis Complete'", timeout=600)
        self.assertFalse(self.b.js("document.getElementById('importBtn').disabled"))
        self.assertEqual(self.b.js("document.querySelectorAll('#transcriptList .transcript-line').length"), 9)
        self.assertEqual(self.b.js("getComputedStyle(document.getElementById('stopBtn')).display"), "none")
        starts = self.b.js("transcriptLines.map(l => l.start)")
        self.assertEqual(starts, sorted(starts))
        self.b.wait_for("document.getElementById('mainAudio').duration > 0", timeout=30)
        self.b.screenshot(os.path.join(SCREENSHOTS, "after_render.png"), full_page=True)

    def test_03_highlight_and_click_to_seek(self):
        starts = self.b.js("transcriptLines.map(l => l.start)")
        self.b.js(f"updateTranscriptHighlight({starts[3] + 0.1}); true")
        self.assertEqual(self.b.js("document.querySelector('.transcript-line.active')?.id"), "transcriptLine_3")

        self.b.js("playFromTranscriptLine(5); true")
        self.b.wait_for("!document.getElementById('mainAudio').paused", timeout=10)
        self.assertLess(abs(self.b.js("document.getElementById('mainAudio').currentTime") - starts[5]), 1.0)
        self.b.wait_for(f"document.getElementById('mainAudio').currentTime > {starts[5] + 0.5}", timeout=10)
        self.assertIn(self.b.js("document.querySelector('.transcript-line.active')?.id"),
                      ("transcriptLine_5", "transcriptLine_6"))
        self.b.screenshot(os.path.join(SCREENSHOTS, "transcript_playing.png"), full_page=True)
        self.b.js("document.getElementById('mainAudio').pause(); true")

    def test_04_edit_one_line_reuses_the_rest(self):
        self.b.js("""const t = document.getElementById('scriptInput');
                     t.value = t.value.replace("It's dropping fast.", "It's dropping really fast.");
                     t.dispatchEvent(new Event('input')); true""")
        self.generate_and_wait()
        self.assertIn("8 of 9 lines were unchanged and reused; 1 was rendered.", self.text("progressStatus"))
        self.assertEqual(self.b.js("document.querySelectorAll('.transcript-reused').length"), 8)

    def test_04b_pronunciation_dictionary(self):
        self.assertIn("None yet", self.text("pronunciationList"))
        # Fill in a row the way a user would: click "+ Add Word", type in both boxes.
        self.b.js("""addPronunciation();
                     const [word, say] = document.querySelectorAll('#pronunciationList .pron-row input');
                     word.value = 'lighthouse'; word.dispatchEvent(new Event('input'));
                     say.value = 'light house tower'; say.dispatchEvent(new Event('input')); true""")
        self.generate_and_wait()
        first_line = self.b.js("transcriptLines[0].text")
        self.assertIn("light house tower control room", first_line)
        self.assertIn("8 of 9 lines were unchanged", self.text("progressStatus"), "only the line containing the word re-renders")

        # The play (test) button plays the word as it will be said.
        self.b.console.clear()
        self.b.js("document.querySelector('#pronunciationList .pron-row .btn-sample').click(); true")
        self.b.wait_for("activePreviewAudio && !activePreviewAudio.paused", timeout=120)
        self.assertTrue(self.b.js("!!document.querySelector('#pronunciationList .pron-row .btn-sample use[href=\"#i-stop\"]')"))
        self.assertEqual(self.b.messages("dialog"), [])
        self.b.wait_for("!!document.querySelector('#pronunciationList .pron-row .btn-sample use[href=\"#i-play\"]')", timeout=30)

        state = self.b.js("collectProjectState()")
        self.assertEqual(state["pronunciations"], [{"word": "lighthouse", "say": "light house tower"}])
        self.b.screenshot(os.path.join(SCREENSHOTS, "pronunciations.png"))

    def test_05_stop_and_resume(self):
        self.b.js(f"document.getElementById('scriptInput').value = {json.dumps(LONG_SCRIPT)}; true")
        self.b.console.clear()
        self.b.js("generateAudio(); true", await_promise=False)
        self.b.wait_for("/\\[Line 2\\//.test(document.getElementById('progressStatus').innerText)", timeout=300)
        self.b.js("stopRender(); true", await_promise=False)
        self.b.wait_for("document.getElementById('progressTitle').innerText === 'Stopped'", timeout=300)

        status = self.text("progressStatus")
        self.assertRegex(status, r"^Stopped after \d+ of 12 lines")
        self.assertEqual(self.b.messages("dialog"), [], "Stop should not show an error")
        done = int(status.split("Stopped after ")[1].split(" ")[0])

        self.generate_and_wait()
        self.assertIn(f"{done} of 12 lines were unchanged", self.text("progressStatus"))

    def test_05b_clear_output(self):
        clear = "document.getElementById('clearOutputBtn')"
        hidden = lambda el: self.b.js(f"document.getElementById('{el}').classList.contains('hidden')")
        script_before = self.b.js("document.getElementById('scriptInput').value")
        cast_before = self.b.js("JSON.stringify(characters)")
        self.assertFalse(self.b.js(f"{clear}.classList.contains('hidden')"), "shown when there is audio")
        self.b.screenshot(os.path.join(SCREENSHOTS, "output_clear_button.png"))
        self.b.js("document.getElementById('mainAudio').play(); true")
        self.b.wait_for("!document.getElementById('mainAudio').paused", timeout=10)

        self.b.js(f"{clear}.click(); true")
        self.assertTrue(self.b.js("document.getElementById('mainAudio').paused"), "playback stops")
        self.assertFalse(self.b.js("document.getElementById('mainAudio').hasAttribute('src')"))
        self.assertFalse(hidden("outputEmpty"), "back to 'No audio yet'")
        self.assertTrue(hidden("transcriptSection"))
        self.assertTrue(hidden("exportSection"))
        self.assertEqual((self.text("outDuration"), self.text("outElapsed")), ("-", "-"))
        self.assertEqual((self.text("currentTimeDisplay"), self.text("totalTimeDisplay")), ("00:00", "00:00"))
        self.assertTrue(self.b.js("document.getElementById('btnPlayMaster').disabled"))
        self.assertTrue(self.b.js(f"{clear}.classList.contains('hidden')"), "nothing left to clear")
        self.assertEqual(self.b.js("document.getElementById('scriptInput').value"), script_before)
        self.assertEqual(self.b.js("JSON.stringify(characters)"), cast_before)

        # Clear is unavailable while rendering; Generate brings the audio back from the remembered lines.
        self.assertTrue(self.b.js(f"generateAudio(); {clear}.disabled"), "disabled while rendering")
        self.b.wait_for("document.getElementById('progressTitle').innerText === 'Synthesis Complete'", timeout=600)
        self.assertIn("12 of 12 lines were unchanged", self.text("progressStatus"))
        self.assertFalse(self.b.js(f"{clear}.classList.contains('hidden') || {clear}.disabled"))

    def test_06_project_state_shape(self):
        state = self.b.js("collectProjectState()")
        self.assertEqual(state["format"], "VoiceCastTTS project")
        self.assertIs(state["evenVolume"], True)
        for key in ("script", "narrator", "characters", "pacing"):
            self.assertIn(key, state)
        self.assertEqual(state["performance"], "natural")

    def test_07_open_project_file(self):
        self.b.console.clear()
        self.b.js("document.getElementById('performanceLevel').value = 'lively'; true")
        opened = self.b.js(f"loadProjectFile(new File([{json.dumps(json.dumps(PROJECT))}], 'Story.voicecast.json'))")
        self.assertTrue(opened)
        self.assertEqual(self.speaker_names(), ["Maya", "Skipper"])
        self.assertEqual(self.b.js("[document.getElementById('voice_Narrator').value, getSpeed('Narrator')]"), ["bm_george", 0.9])
        self.assertTrue(self.b.js("document.querySelector('#voice_Narrator_picker .vp-label').textContent").startswith("bm_george "),
                        "the voice picker shows the voice set by the project")
        self.assertEqual(self.b.js("getSpeed(characters[0].id)"), 1.1)
        self.assertEqual(self.b.js("[document.getElementById('pacingDialogue').value, document.getElementById('pacingNarratorVal').innerText]"),
                         ["0.5", "0.9s"])
        self.assertEqual(self.b.js("characters[1].voice"), "af_heart", "unknown voice falls back to a default")
        self.assertTrue(any("no_such_voice" in m for m in self.b.messages("dialog")), "user is told about the missing voice")
        self.assertEqual(self.text("projectName"), "Story.voicecast.json")
        self.assertEqual(self.b.js("activePronunciations()"), [{"word": "Maya", "say": "My uh"}])
        self.assertEqual(self.b.js("document.querySelector('#pronunciationList .pron-row input').value"), "Maya")
        self.assertFalse(self.b.js("document.getElementById('evenVolumeCheckbox').checked"))
        self.assertEqual(self.b.js("document.getElementById('performanceLevel').value"), "natural",
                         "a project saved before Performance existed opens at Natural")
        self.b.screenshot(os.path.join(SCREENSHOTS, "project_opened.png"))

    def test_08_bad_project_file_is_rejected(self):
        self.b.console.clear()
        self.assertFalse(self.b.js("loadProjectFile(new File(['not json'], 'bad.json'))"))
        self.assertTrue(any("not a VoiceCast project" in m for m in self.b.messages("dialog")))
        self.assertEqual(self.speaker_names(), ["Maya", "Skipper"], "open project is untouched")

    def test_09_autosave_survives_reload(self):
        self.b.js("autosaveNow(); true")
        self.b.goto(self.url)
        self.b.wait_for("document.getElementById('progressStatus').innerText.startsWith('↺ Restored')", timeout=30)
        self.assertTrue(self.b.js("document.getElementById('scriptInput').value").startswith("[Narrator] Once upon a time."))
        self.assertEqual(self.speaker_names(), ["Maya", "Skipper"])
        self.assertEqual(self.text("projectName"), "Story.voicecast.json")
        self.assertEqual(self.b.js("activePronunciations()"), [{"word": "Maya", "say": "My uh"}])
        self.assertFalse(self.b.js("projectHasUnsavedScript()"), "an opened story still counts as saved after a refresh")

    def test_09b_new_session_starts_empty(self):
        # Closing the tab ends the session (sessionStorage); opening the studio again starts empty.
        self.b.js("localStorage.setItem('voicecast.autosave.v1', JSON.stringify(collectProjectState())); true")  # an old-style autosave
        self.b.js("sessionStorage.clear(); true")
        self.b.goto(self.url)
        self.b.wait_for("document.querySelectorAll('#characterCardsContainer .role-row').length >= 1", timeout=30)
        self.assertEqual(self.b.js("document.getElementById('scriptInput').value"), "")
        self.assertEqual(self.text("projectName"), "Unsaved project")
        self.assertIsNone(self.b.js("localStorage.getItem('voicecast.autosave.v1')"), "the old saved script is deleted")

    def test_10_reset_sample_starts_fresh(self):
        self.b.js("loadSampleScript(); true")
        self.assertEqual(self.text("projectName"), "Unsaved project")
        self.assertEqual(self.b.js("pronunciations.length"), 0)
        self.assertTrue(self.b.js("document.getElementById('evenVolumeCheckbox').checked"))

    def test_11_favorites_and_folders(self):
        def saved():
            with open(self.web_server.USER_SETTINGS_PATH, encoding="utf-8") as f:
                return json.load(f)

        # Open the narrator's list: every category is a folder, plus an empty Favorites folder.
        self.b.js("document.getElementById('voice_Narrator_picker').click(); true")
        folders = self.b.js("[...document.querySelectorAll('#voicePickerPanel .vp-folder-head')].map(h => h.dataset.folder)")
        self.assertEqual(folders[0], "★ Favorites", "the stored folder name is unchanged")
        self.assertIn("British Dialogue Voices", folders)
        self.assertEqual(self.b.js("document.querySelector('#voicePickerPanel .vp-folder-head .vp-name').textContent"), "Favorites")
        self.assertIn("Click the star", self.b.js("document.querySelector('#voicePickerPanel .vp-empty').textContent"))

        # Star two voices: they appear in Favorites and are saved on the server.
        for voice in ("af_sky", "bm_george"):
            self.b.js(f"document.querySelector('#voicePickerPanel .vp-star[data-voice=\"{voice}\"]').click(); true")
        self.assertEqual(self.b.js("[...document.querySelectorAll('#voicePickerPanel .vp-folder-fav .vp-choose')].map(b => b.dataset.voice)"),
                         ["af_sky", "bm_george"])
        self.b.wait_for(f"fetch('/api/config').then(r => r.json()).then(d => d.user_settings.favorite_voices.length === 2)", timeout=10)

        # Close a folder: its voices are hidden.
        self.b.js("document.querySelector('#voicePickerPanel .vp-folder-head[data-folder=\"American Dialogue Voices\"]').click(); true")
        self.assertEqual(self.b.js("document.querySelectorAll('#voicePickerPanel .vp-choose[data-voice=\"am_adam\"]').length"), 0)
        self.b.js("document.getElementById('voicePickerPanel').scrollTop = 0; true")
        self.b.screenshot(os.path.join(SCREENSHOTS, "voice_picker_open.png"))

        # Choosing a voice from the list sets the narrator's voice and closes the list.
        self.b.js("document.querySelector('#voicePickerPanel .vp-folder-fav .vp-choose[data-voice=\"af_sky\"]').click(); true")
        self.assertEqual(self.b.js("[document.getElementById('voice_Narrator').value, narratorConfig.voice]"), ["af_sky", "af_sky"])
        self.assertIsNone(self.b.js("document.getElementById('voicePickerPanel')"))
        self.assertTrue(self.b.js("!!document.querySelector('#voice_Narrator_picker .vp-fav use[href=\"#i-star-filled\"]')"))

        # Everything survives a reload (a new session).
        self.b.wait_for("fetch('/api/config').then(r => r.json()).then(d => d.user_settings.closed_voice_folders.length === 1)", timeout=10)
        self.assertEqual({k: saved()[k] for k in ("favorite_voices", "closed_voice_folders", "theme")},
                         {"favorite_voices": ["af_sky", "bm_george"],
                          "closed_voice_folders": ["American Dialogue Voices"], "theme": "system"})
        self.b.goto(self.url)
        self.b.wait_for("document.querySelectorAll('#characterCardsContainer .role-row').length >= 1", timeout=30)
        self.b.js("document.querySelector('#characterCardsContainer .voice-picker-btn').click(); true")
        self.assertEqual(self.b.js("[...document.querySelectorAll('#voicePickerPanel .vp-folder-fav .vp-choose')].map(b => b.dataset.voice)"),
                         ["af_sky", "bm_george"])
        self.assertEqual(self.b.js("document.querySelector('#voicePickerPanel .vp-folder-head[data-folder=\"American Dialogue Voices\"]').getAttribute('aria-expanded')"),
                         "false")

        # Escape closes the list.
        self.b.js("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'})); true")
        self.assertIsNone(self.b.js("document.getElementById('voicePickerPanel')"))

    def test_12_every_button_has_an_action(self):
        missing = self.b.js("""[...document.querySelectorAll('[data-click],[data-input],[data-change]')].flatMap(el =>
            ['click', 'input', 'change'].filter(t => el.dataset[t] && !PAGE_ACTIONS[t][el.dataset[t]])
              .map(t => `${t}:${el.dataset[t]}`))""")
        self.assertEqual(missing, [])
        self.assertGreater(self.b.js("document.querySelectorAll('[data-click]').length"), 30)

        # A few real clicks and edits go through the shared listener.
        self.b.js("document.querySelector('#speedPresets_Narrator [data-value=\"1.10\"]').click(); true")
        self.assertEqual(self.b.js("getSpeed('Narrator')"), 1.1)
        self.b.js("document.querySelector('[data-click=\"stepSpeed\"][data-speaker=\"Narrator\"][data-value=\"0.05\"]').click(); true")
        self.assertEqual(self.b.js("getSpeed('Narrator')"), 1.15)
        self.b.js("""const n = document.getElementById('speedNum_Narrator'); n.value = '0.8';
                     n.dispatchEvent(new Event('input', {bubbles: true})); true""")
        self.assertEqual(self.b.js("getSpeed('Narrator')"), 0.8)
        self.b.js("""const p = document.getElementById('pacingDialogue'); p.value = '0.6';
                     p.dispatchEvent(new Event('input', {bubbles: true})); true""")
        self.assertEqual(self.text("pacingDialogueVal"), "0.6s")
        self.assertTrue(self.b.js("document.getElementById('brandLogo').naturalWidth > 0"), "logo loads from its file")

    def test_13_theme_button_and_speaker_colors(self):
        def settings_theme():
            return self.b.js("fetch('/api/config').then(r => r.json()).then(d => d.user_settings.theme)")

        root = "document.documentElement.getAttribute"
        self.assertEqual(self.b.js(f"{root}('data-theme-choice')"), "system")
        self.b.js("document.getElementById('themeToggle').click(); true")
        self.assertEqual(self.b.js(f"[{root}('data-theme-choice'), {root}('data-theme')]"), ["light", "light"])
        self.b.js("document.getElementById('themeToggle').click(); true")
        self.assertEqual(self.b.js(f"[{root}('data-theme'), document.getElementById('themeToggle').innerText]"), ["dark", "Theme: Dark"])
        self.assertEqual(self.b.js("getComputedStyle(document.body).backgroundColor"), "rgb(11, 17, 32)")
        self.b.wait_for("fetch('/api/config').then(r => r.json()).then(d => d.user_settings.theme === 'dark')", timeout=10)

        # The choice survives a reload, with no flash: theme.js sets it before the page is drawn.
        self.b.goto(self.url)
        self.assertEqual(self.b.js(f"{root}('data-theme')"), "dark")
        self.b.wait_for("document.querySelectorAll('#characterCardsContainer .role-row').length >= 1", timeout=30)
        self.b.js("document.getElementById('themeToggle').click(); true")  # back to Auto
        self.b.wait_for("fetch('/api/config').then(r => r.json()).then(d => d.user_settings.theme === 'system')", timeout=10)

        # Every speaker has a color class and an initial; colors follow cast order, not voice type.
        avatars = self.b.js("[...document.querySelectorAll('#characterCardsContainer .spk-avatar')].map(a => [a.className, a.innerText])")
        self.assertEqual([a[0] for a in avatars], [f"spk-avatar spk-c{i}" for i in range(len(avatars))])
        self.assertEqual([a[1] for a in avatars], self.b.js("characters.map(c => speakerInitial(c.name))"))

    def test_14_numbered_speakers(self):
        self.b.js("loadSampleScript(); true")
        self.b.wait_for("characters.length === 3", timeout=10)
        self.assertEqual(self.speaker_names(), ["Speaker 1", "Speaker 2", "Speaker 3"])
        self.assertEqual(self.b.js("[...document.querySelectorAll('#characterCardsContainer .spk-avatar')].map(a => a.innerText)"),
                         ["1", "2", "3"], "numbered speakers show their number, not S")
        voices = self.b.js("characters.map(c => c.voice)")
        self.assertEqual(len(set(voices)), 3, f"each sample speaker has its own voice: {voices}")

        # A new speaker takes the next free number and yet another voice.
        self.b.js("addSpeakerCardManually(); true")
        self.assertEqual(self.speaker_names()[-1], "Speaker 4")
        self.assertNotIn(self.b.js("characters[3].voice"), voices)
        self.b.js("removeSpeakerCard(characters[1].id); addSpeakerCardManually(); true")
        self.assertEqual(self.speaker_names()[-1], "Speaker 2", "a removed number is reused")
        self.b.js("loadSampleScript(); true")

    def test_15_samples_can_be_stopped(self):
        btn = "document.getElementById('sampleBtn_Narrator')"
        playing = "activePreviewAudio && !activePreviewAudio.paused"

        # The Sample button becomes a highlighted Stop button; clicking it stops the sample.
        self.b.js(f"{btn}.click(); true")
        self.assertTrue(self.b.js(f"{btn}.classList.contains('previewing')"))
        self.assertTrue(self.b.js("document.getElementById('compareBtn_Narrator').disabled"))
        self.b.wait_for(playing, timeout=120)
        self.assertTrue(self.b.js(f"{btn}.getAttribute('aria-label')").startswith("Stop"))
        self.assertTrue(self.b.js(f"!!{btn}.querySelector('use[href=\"#i-stop\"]')"))
        self.b.js(f"{btn}.click(); true")
        self.assertFalse(self.b.js(f"!!({playing})"))
        self.assertEqual(self.b.js(f"[{btn}.getAttribute('aria-label'), {btn}.classList.contains('previewing')]"),
                         ["Hear the Narrator's lines", False])
        self.assertTrue(self.b.js(f"!!{btn}.querySelector('use[href=\"#i-play\"]')"), "the play icon is back")
        self.assertFalse(self.b.js("document.getElementById('compareBtn_Narrator').disabled"))

        # Esc stops it too, and so does starting another speaker's sample.
        self.b.js(f"{btn}.click(); true")
        self.b.wait_for(playing, timeout=120)
        self.b.js("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'})); true")
        self.assertFalse(self.b.js(f"!!({playing})"))
        self.assertIsNone(self.b.js("activePreview"))

        other = "document.getElementById(`sampleBtn_${characters[0].id}`)"
        self.b.js(f"{btn}.click(); true")
        self.b.wait_for(playing, timeout=120)
        self.b.js(f"{other}.click(); true")
        self.assertEqual(self.b.js(f"[{btn}.getAttribute('aria-label'), {other}.classList.contains('previewing')]"),
                         ["Hear the Narrator's lines", True])
        self.b.js(f"{other}.click(); true")
        self.assertIsNone(self.b.js("activePreview"))

    def symbols_on(self, selector):
        return self.b.js(f"""[...document.querySelectorAll({json.dumps(selector)})]
            .map(el => el.textContent).filter(t => [...{json.dumps(REPLACED_SYMBOLS)}].some(s => t.includes(s)))""")

    def test_16_fixed_controls_use_icons(self):
        self.assertEqual(self.symbols_on(".app-header button, .app-header .brand-fallback, .zone-script .toolbar button, "
                                         "#aiPanel summary, #aiPanel button, #exportSection a, .player-bar button, "
                                         ".player-bar label"), [])
        for selector, name in (("[data-click=openProject]", "i-folder"),
                               ("[data-click=saveProject][data-value=false]", "i-save"), ("#aiToggle", "i-sparkle"),
                               ("#btnPlayMaster", "i-play"), ("#downloadZipLink", "i-archive")):
            self.assertTrue(self.b.js(f"!!document.querySelector('{selector} use[href=\"#{name}\"]')"), selector)
        self.assertEqual(self.b.js("[...document.querySelectorAll('svg.icon')].filter(s => s.getAttribute('aria-hidden') !== 'true').length"), 0)
        self.assertEqual(self.b.js("document.querySelector('[data-click=openProject]').innerText.trim()"), "Open")
        self.assertEqual(self.b.js("getComputedStyle(document.querySelector('#aiToggle svg.icon')).stroke"),
                         self.b.js("getComputedStyle(document.getElementById('aiToggle')).color"), "icons take the text color")
        self.assertEqual(self.b.js("document.getElementById('srAnnounce').getAttribute('role')"), "status",
                         "status messages are read out through the announcement area (showStatus)")
        self.assertIsNone(self.b.js("document.getElementById('progressBox').getAttribute('aria-live')"),
                          "the per-line progress text isn't read out")
        self.b.screenshot(os.path.join(SCREENSHOTS, "redesign4_header_bar_light.png"))
        self.b.js("window.applyTheme('dark'); true")
        self.b.screenshot(os.path.join(SCREENSHOTS, "redesign4_header_bar_dark.png"))
        self.b.js("window.applyTheme('light'); true")

    def test_17_cards_and_picker_use_icons(self):
        self.b.js("loadSampleScript(); true")
        self.b.wait_for("characters.length === 3", timeout=10)
        self.b.js("document.getElementById('voice_Narrator_picker').click(); true")
        self.b.js("addPronunciation(); true")
        self.assertEqual(self.symbols_on("button, a, option, summary, .vp-folder-head"), [])
        self.b.js("closeVoicePicker(); true")

        # Sample and Compare are icon-only with spoken names, and give the voice name room.
        first = "characters[0].id"
        self.assertEqual(self.b.js(f"document.getElementById(`sampleBtn_${{{first}}}`).getAttribute('aria-label')"),
                         "Hear Speaker 1's lines")
        self.assertEqual(self.b.js(f"document.getElementById(`compareBtn_${{{first}}}`).getAttribute('aria-label')"),
                         "Compare Speaker 1 at 1.00x and the chosen speed")
        width = self.b.js(f"""['sampleBtn_', 'compareBtn_'].map(p => document.getElementById(p + {first})
                              .getBoundingClientRect().width).reduce((a, b) => a + b, 0)""")
        self.assertLessEqual(width, 80)
        self.assertEqual(self.b.js(f"[...document.getElementById(`gender_${{{first}}}`).options].map(o => o.text)"),
                         ["Female", "Male", "Child / Youth", "Neutral"])

        # Renaming a speaker renames its buttons for screen readers.
        self.b.js(f"""const n = document.getElementById(`name_${{{first}}}`); n.value = 'Maya';
                      n.dispatchEvent(new Event('input', {{bubbles: true}})); true""")
        self.assertEqual(self.b.js(f"document.getElementById(`sampleBtn_${{{first}}}`).getAttribute('aria-label')"),
                         "Hear Maya's lines")

        # Every visible control has a name.
        unnamed = self.b.js("""[...document.querySelectorAll('button, a[href], input:not([type=hidden]), select, textarea')]
            .filter(el => el.offsetParent !== null)
            .filter(el => !(el.getAttribute('aria-label') || el.title || (el.labels && el.labels.length)
                            || el.textContent.trim() || el.placeholder))
            .map(el => el.id || el.outerHTML.slice(0, 80))""")
        self.assertEqual(unnamed, [])
        self.b.js("document.querySelector('#characterCardsContainer .role-row').scrollIntoView(); true")
        self.b.screenshot(os.path.join(SCREENSHOTS, "redesign4_card_light.png"))
        self.b.js("window.applyTheme('dark'); true")
        self.b.screenshot(os.path.join(SCREENSHOTS, "redesign4_card_dark.png"))
        self.b.js("window.applyTheme('light'); loadSampleScript(); pronunciations = []; renderPronunciations(); true")

    def key(self, target_js, key, **mods):
        opts = json.dumps({"key": key, "bubbles": True, "cancelable": True, **mods})
        return self.b.js(f"{target_js}.dispatchEvent(new KeyboardEvent('keydown', {opts}))")

    def stub(self, *names):
        self.b.js("window._real = window._real || {}; window.calls = {};" + "".join(
            f"window._real.{n} = {n}; calls.{n} = 0; {n} = () => {{ calls.{n}++; }};" for n in names) + " true")

    def unstub(self, *names):
        self.b.js("".join(f"{n} = window._real.{n};" for n in names) + " true")

    def test_18_ctrl_enter_generates(self):
        self.stub("generateAudio", "aiWriteOrStop")
        try:
            self.b.js("document.getElementById('aiWriteBtn').disabled = false; true")  # as if a model were loaded
            self.key("document.body", "Enter", ctrlKey=True)
            self.key("document.getElementById('scriptInput')", "Enter", ctrlKey=True)
            self.assertEqual(self.b.js("calls.generateAudio"), 2, "from the page and from inside the script")
            self.key("document.getElementById('aiRequest')", "Enter", ctrlKey=True)
            self.assertEqual(self.b.js("[calls.generateAudio, calls.aiWriteOrStop]"), [2, 1],
                             "in the Assistant's box it presses the Assistant's button")
            self.b.js("aiBusy = true; true")
            self.key("document.getElementById('aiRequest')", "Enter", ctrlKey=True)
            self.assertEqual(self.b.js("calls.aiWriteOrStop"), 1, "never presses Stop by accident")
            self.b.js("aiBusy = false; document.getElementById('aiWriteBtn').disabled = true; true")
            self.key("document.getElementById('aiRequest')", "Enter", ctrlKey=True)
            self.assertEqual(self.b.js("calls.aiWriteOrStop"), 1, "not when the Assistant's button is disabled")
            self.b.js("document.getElementById('aiWriteBtn').disabled = false; true")
            self.b.js("aiBusy = false; document.getElementById('generateBtn').disabled = true; true")
            self.key("document.body", "Enter", ctrlKey=True)
            self.assertEqual(self.b.js("calls.generateAudio"), 2, "not while audio is being made")
        finally:
            self.b.js("aiBusy = false; document.getElementById('generateBtn').disabled = false; true")
            self.unstub("generateAudio", "aiWriteOrStop")

    def test_19_space_plays_only_when_not_typing(self):
        self.stub("toggleMasterPlay")
        try:
            self.b.js("document.getElementById('btnPlayMaster').disabled = false; true")
            self.key("document.body", " ")
            self.assertEqual(self.b.js("calls.toggleMasterPlay"), 1)
            self.key("document.body", " ", repeat=True)
            self.assertEqual(self.b.js("calls.toggleMasterPlay"), 1, "holding Space doesn't toggle again and again")
            self.key("document.getElementById('scriptInput')", " ")
            self.key("document.getElementById('generateBtn')", " ")
            self.assertEqual(self.b.js("calls.toggleMasterPlay"), 1, "typing and buttons keep Space")
            self.b.js("document.getElementById('voice_Narrator_picker').click(); true")
            self.key("document.querySelector('#voicePickerPanel .vp-search')", " ")
            self.assertEqual(self.b.js("calls.toggleMasterPlay"), 1, "the picker's search box types a space")
            self.b.js("closeVoicePicker(); document.getElementById('btnPlayMaster').disabled = true; true")
            self.key("document.body", " ")
            self.assertEqual(self.b.js("calls.toggleMasterPlay"), 1, "nothing to play yet")
        finally:
            self.unstub("toggleMasterPlay")

    def test_20_esc_stops_audio_being_made(self):
        self.stub("stopRender")
        try:
            self.b.js("setShown(document.getElementById('stopBtn'), true); document.getElementById('stopBtn').disabled = false; true")
            self.b.js("document.getElementById('voice_Narrator_picker').click(); true")
            self.key("document", "Escape")
            self.assertIsNone(self.b.js("document.getElementById('voicePickerPanel')"), "the picker closes first")
            self.assertEqual(self.b.js("calls.stopRender"), 0)
            self.key("document", "Escape")
            self.assertEqual(self.b.js("calls.stopRender"), 1)
        finally:
            self.b.js("setShown(document.getElementById('stopBtn'), false); true")
            self.unstub("stopRender")

    def test_21_delivery_cues(self):
        script = ("[Speaker 1] Hi. (whispers) They can hear us.\n[whispers] Still me.\n[pause 2s]\n"
                  "[Speaker 2] [pause] Hello?")
        found = self.b.js(f"extractSpeakersFromText({json.dumps(script)})")
        self.assertEqual(found["speakers"], ["Speaker 1", "Speaker 2"], "cues and pauses aren't speakers")

        self.b.js(f"document.getElementById('scriptInput').value = {json.dumps(script)}; autoDetectSpeakers(true); true")
        self.assertEqual(self.speaker_names(), ["Speaker 1", "Speaker 2"])
        key = self.b.js("characters[0].id")
        self.assertEqual(self.b.js(f"getSampleText('{key}')"), "Hi. They can hear us. Still me.",
                         "samples skip cues and keep the speaker across a cue line")

        prose = '[whispers] "Run," Tom said. "Now." (shouts) "Go!"'
        self.b.js(f"document.getElementById('scriptInput').value = {json.dumps(prose)}; formatProseScript(); true")
        self.assertEqual(self.b.js("document.getElementById('scriptInput').value").split("\n\n"),
                         ["[Tom] [whispers] Run.", "[Tom] Now.", "[Tom] (shouts) Go!"],
                         "Format Prose keeps cues with the part they belong to")
        prose = 'Tom said. (whispers) "Run." He nodded (sadly). "Fine."'
        self.b.js(f"document.getElementById('scriptInput').value = {json.dumps(prose)}; formatProseScript(); true")
        self.assertEqual(self.b.js("document.getElementById('scriptInput').value").split("\n\n"),
                         ["[Tom] (whispers) Run.", "[Narrator] He nodded.", "[Tom] (sadly) Fine."],
                         "a cue at the end of a narration part moves onto the next quote")

        script = "[Timmy] Timmy: the hose isn't working\n[Ann] **Ann:** \"Fix it,\" she said.\n[Bob] Bobby: hi."
        self.b.js(f"document.getElementById('scriptInput').value = {json.dumps(script)}; autoDetectSpeakers(true); true")
        key = self.b.js("characters.find(c => c.name === 'Timmy').id")
        self.assertEqual(self.b.js(f"getSampleText('{key}')"), "the hose isn't working",
                         "a sample doesn't read the speaker's name written again after the tag")
        self.b.js("formatProseScript(); true")
        self.assertEqual(self.b.js("document.getElementById('scriptInput').value").split("\n\n"),
                         ["[Timmy] the hose isn't working", "[Ann] Fix it.", "[Bob] Bobby: hi."],
                         "Format Prose removes the speaker's name written again after the tag")

        script = ("[Speaker 2] Rose, identical to Lily, grinned mischievously. 'Let's play a new game, Lily.'\n"
                  "‘Run,’ Tom said. ‘It’s late.’\n"
                  "[Narrator] The girls' toys were Tom's, and nobody knew.\n"
                  "[Ann] \"She said 'no' to me,\" Ann sighed.")
        self.b.js(f"document.getElementById('scriptInput').value = {json.dumps(script)}; formatProseScript(); true")
        self.assertEqual(self.b.js("document.getElementById('scriptInput').value").split("\n\n"),
                         ["[Narrator] Rose, identical to Lily, grinned mischievously.",
                          "[Speaker 2] Let's play a new game, Lily.",
                          "[Tom] Run.", "[Tom] It’s late.",
                          "[Narrator] The girls' toys were Tom's, and nobody knew.",
                          "[Ann] She said 'no' to me."],
                         "Format Prose also reads single quotes, but not apostrophes")

        script = ("[Narrator] \"Run,\" said Rose. \"Now.\"\n"
                  "[Narrator] \"Hello,\" someone called.")
        self.b.js(f"document.getElementById('scriptInput').value = {json.dumps(script)}; formatProseScript(); true")
        self.assertEqual(self.b.js("document.getElementById('scriptInput').value").split("\n\n"),
                         ["[Rose] Run.", "[Rose] Now.",
                          "[Narrator] Hello.", "[Narrator] Someone called."],
                         "a [Narrator] line gives its quotes to the named speaker, or keeps them with no name")

        for name in ("pause for effect", "Pause Man"):
            self.assertFalse(self.b.js(f"isCueTag({json.dumps(name)})"), name)
        found = self.b.js("extractSpeakersFromText('[Pause Man] Hello.')")
        self.assertEqual(found["speakers"], ["Pause Man"], "only a pause with a number (or none) is a pause")
        self.b.js("loadSampleScript(); true")

    def test_22_skip_buttons_arrow_keys_and_dragging(self):
        audio = "document.getElementById('mainAudio')"
        self.b.js("loadSampleScript(); true")
        self.generate_and_wait()
        self.b.wait_for(f"{audio}.duration > 20", timeout=30)
        duration = self.b.js(f"{audio}.duration")
        now = lambda: self.b.js(f"{audio}.currentTime")

        for btn, icon_name, label in (("btnSkipBack", "i-back10", "Back 10 seconds"),
                                      ("btnSkipForward", "i-forward10", "Forward 10 seconds")):
            self.assertFalse(self.b.js(f"document.getElementById('{btn}').disabled"), btn)
            self.assertTrue(self.b.js(f"!!document.querySelector('#{btn} use[href=\"#{icon_name}\"]')"), btn)
            self.assertEqual(self.b.js(f"document.getElementById('{btn}').getAttribute('aria-label')"), label)

        self.b.js(f"{audio}.currentTime = 15; true")
        self.b.js("document.getElementById('btnSkipBack').click(); true")
        self.assertAlmostEqual(now(), 5, delta=0.1)
        self.b.js("document.getElementById('btnSkipBack').click(); true")
        self.assertAlmostEqual(now(), 0, delta=0.1, msg="back stops at the start")
        self.b.js("document.getElementById('btnSkipForward').click(); true")
        self.assertAlmostEqual(now(), 10, delta=0.1)
        self.b.js(f"{audio}.currentTime = {duration - 3}; document.getElementById('btnSkipForward').click(); true")
        self.assertAlmostEqual(now(), duration, delta=0.1, msg="forward stops at the end")

        self.b.js(f"{audio}.currentTime = 12; true")
        self.key("document.body", "ArrowLeft")
        self.assertAlmostEqual(now(), 2, delta=0.1)
        self.key("document.body", "ArrowRight")
        self.assertAlmostEqual(now(), 12, delta=0.1)
        self.key("document.getElementById('scriptInput')", "ArrowRight")
        self.key("document.getElementById('masterVolume')", "ArrowLeft")
        self.assertAlmostEqual(now(), 12, delta=0.1, msg="typing and sliders keep the arrow keys")

        # Press on the waveform at a quarter, drag to three quarters, let go.
        rect = self.b.js("(() => { const r = document.getElementById('waveformWrapper').getBoundingClientRect();"
                         " return {left: r.left, width: r.width, y: r.top + r.height / 2}; })()")
        at = lambda f: {"clientX": rect["left"] + rect["width"] * f, "clientY": rect["y"], "bubbles": True,
                        "pointerId": 1, "button": 0}
        self.b.js(f"document.getElementById('waveformWrapper').dispatchEvent(new PointerEvent('pointerdown', {json.dumps(at(0.25))})); true")
        self.assertAlmostEqual(now(), duration * 0.25, delta=0.3)
        self.b.js(f"document.dispatchEvent(new PointerEvent('pointermove', {json.dumps(at(0.5))})); true")
        self.assertAlmostEqual(now(), duration * 0.5, delta=0.3, msg="dragging moves the position")
        self.assertEqual(self.text("currentTimeDisplay"), self.b.js(f"formatTime({audio}.currentTime)"))
        self.b.js(f"document.dispatchEvent(new PointerEvent('pointerup', {json.dumps(at(0.75))})); true")
        self.assertAlmostEqual(now(), duration * 0.75, delta=0.3)
        self.b.js(f"document.dispatchEvent(new PointerEvent('pointermove', {json.dumps(at(0.1))})); true")
        self.assertAlmostEqual(now(), duration * 0.75, delta=0.3, msg="after letting go, moving doesn't seek")

        wave = "document.getElementById('waveformWrapper')"
        self.assertEqual(self.b.js(f"{wave}.getAttribute('role')"), "slider")
        self.assertIn(" of ", self.b.js(f"{wave}.getAttribute('aria-valuetext')"))

        self.b.screenshot(os.path.join(SCREENSHOTS, "player_skip_buttons.png"))
        self.b.js("clearOutput(); true")
        self.assertTrue(self.b.js("document.getElementById('btnSkipBack').disabled"), "no audio, no skipping")

    def test_23_voice_blender(self):
        import voice_cast
        real_dir = voice_cast.CUSTOM_VOICES_DIR
        tmp = tempfile.TemporaryDirectory()
        voice_cast.CUSTOM_VOICES_DIR = tmp.name  # the user's real custom_voices folder is never touched
        try:
            self.b.js("reloadVoiceLists()")
            panel = "document.getElementById('blendPanel')"
            self.assertTrue(self.b.js(f"{panel}.classList.contains('hidden')"))
            self.b.js("document.getElementById('blendToggle').click(); true")
            self.assertFalse(self.b.js(f"{panel}.classList.contains('hidden')"))
            self.assertEqual(self.b.js("document.getElementById('blendToggle').getAttribute('aria-expanded')"), "true")
            self.assertEqual(self.b.js("blendRows.length"), 1)
            self.assertEqual(self.b.js("document.getElementById('blendText').value"), voice_cast.BLEND_SAMPLE_TEXT)

            self.b.js("document.getElementById('blendAddRow').click(); true")
            self.assertEqual(self.b.js("blendRows.length"), 2)
            self.b.js("setBlendVoice(blendRows[0].id, 'af_sky'); setBlendVoice(blendRows[1].id, 'af_bella');"
                      "setBlendWeight(blendRows[0].id, 70); setBlendWeight(blendRows[1].id, 30); true")
            self.assertEqual(self.b.js("[...document.querySelectorAll('.blend-share')].map(s => s.innerText)"), ["70%", "30%"])
            self.b.js("document.getElementById('blendAddRow').click(); true")
            self.assertTrue(self.b.js("document.getElementById('blendAddRow').classList.contains('hidden')"), "3 voices at most")
            self.b.js("document.querySelectorAll('[data-click=removeBlendRow]')[2].click(); true")
            self.assertEqual(self.b.js("blendRows.length"), 2)

            # Listen: the button becomes Stop while the sample loads and plays, and stops on a second click.
            listen = "document.getElementById('blendListen')"
            self.b.js(f"{listen}.click(); true")
            self.assertTrue(self.b.js(f"{listen}.classList.contains('previewing')"))
            self.b.wait_for("activePreviewAudio && !activePreviewAudio.paused", timeout=120)
            self.b.js(f"{listen}.click(); true")
            self.assertFalse(self.b.js(f"{listen}.classList.contains('previewing')"))

            # A bad name is explained, nothing is saved.
            self.b.js("document.getElementById('blendName').value = 'old sailor'; saveBlend()")
            self.assertIn("only letters", self.text("blendMessage"))

            self.b.js("document.getElementById('blendName').value = 'test_hero';"
                      "document.getElementById('blendType').value = 'Female'; saveBlend()")
            self.assertIn("Saved", self.text("blendMessage"))
            self.assertTrue(os.path.exists(os.path.join(tmp.name, "test_hero.npy")))
            entry = self.b.js("appConfig.available_popular_voices.find(v => v.id === 'test_hero')")
            self.assertEqual((entry["category"], entry["voice_type"], entry["tone"]),
                             ("My blends", "Female", "70% af_sky + 30% af_bella"))
            self.assertTrue(self.b.js("[...document.getElementById('voice_Narrator').options].some(o => o.value === 'test_hero')"),
                            "the new voice is in every voice list")

            # A speaker uses it; Edit reloads the recipe; saving under the same name doesn't ask.
            self.b.js("characters[0].voice = 'test_hero'; renderCharacterCards(); true")
            dialogs_before = len(self.b.messages("dialog"))
            self.b.js("closeBlender(); editBlend('test_hero'); true")
            self.assertEqual(self.b.js("blendRows.map(r => [r.voice, r.weight])"), [["af_sky", 70], ["af_bella", 30]])
            self.assertEqual(self.b.js("document.getElementById('blendName').value"), "test_hero")
            self.b.js("setBlendWeight(blendRows[0].id, 40); setBlendWeight(blendRows[1].id, 60); saveBlend()")
            self.assertIn("Saved", self.text("blendMessage"))
            self.assertEqual(len(self.b.messages("dialog")), dialogs_before, "editing doesn't ask to replace")
            self.assertEqual(self.b.js("appConfig.available_popular_voices.find(v => v.id === 'test_hero').tone"),
                             "40% af_sky + 60% af_bella")

            # A new blend with a taken name asks first (the test browser answers yes).
            self.b.js("openBlender(); document.getElementById('blendName').value = 'test_hero'; saveBlend()")
            self.assertIn("already exists", self.b.messages("dialog")[-1])
            self.assertIn("Saved", self.text("blendMessage"))

            # Delete asks, names the speaker using it, and that speaker gets a standard voice.
            speaker = self.b.js("characters[0].name")
            self.b.js("deleteBlend('test_hero')")
            question = self.b.messages("dialog")[-1]
            self.assertIn("test_hero", question)
            self.assertIn(speaker, question)
            self.assertFalse(os.path.exists(os.path.join(tmp.name, "test_hero.npy")))
            self.assertNotEqual(self.b.js("characters[0].voice"), "test_hero")
            self.assertTrue(self.b.js("appConfig.available_popular_voices.some(v => v.id === characters[0].voice)"))

            self.b.screenshot(os.path.join(SCREENSHOTS, "voice_blender.png"))
            self.b.js("setBlenderBusy(true); true")
            self.assertTrue(self.b.js("document.getElementById('blendSave').disabled"), "no saving while audio is made")
            self.b.js("setBlenderBusy(false); closeBlender(); true")
        finally:
            voice_cast.CUSTOM_VOICES_DIR = real_dir
            tmp.cleanup()
            self.b.js("reloadVoiceLists()")
            self.b.js("loadSampleScript(); true")

    def test_24_blend_save_double_click(self):
        import voice_cast
        real_dir = voice_cast.CUSTOM_VOICES_DIR
        tmp = tempfile.TemporaryDirectory()
        voice_cast.CUSTOM_VOICES_DIR = tmp.name
        try:
            self.b.js("reloadVoiceLists()")
            self.b.js("openBlender(); document.getElementById('blendName').value = 'twice_clicked'; true")
            dialogs_before = len(self.b.messages("dialog"))
            self.b.js("window._realFetch = window.fetch; window.saveRequests = 0;"
                      "window.fetch = (url, opts) => { if (url === '/api/blend/save') saveRequests++;"
                      " return window._realFetch(url, opts); }; true")
            try:
                self.b.js("Promise.all([saveBlend(), saveBlend()])")
            finally:
                self.b.js("window.fetch = window._realFetch; true")
            self.assertEqual(self.b.js("saveRequests"), 1, "a second click while saving sends nothing")
            self.assertIn("Saved", self.text("blendMessage"))
            self.assertEqual(len(self.b.messages("dialog")), dialogs_before, "the second click doesn't ask to replace")
            self.assertFalse(self.b.js("document.getElementById('blendSave').disabled"))
            self.b.js("deleteBlend('twice_clicked')")
        finally:
            self.b.js("closeBlender(); true")
            voice_cast.CUSTOM_VOICES_DIR = real_dir
            tmp.cleanup()
            self.b.js("reloadVoiceLists()")

    def test_25_blend_panel_tidy_up(self):
        fake = {"id": "fake_blend", "accent": "Blend", "gender": "Female", "category": "My blends", "tone": "100% gone_voice",
                "voice_type": "Female", "recipe": [{"voice": "gone_voice", "share": 1}]}
        self.b.js(f"appConfig.available_popular_voices.push({json.dumps(fake)}); true")
        try:
            # Editing a blend whose ingredient was deleted names the missing voice.
            self.b.js("openBlender('fake_blend'); true")
            self.assertIn("gone_voice", self.b.js("document.querySelector('#blendRows .vp-label').textContent"))
            self.assertIn("gone_voice", self.text("blendMessage"))
            self.b.js("blendEntry('fake_blend').recipe = [{voice: 'gone_a', share: 0.5}, {voice: 'gone_b', share: 0.5}];"
                      "openBlender('fake_blend'); true")
            self.assertIn("gone_a and gone_b aren't installed", self.text("blendMessage"))

            # While audio is being made, a redrawn list keeps Delete off, and a playing Listen can still be stopped.
            self.b.js("setBlenderBusy(true); renderBlendList(); true")
            self.assertTrue(self.b.js("[...document.querySelectorAll('#blendList .blend-delete')].every(b => b.disabled)"))
            self.b.js("const t = beginPreview(document.getElementById('blendListen')); setBlenderBusy(true); window._t = t; true")
            self.assertFalse(self.b.js("document.getElementById('blendListen').disabled"), "Stop stays usable")
            self.b.js("endPreview(window._t); true")
            self.assertTrue(self.b.js("document.getElementById('blendListen').disabled"), "Listen is off again after it stops")
            self.b.js("setBlenderBusy(false); true")

            # The delete question reads well and names everyone using the voice.
            self.b.js("narratorConfig.voice = 'fake_blend'; characters[0].voice = 'fake_blend'; characters[1].voice = 'fake_blend'; true")
            names = self.b.js("[characters[0].name, characters[1].name]")
            self.b.js("window._realFetch = window.fetch; window.deleteRequests = 0;"
                      "window.fetch = (url, opts) => { if (url === '/api/blend/delete') { deleteRequests++;"
                      " return Promise.reject(new TypeError('Failed to fetch')); } return window._realFetch(url, opts); }; true")
            try:
                self.b.js("Promise.all([deleteBlend('fake_blend'), deleteBlend('fake_blend')])")
            finally:
                self.b.js("window.fetch = window._realFetch; true")
            question = self.b.messages("dialog")[-1]
            self.assertIn(f"Delete the voice fake_blend? The Narrator, {names[0]} and {names[1]} use this voice", question)
            self.assertEqual(self.b.js("deleteRequests"), 1, "a second click while deleting sends nothing")
            self.assertIn("could not be deleted", self.text("blendMessage"))
        finally:
            self.b.js("appConfig.available_popular_voices = appConfig.available_popular_voices.filter(v => v.id !== 'fake_blend');"
                      "closeBlender(); true")
            self.b.js("loadSampleScript(); true")

    def test_26_names_keys_and_announcements(self):
        key = self.b.js("characters[0].id")
        sample, remove = f"document.getElementById('sampleBtn_{key}')", f"document.querySelector('[data-click=removeSpeakerCard][data-speaker=\"{key}\"]')"
        real_name = self.b.js("characters[0].name")
        try:
            # While a sample loads, its button is named as a Stop button.
            self.b.js("window._realFetchPreview = fetchPreviewUrl; fetchPreviewUrl = () => new Promise(() => {});"
                      f"playVoiceSample('{key}'); true", await_promise=False)
            self.assertTrue(self.b.js(f"{sample}.getAttribute('aria-label')").startswith("Stop"))

            # Renaming while it plays: the new name stays after the sample stops; the × button follows too.
            self.b.js(f"document.getElementById('name_{key}').value = 'Zed'; onCharacterNameChange('{key}'); stopPreview(); true")
            self.assertEqual(self.b.js(f"{sample}.getAttribute('aria-label')"), "Hear Zed's lines")
            self.assertEqual(self.b.js(f"{remove}.getAttribute('aria-label')"), "Remove Zed")
        finally:
            self.b.js(f"fetchPreviewUrl = window._realFetchPreview; document.getElementById('name_{key}').value = {json.dumps(real_name)};"
                      f"onCharacterNameChange('{key}'); true")

        # Status messages are read out, even the same one twice, with the reason.
        self.b.js("window._announced = []; new MutationObserver(() => { const t = document.getElementById('srAnnounce').textContent;"
                  " if (t) _announced.push(t); }).observe(document.getElementById('srAnnounce'), { childList: true, characterData: true, subtree: true });"
                  "window._script = document.getElementById('scriptInput').value; document.getElementById('scriptInput').value = ''; true")
        try:
            for _ in range(2):
                self.b.js("generateAudio(); true")
                self.b.wait_for("_announced.length && _announced[_announced.length - 1].includes('Type or paste a script first')")
                count = self.b.js("_announced.length")
                self.b.js("_announced.push('-'); true")
            self.assertEqual(count, 3, "announced each time, the second time too")
            self.assertIn("Nothing to generate yet", self.b.js("_announced[0]"))
        finally:
            self.b.js("document.getElementById('scriptInput').value = window._script; true")

        # Ctrl+S saves (handled with the other shortcuts).
        self.b.js("window._realSave = saveProject; window._saves = []; saveProject = (asNew) => { _saves.push(asNew); };"
                  "document.dispatchEvent(new KeyboardEvent('keydown', { key: 's', ctrlKey: true, bubbles: true, cancelable: true }));"
                  "document.dispatchEvent(new KeyboardEvent('keydown', { key: 'S', ctrlKey: true, shiftKey: true, bubbles: true, cancelable: true }));"
                  "saveProject = window._realSave; true")
        self.assertEqual(self.b.js("_saves"), [False, True])

        # An icon and its words are as far apart as the button's gap, not more.
        gap = self.b.js("""(() => { const b = document.querySelector('[data-click=saveProject][data-value=false]');
            const svg = b.querySelector('svg'); const node = [...b.childNodes].find(n => n.nodeType === 3 && n.textContent.trim());
            const r = document.createRange(); const i = node.textContent.search(/\\S/); r.setStart(node, i); r.setEnd(node, i + 1);
            return r.getBoundingClientRect().left - svg.getBoundingClientRect().right; })()""")
        self.assertLessEqual(gap, 7, "icon gap")

    def test_27_performance_setting(self):
        select = "document.getElementById('performanceLevel')"
        self.assertEqual(self.b.js(f"[...{select}.options].map(o => [o.value, o.text])"),
                         [["off", "Off"], ["natural", "Natural"], ["lively", "Lively"]])
        self.assertTrue(self.b.js(f"{select}.closest('.subsection').querySelector('h3').textContent === 'Pacing'"))
        self.assertIn("Narrator gets half", self.b.js(f"{select}.closest('label').title"))

        self.b.js(f"{select}.value = 'lively'; true")
        self.assertEqual(self.b.js("collectProjectState().performance"), "lively")
        state = self.b.js("collectProjectState()")
        self.b.js(f"{select}.value = 'off'; true")
        self.b.js(f"applyProjectState({json.dumps(state)}); true")
        self.assertEqual(self.b.js(f"{select}.value"), "lively", "restored from the project")
        self.b.js(f"applyProjectState({json.dumps({**state, 'performance': 'shouty'})}); true")
        self.assertEqual(self.b.js(f"{select}.value"), "natural", "an unknown value opens at Natural")

        # Generate sends it (the request is caught, so nothing is made).
        self.b.js(f"{select}.value = 'off'; window._realFetch = window.fetch; window.sentBody = null;"
                  "window.fetch = (url, opts) => { if (url === '/api/generate') { window.sentBody = opts.body;"
                  " return Promise.resolve(new Response('')); } return window._realFetch(url, opts); }; true")
        try:
            self.b.js("document.getElementById('scriptInput').value = '[Narrator] Hello.'; generateAudio(); true")
            self.b.wait_for("window.sentBody !== null", timeout=10)
            self.assertEqual(json.loads(self.b.js("window.sentBody"))["performance"], "off")
        finally:
            self.b.js("window.fetch = window._realFetch; true")

        self.b.js("loadSampleScript(); true")
        self.assertEqual(self.b.js(f"{select}.value"), "natural", "a fresh start is Natural")
        self.b.js(f"{select}.scrollIntoView({{block: 'center'}}); true")
        self.b.screenshot(os.path.join(SCREENSHOTS, "performance_setting.png"))

    def test_28_describe_a_voice(self):
        # Earlier steps may have switched the Assistant on: this test checks word matching.
        self.b.js("fetch('/api/user_settings', {method: 'POST', headers: {'Content-Type': 'application/json'},"
                  "body: JSON.stringify({ai_writer_enabled: false})}).then(() => { userSettings.ai_writer_enabled = false; })")
        self.b.js("openBlender(); true")
        self.assertIn("word matching", self.text("blendDescribeHelper"), "the test settings have the Assistant off")
        self.b.js("document.getElementById('blendDescribe').value = 'old British man, gruff'; true")
        self.b.js("suggestBlend()")
        self.b.wait_for("!describeBusy")
        voices = self.b.js("blendRows.map(r => r.voice)")
        self.assertTrue(voices and all(v.startswith("bm_") for v in voices), voices)
        self.assertEqual(self.b.js("blendRows.map(r => r.weight)"), [65, 35])
        self.assertEqual(self.b.js("document.getElementById('blendType').value"), "Male")
        self.assertEqual(self.b.js("document.getElementById('blendName').value"), "old_british_man_gruff")
        helper = self.text("blendDescribeHelper")
        self.assertIn("Picked", helper)
        self.assertIn("left out the age", helper)
        self.b.js("document.getElementById('blendPanel').scrollIntoView({block: 'start'}); true")
        self.b.screenshot(os.path.join(SCREENSHOTS, "describe_voice.png"))

        # Changes in the panel are only replaced after a yes.
        self.b.js("setBlendWeight(blendRows[0].id, 20); window._realConfirm = window.confirm;"
                  "window.confirm = () => false; document.getElementById('blendDescribe').value = 'a cheerful little girl'; true")
        self.b.js("suggestBlend()")
        self.b.wait_for("!describeBusy")
        self.assertIn("Kept", self.text("blendDescribeHelper"))
        self.assertTrue(all(v.startswith("bm_") for v in self.b.js("blendRows.map(r => r.voice)")))
        self.b.js("window.confirm = window._realConfirm; true")
        self.b.js("suggestBlend()")
        self.b.wait_for("!describeBusy")
        self.assertEqual(self.b.js("document.getElementById('blendType').value"), "Child")

        # Nothing to go on: the sliders stay and the unused words are named.
        before = self.b.js("blendRows.map(r => r.voice)")
        self.b.js("document.getElementById('blendDescribe').value = 'zzz qqq'; true")
        self.b.js("suggestBlend()")
        self.b.wait_for("!describeBusy")
        self.assertIn("couldn't find anything", self.text("blendDescribeHelper"))
        self.assertIn("Not used: zzz, qqq.", self.text("blendDescribeHelper"))
        self.assertEqual(self.b.js("blendRows.map(r => r.voice)"), before)

        # Review fix: while editing a saved blend, a suggestion uses its own name, so Save can't overwrite the blend quietly.
        fake = {"id": "fake_troll", "accent": "Blend", "gender": "Male", "category": "My blends", "tone": "100% am_onyx",
                "voice_type": "Male", "recipe": [{"voice": "am_onyx", "share": 1}], "description": "a troll"}
        self.b.js(f"appConfig.available_popular_voices.push({json.dumps(fake)}); openBlender('fake_troll'); true")
        try:
            self.assertEqual(self.b.js("document.getElementById('blendDescribe').value"), "a troll")
            self.b.js("document.getElementById('blendDescribe').value = 'a young man'; true")
            self.b.js("suggestBlend()")
            self.b.wait_for("!describeBusy")
            self.assertEqual(self.b.js("document.getElementById('blendName').value"), "young_man")
        finally:
            self.b.js("appConfig.available_popular_voices = appConfig.available_popular_voices.filter(v => v.id !== 'fake_troll');"
                      "openBlender(); true")

        # Off while audio is being made.
        self.b.js("setBlenderBusy(true); true")
        self.assertTrue(self.b.js("document.getElementById('blendSuggest').disabled"))
        self.b.js("setBlenderBusy(false); closeBlender(); true")
        self.assertFalse(self.b.js("document.getElementById('blendSuggest').disabled"))

    def test_99_no_javascript_errors(self):
        errors = self.b.messages("exception") + self.b.messages("error")
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()

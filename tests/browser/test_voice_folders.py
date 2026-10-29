"""Browser tests for the default Narrator (Make default) and your own voice folders.
They don't render audio, so the Kokoro model files aren't needed.

    python -m unittest tests.browser.test_voice_folders -v
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


class StudioPageCase(unittest.TestCase):
    """A studio server with a temporary settings file, and one page open in the test browser."""
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

    def saved(self, key):
        return self.b.js(f"settingsSaved().then(() => fetch('/api/config')).then(r => r.json()).then(d => d.user_settings.{key})")

    def wait_saved(self, key, expected):
        for _ in range(20):
            if self.saved(key) == expected:
                return
            time.sleep(0.25)
        self.fail(f"{key} was not saved as {expected!r} (it is {self.saved(key)!r})")


@unittest.skipIf(SKIP_REASON, SKIP_REASON)
class DefaultNarratorTests(StudioPageCase):
    def set_narrator(self, voice, speed):
        self.b.js(f"{{ const s = document.getElementById('voice_Narrator'); s.value = '{voice}';"
                  f"s.dispatchEvent(new Event('change', {{bubbles: true}})); }} setSpeed('Narrator', {speed}); true")

    def button(self):
        return self.b.js("(b => [b.textContent, b.disabled])(document.getElementById('narratorDefaultBtn'))")

    def narrator(self):
        return self.b.js("[document.getElementById('voice_Narrator').value, narratorConfig.speed]")

    def test_01_button_reads_default_when_matching(self):
        self.assertEqual(self.narrator(), ["bm_fable", 0.95])
        self.assertEqual(self.button(), ["Default ✓", True])
        os.makedirs(SCREENSHOTS, exist_ok=True)
        self.b.js("document.getElementById('narratorDefaultBtn').scrollIntoView({block: 'center'}); true")
        for theme in ("light", "dark"):
            self.b.js(f"window.applyTheme('{theme}'); true")
            self.b.screenshot(os.path.join(SCREENSHOTS, f"narrator_default_{theme}.png"))
        self.b.js("window.applyTheme('system'); true")

    def test_02_make_default_then_load_sample_uses_it(self):
        self.set_narrator("af_heart", 1.1)
        self.assertEqual(self.button(), ["Make default", False])
        self.b.js("document.getElementById('narratorDefaultBtn').click(); true")
        self.wait_saved("default_narrator", {"voice": "af_heart", "speed": 1.1})
        self.assertEqual(self.button(), ["Default ✓", True])
        self.assertIn("Default Narrator", self.b.js("document.getElementById('progressTitle').innerText"))
        self.set_narrator("bm_george", 0.9)
        self.assertEqual(self.button(), ["Make default", False])
        self.b.js("loadSampleScript(); true")
        self.assertEqual(self.narrator(), ["af_heart", 1.1])
        self.assertEqual(self.button(), ["Default ✓", True])

    def test_03_a_project_keeps_its_narrator(self):
        self.set_narrator("bm_george", 0.9)
        self.b.js("applyProjectState(collectProjectState()); true")
        self.assertEqual(self.narrator(), ["bm_george", 0.9])

    def test_04_a_missing_default_falls_back(self):
        self.assertEqual(self.b.js("(() => { const keep = userSettings.default_narrator;"
                                   "userSettings.default_narrator = {voice: 'no_such_voice', speed: 1.2};"
                                   "const d = defaultNarrator(); userSettings.default_narrator = keep; return d; })()"),
                         {"voice": "bm_fable", "speed": 0.95})


@unittest.skipIf(SKIP_REASON, SKIP_REASON)
class VoiceFolderTests(StudioPageCase):
    def setUp(self):
        self.b.js("closeVoicePicker(); userSettings.voice_folders = {}; userSettings.favorite_voices = [];"
                  "userSettings.closed_voice_folders = []; saveUserSettings()")
        self.open_list()

    # Helpers

    def open_list(self):
        self.b.js("closeVoicePicker(); document.getElementById('voice_Narrator_picker').click(); true")
        self.b.wait_for(f"!!document.querySelector('{PANEL}')", timeout=5)

    def press(self, key):
        self.b.js(f"document.activeElement.dispatchEvent(new KeyboardEvent('keydown', {{key: '{key}', bubbles: true}})); true")

    def folders(self):
        return self.b.js(f"[...document.querySelectorAll('{PANEL} .vp-folder-head .vp-name')].map(e => e.textContent)")

    def voices_in(self, name):
        return self.b.js(f"(() => {{ const f = [...document.querySelectorAll('{PANEL} .vp-folder')]"
                         f".find(f => f.querySelector('.vp-name').textContent === {json.dumps(name)});"
                         "return f ? [...f.querySelectorAll('.vp-choose')].map(c => c.dataset.voice) : null; })()")

    def click_move(self, voice):
        self.b.js(f"document.querySelector('{PANEL} .vp-folder:not(.vp-folder-fav):not(.vp-folder-names) "
                  f".vp-move[data-voice=\"{voice}\"]').click(); true")
        self.b.wait_for(f"!!document.querySelector('{PANEL} .vp-menu')", timeout=5)

    def moved(self):
        """A move waits for the settings to be saved, then rebuilds the list (the menu goes away)."""
        self.b.wait_for(f"!document.querySelector('{PANEL} .vp-menu')", timeout=5)

    def move_new(self, voice, name, wait=True):
        self.click_move(voice)
        self.b.js(f"document.querySelector('{PANEL} .vp-menu .vp-new-folder').click(); true")
        self.b.js(f"document.activeElement.value = {json.dumps(name)}; true")
        self.press("Enter")
        if wait:
            self.moved()

    def move_to(self, voice, name):
        self.click_move(voice)
        self.b.js(f"document.querySelector('{PANEL} .vp-menu button[data-folder={json.dumps(name)}]').click(); true")
        self.moved()

    def back(self, voice):
        self.click_move(voice)
        self.b.js(f"document.querySelector('{PANEL} .vp-menu .vp-back').click(); true")
        self.moved()

    # Tests

    def test_05_move_to_a_new_folder(self):
        self.move_new("bm_fable", "Tier 2")
        self.assertEqual(self.folders()[-1], "Tier 2")
        self.assertEqual(self.voices_in("Tier 2"), ["bm_fable"])
        self.assertNotIn("bm_fable", self.voices_in("Storybook & Fairy Tale Narrators"))
        self.wait_saved("voice_folders", {"bm_fable": "Tier 2"})
        self.assertTrue(self.b.js(f"!!document.querySelector('{PANEL}')"), "the list stays open")
        os.makedirs(SCREENSHOTS, exist_ok=True)
        self.click_move("bm_fable")
        self.b.js(f"document.querySelector('{PANEL} .vp-menu').scrollIntoView({{block: 'center'}}); true")
        for theme in ("light", "dark"):
            self.b.js(f"window.applyTheme('{theme}'); true")
            self.b.screenshot(os.path.join(SCREENSHOTS, f"voice_folder_menu_{theme}.png"))
        self.b.js("window.applyTheme('system'); true")

    def test_06_another_voice_joins_from_the_menu(self):
        self.move_new("bm_fable", "Tier 2")
        self.move_to("bm_george", "Tier 2")
        self.assertEqual(sorted(self.voices_in("Tier 2")), ["bm_fable", "bm_george"])
        self.wait_saved("voice_folders", {"bm_fable": "Tier 2", "bm_george": "Tier 2"})

    def test_07_back_to_usual_folder_and_empty_folder_vanishes(self):
        self.move_new("bm_fable", "Tier 2")
        self.move_to("bm_george", "Tier 2")
        self.back("bm_fable")
        self.back("bm_george")
        self.assertNotIn("Tier 2", self.folders())
        self.assertIn("bm_fable", self.voices_in("Storybook & Fairy Tale Narrators"))
        self.wait_saved("voice_folders", {})

    def test_08_favourites_still_show_a_moved_voice(self):
        self.b.js(f"document.querySelector('{PANEL} .vp-folder:not(.vp-folder-fav) .vp-star[data-voice=\"af_sky\"]').click(); true")
        selected = self.b.js("document.getElementById('voice_Narrator').value")
        self.move_new("af_sky", "Tier 2")
        self.assertIn("af_sky", self.voices_in("Favorites"))
        self.assertEqual(self.voices_in("Tier 2"), ["af_sky"])
        self.assertEqual(self.b.js("document.getElementById('voice_Narrator').value"), selected)

    def test_09_a_usual_folder_name_joins_it(self):
        self.move_new("af_heart", "children & youth")
        self.assertNotIn("children & youth", self.folders())
        self.assertIn("af_heart", self.voices_in("Children & Youth"))
        self.assertNotIn("af_heart", self.voices_in("Storybook & Fairy Tale Narrators"))

    def test_10_search_finds_the_own_folder(self):
        self.move_new("af_sky", "Tier 2")
        self.b.js(f"{{ const s = document.querySelector('{PANEL} .vp-search'); s.value = 'tier';"
                  "s.dispatchEvent(new Event('input', {bubbles: true})); } true")
        self.assertEqual(self.folders(), ["Tier 2"])
        self.assertEqual(self.voices_in("Tier 2"), ["af_sky"])

    def test_11_reserved_name_is_refused(self):
        self.move_new("bm_fable", "Favorites", wait=False)
        self.assertEqual(self.b.js(f"document.querySelector('{PANEL} .vp-nick-note').textContent"),
                         self.web_server.FOLDER_NAME_TAKEN)
        self.wait_saved("voice_folders", {})


    def menu_folders(self, voice):
        self.click_move(voice)
        names = self.b.js(f"[...document.querySelectorAll('{PANEL} .vp-menu .vp-menu-folder')].map(b => b.dataset.folder)")
        self.press("Escape")
        return names

    def test_12_focus_returns_to_the_moved_voice(self):
        self.move_new("bm_fable", "Tier 2")
        self.assertEqual(self.b.js("document.activeElement.className + '|' + document.activeElement.dataset.voice"),
                         "vp-move vp-on|bm_fable")
        self.move_to("bm_george", "Tier 2")
        self.assertEqual(self.b.js("document.activeElement.dataset.voice"), "bm_george")
        self.assertFalse(self.b.js("!!document.activeElement.closest('.vp-folder-fav, .vp-folder-names')"))

    def test_13_same_name_in_other_case_is_the_same_folder(self):
        self.move_new("bm_fable", "Tier 2")
        self.move_new("bm_george", "  tier   2 ")
        self.assertEqual(self.folders().count("Tier 2"), 1)
        self.assertNotIn("tier 2", self.folders())
        self.wait_saved("voice_folders", {"bm_fable": "Tier 2", "bm_george": "Tier 2"})

    def test_14_usual_folder_names_are_not_listed_as_your_folders(self):
        self.move_new("af_heart", "children & youth")
        self.wait_saved("voice_folders", {"af_heart": "Children & Youth"})
        self.assertEqual(self.menu_folders("bm_fable"), [])

    def test_15_folders_of_deleted_voices_are_not_offered(self):
        self.b.js("closeVoicePicker(); userSettings.voice_folders = {no_such_voice: 'Ghost'}; true")
        self.open_list()
        self.assertEqual(self.menu_folders("bm_fable"), [])


# Fake Everyday people entries, as /api/config lists blends (no model or files needed).
EVERYDAY = [
    ("Mom", "Female", 1.05, 0), ("Neighbour_woman", "Female", 1.05, 0), ("Teen_girl_15", "Female", 1.05, 0),
    ("Girl_6", "Child", 1.0, 1.5), ("Boy_8", "Child", None, 0), ("Dad", "Male", None, 0),
    ("Teen_boy_16", "Male", None, 0), ("Grandpa", "Male", 0.92, 0),
]


@unittest.skipIf(SKIP_REASON, SKIP_REASON)
class EverydayPeopleTests(StudioPageCase):
    def setUp(self):
        entries = []
        for name, kind, speed, pitch in EVERYDAY:
            e = {"id": name, "accent": "Blend", "gender": kind, "category": "My blends", "tone": "test",
                 "voice_type": kind, "recipe": [{"voice": "af_sky", "share": 1}], "description": ""}
            if speed:
                e["start_speed"] = speed
            if pitch:
                e["pitch"] = pitch
            entries.append(e)
        # Voices of the same names made on this PC are left out, so only the fakes are seen.
        self.b.js(f"window._realVoices = window._realVoices || appConfig.available_popular_voices.slice();"
                  f"{{ const fake = {json.dumps(entries)}; const names = new Set(fake.map(e => e.id));"
                  "appConfig.available_popular_voices = window._realVoices.filter(v => !names.has(v.id)).concat(fake); }"
                  "userSettings.voice_folders = {}; renderCharacterCards(); true")

    def tearDown(self):
        self.b.js("appConfig.available_popular_voices = window._realVoices; userSettings.voice_folders = {};"
                  "if (window._realPostBlend) { postBlend = window._realPostBlend; } closeBlender(); renderCharacterCards(); true")

    def use_folder(self):
        folders = {name: "Everyday people" for name, *_ in EVERYDAY}
        self.b.js(f"userSettings.voice_folders = {json.dumps(folders)}; true")

    def pick(self, gender, used=()):
        return self.b.js(f"voiceForType('{gender}', 0, new Set({json.dumps(list(used))}))")

    def test_01_blend_panel_sends_pitch_and_speed(self):
        self.b.js("window._realPostBlend = window._realPostBlend || postBlend; window._sent = [];"
                  "postBlend = async (path, body) => { _sent.push([path, body]); return {success: false, error: 'test'}; };"
                  "openBlender(); document.getElementById('blendPitch').value = '1.5';"
                  "document.getElementById('blendSpeed').value = '1.05';"
                  "document.getElementById('blendName').value = 'tester'; true")
        self.b.js("listenToBlend()")
        self.b.js("saveBlend()")
        sent = self.b.js("_sent")
        self.assertEqual(sent[0][0], "/api/blend/preview")
        self.assertEqual(sent[0][1]["pitch"], 1.5)
        self.assertEqual(sent[1][0], "/api/blend/save")
        self.assertEqual((sent[1][1]["pitch"], sent[1][1]["start_speed"]), (1.5, 1.05))
        self.b.js("document.getElementById('blendSpeed').value = ''; true")
        self.b.js("saveBlend()")
        self.assertIsNone(self.b.js("_sent[2][1].start_speed"))

    def test_02_edit_fills_pitch_and_speed(self):
        self.b.js("openBlender('Girl_6'); true")
        self.assertEqual(self.b.js("[document.getElementById('blendPitch').value, document.getElementById('blendSpeed').value]"),
                         ["1.5", "1.00"])
        os.makedirs(SCREENSHOTS, exist_ok=True)
        self.b.js("document.getElementById('blendPitch').scrollIntoView({block: 'center'}); true")
        for theme in ("light", "dark"):
            self.b.js(f"window.applyTheme('{theme}'); true")
            self.b.screenshot(os.path.join(SCREENSHOTS, f"blend_pitch_speed_{theme}.png"))
        self.b.js("window.applyTheme('system'); true")
        self.b.js("closeBlender(); openBlender(); true")
        self.assertEqual(self.b.js("[document.getElementById('blendPitch').value, document.getElementById('blendSpeed').value]"),
                         ["0", ""])

    def test_03_picking_a_voice_sets_its_starting_speed(self):
        self.b.js("{ const s = document.getElementById('voice_char_1'); s.value = 'Grandpa';"
                  "s.dispatchEvent(new Event('change', {bubbles: true})); } true")
        self.assertEqual(self.b.js("characters[0].speed"), 0.92)
        self.b.js("setSpeed('char_1', 1.2); { const s = document.getElementById('voice_char_1'); s.value = 'Boy_8';"
                  "s.dispatchEvent(new Event('change', {bubbles: true})); } true")
        self.assertEqual(self.b.js("characters[0].speed"), 1.2, "no starting speed: the speed stays")

    def test_04_without_the_folder_picks_are_unchanged(self):
        self.assertEqual(self.pick("Female")["voice"], "af_bella")
        self.assertEqual(self.pick("Child")["voice"], "af_sky")
        self.assertEqual(self.b.js("guessGenderAndVoice('Mum', 0).voice"), "af_bella")

    def test_05_everyday_folder_is_preferred_in_order(self):
        self.use_folder()
        self.assertEqual(self.pick("Female"), {"gender": "Female", "voice": "Mom", "speed": 1.05})
        self.assertEqual(self.pick("Female", ["Mom"])["voice"], "Neighbour_woman")
        self.assertEqual(self.pick("Female", ["Mom", "Neighbour_woman"])["voice"], "Teen_girl_15")
        self.assertEqual(self.pick("Female", ["Mom", "Neighbour_woman", "Teen_girl_15"])["voice"], "af_bella")
        self.assertEqual(self.pick("Child")["voice"], "Girl_6")
        self.assertEqual(self.pick("Child", ["Girl_6"]), {"gender": "Child", "voice": "Boy_8", "speed": 1.0})
        self.assertEqual(self.pick("Male")["voice"], "Dad")
        self.assertEqual(self.b.js("guessGenderAndVoice('Sarah', 0).voice"), "Mom")
        self.assertEqual(self.b.js("guessGenderAndVoice('little Timmy', 1, new Set(['Girl_6'])).voice"), "Boy_8")

    def test_06_role_names(self):
        self.use_folder()
        for name, voice in (("Mum", "Mom"), ("mother", "Mom"), ("Grandpa", "Grandpa"), ("Grandad", "Grandpa"),
                            ("Father", "Dad")):
            self.assertEqual(self.b.js(f"guessGenderAndVoice({json.dumps(name)}, 0).voice"), voice, name)
        self.assertEqual(self.b.js("guessGenderAndVoice('Grandpa', 0).speed"), 0.92)
        self.b.js("delete userSettings.voice_folders.Mom; true")
        self.assertEqual(self.b.js("guessGenderAndVoice('Mum', 0).voice"), "Neighbour_woman",
                         "no Mom voice: a woman from the folder, as for any woman's name")


    def detect(self, script):
        self.b.js(f"{{ const t = document.getElementById('scriptInput'); t.value = {json.dumps(script)}; }}"
                  "characters = []; autoDetectSpeakers(true); true")
        return self.b.js("Object.fromEntries(characters.map(c => [c.name, c.voice]))")

    def test_07_detect_keeps_role_voices_for_their_roles(self):
        self.use_folder()
        cast = self.detect("[Sarah] Hi.\n[Mom] Hello.\n[Tom] Yo.\n[Dad] Hey.\n[Emma] Bye.")
        self.assertEqual(cast["Mom"], "Mom")
        self.assertEqual(cast["Dad"], "Dad")
        self.assertEqual(cast["Sarah"], "Neighbour_woman")
        self.assertEqual(cast["Emma"], "Teen_girl_15", "the next unused voice, not the index")
        self.assertEqual(cast["Tom"], "Teen_boy_16")

    def test_08_without_a_role_the_first_voice_goes_first(self):
        self.use_folder()
        cast = self.detect("[Tom] Yo.\n[Sarah] Hi.")
        self.assertEqual(cast, {"Tom": "Dad", "Sarah": "Mom"})

    def test_09_script_assistant_cast_keeps_role_voices(self):
        self.use_folder()
        self.b.js("{ const t = document.getElementById('scriptInput'); t.value = '[Sarah] Hi.\\n[Mom] Hello.'; }"
                  "characters = []; applyAiCast([{name: 'Sarah', voice_type: 'Female'}, {name: 'Mom', voice_type: 'Female'}], new Set()); true")
        cast = self.b.js("Object.fromEntries(characters.map(c => [c.name, c.voice]))")
        self.assertEqual(cast, {"Sarah": "Neighbour_woman", "Mom": "Mom"})



@unittest.skipIf(SKIP_REASON, SKIP_REASON)
class SmallFixesTests(StudioPageCase):
    """Small fixes round, 15 Oct."""
    def setUp(self):
        self.b.js("closeVoicePicker(); closeBlender(); userSettings.voice_folders = {}; saveUserSettings()")

    def tearDown(self):
        self.b.js("if (window._realSave) { saveUserSettings = window._realSave; } true")

    def failing_saves(self):
        self.b.js("window._realSave = window._realSave || saveUserSettings;"
                  "saveUserSettings = async () => ({success: false, error: 'Disk full.'}); true")

    def open_list(self):
        self.b.js("closeVoicePicker(); document.getElementById('voice_Narrator_picker').click(); true")
        self.b.wait_for(f"!!document.querySelector('{PANEL}')", timeout=5)

    def move_new(self, voice, name):
        self.b.js(f"document.querySelector('{PANEL} .vp-folder:not(.vp-folder-fav):not(.vp-folder-names) "
                  f".vp-move[data-voice=\"{voice}\"]').click(); true")
        self.b.js(f"document.querySelector('{PANEL} .vp-menu .vp-new-folder').click(); true")
        self.b.js(f"document.activeElement.value = {json.dumps(name)};"
                  "document.activeElement.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true})); true")

    def moved(self):
        self.b.wait_for(f"!document.querySelector('{PANEL} .vp-menu')", timeout=5)

    def test_01_failed_folder_save_is_undone_and_shown(self):
        self.open_list()
        self.failing_saves()
        self.move_new("bm_fable", "Tier 2")
        self.b.wait_for(f"!!document.querySelector('{PANEL} .vp-nick-note') && "
                        f"document.querySelector('{PANEL} .vp-nick-note').textContent.includes('Disk full.')", timeout=5)
        self.assertEqual(self.b.js("userSettings.voice_folders"), {})

    def test_02_failed_make_default_is_undone(self):
        self.b.js("userSettings.default_narrator = {}; { const s = document.getElementById('voice_Narrator'); s.value = 'af_heart';"
                  "s.dispatchEvent(new Event('change', {bubbles: true})); } true")
        self.failing_saves()
        self.b.js("makeNarratorDefault()")
        self.assertEqual(self.b.js("userSettings.default_narrator"), {})
        self.assertEqual(self.b.js("document.getElementById('narratorDefaultBtn').textContent"), "Make default")
        self.assertIn("Disk full.", self.b.js("document.getElementById('progressStatus').innerText"))
        self.b.js("{ const s = document.getElementById('voice_Narrator'); s.value = 'bm_fable';"
                  "s.dispatchEvent(new Event('change', {bubbles: true})); } true")

    def test_03_folders_of_deleted_voices_are_dropped_on_reload(self):
        self.b.js("userSettings.voice_folders = {ghost_voice: 'Tier 2', af_sky: 'Tier 2'}; true")
        self.b.js("reloadVoiceLists()")
        self.assertEqual(self.b.js("userSettings.voice_folders"), {"af_sky": "Tier 2"})

    def test_04_own_folder_names_show_as_typed(self):
        self.open_list()
        self.move_new("bm_fable", "★ Best")
        self.moved()
        names = self.b.js(f"[...document.querySelectorAll('{PANEL} .vp-folder-own .vp-folder-head .vp-name')].map(e => e.textContent)")
        self.assertEqual(names, ["★ Best"])
        self.assertFalse(self.b.js(f"!!document.querySelector('{PANEL} .vp-folder-own .vp-folder-head .vp-name svg')"))

    def test_05_folder_menu_is_a_plain_group_of_buttons(self):
        self.open_list()
        self.b.js(f"document.querySelector('{PANEL} .vp-folder:not(.vp-folder-fav):not(.vp-folder-names) "
                  ".vp-move[data-voice=\"bm_fable\"]').click(); true")
        self.assertEqual(self.b.js(f"document.querySelector('{PANEL} .vp-menu').getAttribute('role')"), "group")
        self.assertEqual(self.b.js(f"document.querySelectorAll('{PANEL} .vp-menu [role=menuitem]').length"), 0)

    def test_06_deleted_narrator_voice_gets_default_voice_and_speed(self):
        self.b.js("userSettings.default_narrator = {voice: 'af_heart', speed: 1.1}; narratorConfig.voice = 'ghost_voice';"
                  "narratorConfig.speed = 0.7; true")
        self.b.js("reloadVoiceLists()")
        self.assertEqual(self.b.js("[narratorConfig.voice, narratorConfig.speed]"), ["af_heart", 1.1])
        self.b.js("userSettings.default_narrator = {}; true")

    def test_08_a_voice_moved_again_goes_to_the_end(self):
        self.b.js("userSettings.voice_folders = {af_sky: 'Everyday people', af_bella: 'Everyday people'}; true")
        self.b.js("setVoiceFolder('af_sky', 'Tier 2').then(() => setVoiceFolder('af_sky', 'Everyday people'))")
        self.assertEqual(self.b.js("Object.keys(userSettings.voice_folders)"), ["af_bella", "af_sky"])
        self.b.js("setVoiceFolder('af_bella', 'Tier 2')")
        self.assertEqual(self.b.js("Object.keys(userSettings.voice_folders)"), ["af_sky", "af_bella"])

    def pending_suggestion(self, ai=False):
        """Suggest with an answer the test releases later: window._answer(data)."""
        self.b.js("window._realPostBlend = window._realPostBlend || postBlend; window._posted = [];"
                  "window._realFetch = window._realFetch || window.fetch;"
                  "window.fetch = (url, opts) => { _posted.push(String(url)); return window._realFetch(url, opts); };"
                  "postBlend = (path, body) => new Promise(resolve => { window._answer = resolve; });"
                  f"userSettings.ai_writer_enabled = {str(ai).lower()};"
                  "openBlender(); document.getElementById('blendDescribe').value = 'a troll'; suggestBlend(); true")

    def answer(self, helper="words"):
        self.b.js("window._answer({success: true, recipe: [{voice: 'bm_george', share: 1}], voice_type: 'Male',"
                  f" name: 'troll', explanation: 'A troll.', helper: '{helper}', note: '', unknown: []}}); true")

    def restore(self):
        self.b.js("postBlend = window._realPostBlend; window.fetch = window._realFetch; userSettings.ai_writer_enabled = false; true")

    def rows(self):
        return self.b.js("blendRows.map(r => r.voice)")

    def test_09_stop_before_the_answer_wins(self):
        try:
            self.pending_suggestion()
            before = self.rows()
            self.b.js("suggestBlend()")  # Stop
            self.answer()
            self.assertEqual(self.rows(), before)
            self.assertEqual(self.b.js("document.getElementById('blendDescribeHelper').innerText"), "Stopped.")
            self.assertIn("/api/blend/describe/cancel", self.b.js("_posted"))
            self.assertNotIn("/api/llm/cancel", self.b.js("_posted"))
        finally:
            self.restore()

    def test_10_closing_the_panel_drops_a_pending_suggestion(self):
        try:
            self.pending_suggestion()
            self.b.js("closeBlender(); openBlender(); true")
            before = self.rows()
            self.answer()
            self.assertEqual(self.rows(), before)
            self.assertFalse(self.b.js("describeBusy"))
        finally:
            self.restore()

    def test_11_helper_line_says_who_suggested(self):
        try:
            self.pending_suggestion()
            self.answer("words")
            self.assertIn("(Suggested by word matching.)", self.b.js("document.getElementById('blendDescribeHelper').innerText"))
        finally:
            self.restore()

    def test_12_a_typed_name_is_kept_and_not_asked_about(self):
        try:
            self.b.js("window._asked = 0; window._realConfirm = window._realConfirm || window.confirm;"
                      "window.confirm = () => { _asked++; return true; }; true")
            self.pending_suggestion()
            self.b.js("document.getElementById('blendName').value = 'my_troll'; true")
            self.answer()
            self.assertEqual(self.b.js("_asked"), 0)
            self.assertEqual(self.b.js("document.getElementById('blendName').value"), "my_troll")
            self.assertEqual(self.rows(), ["bm_george"])
        finally:
            self.b.js("window.confirm = window._realConfirm; true")
            self.restore()

    def test_13_waiting_for_the_ai_program_shows_seconds(self):
        try:
            self.pending_suggestion(ai=True)
            self.b.wait_for("/\\d+ s$/.test(document.getElementById('blendDescribeHelper').innerText)", timeout=4)
            self.answer("ai")
        finally:
            self.restore()

    def test_14_closing_while_asking_stops_the_server_request(self):
        try:
            self.pending_suggestion()
            self.b.js("closeBlender(); true")
            self.b.wait_for("_posted.includes('/api/blend/describe/cancel')", timeout=3)
            self.answer()
        finally:
            self.restore()

    def test_15_cancelled_delete_keeps_the_suggestion_coming(self):
        try:
            self.b.js("window._realConfirm = window._realConfirm || window.confirm; window.confirm = () => false; true")
            self.pending_suggestion()
            self.b.js("deleteBlend('no_such_blend')")
            self.assertTrue(self.b.js("describeBusy"), "the suggestion is still on its way")
            self.answer()
            self.assertEqual(self.rows(), ["bm_george"])
        finally:
            self.b.js("window.confirm = window._realConfirm; true")
            self.restore()

    def test_16_overlapping_failed_moves_leave_what_is_saved(self):
        self.b.js("window._realSave = window._realSave || saveUserSettings;"
                  "saveUserSettings = () => new Promise(r => setTimeout(() => r({success: false, error: 'Disk full.'}), 150)); true")
        problems = self.b.js("Promise.all([setVoiceFolder('af_sky', 'Tier 2'), setVoiceFolder('af_sky', '')])")
        self.assertTrue(all(p.startswith("Not moved") for p in problems), problems)
        self.assertEqual(self.b.js("userSettings.voice_folders"), {})

    def test_17_a_new_name_typed_while_editing_is_kept(self):
        fake = {"id": "fake_ogre", "accent": "Blend", "gender": "Male", "category": "My blends", "tone": "100% am_onyx",
                "voice_type": "Male", "recipe": [{"voice": "am_onyx", "share": 1}], "description": "an ogre"}
        self.b.js("window._realPostBlend = window._realPostBlend || postBlend;"
                  "postBlend = async () => ({success: true, recipe: [{voice: 'bm_george', share: 1}], voice_type: 'Male',"
                  "  name: 'troll', explanation: 'A troll.', helper: 'words', note: '', unknown: []});"
                  f"appConfig.available_popular_voices.push({json.dumps(fake)}); openBlender('fake_ogre');"
                  "document.getElementById('blendName').value = 'my_new_ogre'; true")
        try:
            self.b.js("suggestBlend()")
            self.assertEqual(self.b.js("document.getElementById('blendName').value"), "my_new_ogre")
            self.assertEqual(self.rows(), ["bm_george"])
            self.b.js("openBlender('fake_ogre'); true")
            self.b.js("suggestBlend()")
            self.assertEqual(self.b.js("document.getElementById('blendName').value"), "troll",
                             "the edited blend's own name is still replaced, so Save can't overwrite it quietly")
        finally:
            self.b.js("postBlend = window._realPostBlend; closeBlender();"
                      "appConfig.available_popular_voices = appConfig.available_popular_voices.filter(v => v.id !== 'fake_ogre'); true")

    def test_18_stop_names_the_request_it_stops(self):
        try:
            self.pending_suggestion()
            self.b.js("window._bodies = []; const f = window.fetch;"
                      "window.fetch = (url, opts) => { _bodies.push([String(url), opts && opts.body]); return f(url, opts); };"
                      "window._describeBody = null; true")
            self.b.js("postBlend = (path, body) => { window._describeBody = body; return new Promise(r => { window._answer = r; }); };"
                      "suggestBlend(); suggestBlend(); true")  # Stop, then ask again
            self.b.js("suggestBlend(); true")  # Stop the second request
            stops = self.b.js("_bodies.filter(b => b[0] === '/api/blend/describe/cancel').map(b => JSON.parse(b[1]).id)")
            self.assertTrue(stops and all(stops), stops)
            self.assertEqual(stops[-1], self.b.js("_describeBody.id"))
            self.assertEqual(len(set(stops)), len(stops), "each request has its own id")
        finally:
            self.restore()

    def test_07_suggest_resets_pitch_and_speed(self):
        self.b.js("window._realPostBlend = window._realPostBlend || postBlend;"
                  "postBlend = async () => ({success: true, recipe: [{voice: 'bm_george', share: 1}], voice_type: 'Male',"
                  "  name: 'troll', explanation: 'A troll.', helper: 'words', note: '', unknown: []});"
                  "openBlender(); document.getElementById('blendPitch').value = '1.5'; document.getElementById('blendSpeed').value = '1.05';"
                  "blendSnapshot = blendState(); document.getElementById('blendDescribe').value = 'a troll'; true")
        self.b.js("suggestBlend()")
        self.b.js("postBlend = window._realPostBlend; true")
        self.assertEqual(self.b.js("[document.getElementById('blendPitch').value, document.getElementById('blendSpeed').value]"),
                         ["0", ""])


if __name__ == "__main__":
    unittest.main()

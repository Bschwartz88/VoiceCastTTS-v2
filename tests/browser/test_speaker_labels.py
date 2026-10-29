"""The browser uses the same label rules and fixtures as synthesis and LM answers."""
import json
import sys
import unittest
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from test_voice_folders import BROWSER, SKIP_REASON, StudioPageCase
CASES = json.loads((HERE.parent / 'speaker_label_cases.json').read_text(encoding='utf-8'))

@unittest.skipIf(BROWSER is None, SKIP_REASON)
class SpeakerLabelPageTests(StudioPageCase):
    def test_01_shared_rules(self):
        for case in CASES:
            with self.subTest(case['input']):
                self.assertEqual(self.b.js(f"cleanSpeakerTag({json.dumps(case['input'])})"),
                                 {'name': case['name'], 'cue': case['cue']})

    def test_02_one_cast_card_for_all_delivery_variants(self):
        script = '[Ben (angry)] Go.\n[Ben (whispering)] Stay.\n[Speaker 1 (Lila)] Hello.'
        self.b.js(f"document.getElementById('scriptInput').value = {json.dumps(script)}; autoDetectSpeakers(true); true")
        self.assertEqual(self.b.js('characters.map(c => c.name)'), ['Ben', 'Lila'])
        self.assertEqual(self.b.js("extractSpeakersFromText(document.getElementById('scriptInput').value).speakers"), ['Ben', 'Lila'])
        key = self.b.js('characters[0].id')
        self.assertEqual(self.b.js(f"getSampleText({json.dumps(key)})"), 'Go. Stay.')
        self.assertEqual(self.b.js('voicesPayload()["Ben (angry)"].voice === voicesPayload().Ben.voice'), True)

    def test_03_format_keeps_unspoken_directions_visible(self):
        script = '[Ben (Angry)] (looks behind him) Go.'
        self.assertEqual(self.b.js(f"formatProseText({json.dumps(script)}).blocks.filter(Boolean)"),
                         ['[Ben] (angrily) (looks behind him) Go.'])
        repeated = '[Ben (Angry)] Ben (Angry): Go.'
        self.assertEqual(self.b.js(f"formatProseText({json.dumps(repeated)}).blocks.filter(Boolean)"),
                         ['[Ben] (angrily) Go.'])
        self.b.js(f"document.getElementById('scriptInput').value = {json.dumps(script)}; formatProseScript(); true")
        self.b.screenshot(str(HERE / 'screenshots' / 'lab_speaker_labels.png'))

    def test_03b_old_cast_labels_keep_voice_speed_and_samples(self):
        self.b.js("characters = [{id:'legacy_ben', name:'Ben (angry)', prevName:'Ben (angry)', "
                  "gender:'Male', voice:'am_eric', speed:.91}]; renderCharacterCards(); "
                  "document.getElementById('scriptInput').value = '[Ben (angry)] Go.'; true")
        self.assertEqual(self.b.js('voicesPayload().Ben.voice'), 'am_eric')
        self.assertEqual(self.b.js("getSampleText('legacy_ben')"), 'Go.')
        self.b.js('autoDetectSpeakers(true); true')
        self.assertEqual(self.b.js('characters.map(c => [c.id,c.name,c.voice,c.speed])'),
                         [['legacy_ben','Ben','am_eric',.91]])

    def test_03c_reloads_wait_for_new_document_and_styles(self):
        for attempt in range(5):
            with self.subTest(reload=attempt):
                self.b.js("window.__oldDocument = true")
                self.b.goto(self.url)
                self.assertIsNone(self.b.js("window.__oldDocument"))
                self.assertTrue(self.b.js("getComputedStyle(document.documentElement)"
                                          ".getPropertyValue('--wave-played-a').trim()"))
                self.assertTrue(self.b.js("[...document.styleSheets].some(s => "
                                          "s.href && s.href.endsWith('/static/studio.css'))"))
                self.b.wait_for("characters.length > 0")

    def test_03d_v2_identity_and_neutral_title_casting(self):
        self.assertIn('VoiceCastTTS v2',self.b.js('document.title'))
        self.assertEqual(Path(sys.prefix).resolve(),HERE.parents[1]/'.venv')
        self.b.js('characters=[]; document.getElementById("scriptInput").value="[Mr. Hale] Hello.\\n[Mrs.Hale] Hi."; '
                  'applyAiCast([{name:"Mr. Hale",voice_type:"Neutral"},{name:"Mrs.Hale",voice_type:"Neutral"}],new Set()); true')
        self.assertEqual(self.b.js('characters.map(c=>c.gender)'),['Male','Female'])
        self.assertNotEqual(*self.b.js('characters.map(c=>c.voice)'))
        for name in ('Tomlinson','Emmaus','Sheriff'):
            self.assertIsNone(self.b.js(f'speakerNameType({json.dumps(name)})'))

    def test_04_no_javascript_errors(self):
        self.assertEqual(self.b.messages('exception') + self.b.messages('error'), [])

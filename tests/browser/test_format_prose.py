"""Browser tests for Format Prose (who speaks, bare speech tags, record-like answers, names in tags)
and for a renamed card still voicing its script tag. The cases are in format_prose_cases.json.

    python -m unittest tests.browser.test_format_prose -v
"""

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from test_voice_folders import BROWSER, SKIP_REASON, StudioPageCase  # noqa: E402

with open(os.path.join(HERE, "format_prose_cases.json"), encoding="utf-8") as f:
    CASES = json.load(f)


@unittest.skipIf(BROWSER is None, SKIP_REASON)
class FormatProseTests(StudioPageCase):
    def format(self, text):
        return self.b.js(f"formatProseText({json.dumps(text)}).blocks.filter(b => b !== '')")

    def test_01_cases(self):
        for case in CASES:
            with self.subTest(case["why"]):
                self.assertEqual(self.format(case["in"]), case["out"])

    def test_02_the_button_formats_the_script_box(self):
        self.b.js("document.getElementById('scriptInput').value = '\"Hi,\" said Tom. \"Bye.\"'; formatProseScript(); true")
        self.assertEqual(self.b.js("document.getElementById('scriptInput').value"), "[Tom] Hi.\n\n[Tom] Bye.")
        self.assertEqual(self.b.js("document.getElementById('progressStatus').innerText"),
                         "✓ Formatted into 2 lines, each with its speaker.")
        self.b.js("formatProseScript(); true")  # again: already done
        self.assertEqual(self.b.js("document.getElementById('progressStatus').innerText"),
                         "Nothing to change: every line already has its speaker.")
        self.b.js("loadSampleScript(); true")

    def test_03_a_renamed_card_still_voices_its_script_tag(self):
        self.b.js("document.getElementById('scriptInput').value = '[Speaker 1 (Lila)] Hello.'; autoDetectSpeakers(true); true")
        key = self.b.js("characters[0].id")
        self.b.js(f"document.getElementById('name_{key}').value = 'Lila'; onCharacterNameChange('{key}'); true")
        voices = self.b.js("voicesPayload()")
        self.assertIn("Lila", voices)
        self.assertEqual(voices["Speaker 1 (Lila)"], voices["Lila"], "the script still says Speaker 1 (Lila)")
        self.b.js("loadSampleScript(); true")


if __name__ == "__main__":
    unittest.main()

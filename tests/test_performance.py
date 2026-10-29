"""Quick tests for performance.py: livelier speed, loudness and gaps from the clues in a line's text."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import performance
from performance import line_delivery, cue_gain, gap_seconds


class LevelTests(unittest.TestCase):
    def test_clean_level(self):
        self.assertEqual(performance.LEVELS, ("off", "natural", "lively"))
        for good in performance.LEVELS:
            self.assertEqual(performance.clean_level(good), good)
        for bad in (None, "", "LIVELY", "loud", 3, ["off"]):
            self.assertEqual(performance.clean_level(bad), "natural")


class DeliveryTests(unittest.TestCase):
    def test_natural_preserves_short_questions_and_plain_speech(self):
        for text in ("Really?", "Sure you did, Grandpa.", "OK.", "TV?", "Plain words.", "Wait! You took the map?"):
            self.assertEqual(line_delivery(text, False, "natural"), (1.0,0.0), text)
        for i in range(60):
            self.assertEqual(line_delivery(f"Line number {i} is plain.",False,"natural"),(1.0,0.0))
    def test_natural_terminal_clues_are_modest_without_gain(self):
        self.assertEqual(line_delivery("Run!",False,"natural"),(1.02,0.0))
        self.assertEqual(line_delivery("Maybe…",False,"natural"),(.97,0.0))
        self.assertEqual(line_delivery('"Run!"',True,"natural"),(1.01,0.0))
        self.assertEqual(line_delivery("STOP.",False,"natural"),(1.0,0.0))
    def test_lively_remains_explicit_and_stronger(self):
        self.assertEqual(line_delivery("Run!",False,"lively"),(1.096,3.2))
        self.assertEqual(line_delivery("Maybe…",False,"lively"),(.888,-3.2))
    def test_off_and_empty_do_not_change_audio(self):
        self.assertEqual(line_delivery("Run!",False,"off"),(1.0,0.0))
        self.assertEqual(line_delivery("",False,"natural"),(1.0,0.0))

class CueGainTests(unittest.TestCase):
    def test_natural_respects_written_cue_without_amplifying_it(self):
        self.assertEqual(cue_gain(-10,"natural"),-10)
        self.assertEqual(cue_gain(5,"natural"),5)
        self.assertEqual(cue_gain(5,"lively"),8)


class GapTests(unittest.TestCase):
    def gap(self, previous, text, level="natural", previous_narrator=False, narrator=False, changed=True, base=0.35):
        return gap_seconds(base, previous, previous_narrator, text, narrator, changed, level)

    def test_short_reply_comes_in_sooner(self):
        self.assertAlmostEqual(self.gap("Where were you?", "No."), 0.21)
        self.assertAlmostEqual(self.gap("Where were you?", "Out with friends."), 0.21)
        self.assertAlmostEqual(self.gap("Where were you?", "No.", level="lively"), 0.14)
        self.assertAlmostEqual(self.gap("Where were you?", "Out with my friends."), 0.35, msg="4 words")

    def test_cut_off_line(self):
        for previous in ("Wait—", "Wait –", "Wait --", '"Wait—"'):
            self.assertAlmostEqual(self.gap(previous, "No, I will not go."), 0.05, msg=previous)
        self.assertAlmostEqual(self.gap("Wait—", "No, I will not go.", base=0.0), 0.0, msg="never longer than the gap")

    def test_unchanged_cases(self):
        self.assertAlmostEqual(self.gap("Where were you?", "No.", changed=False), 0.35, msg="same speaker")
        self.assertAlmostEqual(self.gap("Where were you?", "No.", narrator=True, base=0.65), 0.65, msg="into the Narrator")
        self.assertAlmostEqual(self.gap("Then—", "No.", previous_narrator=True, base=0.65), 0.65, msg="from the Narrator")
        self.assertAlmostEqual(self.gap("Wait—", "No.", level="off"), 0.35, msg="off")
        self.assertAlmostEqual(self.gap(None, "No."), 0.21, msg="no previous line text still counts as a short reply")


if __name__ == "__main__":
    unittest.main()

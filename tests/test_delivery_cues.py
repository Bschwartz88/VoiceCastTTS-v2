"""Quick tests for delivery_cues.py: cutting a script line into spoken pieces and pauses."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import delivery_cues
from delivery_cues import split_cues, is_cue_tag


def speech(text, factor=1.0, gain=0.0, cue=None):
    return {"kind": "speech", "text": text, "speed_factor": factor, "gain_db": gain, "cue": cue}


def pause(seconds):
    return {"kind": "pause", "seconds": seconds}


class SplitCuesTests(unittest.TestCase):
    def test_no_cues_gives_one_plain_piece(self):
        self.assertEqual(split_cues("Hello there."), [speech("Hello there.")])

    def test_cue_reaches_to_the_end_of_the_line(self):
        self.assertEqual(split_cues("I told you already. (whispers) They can hear us."),
                         [speech("I told you already."), speech("They can hear us.", 0.90, -10.0, "whispers")])

    def test_every_spelling_both_brackets_and_capitals(self):
        for word, (factor, gain) in delivery_cues.CUES.items():
            for written in (f"({word})", f"[{word}]", f"( {word.upper()} )"):
                with self.subTest(written=written):
                    self.assertEqual(split_cues(f"{written} Go."), [speech("Go.", factor, gain, word)])

    def test_table_values(self):
        self.assertEqual(delivery_cues.CUES["whispered"], (0.90, -10.0))
        self.assertEqual(delivery_cues.CUES["quietly"], (0.95, -5.0))
        self.assertEqual(delivery_cues.CUES["yelled"], (1.10, 5.0))
        self.assertEqual(delivery_cues.CUES["angrily"], (1.05, 3.0))
        self.assertEqual(delivery_cues.CUES["excitedly"], (1.10, 2.0))
        self.assertEqual(delivery_cues.CUES["sadly"], (0.88, -3.0))
        self.assertEqual(delivery_cues.CUES["slowly"], (0.80, 0.0))
        self.assertEqual(delivery_cues.CUES["quickly"], (1.20, 0.0))
        self.assertEqual(len(delivery_cues.CUES), 16)

    def test_a_later_cue_takes_over(self):
        self.assertEqual(split_cues("(whispers) Quiet. (shouts) Loud!"),
                         [speech("Quiet.", 0.90, -10.0, "whispers"), speech("Loud!", 1.10, 5.0, "shouts")])

    def test_pause_in_the_middle_keeps_the_cue(self):
        self.assertEqual(split_cues("(sadly) One. [pause 1.5s] Two."),
                         [speech("One.", 0.88, -3.0, "sadly"), pause(1.5), speech("Two.", 0.88, -3.0, "sadly")])

    def test_pause_forms(self):
        for written, seconds in (("[pause]", 1.0), ("(pause)", 1.0), ("(pauses)", 1.0), ("[Pause 2]", 2.0),
                                 ("[pause 1.5s]", 1.5), ("[pause 1.5 sec]", 1.5), ("[pause 2 seconds]", 2.0),
                                 ("[pause .5s]", 0.5), ("[pause 9s]", 5.0),
                                 ("[pause -2]", 1.0), ("[pause 0]", 0.0)):
            with self.subTest(written=written):
                self.assertEqual(split_cues(written), [pause(seconds)])

    def test_unknown_brackets_stay_in_the_text(self):
        self.assertEqual(split_cues("(laughs) Fine. (whispers softly) Really."),
                         [speech("(laughs) Fine. (whispers softly) Really.")])

    def test_a_bracket_that_only_starts_with_pause_is_not_a_pause(self):
        for written in ("(pause for effect)", "[pause abc]", "(Pause Man)"):
            with self.subTest(written=written):
                self.assertEqual(split_cues(f"{written} Go."), [speech(f"{written} Go.")])

    def test_an_unclosed_bracket_before_a_cue_is_not_spoken(self):
        self.assertEqual(split_cues("Hi (laughs. (whispers) Come here."),
                         [speech("Hi"), speech("Come here.", 0.90, -10.0, "whispers")])
        self.assertEqual(split_cues("Hi [laughs (soft) (shouts) Run!"),
                         [speech("Hi"), speech("Run!", 1.10, 5.0, "shouts")])

    def test_cue_only_line_has_no_pieces(self):
        self.assertEqual(split_cues("(whispers)"), [])
        self.assertEqual(split_cues("  "), [])

    def test_punctuation_after_a_cue_stays_with_the_words_before_it(self):
        self.assertEqual(split_cues("Come here (whispers)."), [speech("Come here.")])
        self.assertEqual(split_cues("Wait [pause 1s]."), [speech("Wait."), pause(1.0)])
        self.assertEqual(split_cues("(whispers) Run! (shouts)?!"), [speech("Run!?!", 0.90, -10.0, "whispers")])

    def test_punctuation_with_no_words_before_it_is_dropped(self):
        self.assertEqual(split_cues("... (whispers) Hi."), [speech("Hi.", 0.90, -10.0, "whispers")])

    def test_unclosed_bracket_is_just_text(self):
        self.assertEqual(split_cues("(whispers Hello."), [speech("(whispers Hello.")])

    def test_pause_limit_matches_the_studio(self):
        import voice_cast
        self.assertEqual(delivery_cues.MAX_PAUSE_SECONDS, voice_cast.MAX_PAUSE_SECONDS)


class IsCueTagTests(unittest.TestCase):
    def test_cues_and_pauses(self):
        for content in ("whispers", " Shouting ", "pause", "pause 2s", "Pauses"):
            self.assertTrue(is_cue_tag(content), content)

    def test_names_are_not_cues(self):
        for content in ("Narrator", "Speaker 1", "Whisper", "pausecake", "whispers softly", "", "Pause Man",
                        "pause for effect"):
            self.assertFalse(is_cue_tag(content), content)


class StudioPageCopyTests(unittest.TestCase):
    def test_studio_js_knows_the_same_cue_words(self):
        import re
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "static", "studio.js")
        with open(path, encoding="utf-8") as f:
            source = f.read()
        found = re.search(r"const CUE_WORDS = new Set\(\[([^\]]*)\]\)", source)
        self.assertIsNotNone(found, "CUE_WORDS missing from studio.js")
        self.assertEqual(set(re.findall(r"'([a-z]+)'", found.group(1))), set(delivery_cues.CUES))


if __name__ == "__main__":
    unittest.main()

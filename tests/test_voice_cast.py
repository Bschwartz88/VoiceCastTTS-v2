import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import voice_cast

CONFIG = {
    "default_voices": {
        "Narrator": {"voice": "bm_fable", "speed": 0.95},
        "Alice": {"voice": "af_sky", "speed": 1.0},
    },
    "pacing_seconds": {
        "dialogue_turn_pause": 0.35,
        "narrator_transition_pause": 0.65,
        "same_speaker_sentence_pause": 0.15,
    },
}


def dry_run(script, config=CONFIG):
    return voice_cast.render_dialogue_content(script, config=config, dry_run=True)


class ParseScriptTests(unittest.TestCase):
    def test_tags_comments_and_continuations(self):
        script = "# a comment\n[Narrator] Once.\n[Alice] Hi.\nStill Alice."
        self.assertEqual(voice_cast.parse_script(script),
                         [("Narrator", "Once."), ("Alice", "Hi."), ("Alice", "Still Alice.")])

    def test_start_of_line_cues_and_pauses_are_not_speakers(self):
        script = "[Alice] Hi.\n[whispers] Run.\n[pause 2s]\n[Pause] Still Alice.\n[Bob] Yo."
        self.assertEqual(voice_cast.parse_script(script),
                         [("Alice", "Hi."), ("Alice", "[whispers] Run."), ("Alice", "[pause 2s]"),
                          ("Alice", "[Pause] Still Alice."), ("Bob", "Yo.")])
        self.assertEqual(voice_cast.extract_speakers(script), ["Alice", "Bob"])

    def test_speaker_name_repeated_after_the_tag_is_not_read(self):
        script = "[Timmy] Timmy: the hose isn't working\n[Tom] **Tom:** Hi.\n[Ann] Annie: Hello."
        self.assertEqual(voice_cast.parse_script(script),
                         [("Timmy", "the hose isn't working"), ("Tom", "Hi."), ("Ann", "Annie: Hello.")])

    def test_pause_before_any_speaker_belongs_to_the_narrator(self):
        self.assertEqual(voice_cast.parse_script("[pause 2s]\n[Alice] Hi."),
                         [("Narrator", "[pause 2s]"), ("Alice", "Hi.")])


class DefaultCastTests(unittest.TestCase):
    """The sample uses numbered speakers so the user chooses the names."""

    def test_sample_script_uses_numbered_speakers_with_their_own_voices(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "sample_script.txt"), encoding="utf-8") as f:
            speakers = {spk for spk, _ in voice_cast.parse_script(f.read())}
        self.assertEqual(speakers, {"Narrator", "Speaker 1", "Speaker 2", "Speaker 3"})
        defaults = voice_cast.load_config(os.path.join(root, "voice_config.json"))["default_voices"]
        self.assertTrue(speakers <= set(defaults))
        voices = [defaults[s]["voice"] for s in sorted(speakers)]
        self.assertEqual(len(set(voices)), len(voices), "every sample speaker sounds different")

    def test_track_file_names(self):
        self.assertEqual(voice_cast.track_filename("Speaker 1"), "track_Speaker_1.wav")
        self.assertEqual(voice_cast.track_filename("Narrator"), "track_Narrator.wav")
        self.assertEqual(voice_cast.track_filename("../ <evil>"), "track__evil.wav")
        self.assertEqual(voice_cast.track_filename("!!!"), "track_speaker.wav")


class PauseTests(unittest.TestCase):
    def test_same_speaker_with_different_capitalization(self):
        lines = dry_run("[Alice] One.\n[alice] Two.")["lines"]
        self.assertEqual(lines[1]["pause_before"], 0.15)

    def test_narrator_transition(self):
        lines = dry_run("[Narrator] One.\n[Alice] Two.")["lines"]
        self.assertEqual(lines[1]["pause_before"], 0.65)


class ClampTests(unittest.TestCase):
    def test_speed_is_clamped(self):
        self.assertEqual(voice_cast.clamp_speed(50), 2.0)
        self.assertEqual(voice_cast.clamp_speed(0), 0.5)
        self.assertEqual(voice_cast.clamp_speed("fast"), 1.0)
        self.assertEqual(voice_cast.clamp_speed(float("nan")), 1.0)

    def test_pause_is_clamped(self):
        self.assertEqual(voice_cast.clamp_pause(1e9, 0.35), 5.0)
        self.assertEqual(voice_cast.clamp_pause(-3, 0.35), 0.0)
        self.assertEqual(voice_cast.clamp_pause(None, 0.35), 0.35)

    def test_huge_pause_and_speed_in_config_are_clamped(self):
        config = {
            "default_voices": {"Alice": {"voice": "af_sky", "speed": 99}},
            "pacing_seconds": {"dialogue_turn_pause": 1e9},
        }
        result = dry_run("[Alice] One.\n[Bob] Two.", config)
        self.assertEqual(result["lines"][0]["speed"], 2.0)
        self.assertEqual(result["lines"][1]["pause_before"], 5.0)


if __name__ == "__main__":
    unittest.main()

"""Line cache, per-line timings and cancellation, using a fake TTS pipeline (no model needed)."""

import os
import sys
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import voice_cast

SAMPLE_RATE = 24000
CONFIG = {
    "default_voices": {
        "Narrator": {"voice": "bm_fable", "speed": 1.0},
        "Alice": {"voice": "af_sky", "speed": 1.0},
    },
    "pacing_seconds": {
        "dialogue_turn_pause": 0.5,
        "narrator_transition_pause": 1.0,
        "same_speaker_sentence_pause": 0.25,
    },
}
SCRIPT = "[Narrator] One two three.\n[Alice] Four five.\n[Alice] Six."


class FakePipeline:
    """Returns 0.1 s of audio per word, and counts how many lines it synthesized (and at what speed)."""
    backend = "fake"

    def __init__(self):
        self.calls = []
        self.speeds = []

    def generate(self, text, voice, speed):
        self.calls.append(text)
        self.speeds.append(round(speed, 3))
        return [np.full(int(0.1 * SAMPLE_RATE * len(text.split())), 0.5, dtype=np.float32)]


class LineCacheTests(unittest.TestCase):
    def setUp(self):
        voice_cast.clear_line_cache()
        self.fake = FakePipeline()
        patcher = mock.patch.object(voice_cast, "get_pipeline", return_value=self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        # Keep the test from touching the real in-memory "latest audio".
        latest = mock.patch.object(voice_cast, "set_latest_audio")
        latest.start()
        self.addCleanup(latest.stop)

    def render(self, script=SCRIPT, **kwargs):
        return voice_cast.render_dialogue_content(script, config=CONFIG, **kwargs)

    def test_second_render_reuses_every_line(self):
        first = self.render()
        self.assertEqual(len(self.fake.calls), 3)
        self.assertEqual(first["cached_lines"], 0)

        second = self.render()
        self.assertEqual(len(self.fake.calls), 3, "no new synthesis expected")
        self.assertEqual(second["cached_lines"], 3)
        self.assertEqual(first["duration_seconds"], second["duration_seconds"])

    def test_only_edited_line_is_synthesized(self):
        self.render()
        result = self.render(SCRIPT.replace("Four five.", "Four five, edited."))
        self.assertEqual(self.fake.calls[3:], ["Four five, edited."])
        self.assertEqual([line["cached"] for line in result["lines"]], [True, False, True])

    def test_speed_or_voice_change_is_not_reused(self):
        self.render()
        faster = {**CONFIG, "default_voices": {**CONFIG["default_voices"], "Alice": {"voice": "af_sky", "speed": 1.2}}}
        voice_cast.render_dialogue_content(SCRIPT, config=faster)
        self.assertEqual(len(self.fake.calls), 5, "both Alice lines re-synthesized at the new speed")

    def test_line_timings_match_audio_and_pauses(self):
        lines = self.render()["lines"]
        # Narrator: 3 words = 0.3 s; narrator-to-Alice pause 1.0 s; Alice 0.2 s; same-speaker pause 0.25 s; Alice 0.1 s.
        expected = [(0.0, 0.3), (1.3, 1.5), (1.75, 1.85)]
        for line, (start, end) in zip(lines, expected):
            self.assertAlmostEqual(line["start"], start, places=3)
            self.assertAlmostEqual(line["end"], end, places=3)

    def test_speaker_tracks_are_time_aligned(self):
        import io
        import soundfile as sf

        captured = {}
        with mock.patch.object(voice_cast, "set_latest_audio", side_effect=lambda wav, meta, speaker_tracks: captured.update(speaker_tracks)):
            result = self.render()
        self.assertEqual(sorted(captured), ["Alice", "Narrator"])
        self.assertEqual(len(result["tracks"]), 2)

        narrator, _ = sf.read(io.BytesIO(captured["Narrator"]))
        alice, _ = sf.read(io.BytesIO(captured["Alice"]))
        self.assertEqual(len(narrator), len(alice))
        sec = lambda s: int(s * SAMPLE_RATE)
        self.assertGreater(abs(narrator[sec(0.1)]), 0.05)  # narrator speaking at 0.1 s
        self.assertEqual(alice[sec(0.1)], 0.0)             # Alice silent then
        self.assertEqual(narrator[sec(1.4)], 0.0)          # narrator silent at 1.4 s
        self.assertGreater(abs(alice[sec(1.4)]), 0.05)     # Alice speaking

    def test_lines_are_leveled_but_cache_keeps_originals(self):
        captured = {}
        with mock.patch.object(voice_cast, "set_latest_audio", side_effect=lambda wav, meta, speaker_tracks: captured.update(wav=wav)):
            self.render()
        import io
        import soundfile as sf
        mixed, _ = sf.read(io.BytesIO(captured["wav"]))
        # The fake line is a constant 0.5 (-6 dBFS); leveled to -20 dBFS it becomes 0.1.
        self.assertAlmostEqual(abs(mixed[int(0.1 * SAMPLE_RATE)]), 0.1, places=2)
        cached = next(iter(voice_cast._LINE_CACHE.values()))
        self.assertAlmostEqual(float(cached.max()), 0.5, places=5)

    def test_even_volume_off_keeps_original_level(self):
        captured = {}
        config = {**CONFIG, "even_volume": False}
        with mock.patch.object(voice_cast, "set_latest_audio", side_effect=lambda wav, meta, speaker_tracks: captured.update(wav=wav)):
            voice_cast.render_dialogue_content(SCRIPT, config=config)
        import io
        import soundfile as sf
        mixed, _ = sf.read(io.BytesIO(captured["wav"]))
        self.assertAlmostEqual(abs(mixed[int(0.1 * SAMPLE_RATE)]), 0.5, places=2)


    def test_cancel_stops_before_next_line_and_keeps_cache(self):
        calls_before_cancel = []

        def should_cancel():
            calls_before_cancel.append(len(self.fake.calls))
            return len(self.fake.calls) >= 1

        result = self.render(should_cancel=should_cancel)
        self.assertFalse(result["success"])
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["lines_done"], 1)
        self.assertEqual(len(self.fake.calls), 1)

        resumed = self.render()
        self.assertTrue(resumed["success"])
        self.assertEqual(resumed["cached_lines"], 1, "the line rendered before Stop is reused")

    def test_cache_evicts_oldest_when_over_budget(self):
        with mock.patch.object(voice_cast, "MAX_LINE_CACHE_BYTES", int(0.25 * SAMPLE_RATE) * 4):
            self.render("[Alice] One two.\n[Alice] Three four.")
            self.render("[Alice] One two.")
        self.assertEqual(len(self.fake.calls), 3, "first line was evicted to make room for the second")


class LevelingTests(unittest.TestCase):
    @staticmethod
    def speech(amplitude, seconds=1.0):
        t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
        return (amplitude * np.sin(2 * np.pi * 220 * t)).astype(np.float32)

    def test_quiet_and_loud_lines_end_up_equal(self):
        quiet = voice_cast.level_line(self.speech(0.06))
        loud = voice_cast.level_line(self.speech(0.6))
        self.assertAlmostEqual(voice_cast.speech_loudness_dbfs(quiet), voice_cast.speech_loudness_dbfs(loud), delta=0.5)
        self.assertAlmostEqual(voice_cast.speech_loudness_dbfs(loud), voice_cast.TARGET_SPEECH_DBFS, delta=0.5)

    def test_clipping_line_is_brought_under_the_ceiling_even_when_off(self):
        clipped = self.speech(1.3)
        for even in (True, False):
            out = voice_cast.level_line(clipped, even_volume=even)
            self.assertLessEqual(float(np.abs(out).max()), 10 ** (voice_cast.PEAK_CEILING_DBFS / 20) + 1e-6)

    def test_boost_is_capped(self):
        whisper = self.speech(0.001)
        out = voice_cast.level_line(whisper)
        boost_db = 20 * np.log10(np.abs(out).max() / np.abs(whisper).max())
        self.assertAlmostEqual(boost_db, voice_cast.MAX_LEVEL_GAIN_DB, delta=0.1)

    def test_silence_and_input_are_untouched(self):
        silence = np.zeros(SAMPLE_RATE, dtype=np.float32)
        self.assertIs(voice_cast.level_line(silence), silence)
        original = self.speech(0.6)
        before = original.copy()
        voice_cast.level_line(original)
        np.testing.assert_array_equal(original, before)


class DeliveryCueTests(unittest.TestCase):
    """Cues and pauses inside lines (delivery_cues.py), using the same fake pipeline."""

    def setUp(self):
        voice_cast.clear_line_cache()
        self.fake = FakePipeline()
        patcher = mock.patch.object(voice_cast, "get_pipeline", return_value=self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.captured = {}
        latest = mock.patch.object(voice_cast, "set_latest_audio",
                                   side_effect=lambda wav, meta, speaker_tracks: self.captured.update(wav=wav, tracks=speaker_tracks))
        latest.start()
        self.addCleanup(latest.stop)

    def render(self, script, config=CONFIG):
        return voice_cast.render_dialogue_content(script, config=config)

    def mixed(self):
        import io
        import soundfile as sf
        audio, _ = sf.read(io.BytesIO(self.captured["wav"]))
        return audio

    def test_cue_changes_speed_and_loudness_from_the_cue_on(self):
        result = self.render("[Alice] Four five. (whispers) Six.")
        self.assertEqual(self.fake.calls, ["Four five.", "Six."])
        self.assertEqual(self.fake.speeds, [1.0, 0.9])
        audio = self.mixed()
        self.assertAlmostEqual(abs(audio[int(0.1 * SAMPLE_RATE)]), 0.1, places=2)      # normal, leveled
        self.assertAlmostEqual(abs(audio[int(0.25 * SAMPLE_RATE)]), 0.1 * 10 ** (-10 / 20), places=3)  # whispered
        line = result["lines"][0]
        self.assertEqual(line["text"], "Four five. Six.")
        self.assertAlmostEqual(line["end"], 0.3, places=3)

    def test_line_without_cues_is_made_as_before(self):
        self.render("[Alice] (laughs) Four five.")
        self.assertEqual(self.fake.calls, ["Four five."])
        self.assertEqual(list(voice_cast._LINE_CACHE), [voice_cast._line_cache_key("Four five.", "af_sky", 1.0)])

    def test_cue_just_before_the_full_stop_makes_no_extra_sound(self):
        self.render("[Alice] Four five (whispers).")
        self.assertEqual(self.fake.calls, ["Four five."], "the full stop isn't spoken on its own")
        self.assertEqual(list(voice_cast._LINE_CACHE), [voice_cast._line_cache_key("Four five.", "af_sky", 1.0)])

    def test_pause_inside_a_line(self):
        result = self.render("[Alice] Four five. [pause 1.5s] Six.")
        self.assertEqual(self.fake.calls, ["Four five.", "Six."])
        line = result["lines"][0]
        self.assertAlmostEqual(line["end"], 0.2 + 1.5 + 0.1, places=3)
        self.assertEqual(self.mixed()[int(1.0 * SAMPLE_RATE)], 0.0)

    def test_pause_line_adds_silence_and_keeps_the_speaker(self):
        result = self.render("[Alice] Four five.\n[pause 2s]\n[Alice] Six.")
        self.assertEqual(len(result["lines"]), 2, "the pause line has no transcript entry")
        # Alice 0.2 s, pause 2.0 s, same-speaker gap 0.25 s, Alice 0.1 s.
        self.assertAlmostEqual(result["lines"][1]["start"], 2.45, places=3)
        self.assertAlmostEqual(result["lines"][1]["end"], 2.55, places=3)
        self.assertEqual(sorted(self.captured["tracks"]), ["Alice"])

    def test_script_starting_with_a_pause(self):
        result = self.render("[pause 2s]\n[Alice] Four five.")
        self.assertAlmostEqual(result["lines"][0]["start"], 2.0, places=3)
        self.assertEqual(sorted(self.captured["tracks"]), ["Alice"])

    def test_start_of_line_cue_whispers_that_line(self):
        self.render("[Alice] Four five.\n[whispers] Six.")
        self.assertEqual(self.fake.speeds, [1.0, 0.9])

    def test_cue_only_line_is_skipped(self):
        result = self.render("[Alice] (whispers)\n[Alice] Six.")
        self.assertEqual(self.fake.calls, ["Six."])
        self.assertEqual(len(result["lines"]), 1)
        self.assertAlmostEqual(result["lines"][0]["start"], 0.0, places=3)

    def test_changing_a_cue_reuses_the_other_pieces(self):
        self.render("[Alice] Four five. (whispers) Six.")
        result = self.render("[Alice] Four five. (shouts) Six.")
        self.assertEqual(self.fake.calls, ["Four five.", "Six.", "Six."])
        self.assertEqual(self.fake.speeds[-1], 1.1)
        self.assertFalse(result["lines"][0]["cached"], "one piece was new")

    def test_fully_reused_line_counts_as_cached(self):
        self.render("[Alice] Four five. (whispers) Six.")
        result = self.render("[Alice] Four five. (whispers) Six.")
        self.assertEqual(result["cached_lines"], 1)

    def test_pronunciation_of_a_cue_word_does_not_break_the_cue(self):
        config = {**CONFIG, "pronunciations": [{"word": "whispers", "say": "wispers"}]}
        self.render("[Alice] Four five. (whispers) Six.", config)
        self.assertEqual(self.fake.speeds, [1.0, 0.9])

    def test_speed_stays_within_the_limits(self):
        voices = {"Narrator": {"voice": "bm_fable", "speed": 0.5}, "Alice": {"voice": "af_sky", "speed": 2.0}}
        self.render("[Narrator] (slowly) One.\n[Alice] (quickly) Two.", {**CONFIG, "default_voices": voices})
        self.assertEqual(self.fake.speeds, [0.5, 2.0])

    def test_unclosed_bracket_is_spoken_as_before(self):
        result = self.render("[Alice] (whispers Hello.")
        self.assertEqual(self.fake.speeds, [1.0])
        self.assertEqual(len(result["lines"]), 1)

    def test_dry_run_counts_inline_pauses(self):
        result = voice_cast.render_dialogue_content("[Alice] One. [pause 1.5s] Two.", config=CONFIG, dry_run=True)
        self.assertAlmostEqual(result["estimated_pause_sec"], 1.5, places=3)
        self.assertEqual(self.fake.calls, [])


class ApplyGainTests(unittest.TestCase):
    def test_quieter_and_louder(self):
        audio = np.full(100, 0.1, dtype=np.float32)
        self.assertAlmostEqual(float(voice_cast.apply_gain(audio, -10.0)[0]), 0.1 * 10 ** (-0.5), places=5)
        self.assertAlmostEqual(float(voice_cast.apply_gain(audio, 5.0)[0]), 0.1 * 10 ** 0.25, places=5)
        self.assertAlmostEqual(float(audio[0]), 0.1, places=6, msg="the input is never changed")

    def test_louder_stops_at_the_ceiling(self):
        audio = np.full(100, 0.8, dtype=np.float32)
        ceiling = 10 ** (voice_cast.PEAK_CEILING_DBFS / 20)
        self.assertAlmostEqual(float(voice_cast.apply_gain(audio, 5.0).max()), ceiling, places=4)

    def test_no_change_returns_the_same_audio(self):
        audio = np.full(10, 0.1, dtype=np.float32)
        self.assertIs(voice_cast.apply_gain(audio, 0.0), audio)


class PerformanceRenderTests(unittest.TestCase):
    """The Performance setting (performance.py) applied while making the audio, with the fake pipeline."""

    def setUp(self):
        voice_cast.clear_line_cache()
        self.fake = FakePipeline()
        patcher = mock.patch.object(voice_cast, "get_pipeline", return_value=self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.captured = {}
        latest = mock.patch.object(voice_cast, "set_latest_audio",
                                   side_effect=lambda wav, meta, speaker_tracks: self.captured.update(wav=wav))
        latest.start()
        self.addCleanup(latest.stop)

    def render(self, script, level=None, voices=None):
        config = {**CONFIG, "default_voices": {**CONFIG["default_voices"], "Bob": {"voice": "am_adam", "speed": 1.0},
                                               **(voices or {})}}
        if level is not None:
            config["performance"] = level
        return voice_cast.render_dialogue_content(script, config=config)

    def mixed(self):
        import io
        import soundfile as sf
        audio, _ = sf.read(io.BytesIO(self.captured["wav"]))
        return audio

    def test_off_and_no_setting_give_todays_audio(self):
        script = "[Narrator] It was late.\n[Alice] Run! (whispers) Now.\n[Bob] No.\n[Alice] Wait—\n[Bob] Fine, have it."
        self.render(script)
        before, speeds_before = self.captured["wav"], list(self.fake.speeds)
        voice_cast.clear_line_cache()
        self.fake.speeds.clear()
        self.render(script, level="off")
        self.assertEqual(self.captured["wav"], before, "Off is sample for sample the same")
        self.assertEqual(self.fake.speeds, speeds_before)

    def test_natural_exclamation_is_modest_without_loudness_jump(self):
        self.render("[Alice] Run!", level="natural")
        self.assertEqual(self.fake.speeds, [1.02])
        self.assertAlmostEqual(abs(self.mixed()[int(0.05 * SAMPLE_RATE)]), 0.1, places=3)

    def test_narrator_gets_half(self):
        self.render("[Narrator] Run!", level="natural")
        self.assertEqual(self.fake.speeds, [1.01])

    def test_written_cue_wins_without_natural_amplification(self):
        self.render("[Alice] Run! (whispers) Go.", level="natural")
        self.assertEqual(self.fake.calls, ["Run!", "Go."])
        self.assertEqual(self.fake.speeds, [1.02, 0.9], "auto clue on the uncued piece, the cue's own speed after")
        audio = self.mixed()
        self.assertAlmostEqual(abs(audio[int(0.15 * SAMPLE_RATE)]), 0.1 * 10 ** (-10 / 20), places=3)

    def test_short_reply_and_cut_off_gaps(self):
        result = self.render("[Alice] Four five.\n[Bob] No.", level="natural")
        self.assertAlmostEqual(result["lines"][1]["start"], 0.2 + 0.5 * 0.6, places=3)
        result = self.render("[Alice] Wait—\n[Bob] No, I will not go.", level="natural")
        self.assertAlmostEqual(result["lines"][1]["start"], 0.1 + 0.05, places=3)

    def test_cut_off_across_a_pause_line(self):
        result = self.render("[Alice] Wait—\n[pause 1s]\n[Bob] No, I will not go.", level="natural")
        self.assertAlmostEqual(result["lines"][1]["start"], 0.1 + 1.0 + 0.05, places=3)

    def test_narrator_gaps_unchanged(self):
        result = self.render("[Alice] Four five.\n[Narrator] No.", level="lively")
        self.assertAlmostEqual(result["lines"][1]["start"], 0.2 + 1.0, places=3)

    def test_speed_stays_in_range(self):
        self.render("[Alice] Run!", level="lively", voices={"Alice": {"voice": "af_sky", "speed": 2.0}})
        self.assertEqual(self.fake.speeds, [2.0])

    def test_loudness_only_change_reuses_the_cache(self):
        self.render("[Alice] Go NOW, please.", level="natural")
        self.render("[Alice] Go NOW, please.", level="lively")
        self.assertEqual(len(self.fake.calls), 1, "only the loudness changed, so the line is reused")

    def test_clues_in_a_cued_part_stay_with_that_part(self):
        import performance
        self.render("[Alice] Calm down. (shouts) STOP!", level="natural")
        calm = performance.line_delivery("Calm down.", False, "natural")[0]
        self.assertEqual(self.fake.speeds, [round(calm, 3), 1.1], "the shout's ! and STOP don't speed up 'Calm down.'")

    def test_unknown_level_means_natural(self):
        self.render("[Alice] Run!", level="LOUD")
        self.assertEqual(self.fake.speeds, [1.02])


if __name__ == "__main__":
    unittest.main()

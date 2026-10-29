"""Blends inside voice_cast.py, with a fake ONNX model and temporary folders (no real model, no real files)."""

import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import voice_blends
import voice_cast

SHAPE = voice_blends.VOICE_SHAPE


class FakeModel:
    """Stands in for kokoro_onnx.Kokoro: records what it was asked to say."""

    def __init__(self):
        self.voices = {name: np.full(SHAPE, value, dtype=np.float32)
                       for name, value in (("af_sky", 1.0), ("af_bella", 3.0), ("bf_emma", 5.0), ("bm_george", 7.0))}
        self.calls = []

    def create(self, text, voice, speed=1.0, lang="en-us"):
        self.calls.append({"text": text, "voice": voice, "speed": speed, "lang": lang})
        return np.full(24000, 0.5, dtype=np.float32), 24000


class BlendAudioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.voices_dir = os.path.join(self.tmp.name, "custom_voices")
        self.previews_dir = os.path.join(self.tmp.name, "previews")
        os.makedirs(self.voices_dir)
        self.model = FakeModel()
        self.pipeline = voice_cast.UnifiedPipeline("onnx", self.model)
        for name, value in (("CUSTOM_VOICES_DIR", self.voices_dir), ("PREVIEWS_DIR", self.previews_dir),
                            ("_BASE_VOICE_IDS", frozenset(self.model.voices)), ("_CACHED_PIPELINE", self.pipeline)):
            patcher = mock.patch.object(voice_cast, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(voice_cast, "get_pipeline", return_value=self.pipeline)
        patcher.start()
        self.addCleanup(patcher.stop)

    def save(self, name, pairs, voice_type="Female", replace=False):
        return voice_cast.save_blend(name, [{"voice": v, "weight": w} for v, w in pairs], voice_type, replace=replace)

    def test_save_adds_the_voice_and_lists_it_under_my_blends(self):
        self.assertEqual(self.save(" hero ", [("af_sky", 70), ("af_bella", 30)]), "hero")
        self.assertAlmostEqual(float(self.model.voices["hero"][0, 0, 0]), 1.6, places=5)
        entry = next(e for e in voice_cast.get_custom_voice_entries() if e["id"] == "hero")
        self.assertEqual(entry, {"id": "hero", "accent": "Blend", "gender": "Female", "category": "My blends",
                                 "tone": "70% af_sky + 30% af_bella", "voice_type": "Female", "description": "",
                                 "recipe": [{"voice": "af_sky", "share": 0.7}, {"voice": "af_bella", "share": 0.3}]})

    def test_child_type_is_shown_like_elsewhere(self):
        self.save("kid", [("af_sky", 1)], voice_type="Child")
        entry = next(e for e in voice_cast.get_custom_voice_entries() if e["id"] == "kid")
        self.assertEqual(entry["gender"], "Child / Youth")

    def test_older_blend_and_damaged_recipe_keep_todays_entry(self):
        np.save(os.path.join(self.voices_dir, "child_girl.npy"), self.model.voices["af_sky"])
        self.save("hero", [("af_sky", 1)])
        with open(os.path.join(self.voices_dir, "hero.json"), "w", encoding="utf-8") as f:
            f.write("{damaged")
        entries = {e["id"]: e for e in voice_cast.get_custom_voice_entries()}
        self.assertEqual(entries["child_girl"]["category"], "Children & Youth")
        self.assertNotIn("recipe", entries["child_girl"])
        self.assertEqual(entries["hero"]["category"], "Custom Voices")
        self.assertNotIn("recipe", entries["hero"])

    def test_kokoro_names_are_refused(self):
        with self.assertRaises(voice_blends.BlendError):
            self.save("af_sky", [("af_bella", 1)])

    def test_delete_removes_files_and_the_loaded_voice(self):
        self.save("hero", [("af_sky", 1)])
        voice_cast.delete_blend("hero")
        self.assertNotIn("hero", self.model.voices)
        self.assertEqual(os.listdir(self.voices_dir), [])

    def test_save_and_delete_hold_the_model_lock(self):
        held = []

        def lock_is_free():  # asked from another thread, as a Listen or a model load would
            result = []

            def try_lock():
                result.append(voice_cast._PIPELINE_LOCK.acquire(blocking=False))
                if result[0]:
                    voice_cast._PIPELINE_LOCK.release()
            t = threading.Thread(target=try_lock)
            t.start()
            t.join()
            return result[0]

        def checking(real):
            def wrapper(*args, **kwargs):
                held.append(not lock_is_free())
                return real(*args, **kwargs)
            return wrapper

        with mock.patch.object(voice_blends, "save_blend", checking(voice_blends.save_blend)), \
             mock.patch.object(voice_blends, "delete_blend", checking(voice_blends.delete_blend)):
            self.save("hero", [("af_sky", 1)])
            voice_cast.delete_blend("hero")
        self.assertEqual(held, [True, True])

    def test_mostly_british_blend_speaks_british(self):
        self.save("my_narrator", [("bm_george", 60), ("af_sky", 40)])
        self.save("my_yank", [("bm_george", 40), ("af_sky", 60)])
        self.assertEqual(voice_cast.voice_lang("my_narrator"), "en-gb")
        self.assertEqual(voice_cast.voice_lang("my_yank"), "en-us")
        self.assertEqual(voice_cast.voice_lang("bf_emma"), "en-gb")
        self.assertEqual(voice_cast.voice_lang("child_girl"), "en-us", "no recipe: as before")
        self.pipeline.generate("Hello.", voice="my_narrator", speed=1.0)
        self.assertEqual(self.model.calls[-1]["lang"], "en-gb")

    def test_listen_mixes_in_memory_and_reuses_the_same_sample(self):
        recipe = [{"voice": "bf_emma", "weight": 2}, {"voice": "af_sky", "weight": 1}]
        url = voice_cast.generate_blend_preview(recipe, "Hi there.")
        self.assertTrue(url.startswith("/output/previews/preview_blend_"))
        self.assertTrue(os.path.exists(os.path.join(self.previews_dir, url.rsplit("/", 1)[1])))
        call = self.model.calls[-1]
        self.assertIsInstance(call["voice"], np.ndarray, "the mix is passed directly, nothing is saved")
        self.assertAlmostEqual(float(call["voice"][0, 0, 0]), (2 * 5 + 1) / 3, places=5)
        self.assertEqual(call["lang"], "en-gb")
        self.assertEqual(os.listdir(self.voices_dir), [])
        self.assertEqual(voice_cast.generate_blend_preview(recipe, "Hi there."), url)
        self.assertEqual(len(self.model.calls), 1, "same mix and text: the sample is reused")
        voice_cast.generate_blend_preview(recipe, "")
        self.assertEqual(self.model.calls[-1]["text"], voice_cast.BLEND_SAMPLE_TEXT)

    def test_listen_reports_a_missing_voice(self):
        with self.assertRaises(voice_blends.BlendError) as ctx:
            voice_cast.generate_blend_preview([{"voice": "gone_blend", "weight": 1}], "Hi.")
        self.assertIn("gone_blend", str(ctx.exception))

    def test_edited_blend_is_not_reused(self):
        self.save("hero", [("af_sky", 1)])
        first_key = voice_cast._line_cache_key("Hi.", "hero", 1.0)
        first_sample = voice_cast.generate_voice_preview("hero", text="Hi there.")
        later = os.path.getmtime(os.path.join(self.voices_dir, "hero.npy")) + 5
        self.save("hero", [("af_bella", 1)], replace=True)
        os.utime(os.path.join(self.voices_dir, "hero.npy"), (later, later))
        self.assertNotEqual(voice_cast._line_cache_key("Hi.", "hero", 1.0), first_key, "old lines aren't reused")
        self.assertNotEqual(voice_cast.generate_voice_preview("hero", text="Hi there."), first_sample,
                            "the speaker's sample is made fresh")

    def test_a_blend_named_with_b_follows_its_recipe(self):
        self.save("bella_mix", [("af_sky", 70), ("af_bella", 30)])
        self.assertEqual(voice_cast.voice_lang("bella_mix"), "en-us", "an American blend stays American")
        self.save("bob", [("bm_george", 1)])
        self.assertEqual(voice_cast.voice_lang("bob"), "en-gb")

    def test_kokoro_voice_samples_keep_their_names(self):
        url = voice_cast.generate_voice_preview("af_sky", text="Hi there.")
        self.assertRegex(url, r"/preview_af_sky_[0-9a-f]{10}_lv\.wav$")


class PitchTests(unittest.TestCase):
    """Pitch on a blend: Kokoro speaks at speed / f, then the audio is resampled by f (f = 2 ** (semitones / 12))."""
    F3 = 2 ** (3 / 12)
    setUp = BlendAudioTests.setUp
    save = BlendAudioTests.save

    def save_pitched(self, name="girl", pitch=3.0, start_speed=None):
        return voice_cast.save_blend(name, [{"voice": "af_sky", "weight": 1}], "Child",
                                     pitch=pitch, start_speed=start_speed)

    def test_shift_pitch_moves_a_tone(self):
        t = np.arange(24000) / 24000
        tone = np.sin(2 * np.pi * 440 * t).astype(np.float32)
        up = voice_cast.shift_pitch(tone, 12)
        self.assertEqual(len(up), 12000)
        peak_hz = np.argmax(np.abs(np.fft.rfft(up))) * 24000 / len(up)
        self.assertAlmostEqual(peak_hz, 880, delta=5)
        self.assertIs(voice_cast.shift_pitch(tone, 0), tone, "no change: the same array")

    def test_shift_pitch_short_audio(self):
        self.assertEqual(len(voice_cast.shift_pitch(np.zeros(0, dtype=np.float32), 3)), 0)
        self.assertGreater(len(voice_cast.shift_pitch(np.ones(10, dtype=np.float32), 3)), 0)

    def test_voice_pitch(self):
        self.save_pitched(pitch=1.5)
        self.assertEqual(voice_cast.voice_pitch("girl"), 1.5)
        self.assertEqual(voice_cast.voice_pitch("af_sky"), 0.0)
        self.assertEqual(voice_cast.voice_pitch("no_such_voice"), 0.0)

    def test_render_line_uses_pitch(self):
        self.save_pitched()
        part = {"text": "Hi.", "speed_factor": 1.0, "gain_db": 0.0}
        audio, _ = voice_cast._speak_piece(self.pipeline, part, "girl", 1.0, False, 24000)
        self.assertAlmostEqual(self.model.calls[-1]["speed"], 1 / self.F3, places=4)
        self.assertEqual(len(audio), round(24000 / self.F3))
        voice_cast._speak_piece(self.pipeline, part, "af_sky", 1.0, False, 24000)
        self.assertEqual(self.model.calls[-1]["speed"], 1.0, "voices without pitch: as before")

    def test_kokoro_speed_stays_in_its_range(self):
        """kokoro-onnx refuses speeds outside 0.5-2.0, so speed / f is kept inside it."""
        part = {"text": "Hi.", "speed_factor": 1.0, "gain_db": 0.0}
        self.save_pitched("up", pitch=3.0)
        voice_cast._speak_piece(self.pipeline, part, "up", 0.5, False, 24000)
        self.assertGreaterEqual(self.model.calls[-1]["speed"], 0.5)
        self.save_pitched("down", pitch=-3.0)
        voice_cast._speak_piece(self.pipeline, part, "down", 2.0, False, 24000)
        self.assertLessEqual(self.model.calls[-1]["speed"], 2.0)
        voice_cast.generate_voice_preview("down", speed=2.0, text="Hi there.")
        self.assertLessEqual(self.model.calls[-1]["speed"], 2.0)

    def test_shift_pitch_fades_the_edges(self):
        """The resample wraps around at the ends; a 5 ms fade keeps that from clicking."""
        out = voice_cast.shift_pitch(np.ones(24000, dtype=np.float32), 3)
        self.assertLess(abs(float(out[0])), 0.05)
        self.assertLess(abs(float(out[-1])), 0.05)
        self.assertAlmostEqual(float(out[len(out) // 2]), 1.0, delta=0.05, msg="the middle is untouched")

    def test_voice_pitch_reads_the_recipe_once_per_change(self):
        self.save_pitched(pitch=1.5)
        with mock.patch.object(voice_cast.voice_blends, "read_recipe", wraps=voice_cast.voice_blends.read_recipe) as read:
            for _ in range(5):
                self.assertEqual(voice_cast.voice_pitch("girl"), 1.5)
            self.assertLessEqual(read.call_count, 1)
            later = os.path.getmtime(os.path.join(self.voices_dir, "girl.npy")) + 5
            voice_cast.save_blend("girl", [{"voice": "af_sky", "weight": 1}], "Child", replace=True, pitch=-1.0)
            os.utime(os.path.join(self.voices_dir, "girl.npy"), (later, later))
            self.assertEqual(voice_cast.voice_pitch("girl"), -1.0, "a re-saved voice is read again")

    def test_a_resave_in_the_same_instant_is_still_seen(self):
        self.save_pitched(pitch=0)
        times = {ext: os.path.getmtime(os.path.join(self.voices_dir, f"girl.{ext}")) for ext in ("npy", "json")}
        self.assertEqual(voice_cast.voice_pitch("girl"), 0.0)
        voice_cast.save_blend("girl", [{"voice": "af_sky", "weight": 1}], "Child", replace=True, pitch=1.5)
        for ext, t in times.items():  # as if both saves happened within the same clock tick
            os.utime(os.path.join(self.voices_dir, f"girl.{ext}"), (t, t))
        self.assertEqual(voice_cast.voice_pitch("girl"), 1.5)

    def test_pitch_changes_the_cache_key(self):
        self.save_pitched(pitch=0)
        plain = voice_cast._line_cache_key("Hi.", "girl", 1.0)
        later = os.path.getmtime(os.path.join(self.voices_dir, "girl.npy"))
        voice_cast.save_blend("girl", [{"voice": "af_sky", "weight": 1}], "Child", replace=True, pitch=1.5)
        os.utime(os.path.join(self.voices_dir, "girl.npy"), (later, later))  # same file time: the pitch alone must differ
        self.assertNotEqual(voice_cast._line_cache_key("Hi.", "girl", 1.0), plain)

    def test_preview_name_has_the_pitch(self):
        self.save_pitched()
        url = voice_cast.generate_voice_preview("girl", text="Hi there.")
        self.assertIn("_p3.0", url)
        self.assertAlmostEqual(self.model.calls[-1]["speed"], 1 / self.F3, places=4)

    def test_blend_listen_with_pitch(self):
        recipe = [{"voice": "af_sky", "weight": 1}]
        plain = voice_cast.generate_blend_preview(recipe, "Hi there.")
        self.assertEqual(self.model.calls[-1]["speed"], 1.0)
        lifted = voice_cast.generate_blend_preview(recipe, "Hi there.", pitch=3.0)
        self.assertNotEqual(lifted, plain)
        self.assertAlmostEqual(self.model.calls[-1]["speed"], 1 / self.F3, places=4)

    def test_entry_shows_pitch_and_start_speed(self):
        self.save_pitched(pitch=1.5, start_speed=1.05)
        entry = next(e for e in voice_cast.get_custom_voice_entries() if e["id"] == "girl")
        self.assertEqual((entry["pitch"], entry["start_speed"]), (1.5, 1.05))
        self.assertTrue(entry["tone"].endswith(", pitch +1.5"), entry["tone"])
        self.save("plain", [("af_sky", 1)])
        entry = next(e for e in voice_cast.get_custom_voice_entries() if e["id"] == "plain")
        self.assertNotIn("pitch", entry)
        self.assertNotIn("start_speed", entry)


if __name__ == "__main__":
    unittest.main()

"""Quick tests for voice_blends.py. Everything happens in a temporary folder; custom_voices/ is never touched."""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import voice_blends
from voice_blends import BlendError, BlendExists

SHAPE = voice_blends.VOICE_SHAPE
VOICES = {name: np.full(SHAPE, value, dtype=np.float32)
          for name, value in (("af_sky", 1.0), ("af_bella", 3.0), ("bm_george", 5.0))}
BASE = frozenset(VOICES)


def rows(*pairs):
    return [{"voice": v, "weight": w} for v, w in pairs]


class RecipeTests(unittest.TestCase):
    def test_weights_become_shares(self):
        self.assertEqual(voice_blends.normalize_recipe(rows(("af_sky", 70), ("af_bella", 30))),
                         [("af_sky", 0.7), ("af_bella", 0.3)])
        self.assertEqual(voice_blends.normalize_recipe(rows(("af_sky", 50), ("af_bella", 50))),
                         [("af_sky", 0.5), ("af_bella", 0.5)])

    def test_same_voice_twice_adds_up_and_zero_rows_drop_out(self):
        self.assertEqual(voice_blends.normalize_recipe(rows(("af_sky", 25), ("af_bella", 0), ("af_sky", 25))),
                         [("af_sky", 1.0)])

    def test_bad_recipes(self):
        for recipe, words in ((rows(("af_sky", 0)), "above 0"), ([], "1 to 3"),
                              (rows(("a", 1), ("b", 1), ("c", 1), ("d", 1)), "1 to 3"),
                              ([{"voice": "", "weight": 1}], "needs a voice"),
                              ([{"voice": "af_sky", "weight": -1}], "0 or more"),
                              ([{"voice": "af_sky", "weight": float("nan")}], "0 or more"),
                              ([{"voice": "af_sky", "weight": True}], "0 or more"),
                              ([{"voice": "../voices", "weight": 1}], "isn't a voice name"),
                              ([{"voice": "C:/x", "weight": 1}], "isn't a voice name"),
                              ([{"voice": "a\\..\\..\\x", "weight": 1}], "isn't a voice name"),
                              ("af_sky", "1 to 3")):
            with self.subTest(recipe=recipe):
                with self.assertRaises(BlendError) as ctx:
                    voice_blends.normalize_recipe(recipe)
                self.assertIn(words, str(ctx.exception))

    def test_hand_copied_voices_with_spaces_can_be_ingredients(self):
        self.assertEqual(voice_blends.normalize_recipe(rows(("my voice", 1))), [("my voice", 1.0)])

    def test_command_line_may_mix_more(self):
        many = rows(("a", 1), ("b", 1), ("c", 1), ("d", 1))
        self.assertEqual(len(voice_blends.normalize_recipe(many, max_voices=None)), 4)

    def test_mix(self):
        mixed = voice_blends.mix(rows(("af_sky", 75), ("af_bella", 25)), VOICES)
        self.assertEqual(mixed.shape, SHAPE)
        self.assertEqual(mixed.dtype, np.float32)
        self.assertAlmostEqual(float(mixed[0, 0, 0]), 1.5, places=5)

    def test_mix_names_a_missing_or_odd_voice(self):
        with self.assertRaises(BlendError) as ctx:
            voice_blends.mix(rows(("af_sky", 1), ("gone_blend", 1)), VOICES)
        self.assertIn("gone_blend", str(ctx.exception))
        odd = dict(VOICES, odd=np.zeros((3, 3), dtype=np.float32))
        with self.assertRaises(BlendError):
            voice_blends.mix(rows(("odd", 1)), odd)

    def test_label(self):
        self.assertEqual(voice_blends.recipe_label([{"voice": "af_sky", "share": 0.7},
                                                   {"voice": "af_bella", "share": 0.3}]),
                         "70% af_sky + 30% af_bella")


class NameTests(unittest.TestCase):
    def test_good_name(self):
        self.assertEqual(voice_blends.check_name(" old_sailor-2 ", BASE), "old_sailor-2")

    def test_bad_names(self):
        for name, words in (("", "Give the new voice a name"), (None, "Give the new voice a name"),
                            ("old sailor", "only letters"), ("café", "only letters"), ("../x", "only letters"),
                            ("x" * 41, "at most 40"), ("af_sky", "Kokoro voice")):
            with self.subTest(name=name):
                with self.assertRaises(BlendError) as ctx:
                    voice_blends.check_name(name, BASE)
                self.assertIn(words, str(ctx.exception))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = os.path.join(self.tmp.name, "custom_voices")

    def save(self, name="hero", recipe=None, voice_type="Female", replace=False):
        return voice_blends.save_blend(self.folder, name, recipe or rows(("af_sky", 70), ("af_bella", 30)),
                                       voice_type, VOICES, BASE, replace=replace)

    def test_save_writes_voice_and_recipe(self):
        array = self.save()
        np.testing.assert_allclose(np.load(os.path.join(self.folder, "hero.npy")), array)
        with open(os.path.join(self.folder, "hero.json"), encoding="utf-8") as f:
            stored = json.load(f)
        self.assertEqual(stored, {"recipe": [{"voice": "af_sky", "share": 0.7}, {"voice": "af_bella", "share": 0.3}],
                                  "voice_type": "Female"})
        self.assertEqual(voice_blends.read_recipe(self.folder, "hero"), stored)
        self.assertEqual([f for f in os.listdir(self.folder) if f.endswith(".tmp")], [], "no temporary files left")

    def test_description_is_kept(self):
        voice_blends.save_blend(self.folder, "troll", rows(("bm_george", 1)), "Male", VOICES, BASE,
                                description="the grumpy troll under the bridge")
        self.assertEqual(voice_blends.read_recipe(self.folder, "troll")["description"],
                         "the grumpy troll under the bridge")
        self.save()  # no description: the file and the recipe have none, as before
        self.assertNotIn("description", voice_blends.read_recipe(self.folder, "hero"))

    def test_odd_description_in_a_file_is_ignored(self):
        self.save()
        path = os.path.join(self.folder, "hero.json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        data["description"] = 42
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        self.assertNotIn("description", voice_blends.read_recipe(self.folder, "hero"))

    def test_pitch_and_start_speed_are_kept(self):
        voice_blends.save_blend(self.folder, "girl", rows(("af_sky", 1)), "Child", VOICES, BASE,
                                pitch=1.5, start_speed=0.923)
        with open(os.path.join(self.folder, "girl.json"), encoding="utf-8") as f:
            stored = json.load(f)
        self.assertEqual((stored["pitch"], stored["start_speed"]), (1.5, 0.92))
        recipe = voice_blends.read_recipe(self.folder, "girl")
        self.assertEqual((recipe["pitch"], recipe["start_speed"]), (1.5, 0.92))

    def test_defaults_are_not_stored(self):
        voice_blends.save_blend(self.folder, "girl", rows(("af_sky", 1)), "Child", VOICES, BASE,
                                pitch=0, start_speed=None)
        with open(os.path.join(self.folder, "girl.json"), encoding="utf-8") as f:
            stored = json.load(f)
        self.assertNotIn("pitch", stored)
        self.assertNotIn("start_speed", stored)
        self.assertNotIn("pitch", voice_blends.read_recipe(self.folder, "girl"))

    def test_bad_pitch_and_speed(self):
        for pitch in (3.5, -3.5, 1.25, True, "1", float("nan")):
            with self.subTest(pitch=pitch), self.assertRaises(BlendError):
                voice_blends.save_blend(self.folder, "girl", rows(("af_sky", 1)), "Child", VOICES, BASE, pitch=pitch)
        for speed in (0.4, 2.1, True, "1", float("nan")):
            with self.subTest(speed=speed), self.assertRaises(BlendError):
                voice_blends.save_blend(self.folder, "girl", rows(("af_sky", 1)), "Child", VOICES, BASE,
                                        start_speed=speed)
        self.assertFalse(os.path.exists(os.path.join(self.folder, "girl.npy")), "nothing saved")

    def test_odd_pitch_in_a_file_is_ignored(self):
        self.save()
        path = os.path.join(self.folder, "hero.json")
        for pitch, speed in (('"high"', '"x"'), ("9", "5"), ("true", "true")):
            with self.subTest(pitch=pitch):
                with open(path, "w", encoding="utf-8") as f:
                    f.write('{"recipe": [{"voice": "af_sky", "share": 1}], "voice_type": "Female", '
                            f'"pitch": {pitch}, "start_speed": {speed}}}')
                recipe = voice_blends.read_recipe(self.folder, "hero")
                self.assertEqual(recipe["voice_type"], "Female")
                self.assertNotIn("pitch", recipe)
                self.assertNotIn("start_speed", recipe)

    def test_existing_name_needs_replace(self):
        self.save()
        with self.assertRaises(BlendExists) as ctx:
            self.save(recipe=rows(("bm_george", 1)))
        self.assertIn("already exists", str(ctx.exception))
        self.save(recipe=rows(("bm_george", 1)), voice_type="Male", replace=True)
        self.assertEqual(voice_blends.read_recipe(self.folder, "hero")["voice_type"], "Male")

    def test_blend_cannot_use_itself(self):
        self.save()
        with self.assertRaises(BlendError) as ctx:
            self.save(recipe=rows(("hero", 50), ("af_sky", 50)), replace=True)
        self.assertIn("itself", str(ctx.exception))

    def test_voice_type_must_be_known(self):
        with self.assertRaises(BlendError):
            self.save(voice_type="Robot")

    def test_delete(self):
        self.save()
        voice_blends.delete_blend(self.folder, "hero")
        self.assertEqual(os.listdir(self.folder), [])
        with self.assertRaises(BlendError):
            voice_blends.delete_blend(self.folder, "hero")
        with self.assertRaises(BlendError):
            voice_blends.delete_blend(self.folder, "../voice_config")

    def test_older_blend_without_recipe(self):
        os.makedirs(self.folder)
        np.save(os.path.join(self.folder, "child_girl.npy"), VOICES["af_sky"])
        self.assertIsNone(voice_blends.read_recipe(self.folder, "child_girl"))
        voice_blends.delete_blend(self.folder, "child_girl")
        self.assertEqual(os.listdir(self.folder), [])

    def test_damaged_recipes_are_ignored(self):
        self.save()
        path = os.path.join(self.folder, "hero.json")
        for content in ("{not json", '{"recipe": "x", "voice_type": "Female"}',
                        '{"recipe": [{"voice": "af_sky", "share": 2}], "voice_type": "Female"}',
                        '{"recipe": [{"voice": "af_sky", "share": 1}], "voice_type": "Robot"}', "[]"):
            with self.subTest(content=content):
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)
                self.assertIsNone(voice_blends.read_recipe(self.folder, "hero"))

    def test_recipe_saved_with_a_bom_is_read(self):
        self.save()
        path = os.path.join(self.folder, "hero.json")
        with open(path, encoding="utf-8") as f:
            content = f.read()
        with open(path, "w", encoding="utf-8-sig") as f:  # as Notepad may save it
            f.write(content)
        self.assertEqual(voice_blends.read_recipe(self.folder, "hero")["voice_type"], "Female")

    def test_recipe_of_an_odd_name_is_not_looked_up(self):
        self.save()
        self.assertIsNone(voice_blends.read_recipe(self.folder, "../custom_voices/hero"))

    def test_names_that_differ_only_in_capitals_are_refused(self):
        self.save()
        for replace in (False, True):
            with self.subTest(replace=replace):
                with self.assertRaises(BlendError) as ctx:
                    self.save(name="Hero", replace=replace)
                self.assertNotIsInstance(ctx.exception, BlendExists, "no 'Replace it?' for a different spelling")
                self.assertIn("A voice called hero already exists", str(ctx.exception))
        self.assertEqual(sorted(os.listdir(self.folder)), ["hero.json", "hero.npy"])

    def test_forget_recipe(self):
        self.save()
        voice_blends.forget_recipe(self.folder, "hero")
        self.assertIsNone(voice_blends.read_recipe(self.folder, "hero"))
        self.assertTrue(os.path.exists(os.path.join(self.folder, "hero.npy")))
        voice_blends.forget_recipe(self.folder, "nothing_here")  # no error


class CommandLineTests(unittest.TestCase):
    def test_blend_voices_uses_the_same_mixing_and_drops_a_stale_recipe(self):
        import blend_voices
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            voice_blends.save_blend(tmp, "hero", rows(("af_sky", 1)), "Female", VOICES, BASE)
            with mock.patch.object(blend_voices, "CUSTOM_VOICES_DIR", tmp), \
                 mock.patch.object(blend_voices, "load_base_voices", return_value=VOICES):
                blend_voices.blend({"af_sky": 0.25, "af_bella": 0.25, "bm_george": 0.25, "af_sky ": 0.25},
                                   "hero", preview=False)
            saved = np.load(os.path.join(tmp, "hero.npy"))
            self.assertAlmostEqual(float(saved[0, 0, 0]), (1 + 3 + 5 + 1) / 4, places=5)
            self.assertFalse(os.path.exists(os.path.join(tmp, "hero.json")), "the old recipe no longer matches")

    def run_command_line(self, *args):
        import blend_voices
        from unittest import mock
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(blend_voices, "CUSTOM_VOICES_DIR", tmp), \
             mock.patch.object(blend_voices, "load_base_voices", return_value=VOICES), \
             mock.patch.object(sys, "argv", ["blend_voices.py", *args]), \
             contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as ctx:
                blend_voices.main()
            self.assertEqual(os.listdir(tmp), [], "nothing is saved")
        self.assertEqual(ctx.exception.code, 1)
        return out.getvalue()

    def test_command_line_problems_are_plain_messages(self):
        for blend, words in (("af_sky:-1", "weight"), ("af_sky:abc", "weight"), ("no_such:1", "no_such")):
            with self.subTest(blend=blend):
                printed = self.run_command_line("--name", "x", "--blend", blend, "--no-preview")
                self.assertIn(words, printed)
                self.assertNotIn("slider", printed)


if __name__ == "__main__":
    unittest.main()

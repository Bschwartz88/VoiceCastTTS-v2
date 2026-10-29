"""Quick tests for describe_voice.py (no model, no server)."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import describe_voice as dv


class ReadDescriptionTests(unittest.TestCase):
    def test_plain_description(self):
        t = dv.read_description("old British man, gruff")
        self.assertEqual((t["gender"], t["age"], t["accent"], t["sounds"]), ("male", "elder", "british", ["gruff"]))
        self.assertEqual(t["unknown"], [])

    def test_synonyms_become_sound_words(self):
        t = dv.read_description("a raspy, posh, shy lady with a low voice")
        self.assertEqual(t["gender"], "female")
        self.assertEqual(t["sounds"], ["gruff", "refined", "nervous", "deep"])

    def test_girl_and_boy_imply_a_child_unless_an_age_is_given(self):
        self.assertEqual(dv.read_description("a little girl")["age"], "child")
        self.assertEqual(dv.read_description("a boy")["age"], "child")
        t = dv.read_description("a nervous teenage girl who talks fast, American")
        self.assertEqual((t["gender"], t["age"], t["accent"]), ("female", "teen", "american"))
        self.assertEqual(t["sounds"], ["nervous", "lively"])
        self.assertEqual(dv.read_description("grandma")["age"], "elder")

    def test_little_and_young_are_only_hints(self):
        # Review fix: a stronger age word wins, and "young" with woman/man means a grown-up.
        self.assertEqual(dv.read_description("a little old lady")["age"], "elder")
        self.assertEqual(dv.read_description("little old man")["age"], "elder")
        self.assertEqual(dv.read_description("a young woman")["age"], "adult")
        self.assertEqual(dv.read_description("a young man")["age"], "adult")
        self.assertEqual(dv.read_description("a young girl")["age"], "child")
        self.assertEqual(dv.read_description("a young voice")["age"], "teen")
        self.assertEqual(dv.read_description("a little elf")["age"], "child")

    def test_an_age_in_years_is_read(self):
        # Owner's blends showed "5 year old" read as elderly: "old" there is part of the age.
        for text, age in (("a 5 year old happy girl", "child"), ("a five-year-old boy", "child"),
                          ("a 15 year old girl", "teen"), ("a 35-year-old woman", "adult"),
                          ("an 80 years old man", "elder"), ("a 12 yr old", "child")):
            t = dv.read_description(text)
            self.assertEqual(t["age"], age, text)
            self.assertNotIn("year", t["unknown"], text)
        self.assertEqual(dv.read_description("a 5 year old happy girl")["sounds"], ["jolly"])

    def test_numbers_stay_in_the_name(self):
        self.assertEqual(dv.suggest_name("a 5 year old happy girl", set()), "5_year_old_happy_girl")

    def test_unknown_words_are_listed_and_small_words_skipped(self):
        t = dv.read_description("the grumpy troll under the bridge")
        self.assertEqual(t["sounds"], ["gruff"])
        self.assertEqual(t["unknown"], ["troll", "bridge"])

    def test_plurals_and_repeats(self):
        t = dv.read_description("deep deep voices, warm and kind")
        self.assertEqual(t["sounds"], ["deep", "warm"])

    def test_at_most_five_sounds(self):
        t = dv.read_description("deep gruff booming warm soft gentle calm")
        self.assertEqual(len(t["sounds"]), dv.MAX_SOUNDS)

    def test_neutral(self):
        self.assertEqual(dv.read_description("an androgynous calm voice")["gender"], "neutral")

    def test_accents_and_apostrophes_are_read(self):
        self.assertEqual(dv.words("Zoë's café-owner"), ["zoe", "s", "cafe", "owner"])

    def test_nothing_understood(self):
        self.assertTrue(dv.nothing_understood(dv.read_description("a voice like the sea")))
        self.assertFalse(dv.nothing_understood(dv.read_description("a woman")))
        self.assertFalse(dv.nothing_understood(dv.read_description("something deep")))


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# A small table for the choice tests, in the same shape as voice_config.json's voice_traits.
TABLE = {
    "af_bella": {"gender": "female", "age": "adult", "accent": "american", "sounds": ["expressive", "lively"]},
    "am_adam": {"gender": "male", "age": "adult", "accent": "american", "sounds": ["friendly", "warm"]},
    "am_onyx": {"gender": "male", "age": "adult", "accent": "american", "sounds": ["deep", "gruff", "dramatic"]},
    "bm_daniel": {"gender": "male", "age": "adult", "accent": "british", "sounds": ["crisp", "clear"]},
    "bm_lewis": {"gender": "male", "age": "adult", "accent": "british", "sounds": ["thoughtful", "calm"]},
    "bm_george": {"gender": "male", "age": "adult", "accent": "british", "sounds": ["deep", "warm", "calm"]},
    "am_santa": {"gender": "male", "age": "elder", "accent": "american", "sounds": ["booming", "jolly", "warm"]},
    "am_fenrir": {"gender": "male", "age": "adult", "accent": "american", "sounds": ["rugged", "gruff"]},
    "af_sky": {"gender": "female", "age": "child", "accent": "american", "sounds": ["light", "bright"]},
    "af_kore": {"gender": "female", "age": "child", "accent": "american", "sounds": ["soft", "sweet"]},
    "zf_xiaobei": {"gender": "female", "age": "adult", "accent": "chinese", "sounds": ["clear"]},
}
ALL = set(TABLE)


def traits(**kw):
    base = {"gender": "any", "age": "any", "accent": "any", "sounds": [], "unknown": []}
    base.update(kw)
    return base


class TraitTableTests(unittest.TestCase):
    def test_real_table_is_valid_and_names_known_voices(self):
        with open(os.path.join(ROOT, "voice_config.json"), encoding="utf-8") as f:
            config = json.load(f)
        table = config["voice_traits"]
        self.assertEqual(dv.check_voice_traits(table), [])
        built_in = {v["id"] for v in config["available_popular_voices"]}
        # Only the voices that come with Kokoro: the owner's own voices keep their traits in a private file.
        self.assertEqual(set(table), built_in, "exactly the built-in voices have traits in the uploaded file")

    def test_private_traits_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "voice_traits.json")
            self.assertEqual(dv.read_traits_file(path), {}, "no file: nothing")
            good = {"gender": "male", "age": "adult", "accent": "american", "sounds": ["deep"]}
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"my_troll": good, "broken": {"gender": "robot"}, "x": "y"}, f)
            self.assertEqual(dv.read_traits_file(path), {"my_troll": good}, "bad records are skipped")
            with open(path, "w", encoding="utf-8") as f:
                f.write("not json")
            self.assertEqual(dv.read_traits_file(path), {}, "a damaged file is ignored")

    def test_bad_records_are_reported(self):
        problems = dv.check_voice_traits({"x": {"gender": "robot", "age": "adult", "accent": "american", "sounds": ["deep"]},
                                          "y": {"gender": "male", "age": "adult", "accent": "american", "sounds": ["loud"]},
                                          "z": "male"})
        self.assertEqual(len(problems), 3)


class SuggestTests(unittest.TestCase):
    def voices(self, result):
        return [row["voice"] for row in result["recipe"]]

    def test_best_matches_get_the_biggest_shares(self):
        r = dv.suggest(traits(gender="male", sounds=["deep", "gruff", "booming"]), TABLE, ALL)
        # am_onyx 2 + 0.5 (grown-up); bm_george and am_fenrir 1 + 0.5 (table order breaks the tie); am_santa 1.
        self.assertEqual(self.voices(r), ["am_onyx", "bm_george", "am_fenrir"])
        self.assertEqual([row["share"] for row in r["recipe"]], [0.55, 0.30, 0.15])
        self.assertEqual(r["voice_type"], "Male")
        self.assertEqual(r["explanation"], "Picked am_onyx (deep, gruff, dramatic), bm_george (deep, warm, calm), "
                                           "am_fenrir (rugged, gruff) because you asked for: male, deep, gruff, booming.")

    def test_one_good_match_is_used_alone(self):
        r = dv.suggest(traits(gender="female", sounds=["expressive"]), TABLE, ALL)
        self.assertEqual(r["recipe"], [{"voice": "af_bella", "share": 1.0}])

    def test_filters_and_relaxed_age_note(self):
        r = dv.suggest(dv.read_description("old British man, gruff"), TABLE, ALL)
        self.assertTrue(all(v.startswith("bm_") for v in self.voices(r)), r)
        self.assertEqual(self.voices(r), ["bm_daniel", "bm_lewis"])
        self.assertEqual([row["share"] for row in r["recipe"]], [0.65, 0.35])
        self.assertEqual(r["note"], "I have no old voice like that, so I left out the age.")

    def test_relaxed_accent_note(self):
        r = dv.suggest(traits(gender="male", accent="chinese"), TABLE, ALL)
        self.assertEqual(r["note"], "I have no Chinese voice like that, so I left out the accent.")
        self.assertTrue(all(TABLE[v]["gender"] == "male" for v in self.voices(r)))

    def test_never_mixes_genders_without_neutral(self):
        r = dv.suggest(traits(sounds=["warm", "calm", "deep"]), TABLE, ALL)
        self.assertEqual({TABLE[v]["gender"] for v in self.voices(r)}, {"male"})
        mixed = dv.suggest(traits(gender="neutral", sounds=["warm", "lively"]), TABLE, ALL)
        self.assertEqual({TABLE[v]["gender"] for v in self.voices(mixed)}, {"male", "female"})
        self.assertEqual(mixed["voice_type"], "Neutral")

    def test_neutral_always_mixes_a_woman_and_a_man(self):
        # Review fix: table order and scores alone could pick one gender only.
        for t in (traits(gender="neutral"), traits(gender="neutral", sounds=["expressive"]),
                  traits(gender="neutral", sounds=["calm", "warm", "deep"])):
            r = dv.suggest(t, TABLE, ALL)
            self.assertEqual({TABLE[v]["gender"] for v in self.voices(r)}, {"male", "female"}, t)
            self.assertEqual(r["voice_type"], "Neutral")
        r = dv.suggest(traits(gender="neutral", sounds=["expressive"]), TABLE, ALL)
        self.assertEqual(self.voices(r), ["af_bella", "am_adam"])

    def test_chinese_voices_only_on_request(self):
        r = dv.suggest(traits(gender="female", sounds=["clear"]), TABLE, ALL)
        self.assertNotIn("zf_xiaobei", self.voices(r))
        asked = dv.suggest(traits(accent="chinese"), TABLE, ALL)
        self.assertEqual(self.voices(asked), ["zf_xiaobei"])

    def test_only_a_gender_gives_two_all_round_voices(self):
        r = dv.suggest(traits(gender="male"), TABLE, ALL)
        self.assertEqual(self.voices(r), ["am_adam", "am_onyx"])
        self.assertEqual(r["explanation"], "Picked am_adam (friendly, warm), am_onyx (deep, gruff, dramatic) "
                                           "because you asked for: male.")

    def test_children_are_a_child_voice_type(self):
        r = dv.suggest(dv.read_description("a cheerful little girl"), TABLE, ALL)
        self.assertEqual(self.voices(r), ["af_sky", "af_kore"])
        self.assertEqual(r["voice_type"], "Child")

    def test_voices_that_are_not_installed_are_skipped(self):
        r = dv.suggest(traits(gender="male", sounds=["deep", "gruff"]), TABLE, ALL - {"am_onyx"})
        self.assertNotIn("am_onyx", self.voices(r))

    def test_nothing_to_go_on(self):
        self.assertIsNone(dv.suggest(dv.read_description("a voice like the sea"), TABLE, ALL))
        self.assertIsNone(dv.suggest(traits(gender="male"), TABLE, set()))

    def test_same_description_same_suggestion(self):
        t = dv.read_description("a warm calm man")
        self.assertEqual(dv.suggest(t, TABLE, ALL), dv.suggest(t, TABLE, ALL))


class NameTests(unittest.TestCase):
    def test_meaningful_words_joined(self):
        self.assertEqual(dv.suggest_name("the grumpy troll under the bridge", set()), "grumpy_troll_bridge")
        self.assertEqual(dv.suggest_name("Zoë's café-owner", set()), "zoe_cafe_owner")

    def test_at_most_thirty_characters_whole_words(self):
        name = dv.suggest_name("an extraordinarily cantankerous lighthouse keeper", set())
        self.assertEqual(name, "extraordinarily_cantankerous")
        self.assertLessEqual(len(name), dv.MAX_NAME)

    def test_empty_gives_voice_and_taken_names_get_a_number(self):
        self.assertEqual(dv.suggest_name("a the with", set()), "voice")
        self.assertEqual(dv.suggest_name("grumpy troll", {"Grumpy_Troll", "grumpy_troll_2"}), "grumpy_troll_3")


class CleanTraitsTests(unittest.TestCase):
    def test_answer_is_tidied(self):
        t = dv.clean_traits({"gender": " Male", "age": "ELDER", "accent": "irish",
                             "sounds": ["deep", "spooky", "deep", "Gruff", 5]})
        self.assertEqual(t, {"gender": "male", "age": "elder", "accent": "any", "sounds": ["deep", "gruff"], "unknown": []})

    def test_any_and_missing(self):
        self.assertEqual(dv.clean_traits({"gender": "any"}),
                         {"gender": "any", "age": "any", "accent": "any", "sounds": [], "unknown": []})

    def test_not_an_object(self):
        with self.assertRaises(ValueError):
            dv.clean_traits(["male"])


class UsTests(unittest.TestCase):
    def test_lower_case_us_is_not_american(self):
        traits = dv.read_description("a voice that scares us")
        self.assertEqual(traits["accent"], "any")
        self.assertNotIn("us", traits["unknown"])

    def test_capital_us_is_american(self):
        for text in ("a US news anchor", "a U.S. sergeant", "an american mom", "a usa cheerleader"):
            self.assertEqual(dv.read_description(text)["accent"], "american", text)

    def test_an_earlier_accent_word_beats_capital_us(self):
        self.assertEqual(dv.read_description("a British actor playing a US soldier")["accent"], "british")

    def test_capital_us_still_wins_when_it_comes_first(self):
        self.assertEqual(dv.read_description("a US soldier with a British friend")["accent"], "american")

    def test_all_capitals_us_is_just_us(self):
        self.assertEqual(dv.read_description("A GRUMPY TROLL WHO SCARES US")["accent"], "any")


if __name__ == "__main__":
    unittest.main()

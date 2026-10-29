import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from speakability import apply_pronunciations, clean_for_speech, drop_repeated_name, normalize_pronunciations


class AbbreviationTests(unittest.TestCase):
    def test_title_before_name_expands(self):
        self.assertEqual(clean_for_speech("Dr. Smith and Mr. Jones met Mrs. Lee."),
                         "Doctor Smith and Mister Jones met Missus Lee.")

    def test_sentence_final_abbreviation_keeps_period(self):
        self.assertEqual(clean_for_speech("We have maybe 10 min."), "We have maybe 10 minutes.")
        self.assertEqual(clean_for_speech("We don't need 10 min. We just need 2 min of power."),
                         "We don't need 10 minutes. We just need 2 minutes of power.")
        self.assertEqual(clean_for_speech("Wait 5 min. then go."), "Wait 5 minutes then go.")

    def test_units_after_numbers(self):
        self.assertEqual(clean_for_speech("Took 1 hr and 30 secs."), "Took 1 hour and 30 seconds.")
        self.assertEqual(clean_for_speech("Only 1 min."), "Only 1 minute.")
        self.assertEqual(clean_for_speech("The admin console."), "The admin console.")

    def test_title_never_ends_sentence(self):
        self.assertEqual(clean_for_speech("Ask Dr. Who."), "Ask Doctor Who.")

    def test_eg_expands(self):
        self.assertEqual(clean_for_speech("Fruit, e.g., apples."), "Fruit, for example, apples.")

    def test_number_abbreviation_only_before_digit(self):
        self.assertEqual(clean_for_speech("Room No. 5"), "Room number 5")
        self.assertEqual(clean_for_speech("I said no."), "I said no.")

    def test_abbreviation_inside_word_untouched(self):
        self.assertEqual(clean_for_speech("Drive home."), "Drive home.")


class SymbolTests(unittest.TestCase):
    def test_ampersand_between_words(self):
        self.assertEqual(clean_for_speech("Tom & Jerry"), "Tom and Jerry")
        self.assertEqual(clean_for_speech("R&D budget"), "R and D budget")

    def test_currency_with_thousands_separator(self):
        self.assertEqual(clean_for_speech("It cost $1,000 today."), "It cost 1000 dollars today.")
        self.assertEqual(clean_for_speech("Only $5.50, then."), "Only 5.50 dollars, then.")
        self.assertEqual(clean_for_speech("£20 and €3"), "20 pounds and 3 euros")

    def test_plus_only_between_numbers(self):
        self.assertEqual(clean_for_speech("2+2 is 4"), "2 plus 2 is 4")
        self.assertEqual(clean_for_speech("I write C++ code"), "I write C++ code")

    def test_percent_and_stage_cues(self):
        self.assertEqual(clean_for_speech("(whispers) Holding at 80%?"), "Holding at 80 percent?")


class PronunciationTests(unittest.TestCase):
    def test_whole_words_any_case(self):
        table = [("Nguyen", "Win"), ("SQL", "sequel")]
        self.assertEqual(apply_pronunciations("Mr nguyen loves SQL and sql.", table), "Mr Win loves sequel and sequel.")
        self.assertEqual(apply_pronunciations("NguyenCo and MySQL stay.", table), "NguyenCo and MySQL stay.")

    def test_longest_match_and_no_chaining(self):
        table = [("New York", "the Big Apple"), ("York", "Yawk"), ("Apple", "WRONG")]
        self.assertEqual(apply_pronunciations("New York and York.", table), "the Big Apple and Yawk.")

    def test_words_with_symbols(self):
        self.assertEqual(apply_pronunciations("I write C++ daily.", [("C++", "C plus plus")]), "I write C plus plus daily.")

    def test_normalize_accepts_dicts_and_pairs_and_skips_blanks(self):
        entries = [{"word": " SQL ", "say": "sequel"}, ["GIF", "jif"], {"word": "", "say": "x"}, {"word": "y", "say": " "}]
        self.assertEqual(normalize_pronunciations(entries), [("SQL", "sequel"), ("GIF", "jif")])

    def test_normalize_rejects_bad_input(self):
        for bad in ("SQL=sequel", [{"word": 1, "say": "x"}], [["only one"]], [{"word": "w" * 61, "say": "x"}],
                    [["a", "b"]] * 501):
            with self.assertRaises(ValueError):
                normalize_pronunciations(bad)


class RepeatedNameTests(unittest.TestCase):
    """A line like "[Timmy] Timmy: hello" shouldn't read the name out loud."""
    def test_name_and_colon_after_the_tag_are_dropped(self):
        for text in ("Timmy: the hose isn't working", "timmy : the hose isn't working",
                     "**Timmy:** the hose isn't working", "**Timmy**: the hose isn't working",
                     "[Timmy] the hose isn't working", "[Timmy]: the hose isn't working"):
            self.assertEqual(drop_repeated_name("Timmy", text), "the hose isn't working", text)
        self.assertEqual(drop_repeated_name("Dr. Lee", "Dr. Lee: Sit down."), "Sit down.")

    def test_other_text_is_left_alone(self):
        for text in ("Timmy, come here.", "Timmy said hi.", "Tim: hi.", "Timmy:", "Timmy: ",
                     "[whispers] Timmy: hi.", "Timmyboy: hi."):
            self.assertEqual(drop_repeated_name("Timmy", text), text, text)


if __name__ == "__main__":
    unittest.main()

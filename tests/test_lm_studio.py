"""Quick tests for lm_studio.py. They never need the real LM Studio: a small fake
server (fake_lm_studio.py) stands in for it where a server is needed."""

import os
import sys
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import lm_studio


class LocalAddressTests(unittest.TestCase):
    def test_local_addresses_are_accepted_and_tidied(self):
        for given, expected in (("http://127.0.0.1:1234", "http://127.0.0.1:1234"),
                                ("http://localhost:1234/", "http://localhost:1234"),
                                ("http://LOCALHOST:1234/v1/", "http://localhost:1234"),
                                (" http://127.0.0.1:5000/v1 ", "http://127.0.0.1:5000")):
            self.assertEqual(lm_studio.check_local_url(given), expected, given)

    def test_other_addresses_are_refused(self):
        for bad in ("http://192.168.1.5:1234", "https://127.0.0.1:1234", "http://127.0.0.1.evil.com:1234",
                    "http://user:pw@127.0.0.1:1234", "http://127.0.0.1:1234/other", "http://127.0.0.1:99999",
                    "127.0.0.1:1234", "", None, 1234, "http://127.0.0.1:1234?x=1"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                lm_studio.check_local_url(bad)


import copy

import fake_lm_studio
from fake_lm_studio import FakeLMStudio
import shutil
import tempfile

_settings_root = None
_real_settings_dir = lm_studio.SETTINGS_DIR if hasattr(lm_studio, "SETTINGS_DIR") else None


def setUpModule():
    # Never read the user's real LM Studio settings.
    global _settings_root
    _settings_root = tempfile.mkdtemp()
    lm_studio.SETTINGS_DIR = fake_lm_studio.write_settings_folder(_settings_root)
    # Other features' tests use tiny fake stories; steady lengths are tested on purpose below.
    lm_studio.STEADY_LENGTHS = False


def tearDownModule():
    lm_studio.SETTINGS_DIR = _real_settings_dir
    lm_studio.STEADY_LENGTHS = True
    shutil.rmtree(_settings_root, ignore_errors=True)


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeLMStudio()
        self.url = self.fake.start()

    def tearDown(self):
        self.fake.stop()

    def test_only_loaded_text_models_are_listed(self):
        self.fake.models += [copy.deepcopy(fake_lm_studio.EMBEDDING), copy.deepcopy(fake_lm_studio.IMAGE),
                             copy.deepcopy(fake_lm_studio.THINKER)]
        models = lm_studio.get_status(self.url)["models"]
        self.assertEqual([m["key"] for m in models], ["fablewright-12b-v1.1", "ponder-9b"])
        self.assertEqual(models[0], {"key": "fablewright-12b-v1.1", "display_name": "Fablewright 12B v1.1",
                                     "context_length": 12288, "can_disable_thinking": False,
                                     "publisher": "ExampleLabs", "quantization": "Q6_K",
                                     "base_key": "fablewright-12b-v1.1", "thinks": False, "ready": True})
        self.assertTrue(models[1]["can_disable_thinking"])
        self.assertTrue(models[1]["thinks"], "a model that can reason gets room to think")

    def test_base_key_drops_the_file_part(self):
        self.fake.models = [fake_lm_studio._model("fablewright-12b-v1.1@q6_k", "Fablewright 12B v1.1", loaded=True,
                                                  publisher="ExampleLabs", quantization="Q6_K")]
        model = lm_studio.get_status(self.url)["models"][0]
        self.assertEqual((model["key"], model["base_key"]), ("fablewright-12b-v1.1@q6_k", "fablewright-12b-v1.1"))

    def test_no_model_loaded_gives_an_empty_list(self):
        self.fake.models = [copy.deepcopy(fake_lm_studio.UNLOADED)]
        self.assertEqual(lm_studio.get_status(self.url), {"models": []})

    def test_not_running_is_explained(self):
        url = fake_lm_studio.free_port_url()
        with self.assertRaises(lm_studio.LMStudioError) as ctx:
            lm_studio.get_status(url)
        self.assertIn("isn't answering at 127.0.0.1:", str(ctx.exception))
        self.assertIn("Developer tab", str(ctx.exception))

    def test_old_lm_studio_is_explained(self):
        self.fake.models = "not a list"
        with self.assertRaises(lm_studio.LMStudioError) as ctx:
            lm_studio.get_status(self.url)
        self.assertIn("LM Studio 0.4 or newer", str(ctx.exception))


import json as _json

MODEL = {"key": "fablewright-12b-v1.1", "display_name": "Fablewright 12B v1.1",
         "context_length": 12288, "can_disable_thinking": False}


class RequestTests(unittest.TestCase):
    def test_request_uses_the_fixed_format(self):
        body = lm_studio.build_request("write", "A fox meets an owl.", MODEL,
                                       [{"name": "Fox", "voice_type": "Child"}], "Be kind.")
        self.assertEqual(body["model"], "fablewright-12b-v1.1")
        self.assertTrue(body["stream"])
        self.assertEqual(body["response_format"]["type"], "json_schema")
        self.assertEqual(body["response_format"]["json_schema"]["schema"], lm_studio.SCRIPT_SCHEMA)
        self.assertEqual(body["messages"][0], {"role": "system", "content": "Be kind."})
        user = body["messages"][1]["content"]
        self.assertIn("Fox (Child / Youth)", user)
        self.assertIn("Narrator", user)
        self.assertIn("A fox meets an owl.", user)
        self.assertLessEqual(body["max_tokens"], 4096)

    def test_write_and_continue_mention_the_cues(self):
        for mode in ("write", "continue"):
            user = lm_studio.build_messages(mode, "A fox.", [], "x", True, "short")[1]["content"]
            self.assertIn(lm_studio.CUE_NOTE, user, mode)
        self.assertIn("(whispers)", lm_studio.CUE_NOTE)
        self.assertIn("(quickly)", lm_studio.CUE_NOTE)
        self.assertIn("[pause 1.5s]", lm_studio.CUE_NOTE)
        self.assertIn("sparingly", lm_studio.CUE_NOTE)

    def test_split_does_not_mention_the_cues(self):
        user = lm_studio.build_messages("split", "Hello.", [], "x", True)[1]["content"]
        self.assertNotIn("whispers", user)
        self.assertNotIn("pause", user)

    def test_default_write_instructions_allow_the_cues(self):
        import json
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "voice_config.json")
        with open(path, encoding="utf-8") as f:
            write = json.load(f)["llm_defaults"]["instructions"]["write"]
        self.assertIn("Don't use stage directions other than the delivery cues listed in the request", write)

    def test_plain_request_asks_for_tagged_lines(self):
        body = lm_studio.build_request("split", "Hello.", MODEL, [], "Split.", structured=False)
        self.assertNotIn("response_format", body)
        self.assertIn("[Narrator]", body["messages"][1]["content"])

    def test_too_long_text_is_refused_before_sending(self):
        small = dict(MODEL, context_length=2000)
        with self.assertRaises(lm_studio.LMStudioError) as ctx:
            lm_studio.build_request("split", "word " * 3000, small, [], "Split.")
        self.assertIn("too long for Fablewright 12B v1.1", str(ctx.exception))
        self.assertIn("2,000", str(ctx.exception))

    def test_thinking_models_get_extra_room(self):
        big = dict(MODEL, context_length=32768)
        plain = lm_studio.build_request("write", "A story.", big, [], "x", length="short")["max_tokens"]
        thinker = lm_studio.build_request("write", "A story.", dict(big, thinks=True), [], "x",
                                          length="short")["max_tokens"]
        self.assertEqual(thinker, plain + lm_studio.THINKING_ROOM)
        split = lm_studio.build_request("split", "Hi.", dict(big, thinks=True), [], "x")["max_tokens"]
        self.assertEqual(split, lm_studio.build_request("split", "Hi.", big, [], "x")["max_tokens"] + lm_studio.THINKING_ROOM)
        small = dict(MODEL, context_length=4096, thinks=True)
        body = lm_studio.build_request("write", "A story.", small, [], "x", length="short")
        prompt = sum(lm_studio.estimate_tokens(m["content"]) for m in body["messages"])
        self.assertLessEqual(body["max_tokens"], 4096 - prompt, "still fits the model's context")

    def test_split_answer_room_grows_with_the_text(self):
        short = lm_studio.build_request("split", "Hi.", MODEL, [], "Split.")["max_tokens"]
        long = lm_studio.build_request("split", "word " * 1000, MODEL, [], "Split.")["max_tokens"]
        self.assertLess(short, long)


def long_text(paragraphs=30, words=150):
    """Paragraphs of numbered words, so order and loss are easy to see."""
    n = iter(range(1, paragraphs * words + 1))
    return "\n\n".join(" ".join(f"w{next(n)}" for _ in range(words)) + "." for _ in range(paragraphs))


class PieceTests(unittest.TestCase):
    def test_a_text_that_fits_is_one_piece(self):
        self.assertEqual(lm_studio.split_pieces("One. Two.\n\nThree.", 500), ["One. Two.\n\nThree."])

    def test_pieces_keep_every_word_in_order_and_end_at_paragraphs(self):
        text = long_text()
        pieces = lm_studio.split_pieces(text, 500)
        self.assertGreater(len(pieces), 1)
        self.assertEqual(lm_studio._words(" ".join(pieces)), lm_studio._words(text))
        paragraphs = text.split("\n\n")
        for piece in pieces:
            self.assertLessEqual(lm_studio.estimate_tokens(piece), 500)
            self.assertTrue(all(p in paragraphs for p in piece.split("\n\n")), "whole paragraphs only")

    def test_one_long_paragraph_is_cut_at_sentence_ends(self):
        text = " ".join(f"Sentence number {i} is here." for i in range(200))
        pieces = lm_studio.split_pieces(text, 100)
        self.assertEqual(lm_studio._words(" ".join(pieces)), lm_studio._words(text))
        for piece in pieces:
            self.assertLessEqual(lm_studio.estimate_tokens(piece), 100)
            self.assertTrue(piece.endswith("here."), piece[-30:])

    def test_text_without_sentence_ends_is_cut_at_spaces(self):
        text = " ".join(["word"] * 2000)
        pieces = lm_studio.split_pieces(text, 100)
        self.assertEqual(lm_studio._words(" ".join(pieces)), lm_studio._words(text))
        self.assertTrue(all(lm_studio.estimate_tokens(p) <= 100 for p in pieces))


class LongSplitTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeLMStudio()
        self.url = self.fake.start()
        self.cancel = threading.Event()
        self.events = []
        self.text = long_text()
        model = dict(lm_studio._find_loaded_model(self.url, "fablewright-12b-v1.1"))
        self.pieces = lm_studio.split_pieces(self.text, lm_studio.piece_tokens(model, "Split.", "lmstudio"))

    def tearDown(self):
        self.fake.stop()

    def reply(self, piece, cast=()):
        return Reply(content=_json.dumps({"title": "Long", "cast": list(cast),
                                          "lines": [{"speaker": "Narrator", "text": piece}]}))

    def split(self):
        return lm_studio.write_script(self.url, "fablewright-12b-v1.1", "split", self.text, [], "Split.", 30,
                                      self.cancel, on_progress=self.events.append)

    def test_a_long_text_is_split_piece_by_piece(self):
        self.assertGreaterEqual(len(self.pieces), 2)
        first, rest = self.pieces[0].split(" ", 1)
        self.fake.replies += [Reply(content=_json.dumps({"title": "Long", "cast": [{"name": "Fox", "voice_type": "Male"}],
                                                         "lines": [{"speaker": "Fox", "text": first},
                                                                   {"speaker": "Narrator", "text": rest}]}))]
        self.fake.replies += [self.reply(p) for p in self.pieces[1:]]
        result = self.split()
        self.assertEqual(len(self.fake.requests), len(self.pieces))
        self.assertEqual(result["word_differences"], [])
        self.assertEqual(result["length_note"], f"Split in {len(self.pieces)} parts.")
        self.assertIn("Fox", self.fake.requests[1]["messages"][1]["content"], "later parts know the speakers found")
        self.assertIn(self.pieces[1], self.fake.requests[1]["messages"][1]["content"])
        parts = [(e.get("piece"), e.get("pieces")) for e in self.events if e.get("piece")]
        self.assertEqual(parts[-1], (len(self.pieces), len(self.pieces)))
        self.assertEqual(self.events[-1]["words"], len(lm_studio._words(self.text)), "the counter adds up the parts")

    def test_stop_after_the_first_part_keeps_the_rest_unchanged(self):
        self.fake.replies += [self.reply(self.pieces[0]), Reply(content="{}", delay=10)]
        threading.Timer(1.5, self.cancel.set).start()
        result = self.split()
        self.assertTrue(result["stopped_early"])
        self.assertEqual(result["word_differences"], [], "no words are lost")
        self.assertTrue(result["length_note"].startswith(f"Stopped after part 1 of {len(self.pieces)}"),
                        result["length_note"])
        self.assertTrue(result["script"].splitlines()[-1].startswith("[Narrator] "))

    def test_a_later_part_failing_keeps_the_rest_unchanged(self):
        self.fake.replies += [self.reply(self.pieces[0]), Reply(status=500, raw='{"error": "boom"}')]
        result = self.split()
        self.assertEqual(result["word_differences"], [])
        self.assertTrue(result["length_note"].startswith(f"Couldn't split part 2 of {len(self.pieces)}"),
                        result["length_note"])
        self.assertTrue(result["stopped_early"], "ended early: a warning, and no audio by itself")

    def test_stop_in_the_first_part_stops_as_before(self):
        self.fake.replies += [Reply(content="{}", delay=10)]
        threading.Timer(1.0, self.cancel.set).start()
        with self.assertRaises(lm_studio.Cancelled):
            self.split()


class ContinueTests(unittest.TestCase):
    STORY = "[Narrator] Chapter 1. Night fell.\n[Fox] Hello, Owl."

    def test_next_chapter(self):
        self.assertEqual(lm_studio.next_chapter("[Narrator] Night fell."), 2)
        self.assertEqual(lm_studio.next_chapter("[Narrator] Chapter 1. Night fell."), 2)
        self.assertEqual(lm_studio.next_chapter("[Narrator] CHAPTER three. Rain."), 4)
        self.assertEqual(lm_studio.next_chapter("Chapter 2.\nChapter 7.\nChapter 5."), 8, "the highest wins")
        self.assertEqual(lm_studio.next_chapter("The next chapter of her life began."), 2,
                         "a word after 'chapter' that isn't a number is ignored")

    def test_next_chapter_reads_headings_only(self):
        self.assertEqual(lm_studio.next_chapter("[Narrator] Chapter Twenty-One. Snow."), 22)
        self.assertEqual(lm_studio.next_chapter("[Narrator] Chapter twenty one: Snow."), 22)
        self.assertEqual(lm_studio.next_chapter("[Narrator] Chapter IV. Snow."), 5)
        self.assertEqual(lm_studio.next_chapter("[Narrator] Chapter 3\n[Fox] Hi."), 4)
        self.assertEqual(lm_studio.next_chapter("[Narrator] Chapter 2. She read the chapter three times."), 3,
                         "'chapter three times' is not a heading")
        self.assertEqual(lm_studio.next_chapter("[Narrator] Chapter 2. I liked chapter 9 best."), 3)
        self.assertEqual(lm_studio.next_chapter("[Narrator] Chapter I. Snow."), 2)
        self.assertEqual(lm_studio.next_chapter("Chapter Three\nEnd."), 4, "the next line isn't part of the number")
        self.assertEqual(lm_studio.next_chapter("Chapter 1. She read chapter five, then slept."), 2,
                         "a comma means it's inside a sentence")

    def test_continue_messages(self):
        user = lm_studio.build_messages("continue", self.STORY, [{"name": "Fox", "voice_type": "Female"}],
                                        "Be kind.", True, "short", request="The owl finds a key.")[1]["content"]
        self.assertIn("Here is the story so far:\n<<<\n" + self.STORY + "\n>>>", user)
        self.assertIn('Start with a Narrator line that says "Chapter 2."', user)
        self.assertIn("What should happen next: The owl finds a key.", user)
        self.assertIn("about 300 words", user)
        self.assertIn("Fox (Female)", user)
        without = lm_studio.build_messages("continue", self.STORY, [], "x", True, "short")[1]["content"]
        self.assertNotIn("What should happen next", without)

    def test_continue_is_treated_like_write(self):
        big = dict(MODEL, context_length=32768)
        write = lm_studio.build_request("write", "A story.", big, [], "x", length="short", params={"top_p": 0.9})
        cont = lm_studio.build_request("continue", "A story.", big, [], "x", length="short", params={"top_p": 0.9})
        self.assertEqual(cont["max_tokens"], write["max_tokens"])
        self.assertEqual((cont["temperature"], cont["top_p"]), (write["temperature"], 0.9))
        thinker = lm_studio.build_request("continue", "A story.", dict(big, thinks=True), [], "x", length="short")
        self.assertEqual(thinker["max_tokens"], cont["max_tokens"] + lm_studio.THINKING_ROOM)


class StructuredReplyTests(unittest.TestCase):
    def reply(self, **parts):
        data = {"title": "T", "cast": [], "lines": []}
        data.update(parts)
        return _json.dumps(data)

    def test_lines_become_a_tagged_script(self):
        result = lm_studio.parse_structured_reply(self.reply(
            cast=[{"name": "Tom", "voice_type": "Child / Youth"}],
            lines=[{"speaker": "narrator", "text": "It was late."},
                   {"speaker": "Tom", "text": "Who's\nthere?"}]))
        self.assertEqual(lm_studio.lines_to_script(result["lines"]),
                         "[Narrator] It was late.\n[Tom] Who's there?")
        self.assertEqual(lm_studio.script_cast(result["lines"], result["cast"]),
                         [{"name": "Tom", "voice_type": "Child"}])

    def test_odd_speaker_names_are_tidied(self):
        self.assertEqual(lm_studio.clean_speaker("[Tom]"), "Tom")
        self.assertEqual(lm_studio.clean_speaker("NARRATOR"), "Narrator")
        self.assertEqual(lm_studio.clean_speaker("Old\nMan"), "Old Man")
        self.assertEqual(len(lm_studio.clean_speaker("x" * 200)), 60)
        self.assertEqual(lm_studio.clean_speaker("  "), "")

    def test_unknown_cast_member_has_no_voice_type(self):
        lines = [{"speaker": "Owl", "text": "Hoo."}]
        self.assertEqual(lm_studio.script_cast(lines, []), [{"name": "Owl", "voice_type": None}])

    def test_unusable_answers_are_rejected(self):
        for bad in ("not json", "[]", self.reply(), self.reply(lines=[{"speaker": "Tom", "text": "  "}]),
                    self.reply(lines=[{"speaker": "", "text": "Hi"}]), self.reply(lines="Tom: hi")):
            with self.assertRaises(ValueError, msg=bad):
                lm_studio.parse_structured_reply(bad)

    def test_speaker_name_repeated_in_the_text_is_dropped(self):
        result = lm_studio.parse_structured_reply(self.reply(
            lines=[{"speaker": "Timmy", "text": "Timmy: The hose isn't working."}]))
        self.assertEqual(result["lines"], [{"speaker": "Timmy", "text": "The hose isn't working."}])

    def test_thinking_and_fences_around_json_are_ignored(self):
        content = "<think>plan it</think>\n```json\n" + self.reply(lines=[{"speaker": "Tom", "text": "Hi."}]) + "\n```"
        self.assertEqual(lm_studio.parse_structured_reply(content)["lines"], [{"speaker": "Tom", "text": "Hi."}])


    def test_cut_off_answer_keeps_its_complete_lines(self):
        cut = ('{"title": "Fox", "cast": [], "lines": [{"speaker": "Narrator", "text": "Night \\"fell\\"."}, '
               '{"speaker": "Fox", "text": "Fox: Hello, Owl."}, {"speaker": "Owl", "text": "Who goes th')
        self.assertEqual(lm_studio.salvage_lines(cut), [{"speaker": "Narrator", "text": 'Night "fell".'},
                                                        {"speaker": "Fox", "text": "Hello, Owl."}])
        self.assertEqual(lm_studio.salvage_lines('{"title": "Fox", "lin'), [])
        self.assertEqual(lm_studio.salvage_lines(None), [])


class CountWordsTests(unittest.TestCase):
    def test_words_are_counted_without_the_format_names(self):
        answer = '{"title": "Fox", "lines": [{"speaker": "Narrator", "text": "Night fell, didn\'t it?"}]}'
        self.assertEqual(lm_studio.count_words(answer), 6)  # Fox Narrator Night fell didn't it

    def test_a_half_written_word_is_not_counted_yet(self):
        # "Narr" may still become "Narrator", and "spea" may become the format name "speaker",
        # so only finished words count; that way the counter never goes down.
        self.assertEqual(lm_studio.count_words('{"lines": [{"speaker": "Narr'), 0)
        self.assertEqual(lm_studio.count_words('{"lines": [{"spea'), 0)
        self.assertEqual(lm_studio.count_words('{"lines": [{"speaker": "Narrator", "te'), 1)
        self.assertEqual(lm_studio.count_words(""), 0)


class WordCheckTests(unittest.TestCase):
    def test_exact_copy_passes(self):
        lines = [{"speaker": "Narrator", "text": "It was late."}, {"speaker": "Tom", "text": "Who’s there?"}]
        self.assertEqual(lm_studio.word_check('It was late. "Who\'s there?"', lines), [])

    def test_changes_are_reported(self):
        lines = [{"speaker": "Narrator", "text": "It was very late."},
                 {"speaker": "Tom", "text": "Who is there?"}]
        diffs = lm_studio.word_check("It was late. Who's there? he said.", lines)
        kinds = [(d["kind"], d["original"], d["result"]) for d in diffs]
        self.assertIn(("added", "", "very"), kinds)
        self.assertIn(("changed", "who's", "who is"), kinds)
        self.assertIn(("dropped", "he said", ""), kinds)

    def test_name_colon_in_the_original_is_just_words(self):
        lines = [{"speaker": "Narrator", "text": "Note: the end."}]
        self.assertEqual(lm_studio.word_check("Note: the end.", lines), [])


import threading
import time

from fake_lm_studio import Reply

GOOD = _json.dumps({"title": "Fox", "cast": [{"name": "Fox", "voice_type": "Female"}],
                    "lines": [{"speaker": "Narrator", "text": "Night fell."},
                              {"speaker": "Fox", "text": "Hello, Owl."}]})


class WriteScriptTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeLMStudio()
        self.url = self.fake.start()
        self.cancel = threading.Event()

    def tearDown(self):
        self.fake.stop()

    def write(self, mode="write", text="A fox and an owl.", timeout=30, model="fablewright-12b-v1.1", on_progress=None):
        return lm_studio.write_script(self.url, model, mode, text, [], "Be kind.", timeout, self.cancel,
                                      on_progress=on_progress)

    def test_success(self):
        self.fake.replies.append(Reply(content=GOOD))
        result = self.write()
        self.assertEqual(result["script"], "[Narrator] Night fell.\n[Fox] Hello, Owl.")
        self.assertEqual(result["cast"], [{"name": "Fox", "voice_type": "Female"}])
        self.assertFalse(result["used_fallback"])
        self.assertEqual(result["word_differences"], [])
        self.assertEqual(result["warning"], "")
        self.assertEqual(self.fake.requests[0]["model"], "fablewright-12b-v1.1")
        self.assertTrue(self.fake.requests[0]["stream"])

    def test_split_reports_word_differences(self):
        self.fake.replies.append(Reply(content=GOOD))
        result = self.write(mode="split", text="Night fell. Hello, Owl, said Fox.")
        self.assertEqual([d["kind"] for d in result["word_differences"]], ["dropped"])

    def test_cut_short_answer_warns(self):
        self.fake.replies.append(Reply(content=GOOD, finish_reason="length"))
        self.assertIn("cut short", self.write()["warning"])

    def test_unloaded_model_is_refused_without_loading_it(self):
        with self.assertRaises(lm_studio.LMStudioError) as ctx:
            self.write(model="sparrow-12b-it")
        self.assertIn("is no longer loaded", str(ctx.exception))
        self.assertEqual(self.fake.requests, [], "nothing sent, so LM Studio never loads it")

    def test_not_running(self):
        self.url = fake_lm_studio.free_port_url()
        with self.assertRaises(lm_studio.LMStudioError) as ctx:
            self.write()
        self.assertIn("isn't answering", str(ctx.exception))

    def test_silence_before_the_first_word(self):
        self.fake.replies.append(Reply(content=GOOD, delay=3))
        with self.assertRaises(lm_studio.LMStudioError) as ctx:
            self.write(timeout=1)
        self.assertEqual(str(ctx.exception), "LM Studio sent nothing for 1 second, so I stopped waiting.")

    def test_silence_in_the_middle(self):
        self.fake.replies.append(Reply(content=GOOD, pieces=4, pause_after=1, pause=3))
        with self.assertRaises(lm_studio.LMStudioError) as ctx:
            self.write(timeout=1)
        self.assertIn("sent nothing for 1 second", str(ctx.exception))

    def test_slow_but_steady_answer_finishes(self):
        self.fake.replies.append(Reply(content=GOOD, pieces=8, chunk_delay=0.4))
        started = time.monotonic()
        result = self.write(timeout=1)
        self.assertGreater(time.monotonic() - started, 2, "took longer than the limit in total")
        self.assertEqual(result["script"], "[Narrator] Night fell.\n[Fox] Hello, Owl.")

    def test_many_small_pieces_give_the_same_result(self):
        self.fake.replies.append(Reply(content=GOOD, pieces=40))
        self.assertEqual(self.write()["script"], "[Narrator] Night fell.\n[Fox] Hello, Owl.")

    def test_progress_reports_growing_word_counts(self):
        reports = []
        self.fake.replies.append(Reply(content=GOOD, pieces=10, chunk_delay=0.15))
        self.write(on_progress=reports.append)
        self.assertGreaterEqual(len(reports), 2)
        words = [r["words"] for r in reports]
        self.assertEqual(words, sorted(words), "never goes down")
        self.assertGreater(words[-1], 0)
        self.assertLessEqual(words[-1], lm_studio.count_words(GOOD))
        self.assertFalse(any(r["thinking"] for r in reports))

    def test_thinking_is_reported_and_resets_the_clock(self):
        reports = []
        self.fake.replies.append(Reply(content=GOOD, thinking=4, chunk_delay=0.4))
        result = self.write(timeout=1, on_progress=reports.append)
        self.assertEqual(reports[0], {"words": 0, "thinking": True})
        self.assertFalse(reports[-1]["thinking"])
        self.assertNotIn("think", result["script"])

    def test_stop_during_streaming(self):
        self.fake.replies.append(Reply(content=GOOD, pieces=20, chunk_delay=0.5))
        threading.Timer(1.0, self.cancel.set).start()
        started = time.monotonic()
        with self.assertRaises(lm_studio.Cancelled):
            self.write()
        self.assertLess(time.monotonic() - started, 3)

    def test_answer_without_done_is_still_used(self):
        self.fake.replies.append(Reply(content=GOOD, no_done=True))
        self.assertEqual(self.write()["script"], "[Narrator] Night fell.\n[Fox] Hello, Owl.")

    def test_empty_stream_is_not_usable(self):
        self.fake.replies.append(Reply(content=""))
        with self.assertRaises(lm_studio.LMStudioError) as ctx:
            self.write()
        self.assertEqual(str(ctx.exception), lm_studio.NOTHING_USABLE)

    def test_whole_answer_when_lm_studio_does_not_stream(self):
        self.fake.replies.append(Reply(content=GOOD, ignore_stream=True))
        self.assertEqual(self.write()["script"], "[Narrator] Night fell.\n[Fox] Hello, Owl.")

    def test_odd_lines_are_skipped(self):
        self.fake.replies.append(Reply(content=GOOD, junk=True))
        self.assertEqual(self.write()["script"], "[Narrator] Night fell.\n[Fox] Hello, Owl.")

    def test_stop_ends_the_wait_at_once(self):
        self.fake.replies.append(Reply(content=GOOD, delay=10))
        threading.Timer(0.3, self.cancel.set).start()
        started = time.monotonic()
        with self.assertRaises(lm_studio.Cancelled):
            self.write()
        self.assertLess(time.monotonic() - started, 3)

    def test_lm_studio_error_is_explained(self):
        self.fake.replies.append(Reply(status=500, raw='{"error": "boom"}'))
        with self.assertRaises(lm_studio.LMStudioError) as ctx:
            self.write()
        self.assertIn("Developer tab", str(ctx.exception))

    def test_duration_text(self):
        self.assertEqual(lm_studio.duration_text(300), "5 minutes")
        self.assertEqual(lm_studio.duration_text(60), "1 minute")
        self.assertEqual(lm_studio.duration_text(45), "45 seconds")
        self.assertEqual(lm_studio.duration_text(1), "1 second")


def settings_model(key, publisher="test", quantization="Q4_K_M"):
    return {"key": key, "base_key": key.split("@")[0], "publisher": publisher, "quantization": quantization,
            "display_name": key, "context_length": 12288, "can_disable_thinking": False}


import ai_programs
import fake_ollama
from fake_ollama import FakeOllama


class OtherProgramTests(unittest.TestCase):
    def setUp(self):
        self.ollama = FakeOllama()
        self.ollama_url = self.ollama.start()
        self.basic = FakeLMStudio()
        self.basic_url = self.basic.start()
        self.cancel = threading.Event()

    def tearDown(self):
        self.ollama.stop()
        self.basic.stop()

    def test_write_with_ollama(self):
        self.ollama.replies.append(Reply(content=GOOD))
        result = lm_studio.write_script(self.ollama_url, "llama3.2:3b", "write", "A fox.", [], "Be kind.", 30,
                                        self.cancel, program="ollama")
        self.assertEqual(result["script"], "[Narrator] Night fell.\n[Fox] Hello, Owl.")
        self.assertIn("format", self.ollama.requests[0])

    def test_split_with_basic_mode(self):
        self.basic.replies.append(Reply(content=GOOD))
        result = lm_studio.write_script(self.basic_url, "story-model-7b", "split", "Night fell. Hello, Owl.", [],
                                        "Split.", 30, self.cancel, program="jan", context_length=8192)
        self.assertEqual(result["word_differences"], [])
        self.assertEqual(self.basic.requests[0]["model"], "story-model-7b")

    def test_other_programs_never_read_lm_studio_settings(self):
        self.basic.basic_models = ["fablewright-12b-v1.1"]  # same name as a model with a saved LM Studio prompt
        self.basic.replies.append(Reply(content=GOOD))
        lm_studio.write_script(self.basic_url, "fablewright-12b-v1.1", "write", "A fox.", [], "VoiceCast default.", 30,
                               self.cancel, program="other")
        self.assertEqual(self.basic.requests[0]["messages"][0]["content"], "VoiceCast default.")
        info = lm_studio.describe_model_settings(self.basic_url, "fablewright-12b-v1.1", None, "VoiceCast default.",
                                                 program="other")
        self.assertEqual((info["source"], info["lm_studio"]["note"], info["program"]),
                         ("default", "not_lm_studio", "other"))

    def test_ollama_thinking_switched_off_gets_no_thinking_room(self):
        self.ollama.replies.append(Reply(content=GOOD))
        lm_studio.write_script(self.ollama_url, "qwen3:8b", "write", "A fox.", [], "x", 30, self.cancel,
                               length="short", program="ollama")
        sent = self.ollama.requests[0]
        self.assertIs(sent["think"], False)
        self.assertLess(sent["options"]["num_predict"], lm_studio.THINKING_ROOM)

    def test_gpt_oss_gets_thinking_room(self):
        self.ollama.models = [fake_ollama.model("gpt-oss:20b", 131072, ("completion", "thinking"), family="gptoss")]
        self.ollama.replies.append(Reply(content=GOOD))
        lm_studio.write_script(self.ollama_url, "gpt-oss:20b", "write", "A fox.", [], "x", 30, self.cancel,
                               length="short", program="ollama")
        sent = self.ollama.requests[0]
        self.assertEqual(sent["think"], "low")
        self.assertGreater(sent["options"]["num_predict"], lm_studio.THINKING_ROOM)

    def test_too_long_in_basic_mode_points_at_the_text_length_setting(self):
        with self.assertRaises(lm_studio.LMStudioError) as ctx:
            lm_studio.write_script(self.basic_url, "story-model-7b", "split", "word " * 3000, [], "x", 30, self.cancel,
                                   program="jan", context_length=1024)  # too little room even for pieces
        self.assertIn("too long for story-model-7b", str(ctx.exception))
        self.assertIn("Longest text the model can handle", str(ctx.exception))
        self.assertEqual(self.basic.requests, [], "nothing sent")

    def test_too_long_for_the_models_maximum(self):
        self.ollama.models = [fake_ollama.model("tiny:1b", 1024)]  # too little room even for pieces
        with self.assertRaises(lm_studio.LMStudioError) as ctx:
            lm_studio.write_script(self.ollama_url, "tiny:1b", "split", "word " * 3000, [], "x", 30, self.cancel,
                                   program="ollama")
        self.assertIn("too long for tiny:1b", str(ctx.exception))
        self.assertIn("use a model with more room", str(ctx.exception))
        self.assertEqual(self.ollama.requests, [], "nothing sent")

    def test_ollama_room_check_counts_the_margin(self):
        model = {"key": "m", "display_name": "m", "context_length": 4096, "thinks": False}
        prompt = 4096 - lm_studio.MIN_WRITE_TOKENS - 100
        self.assertEqual(lm_studio.answer_tokens("write", "x", prompt, model, "short"), lm_studio.MIN_WRITE_TOKENS + 100)
        with self.assertRaises(lm_studio.LMStudioError):
            lm_studio.answer_tokens("write", "x", prompt, model, "short", program="ollama")
        room = lm_studio.answer_tokens("write", "x", 1000, model, "long", program="ollama")
        self.assertLessEqual(1000 + room + ai_programs.OLLAMA_MARGIN, 4096, "never more than the model holds")

    def test_model_gone_names_the_refresh_button(self):
        with self.assertRaises(lm_studio.LMStudioError) as ctx:
            lm_studio.write_script(self.ollama_url, "gone:7b", "write", "A fox.", [], "x", 30, self.cancel,
                                   program="ollama")
        self.assertEqual(str(ctx.exception), "gone:7b is no longer available. Press the refresh button next to the "
                                             "model list and pick another model.")

    def test_loading_is_reported_until_the_first_words(self):
        self.ollama.replies.append(Reply(content=GOOD, pieces=3))
        events = []
        lm_studio.write_script(self.ollama_url, "qwen3:8b", "write", "A fox.", [], "x", 30, self.cancel,
                               program="ollama", on_progress=events.append)
        self.assertTrue(events[0]["loading"], "qwen3:8b isn't loaded yet")
        self.assertFalse(events[-1]["loading"])

    def test_silence_names_the_program(self):
        self.ollama.replies.append(Reply(content=GOOD, delay=3))
        with self.assertRaises(lm_studio.LMStudioError) as ctx:
            lm_studio.write_script(self.ollama_url, "llama3.2:3b", "write", "A fox.", [], "x", 1, self.cancel,
                                   program="ollama")
        self.assertEqual(str(ctx.exception), "Ollama sent nothing for 1 second, so I stopped waiting.")


class ModelSettingsTests(unittest.TestCase):
    def find(self, *args, **kwargs):
        return lm_studio.find_model_settings(settings_model(*args, **kwargs))

    def test_the_loaded_files_settings_are_read(self):
        s = self.find("fablewright-12b-v1.1@q6_k", "ExampleLabs", "Q6_K")
        self.assertTrue(s["found"])
        self.assertEqual(s["file"], os.path.join("ExampleLabs", "Fablewright-12B-v1.1-GGUF",
                                                 "Fablewright-12B-v1.1-Q6_K.gguf.json"))
        self.assertEqual(s["system_prompt"], fake_lm_studio.FABLEWRIGHT_PROMPT)
        self.assertEqual(s["params"], {"temperature": 0.7, "top_k": 0, "top_p": 0.95,
                                       "repeat_penalty": 1.05, "stop": ["<|im_end|>"]})
        self.assertEqual(s["note"], "")

    def test_quantization_picks_the_file(self):
        self.assertEqual(self.find("fablewright-12b-v1.1@q4_k_m", "ExampleLabs", "Q4_K_M")["system_prompt"], "Q4 prompt.")

    def test_model_without_a_saved_prompt(self):
        s = self.find("ponder-9b", "lmstudio-community", "Q6_K")
        self.assertEqual((s["found"], s["system_prompt"], s["params"], s["note"]), (True, "", {}, ""))

    def test_no_settings_file(self):
        self.assertEqual(self.find("mistral-7b"), lm_studio.NO_SETTINGS)

    def test_two_possible_files_are_not_guessed(self):
        s = self.find("twin-7b", "", "Q8_0")
        self.assertEqual((s["found"], s["note"]), (False, "several"))

    def test_another_publishers_file_is_not_used(self):
        self.assertEqual(self.find("solo-5b", "elsewhere", "Q4_0")["note"], "none")

    def test_exact_name_wins_over_a_longer_one(self):
        self.assertEqual(self.find("sparrow-12b-it", "lmstudio-community", "Q6_K")["system_prompt"], "Plain.")

    def test_publisher_folder_settles_a_tie(self):
        self.assertEqual(self.find("twin-7b", "beta", "Q8_0")["system_prompt"], "B")

    def test_damaged_file(self):
        s = self.find("broken-3b", "x", "Q4_0")
        self.assertEqual((s["found"], s["note"]), (False, "unreadable"))

    def test_oversized_file_is_not_read(self):
        path = os.path.join(lm_studio.SETTINGS_DIR, "big", "Huge-1B-GGUF", "Huge-1B-Q4_0.gguf.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write('{"preset": "", "pad": "' + "x" * 300_000 + '"}')
        try:
            s = self.find("huge-1b", "big", "Q4_0")
            self.assertEqual((s["found"], s["note"]), (False, "unreadable"))
        finally:
            os.remove(path)

    def test_preset_only(self):
        s = self.find("preset-1b", "p", "Q4_0")
        self.assertEqual((s["found"], s["system_prompt"], s["note"]), (True, "", "preset"))

    def test_quantization_must_match_whole(self):
        self.assertEqual(self.find("tokens-2b", "q", "Q4_K")["note"], "none")

    def test_missing_settings_folder(self):
        real = lm_studio.SETTINGS_DIR
        lm_studio.SETTINGS_DIR = os.path.join(real, "no-such-folder")
        try:
            self.assertEqual(self.find("fablewright-12b-v1.1", "ExampleLabs", "Q6_K"), lm_studio.NO_SETTINGS)
        finally:
            lm_studio.SETTINGS_DIR = real

    def test_field_values_are_checked(self):
        fields = [
            {"key": "llm.prediction.systemPrompt", "value": "p" * 30_000},
            {"key": "llm.prediction.temperature", "value": 5},
            {"key": "llm.prediction.topPSampling", "value": {"checked": False, "value": 0.5}},
            {"key": "llm.prediction.topKSampling", "value": 2.5},
            {"key": "llm.prediction.repeatPenalty", "value": True},
            {"key": "llm.prediction.stopStrings", "value": ["s"] * 12 + ["x" * 200, 7]},
            "not a field",
        ]
        prompt, params = lm_studio._read_fields({"operation": {"fields": fields}})
        self.assertEqual(len(prompt), 20_000)
        self.assertEqual(params, {"stop": ["s"] * 10})
        self.assertEqual(lm_studio._read_fields({"operation": "nonsense"}), ("", {}))


class WriteInstructionsTests(unittest.TestCase):
    LM = {"found": True, "file": "f", "system_prompt": "LM prompt.", "params": {"temperature": 0.7}, "note": ""}

    def test_which_instructions_are_used(self):
        choose = lm_studio.choose_write_instructions
        note = "\n\n" + lm_studio.FORMAT_NOTE
        self.assertEqual(choose(self.LM, {"text": "Mine.", "use_own": True}, "Default."), ("Mine." + note, "own"))
        self.assertEqual(choose(self.LM, {"text": "Mine.", "use_own": False}, "Default."), ("LM prompt." + note, "lm_studio"))
        self.assertEqual(choose(self.LM, {"text": "  ", "use_own": True}, "Default."), ("LM prompt." + note, "lm_studio"))
        self.assertEqual(choose(lm_studio.NO_SETTINGS, {"text": "Mine.", "use_own": False}, "Default."),
                         ("Mine." + note, "own"))
        self.assertEqual(choose(lm_studio.NO_SETTINGS, None, "Default."), ("Default.", "default"))

    def test_length_sets_the_words_and_answer_room(self):
        big = dict(MODEL, context_length=32768)
        for length, words, tokens in (("short", "300", 1262), ("medium", "800", 2512), ("long", "2,000", 5512)):
            body = lm_studio.build_request("write", "A story.", big, [], "Be kind.", length=length)
            self.assertIn(f"Length: about {words} words", body["messages"][1]["content"])
            self.assertEqual(body["max_tokens"], tokens, length)

    def test_long_is_capped_by_the_context(self):
        body = lm_studio.build_request("write", "A story.", dict(MODEL, context_length=3000), [], "Be kind.", length="long")
        self.assertGreaterEqual(body["max_tokens"], 1024)
        self.assertLess(body["max_tokens"], 3000)

    def test_long_prompt_reduces_the_room(self):
        body = lm_studio.build_request("write", "A story.", MODEL, [], "p" * 4_000, length="long")
        self.assertGreaterEqual(body["max_tokens"], 1024)
        self.assertLessEqual(body["max_tokens"], 5512)

    def test_lm_studio_settings_go_into_write_only(self):
        params = {"temperature": 0.7, "top_p": 0.95, "top_k": 0, "repeat_penalty": 1.05, "stop": ["<|im_end|>"]}
        write = lm_studio.build_request("write", "A story.", MODEL, [], "x", params=params)
        for key, value in params.items():
            self.assertEqual(write[key], value, key)
        split = lm_studio.build_request("split", "Hi.", MODEL, [], "x", params=params)
        self.assertEqual(split["temperature"], 0.1)
        self.assertNotIn("top_p", split)
        self.assertNotIn("Length:", split["messages"][1]["content"])

    def test_unknown_length_means_medium(self):
        body = lm_studio.build_request("write", "A story.", dict(MODEL, context_length=32768), [], "x", length="epic")
        self.assertEqual(body["max_tokens"], 2512)


class ModelSettingsInRequestTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeLMStudio()
        self.url = self.fake.start()

    def tearDown(self):
        self.fake.stop()

    def write(self, mode="write", own=None, length="medium"):
        self.fake.replies.append(Reply(content=GOOD))
        lm_studio.write_script(self.url, "fablewright-12b-v1.1", mode, "A fox. Hello, Owl.", [],
                               "Default.", 30, threading.Event(), length=length, own=own)
        return self.fake.requests[-1]

    def test_write_uses_the_models_lm_studio_prompt_and_settings(self):
        sent = self.write(length="long")
        self.assertEqual(sent["messages"][0]["content"], fake_lm_studio.FABLEWRIGHT_PROMPT + "\n\n" + lm_studio.FORMAT_NOTE)
        self.assertEqual((sent["temperature"], sent["top_p"], sent["top_k"], sent["repeat_penalty"], sent["stop"]),
                         (0.7, 0.95, 0, 1.05, ["<|im_end|>"]))
        self.assertEqual(sent["max_tokens"], 5512)

    def test_own_instructions_win_when_chosen(self):
        sent = self.write(own={"text": "Mine.", "use_own": True})
        self.assertEqual(sent["messages"][0]["content"], "Mine.\n\n" + lm_studio.FORMAT_NOTE)
        self.assertEqual(sent["temperature"], 0.7, "LM Studio settings still apply")

    def test_split_ignores_lm_studio(self):
        sent = self.write(mode="split", own={"text": "Mine.", "use_own": True})
        self.assertEqual(sent["messages"][0]["content"], "Default.")
        self.assertEqual(sent["temperature"], 0.1)
        self.assertNotIn("top_p", sent)

    def test_continue_uses_the_models_lm_studio_prompt_and_settings(self):
        self.fake.replies.append(Reply(content=GOOD))
        result = lm_studio.write_script(self.url, "fablewright-12b-v1.1", "continue", "[Narrator] Chapter 1. Night.",
                                        [], "Default.", 30, threading.Event(), length="long",
                                        request="Snow falls.")
        sent = self.fake.requests[-1]
        self.assertEqual(sent["messages"][0]["content"], fake_lm_studio.FABLEWRIGHT_PROMPT + "\n\n" + lm_studio.FORMAT_NOTE)
        self.assertEqual(sent["temperature"], 0.7)
        self.assertEqual(sent["max_tokens"], 5512)
        self.assertIn("What should happen next: Snow falls.", sent["messages"][1]["content"])
        self.assertEqual(result["word_differences"], [], "no word check for Continue")

    def test_describe_model_settings(self):
        info = lm_studio.describe_model_settings(self.url, "fablewright-12b-v1.1", None, "Default.")
        self.assertEqual((info["source"], info["quantization"], info["display_name"]),
                         ("lm_studio", "Q6_K", "Fablewright 12B v1.1"))
        self.assertEqual(info["own"], {"text": "", "use_own": False})
        self.assertEqual(info["lm_studio"]["system_prompt"], fake_lm_studio.FABLEWRIGHT_PROMPT)
        self.fake.models = [copy.deepcopy(fake_lm_studio.THINKER)]
        info = lm_studio.describe_model_settings(self.url, "ponder-9b", {"text": "Rhyme.", "use_own": False}, "D.")
        self.assertEqual((info["source"], info["lm_studio"]["system_prompt"]), ("own", ""))
        with self.assertRaises(lm_studio.LMStudioError):
            lm_studio.describe_model_settings(self.url, "sparrow-12b-it", None, "D.")


SAMPLE_REPLIES = {
    "colon_tags": ("Narrator: The wind howled.\nTom: Who's there?\nGrandma: Just the wind, dear.",
                   [("Narrator", "The wind howled."), ("Tom", "Who's there?"), ("Grandma", "Just the wind, dear.")]),
    "bold_tags": ("**Narrator:** The wind howled.\n**Tom**: Who's there?",
                  [("Narrator", "The wind howled."), ("Tom", "Who's there?")]),
    "think_block": ("<think>The user wants a story. Let me plan.\n[Tom] not this</think>\n[Narrator] Snow fell.\n[Tom] Brr.",
                    [("Narrator", "Snow fell."), ("Tom", "Brr.")]),
    "think_open_tag_in_prompt": ("Planning the story...\n</think>\n[Narrator] Snow fell.",
                                 [("Narrator", "Snow fell.")]),
    "missing_tags": ("Once upon a time.\nThere was a fox.\n[Fox] Hello!\nShe waved.",
                     [("Narrator", "Once upon a time."), ("Narrator", "There was a fox."), ("Fox", "Hello!"),
                      ("Fox", "She waved.")]),
    "blank_lines_and_fences": ("```\n\n[Narrator] One.\n\n\n[Owl]   Two.  \n```\n",
                               [("Narrator", "One."), ("Owl", "Two.")]),
    "mixed": ("<think>hmm</think>\nHere is your script:\n```text\n**NARRATOR:** Rain.\nTom: Hi!\n\n[tom] Bye.\n```",
              [("Narrator", "Here is your script:"), ("Narrator", "Rain."), ("Tom", "Hi!"), ("tom", "Bye.")]),
}


class PlainReplyTests(unittest.TestCase):
    def test_sample_replies(self):
        for name, (reply, expected) in SAMPLE_REPLIES.items():
            got = [(l["speaker"], l["text"]) for l in lm_studio.clean_plain_reply(reply)]
            self.assertEqual(got, expected, name)

    def test_speaker_name_repeated_after_the_tag_is_dropped(self):
        got = lm_studio.clean_plain_reply("[Timmy] Timmy: The hose isn't working.\n**Ann:** Ann: Fix it.")
        self.assertEqual([(l["speaker"], l["text"]) for l in got],
                         [("Timmy", "The hose isn't working."), ("Ann", "Fix it.")])

    def test_sentences_with_colons_are_not_speakers(self):
        got = lm_studio.clean_plain_reply("[Narrator] He said this: run.\nThe time was 10:30 exactly.")
        self.assertEqual([l["speaker"] for l in got], ["Narrator", "Narrator"])

    def test_nothing_usable(self):
        self.assertEqual(lm_studio.clean_plain_reply("<think>only thinking</think>\n\n```\n```"), [])

    def test_start_of_line_cues_are_not_speakers(self):
        got = lm_studio.clean_plain_reply("[Tom] Hi.\n[whispers] Over here.\n[pause 1.5s]\nIt was dark.")
        self.assertEqual([(l["speaker"], l["text"]) for l in got],
                         [("Tom", "Hi."), ("Tom", "[whispers] Over here."), ("Tom", "[pause 1.5s]"),
                          ("Tom", "It was dark.")])


class FallbackTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeLMStudio()
        self.url = self.fake.start()

    def tearDown(self):
        self.fake.stop()

    def write(self, mode="write"):
        return lm_studio.write_script(self.url, "fablewright-12b-v1.1", mode, "A story.", [], "Be kind.", 30,
                                      threading.Event())

    def test_refused_format_falls_back_to_plain_lines(self):
        self.fake.replies += [Reply(status=400, raw='{"error": "response_format not supported"}'),
                              Reply(content="Narrator: Snow fell.\nTom: Brr.")]
        result = self.write()
        self.assertTrue(result["used_fallback"])
        self.assertEqual(result["script"], "[Narrator] Snow fell.\n[Tom] Brr.")
        self.assertNotIn("response_format", self.fake.requests[1])
        self.assertEqual(result["cast"], [{"name": "Tom", "voice_type": None}])

    def test_bad_json_falls_back(self):
        self.fake.replies += [Reply(content="{not json"), Reply(content="[Narrator] Snow fell.")]
        self.assertTrue(self.write()["used_fallback"])

    def test_empty_lines_fall_back(self):
        self.fake.replies += [Reply(content='{"title": "", "cast": [], "lines": []}'),
                              Reply(content="[Narrator] Snow fell.")]
        self.assertEqual(self.write()["script"], "[Narrator] Snow fell.")

    def test_fallback_retries_only_once(self):
        self.fake.replies += [Reply(content="{not json"), Reply(content="<think>…</think>")]
        with self.assertRaises(lm_studio.LMStudioError) as ctx:
            self.write()
        self.assertEqual(str(ctx.exception), lm_studio.NOTHING_USABLE)
        self.assertEqual(len(self.fake.requests), 2)

    def test_answers_are_kept_for_troubleshooting(self):
        self.fake.replies += [Reply(content="{not json"), Reply(content="<think>hmm</think>")]
        with self.assertRaises(lm_studio.LMStudioError):
            self.write()
        kept = lm_studio.last_answers
        self.assertEqual([a["structured"] for a in kept], [True, False])
        self.assertEqual([a["answer"] for a in kept], ["{not json", "<think>hmm</think>"])
        self.assertEqual([a["finish"] for a in kept], ["stop", "stop"])

    def test_thinking_that_runs_out_of_room_is_explained(self):
        self.fake.replies.append(Reply(content="", thinking=3, finish_reason="length"))
        with self.assertRaises(lm_studio.LMStudioError) as ctx:
            self.write()
        self.assertEqual(str(ctx.exception), lm_studio.THOUGHT_TOO_LONG)
        self.assertEqual(len(self.fake.requests), 1, "no plain-text retry: it would think just as long")
        self.assertGreater(lm_studio.last_answers[0]["thinking_chars"], 0)

    def test_thinking_that_leaves_no_answer_is_recorded(self):
        self.fake.replies.append(Reply(content="", thinking=3))
        with self.assertRaises(lm_studio.LMStudioError) as ctx:
            self.write()
        self.assertEqual(str(ctx.exception), lm_studio.NOTHING_USABLE)
        self.assertEqual(lm_studio.last_answers[0]["answer"], "")
        self.assertGreater(lm_studio.last_answers[0]["thinking_chars"], 0)

    def test_fallback_keeps_length_settings_and_the_counter(self):
        # Added after the streaming and LM Studio settings work: the second request keeps the
        # chosen length and the model's LM Studio settings, and the panel's counter keeps counting.
        self.fake.replies += [Reply(content="{not json"),
                              Reply(content="[Narrator] Snow fell on the quiet town.\n[Tom] Brr, it is cold.",
                                    pieces=8, chunk_delay=0.15)]
        reports = []
        result = lm_studio.write_script(self.url, "fablewright-12b-v1.1", "write", "A story.", [], "Be kind.", 30,
                                        threading.Event(), length="long", on_progress=reports.append)
        self.assertTrue(result["used_fallback"])
        first, second = self.fake.requests
        self.assertEqual(second["max_tokens"], first["max_tokens"], "same length")
        self.assertEqual((second["temperature"], second["top_p"]), (0.7, 0.95), "LM Studio settings kept")
        self.assertNotIn("response_format", second)
        self.assertGreater(reports[-1]["words"], 0)


class ThinkingCounterTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeLMStudio()
        self.url = self.fake.start()

    def tearDown(self):
        self.fake.stop()

    def test_thinking_inside_the_answer_is_not_counted(self):
        story = GOOD
        thinking = "<think>" + "planning the story carefully " * 20 + "</think>"
        self.fake.replies.append(Reply(content=thinking + story, pieces=30, chunk_delay=0.1))
        reports = []
        result = lm_studio.write_script(self.url, "fablewright-12b-v1.1", "write", "A story.", [], "Be kind.", 30,
                                        threading.Event(), on_progress=reports.append)
        self.assertEqual(result["script"], "[Narrator] Night fell.\n[Fox] Hello, Owl.")
        self.assertTrue(reports[0]["thinking"])
        self.assertEqual(reports[0]["words"], 0)
        self.assertLessEqual(max(r["words"] for r in reports), lm_studio.count_words(story))


class SteadyLengthRequestTests(unittest.TestCase):
    BIG = dict(MODEL, context_length=32768)

    def setUp(self):
        lm_studio.STEADY_LENGTHS = True

    def tearDown(self):
        lm_studio.STEADY_LENGTHS = False

    def user(self, body):
        return body["messages"][1]["content"]

    def test_closing_line_gives_the_range_and_overrides(self):
        for length, words in (("short", "between 250 and 400 words (aim for 300). Plan it as one scene."),
                              ("medium", "between 650 and 1,000 words (aim for 800). Plan it as 2-4 scenes."),
                              ("long", "between 1,700 and 2,400 words (aim for 2,000). Plan it as 5-8 scenes")):
            text = self.user(lm_studio.build_request("write", "A story.", self.BIG, [], "x", length=length))
            last = text.rstrip().splitlines()[-1]
            self.assertTrue(last.startswith("Length: " + words), last)
            self.assertIn("Don't finish early.", last)
            self.assertIn("overrides any other length mentioned earlier", last)
            self.assertNotIn("Length: about", text)

    def test_cap_is_twice_the_top_plus_a_little(self):
        for length, tokens in (("short", 1056), ("medium", 2256), ("long", 5056)):
            body = lm_studio.build_request("write", "A story.", self.BIG, [], "x", length=length)
            self.assertEqual(body["max_tokens"], tokens, length)
        thinker = lm_studio.build_request("write", "A story.", dict(self.BIG, thinks=True), [], "x", length="short")
        self.assertEqual(thinker["max_tokens"], 1056 + lm_studio.THINKING_ROOM)

    def test_own_length_is_spotted(self):
        for text in ("a 100-word poem", "Write 3 paragraphs", "two pages about a fox", "make it 5 minutes long",
                     "1,500 words please", "a ten line story"):
            self.assertTrue(lm_studio.own_length(text), text)
        for text in ("A fox and 3 owls.", "The year 1999.", "a story about pages and words", ""):
            self.assertFalse(lm_studio.own_length(text), text)

    def test_own_length_catches_everyday_wording(self):
        # Review fix: these were missed, so stories were topped up against the owner's own length.
        for text in ("a thousand words", "two thousand words", "two hundred and fifty words", "half a page",
                     "a page about a fox", "a short paragraph", "a few paragraphs", "a 2k-word story", "1.5k words",
                     "a couple of sentences"):
            self.assertTrue(lm_studio.own_length(text), text)
        for text in ("3 friends", "a story about pages and words", "A fox and 3 owls."):
            self.assertFalse(lm_studio.own_length(text), text)

    def test_own_length_keeps_the_old_wording_and_cap(self):
        body = lm_studio.build_request("write", "A 100-word poem about rain.", self.BIG, [], "x", length="medium")
        self.assertIn("Length: about 800 words, unless the request above asks for a different length.", self.user(body))
        self.assertEqual(body["max_tokens"], 2512)
        cont = lm_studio.build_request("continue", "[Narrator] Chapter 1. Rain.", self.BIG, [], "x", length="medium",
                                       request="make it two pages")
        self.assertIn("Length: about 800 words", self.user(cont))

    def test_continue_checks_the_request_not_the_story(self):
        body = lm_studio.build_request("continue", "[Narrator] Chapter 1. It took 3 pages to say.", self.BIG, [], "x",
                                       length="short")
        self.assertIn("between 250 and 400 words", self.user(body))

    def test_split_is_never_length_checked(self):
        body = lm_studio.build_request("split", "Hi.", self.BIG, [], "x")
        self.assertNotIn("Length:", self.user(body))
        self.assertFalse(lm_studio.length_checked("split", "Hi.", "", "long"))

    def test_off_switch_keeps_todays_request(self):
        lm_studio.STEADY_LENGTHS = False
        body = lm_studio.build_request("write", "A story.", self.BIG, [], "x", length="long")
        self.assertIn("Length: about 2,000 words", self.user(body))
        self.assertEqual(body["max_tokens"], 5512)

    def test_more_and_ending_requests(self):
        more = lm_studio.build_request("more", "[Narrator] Night fell.", self.BIG, [], "x", length="long",
                                       more_words=1200)
        text = self.user(more)
        self.assertIn("<<<\n[Narrator] Night fell.\n>>>", text)
        self.assertIn("Carry on this story from exactly where it stops", text)
        self.assertIn("about 1,200 more words", text)
        self.assertIn("don't start a new chapter", text)
        self.assertEqual(more["max_tokens"], 1200 * 2 + 256)
        self.assertEqual(more["temperature"], lm_studio.WRITE_TEMPERATURE)
        ending = lm_studio.build_request("ending", "[Narrator] Night fell.", dict(self.BIG, thinks=True), [], "x")
        self.assertIn("Write only the ending", self.user(ending))
        self.assertEqual(ending["max_tokens"], lm_studio.ENDING_TOKENS + lm_studio.THINKING_ROOM)


def story(words, speaker="Narrator", title="Fox"):
    """A fixed-format answer with about `words` words, in lines of ten."""
    lines = [{"speaker": speaker, "text": " ".join(["word"] * min(10, words - i))} for i in range(0, words, 10)]
    return _json.dumps({"title": title, "cast": [], "lines": lines})


class TopUpTests(unittest.TestCase):
    def setUp(self):
        lm_studio.STEADY_LENGTHS = True
        self.fake = FakeLMStudio()
        self.url = self.fake.start()
        self.cancel = threading.Event()
        self.events = []

    def tearDown(self):
        self.fake.stop()
        lm_studio.STEADY_LENGTHS = False

    def write(self, length="medium", mode="write", text="A fox and an owl.", request=""):
        return lm_studio.write_script(self.url, "fablewright-12b-v1.1", mode, text, [], "Be kind.", 30, self.cancel,
                                      length=length, request=request, on_progress=self.events.append)

    def words(self, result):
        return len(lm_studio._words(" ".join(l.split("] ", 1)[1] for l in result["script"].splitlines())))

    def test_on_target_gets_no_top_up(self):
        self.fake.replies.append(Reply(content=story(700)))
        result = self.write()
        self.assertEqual((len(self.fake.requests), result["length_note"], result["stopped_early"]), (1, "", False))

    def test_overshoot_is_kept(self):
        self.fake.replies.append(Reply(content=story(1300)))
        result = self.write()
        self.assertEqual((len(self.fake.requests), self.words(result), result["length_note"]), (1, 1300, ""))

    def test_short_story_is_topped_up(self):
        self.fake.replies += [Reply(content=story(400)), Reply(content=story(350, speaker="Owl"))]
        result = self.write()
        self.assertEqual(len(self.fake.requests), 2)
        more = self.fake.requests[1]["messages"][1]["content"]
        self.assertIn("Carry on this story from exactly where it stops", more)
        self.assertIn("[Narrator] word", more, "the story so far is sent")
        self.assertIn("about 400 more words", more)
        self.assertEqual(self.words(result), 750)
        self.assertTrue(result["script"].endswith("[Owl] word word word word word word word word word word"))
        self.assertEqual(result["length_note"], "Topped up once to reach 750 words.")
        self.assertEqual(result["title"], "Fox")

    def test_round_limit(self):
        self.fake.replies += [Reply(content=story(100)) for _ in range(3)]
        result = self.write(length="medium")
        self.assertEqual(len(self.fake.requests), 3, "first answer + 2 top-ups for Medium")
        self.assertEqual(result["length_note"], "The model kept ending early: 300 words (Medium is about 800).")
        self.fake.requests.clear()
        self.fake.replies += [Reply(content=story(100)) for _ in range(2)]
        self.write(length="short")
        self.assertEqual(len(self.fake.requests), 2, "Short: 1 top-up")

    def test_a_round_adding_little_ends_the_top_ups(self):
        self.fake.replies += [Reply(content=story(1000)), Reply(content=story(40)), Reply(content=story(500))]
        result = self.write(length="long")
        self.assertEqual(len(self.fake.requests), 2)
        self.assertEqual(result["length_note"], "The model kept ending early: 1,040 words (Long is about 2,000).")

    def test_twice(self):
        self.fake.replies += [Reply(content=story(300)), Reply(content=story(200)), Reply(content=story(200))]
        self.assertEqual(self.write()["length_note"], "Topped up twice to reach 700 words.")

    def test_cut_off_answer_keeps_lines_and_gets_an_ending(self):
        cut = story(500)[:-60]  # unfinished JSON
        self.fake.replies += [Reply(content=cut, finish_reason="length"), Reply(content=story(60, speaker="Owl"))]
        result = self.write()
        self.assertEqual(len(self.fake.requests), 2)
        self.assertIn("Write only the ending", self.fake.requests[1]["messages"][1]["content"])
        self.assertFalse(result["used_fallback"])
        self.assertIn("[Owl]", result["script"])
        self.assertEqual((result["length_note"], result["warning"]), ("Added an ending (the model ran out of room).", ""))

    def test_a_cut_off_top_up_gets_an_ending_and_stops(self):
        # Review fix: a top-up stopped by its own room limit was reported as a success with no ending.
        self.fake.replies += [Reply(content=story(300)), Reply(content=story(500)[:-60], finish_reason="length"),
                              Reply(content=story(60, speaker="Owl"))]
        result = self.write()
        self.assertEqual(len(self.fake.requests), 3)
        self.assertIn("Write only the ending", self.fake.requests[2]["messages"][1]["content"])
        self.assertTrue(result["script"].endswith("word word word word word word word word word word"))
        self.assertIn("[Owl]", result["script"])
        self.assertIn("Added an ending", result["length_note"])
        self.assertEqual(result["warning"], "")

    def test_a_cut_off_ending_keeps_the_warning(self):
        self.fake.replies += [Reply(content=story(500)[:-60], finish_reason="length"),
                              Reply(content=story(60)[:-30], finish_reason="length")]
        result = self.write()
        self.assertEqual(len(self.fake.requests), 2)
        self.assertEqual(result["warning"], lm_studio.CUT_SHORT)
        self.assertNotIn("Added an ending (the model ran out of room).", result["length_note"])

    def test_own_length_and_split_are_never_topped_up(self):
        self.fake.replies += [Reply(content=story(50)), Reply(content=story(50))]
        self.write(text="A 50-word poem about rain.")
        self.write(mode="split", text=" ".join(["word"] * 50))
        self.assertEqual(len(self.fake.requests), 2)

    def test_continue_counts_only_the_new_chapter_and_sends_the_old_ones(self):
        old = "[Narrator] Chapter 1. " + " ".join(["old"] * 900)
        self.fake.replies += [Reply(content=story(300)), Reply(content=story(400))]
        result = self.write(mode="continue", text=old)
        self.assertEqual(len(self.fake.requests), 2, "300 new words is short even though the old chapter is long")
        more = self.fake.requests[1]["messages"][1]["content"]
        self.assertIn("old old old", more)
        self.assertIn("[Narrator] word", more)
        self.assertNotIn("old", result["script"])

    def test_progress_totals_and_rounds(self):
        self.fake.replies += [Reply(content=story(300)), Reply(content=story(500))]
        self.write()
        later = [e for e in self.events if e.get("phase") == "more"]
        self.assertTrue(later, self.events)
        self.assertEqual((later[0]["round"], later[0]["rounds"]), (1, 2))
        self.assertGreaterEqual(later[-1]["words"], 300, "the counter keeps the words already written")

    def test_stop_during_a_top_up_keeps_the_story(self):
        self.fake.replies += [Reply(content=story(300)), Reply(content=story(300), delay=10)]
        threading.Timer(1.0, self.cancel.set).start()
        result = self.write()
        self.assertTrue(result["stopped_early"])
        self.assertEqual(self.words(result), 300)
        self.assertEqual(result["length_note"], "Stopped while adding more: kept 300 words.")

    def test_a_failing_top_up_keeps_the_story(self):
        self.fake.replies += [Reply(content=story(300)), Reply(status=500, raw='{"error": "boom"}')]
        result = self.write()
        self.assertEqual(self.words(result), 300)
        self.assertTrue(result["length_note"].startswith("Couldn't add more"), result["length_note"])

    def test_a_new_round_is_reported_at_once(self):
        self.fake.replies += [Reply(content=story(300)), Reply(content=story(500))]
        self.write()
        later = [e for e in self.events if e.get("phase") == "more"]
        self.assertEqual(later[0]["words"], 300, "the counter says 'adding more' before the model's first piece")

    def test_a_failing_top_up_note_is_plain(self):
        self.fake.replies += [Reply(content=story(300)), Reply(status=500, raw='{"error": "boom"}')]
        note = self.write()["length_note"]
        self.assertTrue(note.startswith("Couldn't add more, so I kept 300 words."), note)
        self.assertNotIn("more (", note, "the error is not wrapped in brackets")

    def test_a_failing_top_up_note_ends_cleanly_after_a_question(self):
        self.fake.replies.append(Reply(content=story(300)))
        real = lm_studio._write_round
        calls = []

        def second_round_fails(*args, **kwargs):
            calls.append(1)
            if len(calls) > 1:
                raise lm_studio.LMStudioError("Is LM Studio still running?")
            return real(*args, **kwargs)

        with unittest.mock.patch.object(lm_studio, "_write_round", side_effect=second_round_fails):
            note = self.write()["length_note"]
        self.assertTrue(note.endswith("Is LM Studio still running?"), note)

    def test_a_failing_ending_says_ending(self):
        self.fake.replies += [Reply(content=story(500)[:-60], finish_reason="length"), Reply(status=500, raw='{"error": "boom"}')]
        note = self.write()["length_note"]
        self.assertTrue(note.startswith("Couldn't write an ending"), note)

    def test_stop_during_the_ending_says_ending(self):
        self.fake.replies += [Reply(content=story(500)[:-60], finish_reason="length"), Reply(content=story(60), delay=10)]
        threading.Timer(1.0, self.cancel.set).start()
        result = self.write()
        self.assertTrue(result["stopped_early"])
        self.assertTrue(result["length_note"].startswith("Stopped while writing the ending"), result["length_note"])

    def test_characters_new_in_a_top_up_get_their_type(self):
        more = _json.dumps({"title": "Fox", "cast": [{"name": "Owl", "voice_type": "Female"}],
                            "lines": [{"speaker": "Owl", "text": " ".join(["hoo"] * 10)} for _ in range(50)]})
        self.fake.replies += [Reply(content=story(300)), Reply(content=more)]
        result = self.write()
        self.assertIn({"name": "Owl", "voice_type": "Female"}, result["cast"])

    def test_a_top_up_does_not_change_a_known_characters_type(self):
        first = _json.dumps({"title": "Fox", "cast": [{"name": "Owl", "voice_type": "Female"}],
                             "lines": [{"speaker": "Owl", "text": " ".join(["hoo"] * 10)} for _ in range(30)]})
        more = _json.dumps({"title": "Fox", "cast": [{"name": "Owl", "voice_type": "Male"}],
                            "lines": [{"speaker": "Owl", "text": " ".join(["hoo"] * 10)} for _ in range(50)]})
        self.fake.replies += [Reply(content=first), Reply(content=more)]
        result = self.write()
        self.assertIn({"name": "Owl", "voice_type": "Female"}, result["cast"])

    def test_thinking_model_rounds_keep_their_room(self):
        self.fake.models.append(copy.deepcopy(fake_lm_studio.THINKER))
        self.fake.replies += [Reply(content=story(300)), Reply(content=story(500))]
        lm_studio.write_script(self.url, "ponder-9b", "write", "A fox.", [], "x", 30, self.cancel, length="medium")
        self.assertEqual(self.fake.requests[1]["max_tokens"], 500 * 2 + 256 + lm_studio.THINKING_ROOM)


class DescribeVoiceTests(unittest.TestCase):
    GOOD = _json.dumps({"gender": "male", "age": "adult", "accent": "any", "sounds": ["deep", "gruff", "booming"]})

    def setUp(self):
        self.fake = FakeLMStudio()
        self.url = self.fake.start()

    def tearDown(self):
        self.fake.stop()

    def ask(self, model="fablewright-12b-v1.1", timeout=10):
        return lm_studio.describe_voice_traits(self.url, model, "the grumpy troll under the bridge",
                                               timeout, threading.Event())

    def test_answer_becomes_traits(self):
        self.fake.replies.append(fake_lm_studio.Reply(content=self.GOOD))
        self.assertEqual(self.ask(), {"gender": "male", "age": "adult", "accent": "any",
                                      "sounds": ["deep", "gruff", "booming"], "unknown": []})
        body = self.fake.requests[0]
        self.assertEqual(body["response_format"]["json_schema"]["name"], "voicecast_voice")
        self.assertIn("grumpy troll", body["messages"][1]["content"])
        self.assertIn("booming", body["messages"][0]["content"], "the instructions list the sound words")
        self.assertLessEqual(body["max_tokens"], lm_studio.DESCRIBE_ANSWER_TOKENS)

    def test_json_inside_other_text_is_found(self):
        self.fake.replies.append(fake_lm_studio.Reply(content="<think>hmm</think>Sure!\n```json\n" + self.GOOD + "\n```"))
        self.assertEqual(self.ask()["sounds"], ["deep", "gruff", "booming"])

    def test_refused_format_is_asked_again_plainly(self):
        self.fake.replies += [fake_lm_studio.Reply(status=400, raw=_json.dumps({"error": "no json_schema"})),
                              fake_lm_studio.Reply(content=self.GOOD)]
        self.assertEqual(self.ask()["gender"], "male")
        self.assertNotIn("response_format", self.fake.requests[1])

    def test_unreadable_answer_is_an_error(self):
        self.fake.replies.append(fake_lm_studio.Reply(content="A troll sounds deep."))
        with self.assertRaises(lm_studio.LMStudioError) as ctx:
            self.ask()
        self.assertIn("couldn't be read", str(ctx.exception))

    def test_thinking_models_get_room_to_think(self):
        self.fake.models.append(copy.deepcopy(fake_lm_studio.THINKER))
        self.fake.replies.append(fake_lm_studio.Reply(content=self.GOOD))
        self.ask(model="ponder-9b")
        self.assertGreater(self.fake.requests[0]["max_tokens"], lm_studio.DESCRIBE_ANSWER_TOKENS)


if __name__ == "__main__":
    unittest.main()

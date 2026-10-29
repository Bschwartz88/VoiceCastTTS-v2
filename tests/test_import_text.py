"""Tests for import_text: tidying .txt and .md text, and fetching shared Google Docs."""

import os
import socket
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fake_google_docs
import import_text
from import_text import ImportProblem, clean_text, count_words, tidy_file, tidy_markdown


class CleanTextTests(unittest.TestCase):
    def test_removes_bom_and_windows_line_endings(self):
        self.assertEqual(clean_text("﻿One\r\nTwo\rThree"), "One\nTwo\nThree")

    def test_removes_control_characters_but_keeps_tabs(self):
        self.assertEqual(clean_text("A\x00B\x07C\tD"), "ABC\tD")


class TidyFileTests(unittest.TestCase):
    def test_txt_is_kept_as_written(self):
        text = "[Tom] *Hello* there.\n\n[Narrator] He waved."
        self.assertEqual(tidy_file("story.txt", text), text)

    def test_extension_is_case_insensitive(self):
        self.assertEqual(tidy_file("STORY.MD", "## Chapter 2"), "Chapter 2")

    def test_other_extensions_are_refused(self):
        for name in ("story.docx", "story", "story.md.exe", ""):
            with self.assertRaises(ImportProblem) as e:
                tidy_file(name, "text")
            self.assertEqual(str(e.exception), import_text.NOT_TXT_OR_MD)

    def test_lines_starting_with_hash_are_kept_readable_in_both_kinds(self):
        # The player skips lines that start with "#", so the import removes the marks.
        self.assertEqual(tidy_file("a.txt", "# Chapter 1\n#1 fan"), "Chapter 1\n1 fan")
        self.assertEqual(tidy_file("a.md", "#hashtag day"), "hashtag day")

    def test_three_or_more_blank_lines_become_one(self):
        self.assertEqual(tidy_file("a.txt", "One\n\n\n\nTwo  "), "One\n\nTwo")


class TidyMarkdownTests(unittest.TestCase):
    def check(self, source, expected):
        self.assertEqual(tidy_file("a.md", source), expected)

    def test_headings_become_plain_lines(self):
        self.check("# Title\n## Chapter 2 ##\n###### Small", "Title\nChapter 2\nSmall")

    def test_bold_and_italic_marks_are_removed(self):
        self.check("**Bold** and __strong__, *it* and _em_ and ***both***", "Bold and strong, it and em and both")

    def test_marks_inside_words_stay(self):
        self.check("snake_case and 5*3*2 and my__var", "snake_case and 5*3*2 and my__var")

    def test_links_keep_their_text_and_images_go(self):
        self.check("See [the map](https://x.example/m) ![a fox](fox.png) <https://x.example>", "See the map")

    def test_speaker_tags_and_cues_are_never_changed(self):
        self.check("[Tom] (whispers) Run. [pause 2s]\n[Narrator] **Tom:** fled.", "[Tom] (whispers) Run. [pause 2s]\n[Narrator] Tom: fled.")

    def test_code_blocks_are_dropped_and_inline_code_keeps_its_text(self):
        self.check("Before\n```\nprint('x')\n```\nUse `Generate` now\n~~~\nmore\n~~~\nAfter", "Before\nUse Generate now\nAfter")

    def test_lists_quotes_and_dividing_lines(self):
        self.check("- one\n* two\n+ three\n1. four\n2) five\n> quoted\n> - nested\n---\n***\n___", "one\ntwo\nthree\nfour\nfive\nquoted\nnested")

    def test_tables_become_plain_lines(self):
        self.check("| Name | Role |\n|---|:---:|\n| Tom | Fox |", "Name, Role\nTom, Fox")

    def test_html_tags_are_removed(self):
        self.check("Line<br>with <b>tags</b>", "Linewith tags")

    def test_front_matter_is_dropped(self):
        self.check("---\ntitle: Fox\n---\nOnce upon a time.", "Once upon a time.")

    def test_escapes_become_plain_characters(self):
        self.check("\\*not italic\\* and \\_x\\_\n\\- not a list\n\\[Tom\\] said", "*not italic* and _x_\n- not a list\n[Tom] said")

    def test_escaped_hash_at_line_start_is_still_read(self):
        self.check("\\# not a heading", "not a heading")


class AwkwardLongLinesTests(unittest.TestCase):
    """Odd or crafted long lines must not make the tidier slow (it would freeze the whole studio)."""

    N = 40_000

    def test_long_awkward_lines_are_tidied_quickly(self):
        n = self.N
        lines = {
            "heading then spaces": "# a" + " " * n + "b",
            "spaces only then x": " " * n + "x",
            "single stars": " *x" * (n // 3),
            "single underscores": " _x" * (n // 3),
            "double stars": " **x" * (n // 4),
            "double underscores": " __x" * (n // 4),
            "open brackets": "[" * n,
            "unclosed link": "[a](" * (n // 4),
            "unclosed autolinks": "<http://a" * (n // 9),
            "backticks": "`a" * (n // 2) + "``",
            "dividing-line look-alike": "- " * (n // 2) + "x",
            "table look-alike": "|" + " " * n + "x",
            "quote marks": "> " * (n // 2) + "x",
        }
        for name, line in lines.items():
            for kind in ("a.md", "a.txt"):
                start = time.perf_counter()
                tidy_file(kind, line)
                self.assertLess(time.perf_counter() - start, 1.0, f"{name} ({kind}) is slow")

    def test_speaker_tag_right_before_a_cue_is_kept(self):
        self.assertEqual(tidy_file("a.md", "[Tom](whispers) Hi"), "[Tom](whispers) Hi")


class CountWordsTests(unittest.TestCase):
    def test_counts_words(self):
        self.assertEqual(count_words("[Tom] Hello there,  friend.\nBye"), 5)
        self.assertEqual(count_words(""), 0)


class GoogleDocTests(unittest.TestCase):
    def setUp(self):
        self.google = fake_google_docs.FakeGoogle()
        self.restore = self.google.point(import_text)

    def tearDown(self):
        self.restore()
        self.google.close()

    def answer(self, status=200, body=b"# Chapter 1\n**Fox:** hello", headers=None, path=fake_google_docs.EXPORT_PATH):
        self.google.routes[path] = (status, headers or {"Content-Type": "text/markdown; charset=utf-8"}, body)

    def test_link_forms_are_accepted(self):
        doc_id = fake_google_docs.DOC_ID
        for link in (f"https://docs.google.com/document/d/{doc_id}", f"  https://docs.google.com/document/d/{doc_id}/edit?usp=sharing ",
                     f"https://docs.google.com/document/d/{doc_id}/edit#heading=h.x", f"https://docs.google.com/document/u/1/d/{doc_id}/edit"):
            self.assertEqual(import_text.parse_doc_id(link), doc_id)

    def test_other_links_are_refused(self):
        for link in ("http://docs.google.com/document/d/1AbCdEfGhIjKlMnOpQrStUvWx/edit", "https://docs.google.com.evil.example/document/d/1AbCdEfGhIjKlMnOpQrStUvWx",
                     "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWx", "https://docs.google.com/document/d/short", "not a link", ""):
            with self.assertRaises(ImportProblem) as e:
                import_text.parse_doc_id(link)
            self.assertEqual(str(e.exception), import_text.NOT_A_DOC_LINK)

    def test_fetches_and_tidies_the_doc(self):
        self.answer()
        self.assertEqual(import_text.fetch_google_doc(fake_google_docs.LINK), "Chapter 1\nFox: hello")

    def test_windows_1252_answer_keeps_its_dashes_and_quotes(self):
        self.answer(body="Fox — “owl” café".encode("cp1252"))
        self.assertEqual(import_text.fetch_google_doc(fake_google_docs.LINK), "Fox — “owl” café")

    def test_sends_no_cookies_or_account_details(self):
        self.answer()
        import_text.fetch_google_doc(fake_google_docs.LINK)
        headers = {k.lower() for k in self.google.requests[0]["headers"]}
        self.assertNotIn("cookie", headers)
        self.assertNotIn("authorization", headers)

    def test_follows_a_redirect_on_an_allowed_host(self):
        self.answer(302, b"", {"Location": "/download/1"})
        self.answer(body=b"Hello", path="/download/1")
        self.assertEqual(import_text.fetch_google_doc(fake_google_docs.LINK), "Hello")

    def test_redirect_to_sign_in_means_not_shared(self):
        self.answer(302, b"", {"Location": "https://accounts.google.com/ServiceLogin?continue=x"})
        with self.assertRaises(ImportProblem) as e:
            import_text.fetch_google_doc(fake_google_docs.LINK)
        self.assertEqual(str(e.exception), import_text.NOT_SHARED)

    def test_html_answer_means_not_shared(self):
        self.answer(body=b"<html>Sign in</html>", headers={"Content-Type": "text/html"})
        with self.assertRaises(ImportProblem) as e:
            import_text.fetch_google_doc(fake_google_docs.LINK)
        self.assertEqual(str(e.exception), import_text.NOT_SHARED)

    def test_redirect_to_another_host_is_refused_without_connecting(self):
        self.answer(302, b"", {"Location": "http://localhost:9/secret"})
        with self.assertRaises(ImportProblem) as e:
            import_text.fetch_google_doc(fake_google_docs.LINK)
        self.assertEqual(str(e.exception), import_text.UNEXPECTED)
        self.assertEqual(len(self.google.requests), 1)

    def test_too_many_redirects(self):
        self.answer(302, b"", {"Location": fake_google_docs.EXPORT_PATH})
        with self.assertRaises(ImportProblem) as e:
            import_text.fetch_google_doc(fake_google_docs.LINK)
        self.assertEqual(str(e.exception), import_text.UNEXPECTED)

    def test_not_found(self):
        with self.assertRaises(ImportProblem) as e:
            import_text.fetch_google_doc(fake_google_docs.LINK)
        self.assertEqual(str(e.exception), import_text.NOT_FOUND)

    def test_forbidden_means_not_shared(self):
        self.answer(403, b"no")
        with self.assertRaises(ImportProblem) as e:
            import_text.fetch_google_doc(fake_google_docs.LINK)
        self.assertEqual(str(e.exception), import_text.NOT_SHARED)

    def test_too_big(self):
        self.answer(body=b"x" * 200)
        saved = import_text.MAX_IMPORT_BYTES
        import_text.MAX_IMPORT_BYTES = 100
        try:
            with self.assertRaises(ImportProblem) as e:
                import_text.fetch_google_doc(fake_google_docs.LINK)
        finally:
            import_text.MAX_IMPORT_BYTES = saved
        self.assertEqual(str(e.exception), import_text.TOO_BIG)

    def test_slow_answer_gives_up(self):
        self.answer()
        self.google.delay = 2
        import_text.FETCH_TIMEOUT = 0.5  # restored by tearDown
        with self.assertRaises(ImportProblem) as e:
            import_text.fetch_google_doc(fake_google_docs.LINK)
        self.assertEqual(str(e.exception), import_text.NO_CONNECTION)

    def test_slow_trickle_gives_up_after_the_overall_limit(self):
        # Each piece arrives well within the limit, but the whole answer takes too long.
        self.answer(body=b"x" * 100)
        self.google.drip = 0.2
        import_text.FETCH_TIMEOUT = 0.5  # restored by tearDown
        start = time.perf_counter()
        with self.assertRaises(ImportProblem) as e:
            import_text.fetch_google_doc(fake_google_docs.LINK)
        self.assertEqual(str(e.exception), import_text.NO_CONNECTION)
        self.assertLess(time.perf_counter() - start, 1.5)

    def test_malformed_redirect_is_an_unexpected_answer(self):
        self.answer(302, b"", {"Location": "https://[::1"})
        with self.assertRaises(ImportProblem) as e:
            import_text.fetch_google_doc(fake_google_docs.LINK)
        self.assertEqual(str(e.exception), import_text.UNEXPECTED)

    def test_redirect_with_an_account_part_or_another_port_is_refused(self):
        port = self.google.server.server_address[1]
        for location in (f"http://someone@127.0.0.1:{port}/download/1", f"http://a:b@127.0.0.1:{port}/download/1",
                         "http://127.0.0.1:9/download/1", f"http://127.0.0.1:{port}\@x/download/1"):
            self.google.requests.clear()
            self.answer(302, b"", {"Location": location})
            self.answer(body=b"Hello", path="/download/1")
            with self.assertRaises(ImportProblem) as e:
                import_text.fetch_google_doc(fake_google_docs.LINK)
            self.assertEqual(str(e.exception), import_text.UNEXPECTED, location)
            self.assertEqual(len(self.google.requests), 1, location)

    def test_answers_that_are_not_text_are_refused(self):
        self.answer(body=b"%PDF-1.7", headers={"Content-Type": "application/pdf"})
        with self.assertRaises(ImportProblem) as e:
            import_text.fetch_google_doc(fake_google_docs.LINK)
        self.assertEqual(str(e.exception), import_text.UNEXPECTED)

    def test_nothing_listening(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            dead_port = s.getsockname()[1]
        import_text.EXPORT_URL = f"http://127.0.0.1:{dead_port}/document/d/{{id}}/export"
        with self.assertRaises(ImportProblem) as e:
            import_text.fetch_google_doc(fake_google_docs.LINK)
        self.assertEqual(str(e.exception), import_text.NO_CONNECTION)


if __name__ == "__main__":
    unittest.main()

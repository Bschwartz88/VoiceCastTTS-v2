"""Tests for reading LM Studio's saved chats (lmstudio_chats.py), with sample files in a temporary folder.

    cd tests && python -m unittest test_lmstudio_chats -v
"""

import json
import os
import sys
import tempfile
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import lmstudio_chats  # noqa: E402


def text_part(text):
    return [{"type": "text", "text": text}]


def user(text):
    return {"versions": [{"type": "singleStep", "role": "user", "content": text_part(text)}], "currentlySelected": 0}


def answer(*texts, selected=None, thinking=""):
    """An AI answer as LM Studio saves it: one version per regeneration, the shown one selected."""
    versions = []
    for text in texts:
        steps = []
        if thinking:
            steps.append({"type": "contentBlock", "style": {"type": "thinking"}, "content": text_part(thinking)})
        steps.append({"type": "contentBlock", "content": text_part(text)})
        steps.append({"type": "debugInfoBlock", "debugInfo": "Conversation naming technique: 'prompt'"})
        versions.append({"type": "multiStep", "role": "assistant", "steps": steps})
    return {"versions": versions, "currentlySelected": len(texts) - 1 if selected is None else selected}


FOX = {"name": "Fox story", "createdAt": 1_760_000_000_000, "lastUsedModel": {"identifier": "fablewright-12b-v1.1"},
       "messages": [user("Write a story about a fox."),
                    answer("<think>A fox, then.</think>[Narrator] Night fell.", thinking="Plan: a fox."),
                    user("Now the owl."),
                    answer("[Owl] Old try.", "[Owl] Hoo."),
                    user("Thanks!")]}
PLAIN = {"createdAt": 1_750_000_000_000,
         "messages": [{"role": "user", "content": "Tell me about the moon, please, in a few lines of verse"},
                      {"role": "assistant", "content": "The moon is bright."}]}
EMPTY = {"name": None, "createdAt": 1_770_000_000_000, "messages": []}


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.folder = self.dir.name
        for name, data in (("1760000000000.conversation.json", FOX), ("plain.conversation.json", PLAIN),
                           ("empty.conversation.json", EMPTY)):
            with open(os.path.join(self.folder, name), "w", encoding="utf-8") as f:
                json.dump(data, f)
        with open(os.path.join(self.folder, "broken.conversation.json"), "w", encoding="utf-8") as f:
            f.write("{ not json")
        with open(os.path.join(self.folder, "notes.txt"), "w", encoding="utf-8") as f:
            f.write("not a chat")

    def tearDown(self):
        self.dir.cleanup()

    def test_chats_with_answers_are_listed_newest_first(self):
        chats = lmstudio_chats.list_chats(self.folder)
        self.assertEqual([c["file"] for c in chats], ["1760000000000.conversation.json", "plain.conversation.json"],
                         "empty and unreadable chats are left out")
        self.assertEqual(chats[0], {"file": "1760000000000.conversation.json", "name": "Fox story",
                                    "model": "fablewright-12b-v1.1", "when": 1_760_000_000_000, "answers": 2})
        self.assertEqual(chats[1]["name"], "Tell me about the moon, please, in a few lines of…",
                         "no name: the start of the first message")
        self.assertEqual(chats[1]["model"], "")

    def test_a_missing_folder_has_no_chats(self):
        self.assertEqual(lmstudio_chats.list_chats(os.path.join(self.folder, "nope")), [])

    def test_very_big_files_are_skipped(self):
        small = os.path.getsize(os.path.join(self.folder, "plain.conversation.json"))
        with unittest.mock.patch.object(lmstudio_chats, "MAX_CHAT_BYTES", small):
            self.assertEqual([c["file"] for c in lmstudio_chats.list_chats(self.folder)], ["plain.conversation.json"])

    def test_the_last_answer_as_shown_without_thinking(self):
        self.assertEqual(lmstudio_chats.chat_text(self.folder, "1760000000000.conversation.json", "last"), "[Owl] Hoo.")

    def test_all_the_answers(self):
        self.assertEqual(lmstudio_chats.chat_text(self.folder, "1760000000000.conversation.json", "all"),
                         "[Narrator] Night fell.\n\n[Owl] Hoo.")

    def test_plain_messages(self):
        self.assertEqual(lmstudio_chats.chat_text(self.folder, "plain.conversation.json", "last"), "The moon is bright.")

    def test_only_chats_in_the_folder_can_be_read(self):
        for bad in ("../secret.conversation.json", "notes.txt", "gone.conversation.json", "", None, 5):
            with self.assertRaises(lmstudio_chats.ChatProblem, msg=repr(bad)):
                lmstudio_chats.chat_text(self.folder, bad, "last")

    def test_problems_are_plain(self):
        with self.assertRaises(lmstudio_chats.ChatProblem) as ctx:
            lmstudio_chats.chat_text(self.folder, "empty.conversation.json", "last")
        self.assertEqual(str(ctx.exception), lmstudio_chats.NO_ANSWERS)
        with self.assertRaises(lmstudio_chats.ChatProblem) as ctx:
            lmstudio_chats.chat_text(self.folder, "broken.conversation.json", "last")
        self.assertEqual(str(ctx.exception), lmstudio_chats.UNREADABLE)
        with self.assertRaises(lmstudio_chats.ChatProblem):
            lmstudio_chats.chat_text(self.folder, "plain.conversation.json", "everything")


if __name__ == "__main__":
    unittest.main()

"""Quick tests for ai_programs.py: the program list, the shared HTTP helper, model listing and
sending a request for each kind of program. Pretend servers stand in for the programs."""

import os
import sys
import json
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ai_programs
import fake_lm_studio
import lm_studio
from fake_lm_studio import FakeLMStudio, Reply
import fake_ollama
from fake_ollama import FakeOllama


class ProgramTableTests(unittest.TestCase):
    def test_programs_and_their_usual_addresses(self):
        self.assertEqual(list(ai_programs.PROGRAMS), ["lmstudio", "ollama", "jan", "koboldcpp", "llamacpp", "other"])
        self.assertEqual(ai_programs.PROGRAMS["lmstudio"],
                         {"name": "LM Studio", "default_url": "http://127.0.0.1:1234", "kind": "lmstudio"})
        self.assertEqual(ai_programs.PROGRAMS["ollama"]["default_url"], "http://127.0.0.1:11434")
        self.assertEqual(ai_programs.PROGRAMS["jan"]["default_url"], "http://127.0.0.1:1337")
        self.assertEqual(ai_programs.PROGRAMS["koboldcpp"]["default_url"], "http://127.0.0.1:5001")
        self.assertEqual(ai_programs.PROGRAMS["llamacpp"]["default_url"], "http://127.0.0.1:8080")
        self.assertEqual(ai_programs.PROGRAMS["other"]["default_url"], "")
        self.assertEqual({p["kind"] for k, p in ai_programs.PROGRAMS.items() if k not in ("lmstudio", "ollama")},
                         {"basic"})

    def test_unknown_program_means_lm_studio(self):
        self.assertEqual(ai_programs.program_info("nonsense")["name"], "LM Studio")
        self.assertEqual(ai_programs.program_info(None)["kind"], "lmstudio")

    def test_lm_studio_names_still_work(self):
        self.assertIs(lm_studio.LMStudioError, ai_programs.AIProgramError)
        self.assertIs(lm_studio.Cancelled, ai_programs.Cancelled)
        self.assertEqual(lm_studio.check_local_url("http://localhost:1234/v1"), "http://localhost:1234")


class NotAnsweringTests(unittest.TestCase):
    def test_lm_studio_message_is_unchanged(self):
        self.assertEqual(ai_programs.not_running_message("http://127.0.0.1:1234"),
                         "LM Studio isn't answering at 127.0.0.1:1234. Start the server in LM Studio's Developer tab, "
                         "then press the refresh button next to the model list.")

    def test_other_programs_are_named(self):
        self.assertEqual(ai_programs.not_running_message("http://127.0.0.1:11434", "ollama"),
                         "Ollama isn't answering at 127.0.0.1:11434. Start it, or check the address.")
        self.assertEqual(ai_programs.not_running_message("http://127.0.0.1:9", "other"),
                         "Your AI program isn't answering at 127.0.0.1:9. Start it, or check the address.")

    def test_request_to_nothing_names_the_program(self):
        with self.assertRaises(ai_programs.AIProgramError) as ctx:
            ai_programs.request(fake_lm_studio.free_port_url(), "GET", "/api/tags", program="ollama")
        self.assertIn("Ollama isn't answering", str(ctx.exception))


GOOD = '{"title": "T", "cast": [], "lines": [{"speaker": "Narrator", "text": "Night fell."}]}'


class BasicModeTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeLMStudio()
        self.url = self.fake.start()
        self.cancel = threading.Event()

    def tearDown(self):
        self.fake.stop()

    def test_models_come_from_the_standard_list(self):
        models = ai_programs.list_models("jan", self.url, context_length=8192)
        self.assertEqual([m["key"] for m in models], ["story-model-7b", "helper-3b"])
        self.assertEqual(models[0], {"key": "story-model-7b", "display_name": "story-model-7b", "context_length": 8192,
                                     "can_disable_thinking": False, "publisher": "", "quantization": "",
                                     "base_key": "story-model-7b", "thinks": False, "ready": True})

    def test_basic_mode_on_a_server_without_models_list(self):
        self.fake.serve_models_list = False
        with self.assertRaises(ai_programs.AIProgramError) as ctx:
            ai_programs.list_models("koboldcpp", self.url)
        self.assertIn("Something answered at", str(ctx.exception))
        self.assertIn("but not like KoboldCpp. Check the AI program setting.", str(ctx.exception))

    def test_other_program_reads_naturally_mid_sentence(self):
        self.fake.serve_models_list = False
        with self.assertRaises(ai_programs.AIProgramError) as ctx:
            ai_programs.list_models("other", self.url)
        self.assertIn("but not like your AI program. Check", str(ctx.exception))

    def test_no_models_in_basic_mode(self):
        self.fake.basic_models = []
        self.assertEqual(ai_programs.list_models("llamacpp", self.url), [])

    def test_chat_reads_the_standard_stream(self):
        self.fake.replies.append(Reply(content=GOOD, finish_reason="length"))
        body = {"model": "story-model-7b", "messages": [{"role": "user", "content": "Hi"}], "stream": True,
                "max_tokens": 100}
        content, finish, _ = ai_programs.chat("jan", self.url, body, 30, self.cancel, {"key": "story-model-7b"})
        self.assertEqual((content, finish), (GOOD, "length"))
        self.assertEqual(self.fake.requests[0]["model"], "story-model-7b")

    def test_lm_studio_models_are_ready(self):
        self.assertTrue(all(m["ready"] for m in ai_programs.list_models("lmstudio", self.url)))


class OllamaListTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeOllama()
        self.url = self.fake.start()

    def tearDown(self):
        self.fake.stop()

    def test_installed_text_models_with_their_details(self):
        models = ai_programs.list_models("ollama", self.url)
        self.assertEqual([m["key"] for m in models], ["llama3.2:3b", "qwen3:8b"], "the embedding model is left out")
        llama, qwen = models
        self.assertEqual((llama["context_length"], llama["thinks"], llama["ready"]), (131072, False, True))
        self.assertEqual((qwen["context_length"], qwen["thinks"], qwen["can_disable_thinking"], qwen["ready"]),
                         (40960, True, True, False))
        self.assertEqual(llama["quantization"], "Q4_K_M")

    def test_missing_details_fall_back_safely(self):
        self.fake.models = [fake_ollama.model("old:1b", context_length=None, capabilities=None)]
        [m] = ai_programs.list_models("ollama", self.url)
        self.assertEqual((m["context_length"], m["thinks"]), (4096, False))

    def test_odd_running_list_is_explained(self):
        self.fake.ps_answer = [{"name": "llama3.2:3b"}]  # a list, not Ollama's {"models": [...]}
        with self.assertRaises(ai_programs.AIProgramError) as ctx:
            ai_programs.list_models("ollama", self.url)
        self.assertIn("but not like Ollama", str(ctx.exception))

    def test_gpt_oss_cannot_switch_thinking_off(self):
        self.fake.models = [fake_ollama.model("gpt-oss:20b", 131072, ("completion", "thinking"), family="gptoss")]
        [m] = ai_programs.list_models("ollama", self.url)
        self.assertEqual((m["thinks"], m["can_disable_thinking"], m["thinking_level"]), (True, False, "low"))
        out = ai_programs.ollama_body(SCHEMA_BODY, m)
        self.assertEqual(out["think"], "low", "gpt-oss ignores think: false, so ask for its shortest thinking")

    def test_no_models_installed(self):
        self.fake.models, self.fake.loaded = [], []
        with self.assertRaises(ai_programs.AIProgramError) as ctx:
            ai_programs.list_models("ollama", self.url)
        self.assertIn("Ollama has no models installed yet. Get one with: ollama pull", str(ctx.exception))

    def test_lm_studio_answering_as_ollama_is_explained(self):
        lm = fake_lm_studio.FakeLMStudio()
        url = lm.start()
        try:
            with self.assertRaises(ai_programs.AIProgramError) as ctx:
                ai_programs.list_models("ollama", url)
            self.assertIn("but not like Ollama", str(ctx.exception))
        finally:
            lm.stop()

    def test_ollama_answering_as_lm_studio_is_explained(self):
        with self.assertRaises(ai_programs.AIProgramError) as ctx:
            ai_programs.list_models("lmstudio", self.url)
        self.assertIn("but not like LM Studio. Check the AI program setting.", str(ctx.exception))

    def test_not_running(self):
        with self.assertRaises(ai_programs.AIProgramError) as ctx:
            ai_programs.list_models("ollama", fake_lm_studio.free_port_url())
        self.assertIn("Ollama isn't answering", str(ctx.exception))


SCHEMA_BODY = {"model": "qwen3:8b",
               "messages": [{"role": "system", "content": "Be kind."}, {"role": "user", "content": "x" * 400}],
               "temperature": 0.8, "max_tokens": 1000, "stream": True,
               "response_format": {"type": "json_schema", "json_schema": {"name": "voicecast_script", "strict": True,
                                                                          "schema": {"type": "object"}}}}
QWEN = {"key": "qwen3:8b", "context_length": 40960, "thinks": True, "can_disable_thinking": True}


class OllamaRequestTests(unittest.TestCase):
    def test_body_is_translated(self):
        out = ai_programs.ollama_body(SCHEMA_BODY, QWEN)
        self.assertEqual(out["model"], "qwen3:8b")
        self.assertEqual(out["messages"], SCHEMA_BODY["messages"])
        self.assertTrue(out["stream"])
        self.assertEqual(out["format"], {"type": "object"})
        self.assertIs(out["think"], False)
        self.assertEqual(out["options"]["temperature"], 0.8)
        self.assertEqual(out["options"]["num_predict"], 1000)
        self.assertNotIn("response_format", out)
        self.assertNotIn("max_tokens", out)

    def test_num_ctx_covers_the_request_within_the_maximum(self):
        out = ai_programs.ollama_body(SCHEMA_BODY, QWEN)
        prompt = (sum(len(m["content"]) // 4 + 1 for m in SCHEMA_BODY["messages"])
                  + len(json.dumps({"type": "object"})) // 4 + 1)
        self.assertGreaterEqual(out["options"]["num_ctx"], prompt + 1000 + ai_programs.OLLAMA_MARGIN)
        self.assertEqual(out["options"]["num_ctx"] % ai_programs.OLLAMA_CONTEXT_STEP, 0)
        small = dict(QWEN, context_length=1200)
        self.assertEqual(ai_programs.ollama_body(SCHEMA_BODY, small)["options"]["num_ctx"], 1200)

    def test_num_ctx_is_steady_so_ollama_keeps_the_model_loaded(self):
        longer = dict(SCHEMA_BODY, messages=[SCHEMA_BODY["messages"][0], {"role": "user", "content": "x" * 900}])
        plain = {k: v for k, v in SCHEMA_BODY.items() if k != "response_format"}
        sizes = {ai_programs.ollama_body(b, QWEN)["options"]["num_ctx"] for b in (SCHEMA_BODY, longer, plain)}
        self.assertEqual(len(sizes), 1, "a changed room makes Ollama reload the model")

    def test_plain_request_and_a_model_that_does_not_think(self):
        plain = {k: v for k, v in SCHEMA_BODY.items() if k != "response_format"}
        out = ai_programs.ollama_body(plain, dict(QWEN, thinks=False, can_disable_thinking=False))
        self.assertNotIn("format", out)
        self.assertNotIn("think", out)

    def test_other_settings_go_into_options(self):
        out = ai_programs.ollama_body(dict(SCHEMA_BODY, top_p=0.9, top_k=40, repeat_penalty=1.1), QWEN)
        self.assertEqual((out["options"]["top_p"], out["options"]["top_k"], out["options"]["repeat_penalty"]),
                         (0.9, 40, 1.1))


class OllamaChatTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeOllama()
        self.url = self.fake.start()
        self.cancel = threading.Event()

    def tearDown(self):
        self.fake.stop()

    def chat(self, timeout=30, on_progress=None):
        return ai_programs.chat("ollama", self.url, SCHEMA_BODY, timeout, self.cancel, QWEN, on_progress)

    def test_streamed_answer_and_thinking(self):
        self.fake.replies.append(Reply(content=GOOD, thinking=2, pieces=5))
        seen = []
        content, finish, record = self.chat(on_progress=lambda text, thinking: seen.append((text, thinking)))
        self.assertEqual((content, finish), (GOOD, "stop"))
        self.assertTrue(seen[0][1], "thinking was reported first")
        self.assertEqual(seen[-1][0], GOOD)
        self.assertGreater(record["thinking_chars"], 0)
        self.assertEqual(self.fake.requests[0]["options"]["num_predict"], 1000)

    def test_cut_short(self):
        self.fake.replies.append(Reply(content=GOOD, finish_reason="length"))
        self.assertEqual(self.chat()[1], "length")

    def test_refused_format_falls_back(self):
        self.fake.replies.append(Reply(status=400))
        with self.assertRaises(ai_programs.FormatRefused):
            self.chat()

    def test_other_errors_name_ollama(self):
        self.fake.replies.append(Reply(status=500))
        with self.assertRaises(ai_programs.AIProgramError) as ctx:
            self.chat()
        self.assertEqual(str(ctx.exception),
                         "Ollama reported a problem (error 500). Its own window or log shows the details.")

    def test_stop_during_ollama_stream(self):
        self.fake.replies.append(Reply(content=GOOD, pieces=20, chunk_delay=0.3))
        threading.Timer(0.5, self.cancel.set).start()
        with self.assertRaises(ai_programs.Cancelled):
            self.chat()

    def test_error_in_the_middle_of_the_answer(self):
        self.fake.mid_stream_error = "model runner has unexpectedly stopped"
        self.fake.replies.append(Reply(content=GOOD))
        with self.assertRaises(ai_programs.AIProgramError) as ctx:
            self.chat()
        self.assertEqual(str(ctx.exception), "Ollama reported a problem: model runner has unexpectedly stopped")

    def test_ollama_silence_names_ollama(self):
        self.fake.replies.append(Reply(content=GOOD, delay=3))
        with self.assertRaises(TimeoutError):
            self.chat(timeout=1)


if __name__ == "__main__":
    unittest.main()

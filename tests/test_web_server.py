"""Starts the studio server on a free local port and checks its request protections.
Uses dry runs only, so the TTS model is never loaded."""

import http.client
import json
import os
import re
import sys
import tempfile
import socket
import threading
import time
import unittest
import unittest.mock
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fake_google_docs
import fake_lm_studio
from fake_lm_studio import Reply
import import_text
import web_server


def setUpModule():
    web_server.lm_studio.STEADY_LENGTHS = False  # tiny fake stories; steady lengths are tested in test_lm_studio


def tearDownModule():
    web_server.lm_studio.STEADY_LENGTHS = True


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = web_server.StudioServer(("127.0.0.1", 0), web_server.StudioHandler)
        cls.port = cls.server.server_address[1]
        cls.origin = f"http://127.0.0.1:{cls.port}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        # Save favorites to a temporary file, never the user's real one.
        cls.real_settings_path = web_server.USER_SETTINGS_PATH
        cls.settings_dir = tempfile.TemporaryDirectory()
        web_server.USER_SETTINGS_PATH = os.path.join(cls.settings_dir.name, "user_settings.json")
        # ...and the fake LM Studio settings folder, never the user's real one.
        cls.real_lm_settings_dir = getattr(web_server.lm_studio, "SETTINGS_DIR", None)
        web_server.lm_studio.SETTINGS_DIR = fake_lm_studio.write_settings_folder(
            os.path.join(cls.settings_dir.name, "lmstudio"))

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        web_server.USER_SETTINGS_PATH = cls.real_settings_path
        web_server.lm_studio.SETTINGS_DIR = cls.real_lm_settings_dir
        cls.settings_dir.cleanup()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        conn.request(method, path, body=body, headers={"Authorization": f"Bearer {self.server.session_token}", **(headers or {})})
        response = conn.getresponse()
        data = response.read()
        conn.close()
        return response, data

    def generate(self, payload, **extra_headers):
        headers = {"Content-Type": "application/json", "Origin": self.origin}
        headers.update(extra_headers)
        response, data = self.request("POST", "/api/generate", json.dumps(payload), headers)
        return response, data

    def complete_result(self, data):
        events = [json.loads(chunk[len("data: "):]) for chunk in data.decode().split("\n\n") if chunk.startswith("data: ")]
        return events[-1]["result"]

    # Host and Origin checks

    def test_page_loads_with_security_headers(self):
        response, _ = self.request("GET", "/")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("X-Content-Type-Options"), "nosniff")
        self.assertEqual(response.getheader("X-Frame-Options"), "DENY")
        self.assertIn("frame-ancestors 'none'", response.getheader("Content-Security-Policy"))

    def test_scripts_are_limited_to_the_studio_files(self):
        response, page = self.request("GET", "/")
        policy = dict(part.strip().split(" ", 1) for part in response.getheader("Content-Security-Policy").split(";"))
        self.assertEqual(policy["script-src"], "'self'")
        self.assertEqual(policy["style-src"], "'self'")
        self.assertNotIn('style="', page.decode("utf-8"), "no inline style attributes")
        html = page.decode("utf-8")
        self.assertNotRegex(html, r"\son[a-z]+=", "no inline event handlers")
        self.assertNotRegex(html, r"<script>", "no inline scripts")
        self.assertNotIn("<style>", html)

    def test_page_files_are_served_with_their_types(self):
        for path, content_type in (("/static/studio.js", "text/javascript; charset=utf-8"),
                                   ("/static/studio.css", "text/css; charset=utf-8"),
                                   ("/logo_128.png", "image/png")):
            response, data = self.request("GET", path)
            self.assertEqual(response.status, 200, path)
            self.assertEqual(response.getheader("Content-Type"), content_type)
            self.assertGreater(len(data), 0)

    def test_page_code_has_no_inline_handlers(self):
        with open(os.path.join(os.path.dirname(web_server.WEB_DIR), "web", "static", "studio.js"), encoding="utf-8") as f:
            code = f.read()
        self.assertNotRegex(code, r"\son(click|input|change|error|mouse\w+)=\"", "templates must use data- actions")

    def test_localhost_name_is_accepted(self):
        response, _ = self.request("GET", "/api/config", headers={"Host": f"localhost:{self.port}"})
        self.assertEqual(response.status, 200)

    def test_foreign_host_is_rejected(self):
        response, _ = self.request("GET", "/api/config", headers={"Host": f"evil.example:{self.port}"})
        self.assertEqual(response.status, 403)

    def test_cross_site_post_is_rejected(self):
        response, _ = self.generate({"script": "[Alice] Hi.", "dry_run": True}, Origin="https://evil.example")
        self.assertEqual(response.status, 403)
        response, _ = self.request("POST", "/api/export_latest", headers={"Origin": "https://evil.example"})
        self.assertEqual(response.status, 403)

    def test_cancel_requires_origin_and_reports_idle(self):
        response, _ = self.request("POST", "/api/cancel", headers={"Origin": "https://evil.example"})
        self.assertEqual(response.status, 403)
        response, data = self.request("POST", "/api/cancel", headers={"Origin": self.origin})
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(data), {"success": True, "was_rendering": False})

    def test_post_without_origin_is_rejected(self):
        response, _ = self.request("POST", "/api/generate", "{}", {"Content-Type": "application/json"})
        self.assertEqual(response.status, 403)

    # Body checks

    def test_non_json_content_type_is_rejected(self):
        response, _ = self.generate({"script": "x"}, **{"Content-Type": "text/plain"})
        self.assertEqual(response.status, 415)

    def test_negative_content_length_is_rejected(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        conn.putrequest("POST", "/api/generate")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Authorization", f"Bearer {self.server.session_token}")
        conn.putheader("Origin", self.origin)
        conn.putheader("Content-Length", "-1")
        conn.endheaders()
        self.assertEqual(conn.getresponse().status, 400)
        conn.close()

    def test_performance_level_reaches_the_render(self):
        seen = []
        real = web_server.voice_cast.render_dialogue_content

        def spy(*args, **kwargs):
            seen.append(kwargs["config"].get("performance"))
            return real(*args, **kwargs)

        with unittest.mock.patch.object(web_server.voice_cast, "render_dialogue_content", side_effect=spy):
            for sent in ("lively", "off", "LOUD", None, 7):
                payload = {"script": "[Alice] Hi.", "dry_run": True}
                if sent is not None:
                    payload["performance"] = sent
                response, data = self.generate(payload)
                self.assertEqual(response.status, 200)
                self.assertTrue(self.complete_result(data)["success"])
        self.assertEqual(seen, ["lively", "off", "natural", "natural", "natural"],
                         "missing or unknown means Natural, never an error")

    def test_valid_dry_run_succeeds(self):
        response, data = self.generate({
            "script": "[Narrator] Once.\n[Alice] Hi.",
            "dry_run": True,
            "voices": {"Narrator": {"voice": "bm_fable", "speed": 0.95}, "Alice": {"voice": "af_sky", "speed": 1.05}},
            "pacing": {"dialogue_turn_pause": 0.4, "narrator_transition_pause": 0.7},
        })
        self.assertEqual(response.status, 200)
        result = self.complete_result(data)
        self.assertTrue(result["success"])
        self.assertEqual(result["lines"][1]["pause_before"], 0.7)

    def test_extreme_values_are_clamped(self):
        _, data = self.generate({
            "script": "[Alice] One.\n[Bob] Two.",
            "dry_run": True,
            "voices": {"Alice": {"voice": "af_sky", "speed": 1000}, "Bob": {"voice": "am_puck", "speed": 1}},
            "pacing": {"dialogue_turn_pause": 1e9},
            "performance": "off",  # Natural would shorten this short reply's gap
        })
        result = self.complete_result(data)
        self.assertTrue(result["success"])
        self.assertEqual(result["lines"][0]["speed"], 2.0)
        self.assertEqual(result["lines"][1]["pause_before"], 5.0)

    def test_unknown_voice_is_rejected(self):
        _, data = self.generate({"script": "[Alice] Hi.", "dry_run": True,
                                 "voices": {"Alice": {"voice": "../../evil", "speed": 1}}})
        result = self.complete_result(data)
        self.assertFalse(result["success"])
        self.assertIn("Unknown voice", result["error"])

    def test_pronunciations_are_applied(self):
        _, data = self.generate({"script": "[Narrator] Ask Nguyen about the SQL query.", "dry_run": True,
                                 "pronunciations": [{"word": "Nguyen", "say": "Win"}, {"word": "sql", "say": "sequel"}]})
        result = self.complete_result(data)
        self.assertTrue(result["success"])
        self.assertEqual(result["lines"][0]["text"], "Ask Win about the sequel query.")

    def test_bad_pronunciations_are_rejected(self):
        _, data = self.generate({"script": "[Narrator] Hi.", "dry_run": True, "pronunciations": "SQL=sequel"})
        result = self.complete_result(data)
        self.assertFalse(result["success"])
        self.assertIn("Pronunciations must be a list", result["error"])

    def test_wrong_types_are_rejected(self):
        _, data = self.generate({"script": ["not", "text"], "dry_run": True})
        self.assertFalse(self.complete_result(data)["success"])

    def test_favicon_is_served(self):
        response, data = self.request("GET", "/favicon.ico")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Type"), "image/png")
        self.assertTrue(data.startswith(b"\x89PNG"))

    def test_path_traversal_is_blocked(self):
        response, _ = self.request("GET", "/output/..%2F..%2Fweb_server.py")
        self.assertEqual(response.status, 403)


    # Favorite voices and closed folders

    def save_settings(self, body, **extra_headers):
        headers = {"Content-Type": "application/json", "Origin": self.origin}
        headers.update(extra_headers)
        response, data = self.request("POST", "/api/user_settings", body, headers)
        return response, json.loads(data) if response.status == 200 else None

    def config_settings(self):
        _, data = self.request("GET", "/api/config")
        return json.loads(data)["user_settings"]

    def test_settings_are_saved_and_returned_with_config(self):
        settings = {"favorite_voices": ["af_sky", "bm_george", "af_sky"], "closed_voice_folders": ["Children & Youth"]}
        response, result = self.save_settings(json.dumps(settings))
        self.assertTrue(result["success"])
        expected = {"favorite_voices": ["af_sky", "bm_george"], "closed_voice_folders": ["Children & Youth"], "theme": "system",
                    "ai_writer_enabled": False, "llm_model": "", "llm_base_url": "",
                    "llm_timeout_seconds": 180, "llm_instructions": {},
                    "llm_length": "medium", "llm_auto_audio": False, "llm_auto_split": False, "import_links_enabled": False, "inbox_enabled": False, "llm_model_prompts": {}, "voice_nicknames": {}, "default_narrator": {}, "voice_folders": {},
                    "llm_program": "lmstudio", "llm_context_length": 4096}
        self.assertEqual(result["user_settings"], expected, "duplicates are dropped")
        self.assertEqual(self.config_settings(), expected)

    def test_bad_settings_are_rejected_and_not_saved(self):
        self.save_settings(json.dumps({"favorite_voices": ["af_sky"], "closed_voice_folders": []}))
        for bad in (json.dumps({"favorite_voices": "af_sky"}),
                    json.dumps({"favorite_voices": [123]}),
                    json.dumps({"favorite_voices": ["x" * 101]}),
                    json.dumps({"favorite_voices": ["v"] * 201}),
                    json.dumps(["af_sky"]),
                    json.dumps({"theme": "purple"}),
                    "not json"):
            _, result = self.save_settings(bad)
            self.assertFalse(result["success"], bad[:40])
        self.assertEqual(self.config_settings()["favorite_voices"], ["af_sky"])

    def test_settings_need_the_studio_page_as_origin(self):
        response, _ = self.save_settings(json.dumps({"favorite_voices": []}), Origin="http://evil.example")
        self.assertEqual(response.status, 403)

    def test_damaged_settings_file_starts_empty(self):
        with open(web_server.USER_SETTINGS_PATH, "w", encoding="utf-8") as f:
            f.write("{ damaged")
        self.assertEqual(web_server.load_user_settings(), {
            "favorite_voices": [], "closed_voice_folders": [], "theme": "system",
            "ai_writer_enabled": False, "llm_model": "", "llm_base_url": "",
            "llm_timeout_seconds": 180, "llm_instructions": {},
                    "llm_length": "medium", "llm_auto_audio": False, "llm_auto_split": False, "import_links_enabled": False, "inbox_enabled": False, "llm_model_prompts": {}, "voice_nicknames": {}, "default_narrator": {}, "voice_folders": {},
                    "llm_program": "lmstudio", "llm_context_length": 4096})

    def test_default_narrator_is_checked_and_kept(self):
        v = web_server.validate_user_settings
        self.assertEqual(v({})["default_narrator"], {})
        self.assertEqual(v({"default_narrator": {"voice": "af_heart", "speed": 1.234}})["default_narrator"],
                         {"voice": "af_heart", "speed": 1.23})
        for bad in ("af_heart", {"voice": "", "speed": 1}, {"voice": "x" * 101, "speed": 1},
                    {"voice": "af_heart"}, {"voice": "af_heart", "speed": True},
                    {"voice": "af_heart", "speed": 0.4}, {"voice": "af_heart", "speed": 2.01}):
            with self.assertRaises(web_server.RequestError, msg=str(bad)[:60]):
                v({"default_narrator": bad})

    def test_voice_folders_are_tidied_and_checked(self):
        v = web_server.validate_user_settings
        self.assertEqual(v({})["voice_folders"], {})
        self.assertEqual(v({"voice_folders": {"bm_fable": "  Tier   2 ", "af_sky": "  "}})["voice_folders"],
                         {"bm_fable": "Tier 2"})
        for bad in ([], {"bm_fable": 2}, {"": "Tier 2"}, {"bm_fable": "x" * 31},
                    {f"v{i}": "Tier 2" for i in range(201)}):
            with self.assertRaises(web_server.RequestError, msg=str(bad)[:60]):
                v({"voice_folders": bad})
        for taken in ("Favorites", "favorites", "\u2605 Favorites", "My names", "\u270e MY NAMES"):
            with self.assertRaises(web_server.RequestError) as cm:
                v({"voice_folders": {"bm_fable": taken}})
            self.assertEqual(str(cm.exception), web_server.FOLDER_NAME_TAKEN)

    def test_voice_nicknames_are_tidied_and_saved(self):
        _, result = self.save_settings(json.dumps({"voice_nicknames": {
            "bm_george": "  Skipper   Joe ", "af_sky": "Sky", "am_adam": "   "}}))
        self.assertTrue(result["success"], result)
        self.assertEqual(result["user_settings"]["voice_nicknames"], {"bm_george": "Skipper Joe", "af_sky": "Sky"})
        self.assertEqual(self.config_settings()["voice_nicknames"], {"bm_george": "Skipper Joe", "af_sky": "Sky"})

    def test_bad_voice_nicknames_are_refused(self):
        self.save_settings(json.dumps({"voice_nicknames": {"bm_george": "Skipper"}}))
        for bad in ({"voice_nicknames": ["Skipper"]},
                    {"voice_nicknames": {"bm_george": 5}},
                    {"voice_nicknames": {"bm_george": "x" * 31}},
                    {"voice_nicknames": {"": "Skipper"}},
                    {"voice_nicknames": {"v" * 101: "Skipper"}},
                    {"voice_nicknames": {f"v{i}": f"n{i}" for i in range(201)}},
                    {"voice_nicknames": {"bm_george": "Skipper", "af_sky": "SKIPPER"}}):
            _, result = self.save_settings(json.dumps(bad))
            self.assertFalse(result["success"], str(bad)[:60])
        _, result = self.save_settings(json.dumps({"voice_nicknames": {"a": "Gran", "b": "gran"}}))
        self.assertEqual(result["error"], "Two voices can't have the same nickname: \"gran\".")
        self.assertEqual(self.config_settings()["voice_nicknames"], {"bm_george": "Skipper"}, "nothing bad was saved")

    def test_theme_choice_is_saved(self):
        _, result = self.save_settings(json.dumps({"favorite_voices": ["af_sky"], "theme": "dark"}))
        self.assertTrue(result["success"])
        self.assertEqual(self.config_settings()["theme"], "dark")

    def test_ai_writer_settings_are_saved(self):
        settings = {"ai_writer_enabled": True, "llm_model": "fablewright-12b-v1.1",
                    "llm_base_url": "http://LOCALHOST:1234/v1", "llm_timeout_seconds": 5000,
                    "llm_instructions": {"write": "Write gently."}}
        _, result = self.save_settings(json.dumps(settings))
        self.assertTrue(result["success"], result)
        saved = self.config_settings()
        self.assertTrue(saved["ai_writer_enabled"])
        self.assertEqual(saved["llm_base_url"], "http://localhost:1234")
        self.assertEqual(saved["llm_timeout_seconds"], 1800, "clamped to 30 minutes")
        self.assertEqual(saved["llm_instructions"], {"write": "Write gently."})

    def test_bad_ai_writer_settings_are_rejected(self):
        for bad in ({"ai_writer_enabled": "yes"},
                    {"llm_base_url": "http://192.168.1.5:1234"},
                    {"llm_timeout_seconds": "fast"},
                    {"llm_timeout_seconds": True},
                    {"llm_model": "x" * 101},
                    {"llm_instructions": {"poem": "x"}},
                    {"llm_instructions": {"write": "   "}},
                    {"llm_instructions": {"write": "x" * 4001}},
                    {"llm_instructions": "write"}):
            _, result = self.save_settings(json.dumps(bad))
            self.assertFalse(result["success"], bad)


    # Script Assistant (LM Studio)

    def enable_ai(self, base_url, **extra):
        settings = {"ai_writer_enabled": True, "llm_base_url": base_url}
        settings.update(extra)
        _, result = self.save_settings(json.dumps(settings))
        self.assertTrue(result["success"], result)

    def test_llm_status_is_refused_while_turned_off(self):
        self.save_settings(json.dumps({"ai_writer_enabled": False}))
        response, data = self.request("GET", "/api/llm/status")
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(data), {"success": False, "error": "Script Assistant is turned off."})

    def test_llm_status_lists_loaded_models(self):
        fake = fake_lm_studio.FakeLMStudio()
        url = fake.start()
        try:
            self.enable_ai(url, llm_model="fablewright-12b-v1.1")
            _, data = self.request("GET", "/api/llm/status")
            result = json.loads(data)
            self.assertTrue(result["success"], result)
            self.assertEqual([m["key"] for m in result["models"]], ["fablewright-12b-v1.1"])
            self.assertEqual(result["last_model"], "fablewright-12b-v1.1")
        finally:
            fake.stop()

    def test_llm_status_explains_when_lm_studio_is_off(self):
        self.enable_ai(fake_lm_studio.free_port_url())
        _, data = self.request("GET", "/api/llm/status")
        result = json.loads(data)
        self.assertFalse(result["success"])
        self.assertIn("isn't answering", result["error"])

    def test_llm_status_needs_a_local_host_header(self):
        response, _ = self.request("GET", "/api/llm/status", headers={"Host": f"evil.example:{self.port}"})
        self.assertEqual(response.status, 403)

    def test_program_settings_are_checked(self):
        _, result = self.save_settings(json.dumps({"llm_program": "ollama", "llm_context_length": 8192}))
        self.assertTrue(result["success"], result)
        self.assertEqual((result["user_settings"]["llm_program"], result["user_settings"]["llm_context_length"]),
                         ("ollama", 8192))
        for bad in ({"llm_program": "chatgpt"}, {"llm_context_length": 1000}, {"llm_context_length": 300000},
                    {"llm_context_length": "big"}, {"llm_context_length": True}, {"llm_context_length": 4096.5}):
            _, result = self.save_settings(json.dumps(bad))
            self.assertFalse(result["success"], bad)

    def test_old_settings_file_means_lm_studio(self):
        with open(web_server.USER_SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump({"ai_writer_enabled": True, "llm_model": "fablewright-12b-v1.1"}, f)
        llm = web_server.llm_settings()
        self.assertEqual((llm["program"], llm["base_url"], llm["context_length"]),
                         ("lmstudio", "http://127.0.0.1:1234", 4096))

    def test_address_follows_the_program_when_none_is_saved(self):
        self.save_settings(json.dumps({"ai_writer_enabled": True, "llm_program": "ollama"}))
        self.assertEqual(web_server.llm_settings()["base_url"], "http://127.0.0.1:11434")
        self.save_settings(json.dumps({"ai_writer_enabled": True, "llm_program": "ollama",
                                       "llm_base_url": "http://127.0.0.1:9999"}))
        self.assertEqual(web_server.llm_settings()["base_url"], "http://127.0.0.1:9999")

    def test_config_lists_the_programs(self):
        _, data = self.request("GET", "/api/config")
        programs = json.loads(data)["ai_programs"]
        self.assertEqual([p["id"] for p in programs], ["lmstudio", "ollama", "jan", "koboldcpp", "llamacpp", "other"])
        self.assertEqual(programs[1], {"id": "ollama", "name": "Ollama", "default_url": "http://127.0.0.1:11434",
                                       "kind": "ollama"})

    def test_llm_status_with_ollama(self):
        import fake_ollama
        fake = fake_ollama.FakeOllama()
        url = fake.start()
        try:
            self.enable_ai(url, llm_program="ollama")
            _, data = self.request("GET", "/api/llm/status")
            result = json.loads(data)
            self.assertTrue(result["success"], result)
            self.assertEqual((result["program"], result["program_name"]), ("ollama", "Ollama"))
            self.assertEqual([(m["key"], m["ready"]) for m in result["models"]],
                             [("llama3.2:3b", True), ("qwen3:8b", False)])
        finally:
            fake.stop()

    def test_llm_defaults_come_from_voice_config(self):
        self.save_settings(json.dumps({"ai_writer_enabled": True}))
        llm = web_server.llm_settings()
        self.assertEqual(llm["base_url"], "http://127.0.0.1:1234")
        self.assertEqual(llm["timeout"], 180)
        self.assertIn("Narrator", llm["instructions"]["write"])
        self.assertIn("exactly", llm["instructions"]["split"])
        self.save_settings(json.dumps({"ai_writer_enabled": True, "llm_instructions": {"split": "Mine."}}))
        self.assertEqual(web_server.llm_settings()["instructions"]["split"], "Mine.")


    def sse_events(self, data):
        return [json.loads(chunk[len("data: "):]) for chunk in data.decode().split("\n\n")
                if chunk.startswith("data: ")]

    def write_script(self, payload, **extra_headers):
        """Posts to /api/llm/write_script; returns (response, the result event without its type)."""
        headers = {"Content-Type": "application/json", "Origin": self.origin}
        headers.update(extra_headers)
        response, data = self.request("POST", "/api/llm/write_script", json.dumps(payload), headers)
        if response.status != 200:
            return response, None
        self.last_events = self.sse_events(data)
        result = dict(self.last_events[-1])
        self.assertEqual(result.pop("type"), "result")
        return response, result

    GOOD_REPLY = json.dumps({"title": "T", "cast": [], "lines": [{"speaker": "Narrator", "text": "Hi."}]})

    def test_write_script_returns_a_tagged_script(self):
        fake = fake_lm_studio.FakeLMStudio()
        self.enable_ai(fake.start())
        try:
            fake.replies.append(Reply(content=self.GOOD_REPLY))
            _, result = self.write_script({"mode": "write", "text": "A story.", "model": "fablewright-12b-v1.1",
                                           "cast": [{"name": "Tom", "voice_type": "Child"}]})
            self.assertTrue(result["success"], result)
            self.assertEqual(result["script"], "[Narrator] Hi.")
            self.assertIn("Tom (Child / Youth)", fake.requests[0]["messages"][1]["content"])
        finally:
            fake.stop()

    def test_write_script_is_refused_while_turned_off(self):
        self.save_settings(json.dumps({"ai_writer_enabled": False}))
        _, result = self.write_script({"mode": "write", "text": "x", "model": "m", "cast": []})
        self.assertEqual(result, {"success": False, "error": "Script Assistant is turned off."})

    def test_write_script_checks_origin_and_type(self):
        response, _ = self.write_script({"mode": "write"}, Origin="https://evil.example")
        self.assertEqual(response.status, 403)
        response, _ = self.write_script({"mode": "write"}, **{"Content-Type": "text/plain"})
        self.assertEqual(response.status, 415)

    def test_write_script_rejects_bad_requests(self):
        self.enable_ai(fake_lm_studio.free_port_url())
        for bad in ({"mode": "poem", "text": "x", "model": "m", "cast": []},
                    {"mode": "write", "text": "  ", "model": "m", "cast": []},
                    {"mode": "write", "text": "x" * 20_001, "model": "m", "cast": []},
                    {"mode": "write", "text": "x", "model": "", "cast": []},
                    {"mode": "write", "text": "x", "model": "m", "cast": [{"name": "A", "voice_type": "Robot"}]},
                    {"mode": "write", "text": "x", "model": "m", "cast": "Tom"}):
            _, result = self.write_script(bad)
            self.assertFalse(result["success"], bad)

    def test_length_note_and_stopped_early_reach_the_page(self):
        self.enable_ai(fake_lm_studio.free_port_url(), llm_model="m")
        done = {"title": "T", "script": "[Narrator] Hi.", "cast": [], "used_fallback": False, "word_differences": [],
                "warning": "", "length_note": "Stopped while adding more: kept 2 words.", "stopped_early": True}
        with unittest.mock.patch.object(web_server.lm_studio, "write_script", return_value=done):
            _, result = self.write_script({"mode": "write", "text": "A story.", "model": "m", "cast": []})
        self.assertEqual((result["length_note"], result["stopped_early"]), (done["length_note"], True))

    def test_second_write_waits_its_turn_and_stop_works(self):
        fake = fake_lm_studio.FakeLMStudio()
        self.enable_ai(fake.start())
        try:
            fake.replies.append(Reply(content=self.GOOD_REPLY, delay=10))
            payload = {"mode": "write", "text": "A story.", "model": "fablewright-12b-v1.1", "cast": []}
            first = {}
            worker = threading.Thread(target=lambda: first.update(result=self.write_script(payload)[1]))
            worker.start()
            deadline = time.monotonic() + 5
            while not fake.requests and time.monotonic() < deadline:
                time.sleep(0.05)
            _, second = self.write_script(payload)
            self.assertEqual(second["error"], "Already writing a script. Wait for it to finish or press Stop.")
            _, data = self.request("POST", "/api/llm/cancel", headers={"Origin": self.origin})
            self.assertEqual(json.loads(data), {"success": True, "was_writing": True})
            worker.join(5)
            self.assertEqual(first["result"], {"success": False, "error": "Stopped.", "stopped": True})
        finally:
            fake.stop()

    def test_llm_cancel_needs_origin(self):
        response, _ = self.request("POST", "/api/llm/cancel", headers={"Origin": "https://evil.example"})
        self.assertEqual(response.status, 403)


    def test_length_and_model_prompts_are_saved(self):
        _, result = self.save_settings(json.dumps({
            "llm_length": "long",
            "llm_model_prompts": {"ponder-9b": {"text": "Rhyme.", "use_own": False},
                                  "empty": {"text": "  ", "use_own": False}}}))
        self.assertTrue(result["success"], result)
        self.assertEqual(result["user_settings"]["llm_length"], "long")
        self.assertEqual(result["user_settings"]["llm_model_prompts"],
                         {"ponder-9b": {"text": "Rhyme.", "use_own": False}}, "empty entries are dropped")

    def test_auto_audio_setting(self):
        _, result = self.save_settings(json.dumps({"llm_auto_audio": True}))
        self.assertTrue(result["success"], result)
        self.assertTrue(result["user_settings"]["llm_auto_audio"])
        _, result = self.save_settings(json.dumps({"llm_auto_audio": "yes"}))
        self.assertFalse(result["success"])

    def test_auto_split_setting(self):
        _, result = self.save_settings(json.dumps({"llm_auto_split": True}))
        self.assertTrue(result["success"], result)
        self.assertTrue(result["user_settings"]["llm_auto_split"])
        _, result = self.save_settings(json.dumps({"llm_auto_split": "yes"}))
        self.assertFalse(result["success"])

    def test_bad_length_and_model_prompts_are_rejected(self):
        for bad in ({"llm_length": "epic"},
                    {"llm_model_prompts": []},
                    {"llm_model_prompts": {"m": "text"}},
                    {"llm_model_prompts": {"m": {"text": "x" * 20_001}}},
                    {"llm_model_prompts": {"m": {"text": "x", "use_own": "yes"}}},
                    {"llm_model_prompts": {f"m{i}": {"text": "x"} for i in range(51)}},
                    {"llm_model_prompts": {"x" * 101: {"text": "x"}}}):
            _, result = self.save_settings(json.dumps(bad))
            self.assertFalse(result["success"], bad)

    def model_settings(self, key):
        _, data = self.request("GET", "/api/llm/model_settings?model=" + urllib.parse.quote(key))
        return json.loads(data)

    def test_model_settings_is_refused_while_turned_off(self):
        self.save_settings(json.dumps({"ai_writer_enabled": False}))
        self.assertEqual(self.model_settings("fablewright-12b-v1.1"),
                         {"success": False, "error": "Script Assistant is turned off."})

    def test_model_settings_reports_the_lm_studio_prompt(self):
        fake = fake_lm_studio.FakeLMStudio()
        url = fake.start()
        try:
            self.enable_ai(url)
            result = self.model_settings("fablewright-12b-v1.1")
            self.assertTrue(result["success"], result)
            self.assertEqual((result["source"], result["quantization"]), ("lm_studio", "Q6_K"))
            self.assertEqual(result["lm_studio"]["system_prompt"], fake_lm_studio.FABLEWRIGHT_PROMPT)
            self.enable_ai(url, llm_model_prompts={"fablewright-12b-v1.1": {"text": "Mine.", "use_own": True}})
            self.assertEqual(self.model_settings("fablewright-12b-v1.1")["source"], "own")
            result = self.model_settings("sparrow-12b-it")
            self.assertFalse(result["success"])
            self.assertIn("is no longer loaded", result["error"])
            self.assertFalse(self.model_settings("")["success"])
        finally:
            fake.stop()

    def test_write_script_uses_length_and_the_models_settings(self):
        fake = fake_lm_studio.FakeLMStudio()
        self.enable_ai(fake.start())
        try:
            fake.replies.append(Reply(content=self.GOOD_REPLY))
            _, result = self.write_script({"mode": "write", "text": "A story.", "model": "fablewright-12b-v1.1",
                                           "cast": [], "length": "long"})
            self.assertTrue(result["success"], result)
            sent = fake.requests[0]
            self.assertEqual(sent["max_tokens"], 5512)
            self.assertTrue(sent["messages"][0]["content"].startswith(fake_lm_studio.FABLEWRIGHT_PROMPT))
            self.assertEqual(sent["temperature"], 0.7)
        finally:
            fake.stop()

    def test_removed_presets_cannot_supply_an_empty_request(self):
        old = {"mode":"write", "text":"", "model":"m", "cast":[], "preset":"bedtime"}
        with self.assertRaises(web_server.RequestError):
            web_server.validate_llm_payload(old)
        result = web_server.validate_llm_payload(dict(old, text="A fox and an owl."))
        self.assertEqual(result[1], "A fox and an owl.")

    def test_legacy_preset_setting_is_dropped(self):
        _, result = self.save_settings(json.dumps({"llm_preset":"mystery"}))
        self.assertTrue(result["success"], result)
        self.assertNotIn("llm_preset", result["user_settings"])

    def test_split_takes_longer_texts(self):
        ok = {"mode": "split", "text": "word " * 39_000, "model": "m", "cast": []}
        self.assertEqual(web_server.validate_llm_payload(ok)[0], "split")
        for mode, size, limit in (("split", 200_001, "200,000"), ("write", 20_001, "20,000")):
            with self.assertRaises(web_server.RequestError) as ctx:
                web_server.validate_llm_payload(dict(ok, mode=mode, text="x" * size))
            self.assertEqual(str(ctx.exception), f"That's too much text at once (the limit is {limit} characters).")

    def test_continue_payload_is_checked(self):
        ok = {"mode": "continue", "text": "[Narrator] Chapter 1.", "model": "m", "cast": [], "request": "Snow."}
        self.assertEqual(web_server.validate_llm_payload(ok),
                         ("continue", "[Narrator] Chapter 1.", "m", [], "medium", "Snow."))
        self.assertEqual(web_server.validate_llm_payload(dict(ok, request=None))[5], "", "the request is optional")
        for bad, message in ((dict(ok, text="  "), "There's no story to continue."),
                             (dict(ok, text="x" * 20_001),
                              "The story is too long to continue (the limit is 20,000 characters)."),
                             (dict(ok, request="x" * 2_001), None),
                             (dict(ok, request=5), None)):
            with self.assertRaises(web_server.RequestError) as ctx:
                web_server.validate_llm_payload(bad)
            if message:
                self.assertEqual(str(ctx.exception), message)

    def test_continue_writes_the_next_chapter(self):
        fake = fake_lm_studio.FakeLMStudio()
        self.enable_ai(fake.start())
        try:
            fake.replies.append(Reply(content=self.GOOD_REPLY))
            _, result = self.write_script({"mode": "continue", "text": "[Narrator] Chapter 4. Rain.",
                                           "model": "fablewright-12b-v1.1", "cast": [], "length": "short",
                                           "request": "The fox sleeps."})
            self.assertTrue(result["success"], result)
            sent = fake.requests[0]
            self.assertTrue(sent["messages"][0]["content"].startswith(fake_lm_studio.FABLEWRIGHT_PROMPT))
            self.assertIn('"Chapter 5."', sent["messages"][1]["content"])
            self.assertIn("The fox sleeps.", sent["messages"][1]["content"])
        finally:
            fake.stop()

    def test_saved_time_limit_is_kept(self):
        self.save_settings(json.dumps({"ai_writer_enabled": True, "llm_timeout_seconds": 300}))
        self.assertEqual(web_server.llm_settings()["timeout"], 300)

    def test_write_script_streams_progress_then_the_result(self):
        fake = fake_lm_studio.FakeLMStudio()
        self.enable_ai(fake.start())
        try:
            fake.replies.append(Reply(content=self.GOOD_REPLY, pieces=6, chunk_delay=0.6))
            response, result = self.write_script({"mode": "write", "text": "A story.",
                                                  "model": "fablewright-12b-v1.1", "cast": []})
            self.assertEqual(response.getheader("Content-Type"), "text/event-stream")
            self.assertTrue(result["success"], result)
            self.assertEqual(result["script"], "[Narrator] Hi.")
            progress = [e for e in self.last_events if e["type"] == "progress"]
            self.assertGreaterEqual(len(progress), 2)
            self.assertGreater(progress[-1]["words"], 0)
            self.assertEqual(set(progress[-1]), {"type", "words", "thinking"})
        finally:
            fake.stop()

    def test_closing_the_tab_stops_lm_studio(self):
        fake = fake_lm_studio.FakeLMStudio()
        self.enable_ai(fake.start())
        try:
            fake.replies.append(Reply(content=self.GOOD_REPLY, pieces=40, chunk_delay=0.25))
            payload = json.dumps({"mode": "write", "text": "A story.", "model": "fablewright-12b-v1.1",
                                  "cast": []}).encode("utf-8")
            tab = socket.create_connection(("127.0.0.1", self.port), timeout=10)
            tab.sendall(b"POST /api/llm/write_script HTTP/1.1\r\n"
                        + f"Host: 127.0.0.1:{self.port}\r\nOrigin: {self.origin}\r\nAuthorization: Bearer {self.server.session_token}\r\n".encode("ascii")
                        + f"Content-Type: application/json\r\nContent-Length: {len(payload)}\r\n\r\n".encode("ascii")
                        + payload)
            received = b""
            while b'"progress"' not in received:
                piece = tab.recv(4096)
                self.assertTrue(piece, "the studio answered before any progress")
                received += piece
            tab.shutdown(socket.SHUT_RDWR)  # the tab closes
            tab.close()
            fake.replies.append(Reply(content=self.GOOD_REPLY))
            deadline = time.monotonic() + 5
            while True:
                _, result = self.write_script({"mode": "write", "text": "Again.", "model": "fablewright-12b-v1.1",
                                               "cast": []})
                if result["success"] or time.monotonic() > deadline:
                    break
                time.sleep(0.2)
            self.assertTrue(result["success"], "the first write stopped, so the next one runs")
        finally:
            fake.stop()

    def test_unusable_answer_is_saved_for_troubleshooting(self):
        fake = fake_lm_studio.FakeLMStudio()
        self.enable_ai(fake.start())
        real_path = web_server.ASSISTANT_PROBLEM_PATH
        web_server.ASSISTANT_PROBLEM_PATH = os.path.join(self.settings_dir.name, "problem.txt")
        try:
            fake.replies += [Reply(content="{not json"), Reply(content="")]
            _, result = self.write_script({"mode": "write", "text": "A story.", "model": "fablewright-12b-v1.1",
                                           "cast": [], "length": "short"})
            self.assertFalse(result["success"])
            self.assertTrue(result["error"].startswith(web_server.lm_studio.NOTHING_USABLE), result["error"])
            self.assertIn("script_assistant_problem.txt", result["error"])
            with open(web_server.ASSISTANT_PROBLEM_PATH, encoding="utf-8") as f:
                saved = f.read()
            for expected in ("fablewright-12b-v1.1", "write", "short", "fixed format", "plain text", "{not json"):
                self.assertIn(expected, saved)
        finally:
            web_server.ASSISTANT_PROBLEM_PATH = real_path
            fake.stop()

    def test_thinking_out_of_room_is_saved_too(self):
        fake = fake_lm_studio.FakeLMStudio()
        self.enable_ai(fake.start())
        real_path = web_server.ASSISTANT_PROBLEM_PATH
        web_server.ASSISTANT_PROBLEM_PATH = os.path.join(self.settings_dir.name, "problem2.txt")
        try:
            fake.replies.append(Reply(content="", thinking=2, finish_reason="length"))
            _, result = self.write_script({"mode": "write", "text": "A story.", "model": "fablewright-12b-v1.1",
                                           "cast": []})
            self.assertTrue(result["error"].startswith(web_server.lm_studio.THOUGHT_TOO_LONG), result["error"])
            self.assertIn("script_assistant_problem.txt", result["error"])
            self.assertTrue(os.path.exists(web_server.ASSISTANT_PROBLEM_PATH))
        finally:
            web_server.ASSISTANT_PROBLEM_PATH = real_path
            fake.stop()

    def test_tab_gone_before_the_answer_starts_does_not_block_later_writes(self):
        fake = fake_lm_studio.FakeLMStudio()
        self.enable_ai(fake.start())
        payload = {"mode": "write", "text": "A story.", "model": "fablewright-12b-v1.1", "cast": []}
        real_end_headers = web_server.StudioHandler.end_headers
        failed = []

        def tab_already_gone(handler):
            if handler.path == "/api/llm/write_script" and not failed:
                failed.append(True)
                raise ConnectionAbortedError("the tab closed")
            return real_end_headers(handler)

        web_server.StudioHandler.end_headers = tab_already_gone
        try:
            try:
                self.write_script(payload)
            except (OSError, http.client.HTTPException):
                pass  # the studio hung up, as expected
        finally:
            web_server.StudioHandler.end_headers = real_end_headers
        try:
            fake.replies.append(Reply(content=self.GOOD_REPLY))
            _, result = self.write_script(payload)
            self.assertTrue(result["success"], result)
        finally:
            fake.stop()

    def test_write_script_rejects_a_bad_length(self):
        self.enable_ai(fake_lm_studio.free_port_url())
        _, result = self.write_script({"mode": "write", "text": "x", "model": "m", "cast": [], "length": "epic"})
        self.assertFalse(result["success"])
        self.assertIn("'length'", result["error"])

    # Describe a voice

    def describe(self, payload, **extra_headers):
        headers = {"Content-Type": "application/json", "Origin": self.origin}
        headers.update(extra_headers)
        response, data = self.request("POST", "/api/blend/describe", json.dumps(payload), headers)
        return response, json.loads(data) if response.status == 200 else None

    def test_describe_uses_the_private_traits_of_own_voices(self):
        import voice_cast
        self.save_settings(json.dumps({"ai_writer_enabled": False}))
        with tempfile.TemporaryDirectory() as folder:
            open(os.path.join(folder, "my_troll.npy"), "wb").close()
            with open(os.path.join(folder, "voice_traits.json"), "w", encoding="utf-8") as f:
                json.dump({"my_troll": {"gender": "male", "age": "adult", "accent": "american",
                                        "sounds": ["deep", "gruff", "booming", "rugged", "dramatic"]}}, f)
            with unittest.mock.patch.object(voice_cast, "CUSTOM_VOICES_DIR", folder):
                _, result = self.describe({"description": "a deep gruff booming rugged dramatic man"})
        self.assertEqual(result["recipe"][0]["voice"], "my_troll", result)

    def test_describe_with_word_matching(self):
        self.save_settings(json.dumps({"ai_writer_enabled": False}))
        _, result = self.describe({"description": "old British man, gruff"})
        self.assertTrue(result["success"], result)
        self.assertEqual(result["helper"], "words")
        self.assertTrue(all(row["voice"].startswith("bm_") for row in result["recipe"]), result)
        self.assertEqual(result["voice_type"], "Male")
        self.assertEqual(result["name"], "old_british_man_gruff")
        self.assertIn("left out the age", result["note"])

    def test_describe_lists_unused_words_and_says_when_nothing_fits(self):
        self.save_settings(json.dumps({"ai_writer_enabled": False}))
        _, result = self.describe({"description": "a voice like the sea"})
        self.assertFalse(result["success"])
        self.assertIn("couldn't find anything", result["error"])
        self.assertEqual(result["unknown"], ["sea"])

    def test_describe_checks_its_input(self):
        for bad, words in (({"description": "   "}, "Describe the voice first"),
                           ({"description": "x" * 201}, "200 characters"),
                           ({"description": 5}, "Describe the voice first"),
                           (["x"], "could not be read")):
            _, result = self.describe(bad)
            self.assertFalse(result["success"])
            self.assertIn(words, result["error"])
        response, _ = self.describe({"description": "a man"}, Origin="https://evil.example")
        self.assertEqual(response.status, 403)
        response, _ = self.describe({"description": "a man"}, **{"Content-Type": "text/plain"})
        self.assertEqual(response.status, 415)

    def test_describe_with_the_ai_program(self):
        fake = fake_lm_studio.FakeLMStudio()
        self.enable_ai(fake.start(), llm_model="fablewright-12b-v1.1")
        try:
            fake.replies.append(Reply(content=json.dumps(
                {"gender": "male", "age": "adult", "accent": "any", "sounds": ["deep", "gruff", "booming"]})))
            _, result = self.describe({"description": "the grumpy troll under the bridge"})
            self.assertTrue(result["success"], result)
            self.assertEqual((result["helper"], result["note"], result["unknown"]), ("ai", "", []))
            self.assertEqual(result["name"], "grumpy_troll_bridge")
        finally:
            fake.stop()

    def test_describe_falls_back_to_word_matching(self):
        fake = fake_lm_studio.FakeLMStudio()
        self.enable_ai(fake.start(), llm_model="fablewright-12b-v1.1")
        try:
            fake.replies.append(Reply(content="I think a troll sounds deep."))
            _, result = self.describe({"description": "a gruff old man"})
            self.assertTrue(result["success"], result)
            self.assertEqual(result["helper"], "words")
            self.assertIn("couldn't be read", result["note"])
            self.assertIn("I used word matching instead.", result["note"])
        finally:
            fake.stop()
        self.enable_ai(fake_lm_studio.free_port_url(), llm_model="")
        _, result = self.describe({"description": "a gruff old man"})
        self.assertIn("No model is chosen", result["note"])

    def test_describe_uses_word_matching_while_the_assistant_writes(self):
        self.enable_ai(fake_lm_studio.free_port_url(), llm_model="fablewright-12b-v1.1")
        web_server._LLM_LOCK.acquire()
        try:
            _, result = self.describe({"description": "a gruff old man"})
        finally:
            web_server._LLM_LOCK.release()
        self.assertEqual(result["helper"], "words")
        self.assertIn("busy", result["note"])

    def test_describe_waits_at_most_a_minute(self):
        self.enable_ai(fake_lm_studio.free_port_url(), llm_model="m", llm_timeout_seconds=1800)
        seen = {}

        def fake_traits(*args, **kwargs):
            seen["timeout"] = args[3]
            return {"gender": "male", "age": "any", "accent": "any", "sounds": ["deep"], "unknown": []}

        with unittest.mock.patch.object(web_server.lm_studio, "describe_voice_traits", side_effect=fake_traits):
            _, result = self.describe({"description": "a deep voice"})
        self.assertEqual(seen["timeout"], 60)
        self.assertEqual(result["helper"], "ai")

    def test_describe_can_be_stopped(self):
        fake = fake_lm_studio.FakeLMStudio()
        self.enable_ai(fake.start(), llm_model="fablewright-12b-v1.1")
        try:
            fake.replies.append(Reply(content="{}", delay=10))
            first = {}
            worker = threading.Thread(target=lambda: first.update(result=self.describe({"description": "a troll"})[1]))
            worker.start()
            deadline = time.monotonic() + 5
            while not fake.requests and time.monotonic() < deadline:
                time.sleep(0.05)
            self.request("POST", "/api/blend/describe/cancel", headers={"Origin": self.origin})
            worker.join(5)
            self.assertEqual(first["result"], {"success": False, "error": "Stopped.", "stopped": True})
        finally:
            fake.stop()

    def test_describe_stop_that_arrives_first_still_stops(self):
        self.enable_ai(fake_lm_studio.free_port_url(), llm_model="m")
        self.request("POST", "/api/blend/describe/cancel", json.dumps({"id": "t1"}),
                     {"Content-Type": "application/json", "Origin": self.origin, "Authorization": f"Bearer {self.server.session_token}"})
        with unittest.mock.patch.object(web_server.lm_studio, "describe_voice_traits",
                                        side_effect=lambda *a, **k: (a[4].wait(2), {"gender": "male", "age": "any",
                                        "accent": "any", "sounds": [], "unknown": []})[1]):
            _, result = self.describe({"description": "a troll", "id": "t1"})
        self.assertEqual(result, {"success": False, "error": "Stopped.", "stopped": True})

    def test_describe_stop_for_an_earlier_request_is_ignored(self):
        self.enable_ai(fake_lm_studio.free_port_url(), llm_model="m")
        self.request("POST", "/api/blend/describe/cancel", json.dumps({"id": "old"}),
                     {"Content-Type": "application/json", "Origin": self.origin, "Authorization": f"Bearer {self.server.session_token}"})
        with unittest.mock.patch.object(web_server.lm_studio, "describe_voice_traits",
                                        return_value={"gender": "male", "age": "any", "accent": "any",
                                                      "sounds": ["deep"], "unknown": []}):
            _, result = self.describe({"description": "a deep voice", "id": "new"})
        self.assertEqual(result["helper"], "ai")

    def test_describe_note_ends_cleanly_after_a_question(self):
        self.enable_ai(fake_lm_studio.free_port_url(), llm_model="m")
        with unittest.mock.patch.object(web_server.lm_studio, "describe_voice_traits",
                                        side_effect=web_server.lm_studio.LMStudioError("Is LM Studio running?")):
            _, result = self.describe({"description": "a deep voice"})
        self.assertEqual(result["note"], "Is LM Studio running? I used word matching instead.")

    def test_describe_stop_leaves_the_script_assistant_alone(self):
        web_server._LLM_CANCEL_EVENT.clear()
        response, _ = self.request("POST", "/api/blend/describe/cancel", headers={"Origin": self.origin})
        self.assertEqual(response.status, 200)
        self.assertFalse(web_server._LLM_CANCEL_EVENT.is_set(), "a running script is not stopped")

    def test_describe_says_when_the_ai_program_understood_nothing(self):
        fake = fake_lm_studio.FakeLMStudio()
        self.enable_ai(fake.start(), llm_model="fablewright-12b-v1.1")
        try:
            fake.replies.append(Reply(content=json.dumps({"gender": "any", "age": "any", "accent": "any", "sounds": []})))
            _, result = self.describe({"description": "a gruff old man"})
            self.assertEqual(result["helper"], "words")
            self.assertIn("couldn't make sense of the description", result["note"])
        finally:
            fake.stop()

    def test_script_assistant_says_describe_is_using_the_ai_program(self):
        web_server._LLM_LOCK.acquire()
        web_server._LLM_USER = "describe"
        try:
            self.enable_ai(fake_lm_studio.free_port_url(), llm_model="m")
            _, result = self.write_script({"mode": "write", "text": "A story.", "model": "m", "cast": []})
        finally:
            web_server._LLM_USER = None
            web_server._LLM_LOCK.release()
        self.assertIn("Describe a voice", result["error"])


class BlendServerTests(unittest.TestCase):
    """The blender's three actions, with a fake voice model and a temporary voices folder."""

    @classmethod
    def setUpClass(cls):
        import numpy as np
        import voice_cast
        cls.np, cls.voice_cast = np, voice_cast
        cls.tmp = tempfile.TemporaryDirectory()
        shape = (510, 1, 256)

        class Model:
            voices = {"af_sky": np.full(shape, 1.0, dtype=np.float32), "af_bella": np.full(shape, 3.0, dtype=np.float32)}

            def create(self, text, voice, speed=1.0, lang="en-us"):
                return np.full(24000, 0.5, dtype=np.float32), 24000

        cls.pipeline = voice_cast.UnifiedPipeline("onnx", Model())
        cls.patches = [unittest.mock.patch.object(voice_cast, name, value) for name, value in (
            ("CUSTOM_VOICES_DIR", os.path.join(cls.tmp.name, "custom_voices")),
            ("PREVIEWS_DIR", os.path.join(cls.tmp.name, "previews")),
            ("_BASE_VOICE_IDS", frozenset({"af_sky", "af_bella"})),
            ("_CACHED_PIPELINE", cls.pipeline))]
        cls.patches.append(unittest.mock.patch.object(voice_cast, "get_pipeline", return_value=cls.pipeline))
        for p in cls.patches:
            p.start()
        cls.server = web_server.StudioServer(("127.0.0.1", 0), web_server.StudioHandler)
        cls.port = cls.server.server_address[1]
        cls.origin = f"http://127.0.0.1:{cls.port}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        for p in cls.patches:
            p.stop()
        cls.tmp.cleanup()

    def post(self, path, payload, origin=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        conn.request("POST", path, body=json.dumps(payload),
                     headers={"Content-Type": "application/json", "Origin": origin or self.origin, "Authorization": f"Bearer {self.server.session_token}"})
        response = conn.getresponse()
        data = response.read()
        conn.close()
        return response.status, (json.loads(data) if response.status == 200 else data)

    RECIPE = [{"voice": "af_sky", "weight": 70}, {"voice": "af_bella", "weight": 30}]

    def test_preview(self):
        status, data = self.post("/api/blend/preview", {"recipe": self.RECIPE, "text": "Hi there."})
        self.assertEqual(status, 200)
        self.assertTrue(data["success"], data)
        self.assertTrue(data["preview_url"].startswith("/output/previews/preview_blend_"))

    def test_save_exists_replace_and_delete(self):
        body = {"name": "srv_hero", "recipe": self.RECIPE, "voice_type": "Female"}
        self.assertEqual(self.post("/api/blend/save", body)[1], {"success": True, "voice": "srv_hero"})
        _, again = self.post("/api/blend/save", body)
        self.assertEqual((again["success"], again.get("exists")), (False, True))
        self.assertIn("already exists", again["error"])
        self.assertTrue(self.post("/api/blend/save", dict(body, replace=True))[1]["success"])
        self.assertTrue(self.post("/api/blend/delete", {"name": "srv_hero"})[1]["success"])
        _, gone = self.post("/api/blend/delete", {"name": "srv_hero"})
        self.assertFalse(gone["success"])
        self.assertIn("no voice called", gone["error"])

    def test_save_keeps_a_description(self):
        body = {"name": "srv_troll", "recipe": self.RECIPE, "voice_type": "Male", "description": "  a grumpy troll "}
        self.assertTrue(self.post("/api/blend/save", body)[1]["success"])
        entry = next(e for e in self.voice_cast.get_custom_voice_entries() if e["id"] == "srv_troll")
        self.assertEqual(entry["description"], "a grumpy troll")
        _, bad = self.post("/api/blend/save", dict(body, name="srv_long", description="x" * 201))
        self.assertFalse(bad["success"])
        self.assertIn("200 characters", bad["error"])
        self.post("/api/blend/delete", {"name": "srv_troll"})

    def test_save_keeps_pitch_and_starting_speed(self):
        body = {"name": "srv_girl", "recipe": self.RECIPE, "voice_type": "Child", "pitch": 1.5, "start_speed": 1.05}
        self.assertTrue(self.post("/api/blend/save", body)[1]["success"])
        entry = next(e for e in self.voice_cast.get_custom_voice_entries() if e["id"] == "srv_girl")
        self.assertEqual((entry["pitch"], entry["start_speed"]), (1.5, 1.05))
        for bad in ({"pitch": "high"}, {"pitch": 5}, {"start_speed": "fast"}):
            _, data = self.post("/api/blend/save", dict(body, name="srv_bad", **bad))
            self.assertFalse(data["success"], bad)
            self.assertIn("must be from", data["error"])
        self.post("/api/blend/delete", {"name": "srv_girl"})

    def test_preview_with_pitch(self):
        _, plain = self.post("/api/blend/preview", {"recipe": self.RECIPE, "text": "Hi there."})
        _, lifted = self.post("/api/blend/preview", {"recipe": self.RECIPE, "text": "Hi there.", "pitch": 1.5})
        self.assertTrue(lifted["success"], lifted)
        self.assertNotEqual(lifted["preview_url"], plain["preview_url"])
        _, bad = self.post("/api/blend/preview", {"recipe": self.RECIPE, "text": "Hi.", "pitch": 9})
        self.assertFalse(bad["success"])

    def test_bad_input_gets_a_plain_message(self):
        for path, body, words in (("/api/blend/save", {"name": "bad name", "recipe": self.RECIPE, "voice_type": "Male"}, "only letters"),
                                  ("/api/blend/save", {"name": "ok", "recipe": [{"voice": "af_sky", "weight": 0}], "voice_type": "Male"}, "above 0"),
                                  ("/api/blend/preview", {"recipe": self.RECIPE, "text": 5}, "sample sentence"),
                                  ("/api/blend/preview", ["not", "an", "object"], "could not be read")):
            with self.subTest(body=body):
                status, data = self.post(path, body)
                self.assertEqual(status, 200)
                self.assertFalse(data["success"])
                self.assertIn(words, data["error"])

    def test_other_websites_are_refused(self):
        status, _ = self.post("/api/blend/delete", {"name": "x"}, origin="http://evil.example")
        self.assertEqual(status, 403)

    def test_unexpected_problems_are_logged_not_blamed_on_the_request(self):
        with unittest.mock.patch.object(self.voice_cast, "generate_blend_preview", side_effect=ValueError("boom")), \
             unittest.mock.patch.object(web_server, "log_exception") as logged:
            _, data = self.post("/api/blend/preview", {"recipe": self.RECIPE, "text": "Hi."})
        self.assertEqual(data, {"success": False, "error": web_server.GENERIC_ERROR})
        logged.assert_called_once()
        _, bad = self.post("/api/blend/preview", None)
        self.assertIn("could not be read", bad["error"])

    def test_save_and_delete_wait_for_a_render(self):
        web_server._GENERATION_LOCK.acquire()
        try:
            for path, body in (("/api/blend/save", {"name": "busy_one", "recipe": self.RECIPE, "voice_type": "Male"}),
                               ("/api/blend/delete", {"name": "busy_one"})):
                _, data = self.post(path, body)
                self.assertEqual(data, {"success": False, "error": web_server.BLEND_BUSY})
        finally:
            web_server._GENERATION_LOCK.release()
        self.assertFalse(os.path.exists(os.path.join(self.voice_cast.CUSTOM_VOICES_DIR, "busy_one.npy")))


class ImportServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = web_server.StudioServer(("127.0.0.1", 0), web_server.StudioHandler)
        cls.port = cls.server.server_address[1]
        cls.origin = f"http://127.0.0.1:{cls.port}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.real_settings_path = web_server.USER_SETTINGS_PATH
        cls.settings_dir = tempfile.TemporaryDirectory()
        web_server.USER_SETTINGS_PATH = os.path.join(cls.settings_dir.name, "user_settings.json")
        cls.google = fake_google_docs.FakeGoogle()
        cls.restore_google = cls.google.point(import_text)

    @classmethod
    def tearDownClass(cls):
        cls.restore_google()
        cls.google.close()
        cls.server.shutdown()
        cls.server.server_close()
        web_server.USER_SETTINGS_PATH = cls.real_settings_path
        cls.settings_dir.cleanup()

    def setUp(self):
        self.google.routes.clear()
        self.google.requests.clear()
        self.set_links(False)

    def post(self, path, payload, origin=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        conn.request("POST", path, json.dumps(payload),
                     {"Content-Type": "application/json", "Origin": origin or self.origin, "Authorization": f"Bearer {self.server.session_token}"})
        response = conn.getresponse()
        data = response.read()
        conn.close()
        return response, (json.loads(data) if response.status == 200 else None)

    def set_links(self, on):
        _, data = self.post("/api/user_settings", {"import_links_enabled": on})
        self.assertTrue(data["success"])

    def test_setting_is_off_by_default_and_must_be_true_or_false(self):
        self.assertFalse(web_server.validate_user_settings({})["import_links_enabled"])
        with self.assertRaises(web_server.RequestError):
            web_server.validate_user_settings({"import_links_enabled": "yes"})

    def test_md_file_is_tidied(self):
        _, data = self.post("/api/import/text", {"filename": "Fox.md", "text": "# Chapter 1\n**Fox** ran."})
        self.assertEqual(data, {"success": True, "text": "Chapter 1\nFox ran.", "words": 4})

    def test_other_file_types_are_refused(self):
        _, data = self.post("/api/import/text", {"filename": "Fox.docx", "text": "hi"})
        self.assertEqual(data, {"success": False, "error": import_text.NOT_TXT_OR_MD})

    def test_empty_file_is_refused(self):
        _, data = self.post("/api/import/text", {"filename": "a.txt", "text": " \n\n "})
        self.assertEqual(data, {"success": False, "error": import_text.EMPTY})

    def test_too_much_text_is_refused(self):
        _, data = self.post("/api/import/text", {"filename": "a.txt", "text": "x" * (web_server.MAX_SCRIPT_CHARS + 1)})
        self.assertFalse(data["success"])
        self.assertIn("2,000,000", data["error"])

    def test_bad_body(self):
        _, data = self.post("/api/import/text", {"filename": 3})
        self.assertFalse(data["success"])

    def test_requests_from_other_sites_are_refused(self):
        response, _ = self.post("/api/import/text", {"filename": "a.txt", "text": "hi"}, origin="https://evil.example")
        self.assertEqual(response.status, 403)
        response, _ = self.post("/api/import/google_doc", {"url": fake_google_docs.LINK}, origin="https://evil.example")
        self.assertEqual(response.status, 403)

    def test_links_are_refused_while_the_setting_is_off_without_contacting_google(self):
        self.google.routes[fake_google_docs.EXPORT_PATH] = (200, {"Content-Type": "text/markdown"}, b"Hello")
        _, data = self.post("/api/import/google_doc", {"url": fake_google_docs.LINK})
        self.assertEqual(data, {"success": False, "error": web_server.IMPORT_LINKS_OFF})
        self.assertEqual(self.google.requests, [])

    def chats_folder(self):
        """A temporary LM Studio conversations folder with one saved chat."""
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        chat = {"name": "Fox story", "createdAt": 1_760_000_000_000, "lastUsedModel": {"identifier": "m"},
                "messages": [{"role": "user", "content": "A fox story."},
                             {"role": "assistant", "content": "[Narrator] Night fell.\n[Fox] Hello."}]}
        with open(os.path.join(folder.name, "1.conversation.json"), "w", encoding="utf-8") as f:
            json.dump(chat, f)
        patcher = unittest.mock.patch.object(web_server.lmstudio_chats, "CHATS_DIR", folder.name)
        patcher.start()
        self.addCleanup(patcher.stop)

    def get_json(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        conn.request("GET", path, headers={"Authorization": f"Bearer {self.server.session_token}"})
        response = conn.getresponse()
        data = response.read()
        conn.close()
        return json.loads(data)

    def test_saved_lm_studio_chats_are_listed(self):
        self.chats_folder()
        self.assertEqual(self.get_json("/api/lmstudio/chats"), {"success": True, "chats": [
            {"file": "1.conversation.json", "name": "Fox story", "model": "m", "when": 1_760_000_000_000, "answers": 1}]})

    def test_a_saved_chat_is_imported(self):
        self.chats_folder()
        _, data = self.post("/api/import/lmstudio_chat", {"file": "1.conversation.json", "which": "last"})
        text = "[Narrator] Night fell.\n[Fox] Hello."
        self.assertEqual(data, {"success": True, "text": text, "words": import_text.count_words(text)})
        _, data = self.post("/api/import/lmstudio_chat", {"file": "../1.conversation.json", "which": "last"})
        self.assertFalse(data["success"])
        response, _ = self.post("/api/import/lmstudio_chat", {"file": "1.conversation.json", "which": "last"},
                                origin="https://evil.example")
        self.assertEqual(response.status, 403)

    def test_link_import_when_on(self):
        self.set_links(True)
        self.google.routes[fake_google_docs.EXPORT_PATH] = (200, {"Content-Type": "text/markdown"}, b"## Two\nHello there")
        _, data = self.post("/api/import/google_doc", {"url": fake_google_docs.LINK})
        self.assertEqual(data, {"success": True, "text": "Two\nHello there", "words": 3})

    def test_link_problems_come_back_as_messages(self):
        self.set_links(True)
        _, data = self.post("/api/import/google_doc", {"url": "https://example.com/doc"})
        self.assertEqual(data, {"success": False, "error": import_text.NOT_A_DOC_LINK})
        _, data = self.post("/api/import/google_doc", {"url": fake_google_docs.LINK})  # fake answers 404
        self.assertEqual(data, {"success": False, "error": import_text.NOT_FOUND})


class InboxServerTests(unittest.TestCase):
    """Send to VoiceCast: the one-slot inbox that voicecast_mcp.py posts to and the page takes from."""
    @classmethod
    def setUpClass(cls):
        cls.server = web_server.StudioServer(("127.0.0.1", 0), web_server.StudioHandler)
        cls.port = cls.server.server_address[1]
        cls.origin = f"http://127.0.0.1:{cls.port}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.real_settings_path = web_server.USER_SETTINGS_PATH
        cls.settings_dir = tempfile.TemporaryDirectory()
        web_server.USER_SETTINGS_PATH = os.path.join(cls.settings_dir.name, "user_settings.json")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        web_server.USER_SETTINGS_PATH = cls.real_settings_path
        cls.settings_dir.cleanup()

    def setUp(self):
        self.switch(True)
        web_server.clear_inbox()

    def post(self, path, payload, origin=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        conn.request("POST", path, json.dumps(payload),
                     {"Content-Type": "application/json", "Origin": origin or self.origin, "Authorization": f"Bearer {self.server.session_token}"})
        response = conn.getresponse()
        data = response.read()
        conn.close()
        return response, (json.loads(data) if response.status == 200 else None)

    def switch(self, on):
        _, data = self.post("/api/user_settings", {"inbox_enabled": on})
        self.assertTrue(data["success"])

    def take(self):
        _, data = self.post("/api/inbox/take", {})
        return data

    def test_setting_is_off_by_default_and_must_be_true_or_false(self):
        self.assertFalse(web_server.validate_user_settings({})["inbox_enabled"])
        with self.assertRaises(web_server.RequestError):
            web_server.validate_user_settings({"inbox_enabled": "yes"})

    def test_sent_script_is_taken_once(self):
        _, data = self.post("/api/inbox", {"text": "[Narrator] Hi.", "title": "The Fox"})
        self.assertEqual(data, {"success": True})
        self.assertEqual(self.take(), {"success": True, "item": {"text": "[Narrator] Hi.", "title": "The Fox"}})
        self.assertEqual(self.take(), {"success": True, "item": None})

    def test_a_newer_script_replaces_a_waiting_one(self):
        self.post("/api/inbox", {"text": "First."})
        self.post("/api/inbox", {"text": "Second."})
        self.assertEqual(self.take()["item"], {"text": "Second.", "title": ""})

    def test_title_is_tidied_and_cut(self):
        self.post("/api/inbox", {"text": "Hi.", "title": "  The \n Fox  " + "x" * 200})
        title = self.take()["item"]["title"]
        self.assertTrue(title.startswith("The Fox x"))
        self.assertEqual(len(title), web_server.INBOX_TITLE_CHARS)

    def test_bad_scripts_are_refused(self):
        for bad in ({"text": "  \n "}, {"text": 5}, {}, {"text": "Hi.", "title": 3}, ["Hi."],
                    {"text": "x" * (web_server.MAX_SCRIPT_CHARS + 1)}):
            _, data = self.post("/api/inbox", bad)
            self.assertFalse(data["success"], bad if len(str(bad)) < 80 else "too long")
            self.assertTrue(data["error"])
        self.assertIsNone(self.take()["item"])

    def test_switched_off_refuses_both_ways(self):
        self.switch(False)
        _, data = self.post("/api/inbox", {"text": "Hi."})
        self.assertEqual(data, {"success": False, "code": "inbox_off", "error": web_server.INBOX_OFF})
        self.assertEqual(self.take()["code"], "inbox_off")

    def test_switching_off_empties_the_slot(self):
        self.post("/api/inbox", {"text": "Waiting."})
        self.switch(False)
        self.switch(True)
        self.assertIsNone(self.take()["item"])

    def test_other_sites_cannot_send_or_take(self):
        response, _ = self.post("/api/inbox", {"text": "Hi."}, origin="https://evil.example")
        self.assertEqual(response.status, 403)
        self.post("/api/inbox", {"text": "Mine."})
        response, _ = self.post("/api/inbox/take", {}, origin="https://evil.example")
        self.assertEqual(response.status, 403)
        self.assertEqual(self.take()["item"]["text"], "Mine.")

    def test_get_is_not_a_way_in(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        conn.request("GET", "/api/inbox/take", headers={"Authorization": f"Bearer {self.server.session_token}"})
        self.assertEqual(conn.getresponse().status, 404)
        conn.close()


if __name__ == "__main__":
    unittest.main()

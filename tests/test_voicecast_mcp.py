"""Send to VoiceCast: the MCP helper LM Studio starts (voicecast_mcp.py), against a real studio server
on a free port with temporary settings. No model is loaded."""

import http.client
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import unittest.mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import voicecast_mcp
import web_server


def call(name, arguments, msg_id=7):
    return voicecast_mcp.handle_message({"jsonrpc": "2.0", "id": msg_id, "method": "tools/call",
                                         "params": {"name": name, "arguments": arguments}})


def tool_text(reply):
    return reply["result"]["content"][0]["text"]


class ProtocolTests(unittest.TestCase):
    def test_initialize_answers_with_the_asked_version_when_known(self):
        reply = voicecast_mcp.handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                              "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                                                         "clientInfo": {"name": "test", "version": "1"}}})
        self.assertEqual(reply["id"], 1)
        self.assertEqual(reply["result"]["protocolVersion"], "2025-03-26")
        self.assertIn("tools", reply["result"]["capabilities"])
        self.assertEqual(reply["result"]["serverInfo"]["name"], "voicecast")

    def test_initialize_with_an_unknown_version_gets_our_newest(self):
        reply = voicecast_mcp.handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                              "params": {"protocolVersion": "1999-01-01"}})
        self.assertEqual(reply["result"]["protocolVersion"], voicecast_mcp.PROTOCOL_VERSIONS[0])

    def test_notifications_get_no_answer(self):
        self.assertIsNone(voicecast_mcp.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        self.assertIsNone(voicecast_mcp.handle_message({"jsonrpc": "2.0", "method": "anything/else"}))

    def test_ping(self):
        self.assertEqual(voicecast_mcp.handle_message({"jsonrpc": "2.0", "id": "a", "method": "ping"}),
                         {"jsonrpc": "2.0", "id": "a", "result": {}})

    def test_tools_list_has_one_tool_needing_text(self):
        reply = voicecast_mcp.handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = reply["result"]["tools"]
        self.assertEqual([t["name"] for t in tools], ["send_to_voicecast"])
        schema = tools[0]["inputSchema"]
        self.assertEqual(schema["required"], ["text"])
        self.assertEqual(set(schema["properties"]), {"text", "title"})
        self.assertIn("[Narrator]", tools[0]["description"])

    def test_unknown_method_and_bad_requests(self):
        self.assertEqual(voicecast_mcp.handle_message({"jsonrpc": "2.0", "id": 3, "method": "resources/list"})["error"]["code"], -32601)
        self.assertEqual(voicecast_mcp.handle_message({"id": 3, "method": "ping"})["error"]["code"], -32600)
        self.assertEqual(call("other_tool", {"text": "x"})["error"]["code"], -32602)
        bad = voicecast_mcp.handle_message({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": "x"})
        self.assertEqual(bad["error"]["code"], -32602)

    def test_empty_text_is_refused_without_sending(self):
        with unittest.mock.patch.object(voicecast_mcp, "send_script") as send:
            for args in ({"text": "  "}, {}, {"text": 5}):
                reply = call("send_to_voicecast", args)
                self.assertTrue(reply["result"]["isError"])
        send.assert_not_called()


class SendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = web_server.StudioServer(("127.0.0.1", 0), web_server.StudioHandler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.real_settings_path = web_server.USER_SETTINGS_PATH
        cls.settings_dir = tempfile.TemporaryDirectory()
        web_server.USER_SETTINGS_PATH = os.path.join(cls.settings_dir.name, "user_settings.json")
        cls.real_port = voicecast_mcp.PORT
        voicecast_mcp.PORT = cls.port
        cls.token_patch = unittest.mock.patch.object(voicecast_mcp.session_access, "read_session", return_value=cls.server.session_token)
        cls.token_patch.start()

    @classmethod
    def tearDownClass(cls):
        cls.token_patch.stop()
        voicecast_mcp.PORT = cls.real_port
        cls.server.shutdown()
        cls.server.server_close()
        web_server.USER_SETTINGS_PATH = cls.real_settings_path
        cls.settings_dir.cleanup()

    def setUp(self):
        self.switch(True)
        web_server.clear_inbox()

    def switch(self, on):
        settings = web_server.validate_user_settings({"inbox_enabled": on})
        web_server.save_user_settings(settings)

    def take(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        conn.request("POST", "/api/inbox/take", "{}", {"Content-Type": "application/json",
                                                       "Origin": f"http://127.0.0.1:{self.port}", "Authorization": f"Bearer {self.server.session_token}"})
        data = json.loads(conn.getresponse().read())
        conn.close()
        return data["item"]

    def test_sent_script_reaches_the_inbox(self):
        reply = call("send_to_voicecast", {"text": "[Narrator] Once.\n[Fox] Hi!", "title": "The Fox"})
        self.assertFalse(reply["result"]["isError"])
        self.assertEqual(tool_text(reply), voicecast_mcp.SENT)
        self.assertEqual(self.take(), {"text": "[Narrator] Once.\n[Fox] Hi!", "title": "The Fox"})

    def test_accented_text_arrives_unchanged(self):
        text = "[Narrator] Café, naïve, 你好 — “quoted”."
        self.assertEqual(voicecast_mcp.send_script(text), (True, voicecast_mcp.SENT))
        self.assertEqual(self.take()["text"], text)

    def test_switched_off_passes_on_the_studio_message(self):
        self.switch(False)
        reply = call("send_to_voicecast", {"text": "Hi."})
        self.assertTrue(reply["result"]["isError"])
        self.assertEqual(tool_text(reply), web_server.INBOX_OFF)

    def test_server_refusal_is_passed_on(self):
        ok, message = voicecast_mcp.send_script("x" * (web_server.MAX_SCRIPT_CHARS + 1))
        self.assertFalse(ok)
        self.assertIn("2,000,000", message)

    def test_body_too_large_is_reported(self):
        # 6,000,000 "é" is 12 MB of UTF-8, over the studio's 10 MB request limit: refused before sending.
        self.assertEqual(voicecast_mcp.MAX_REQUEST_BYTES, web_server.MAX_BODY_BYTES)
        ok, message = voicecast_mcp.send_script("é" * 6_000_000)
        self.assertEqual((ok, message), (False, voicecast_mcp.TOO_LONG))

    def test_not_running(self):
        with socket.socket() as s:  # a port nothing listens on
            s.bind(("127.0.0.1", 0))
            free_port = s.getsockname()[1]
        with unittest.mock.patch.object(voicecast_mcp, "PORT", free_port):
            self.assertEqual(voicecast_mcp.send_script("Hi."), (False, voicecast_mcp.NOT_RUNNING))

    def test_proxy_settings_are_ignored(self):
        with unittest.mock.patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:9", "http_proxy": "http://127.0.0.1:9",
                                                   "NO_PROXY": "", "no_proxy": ""}):
            self.assertEqual(voicecast_mcp.send_script("Hi."), (True, voicecast_mcp.SENT))


class FindStudioTests(unittest.TestCase):
    """VoiceCast moves off 7871 when another app holds it; the studio writes the port it got to PORT_FILE."""
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        port_file = os.path.join(self.dir.name, "studio_port.txt")
        self.real_port_file = voicecast_mcp.PORT_FILE
        for patch in (unittest.mock.patch.object(voicecast_mcp, "PORT_FILE", port_file),
                      unittest.mock.patch.dict(os.environ, {"VOICECAST_PORT": ""})):
            patch.start()
            self.addCleanup(patch.stop)
        self.port_file = port_file

    def write(self, text):
        with open(self.port_file, "w", encoding="utf-8") as f:
            f.write(text)

    def test_the_port_the_studio_wrote_is_used(self):
        self.write("8080\n")
        self.assertEqual(voicecast_mcp.studio_port(), 8080)

    def test_no_file_or_a_damaged_one_means_7871(self):
        self.assertEqual(voicecast_mcp.studio_port(), 7871)
        for bad in ("", "abc", "0", "70000", "-5"):
            self.write(bad)
            self.assertEqual(voicecast_mcp.studio_port(), 7871, bad)

    def test_voicecast_port_setting_wins(self):
        self.write("8080")
        with unittest.mock.patch.dict(os.environ, {"VOICECAST_PORT": "9090"}):
            self.assertEqual(voicecast_mcp.studio_port(), 9090)

    def test_the_server_writes_its_port_where_the_helper_reads_it(self):
        self.assertEqual(os.path.normcase(os.path.abspath(web_server.PORT_FILE)),
                         os.path.normcase(os.path.abspath(os.path.join(ROOT, "output", "studio_port.txt"))))
        self.assertEqual(os.path.normcase(os.path.abspath(self.real_port_file)),
                         os.path.normcase(os.path.abspath(web_server.PORT_FILE)))
        with unittest.mock.patch.object(web_server, "PORT_FILE", self.port_file):
            web_server.write_port_file(8888)
        self.assertEqual(voicecast_mcp.studio_port(), 8888)


class FoundPortSendTests(unittest.TestCase):
    def test_sending_follows_the_port_file(self):
        server = web_server.StudioServer(("127.0.0.1", 0), web_server.StudioHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with tempfile.TemporaryDirectory() as d:
            real_settings = web_server.USER_SETTINGS_PATH
            web_server.USER_SETTINGS_PATH = os.path.join(d, "user_settings.json")
            try:
                web_server.save_user_settings(web_server.validate_user_settings({"inbox_enabled": True}))
                port_file = os.path.join(d, "studio_port.txt")
                with open(port_file, "w", encoding="utf-8") as f:
                    f.write(str(server.server_address[1]))
                with unittest.mock.patch.object(voicecast_mcp.session_access, "read_session", return_value=server.session_token), \
                        unittest.mock.patch.object(voicecast_mcp, "PORT", None), \
                        unittest.mock.patch.object(voicecast_mcp, "PORT_FILE", port_file), \
                        unittest.mock.patch.dict(os.environ, {"VOICECAST_PORT": ""}):
                    self.assertEqual(voicecast_mcp.send_script("Hi."), (True, voicecast_mcp.SENT))
            finally:
                web_server.USER_SETTINGS_PATH = real_settings
                web_server.clear_inbox()


class RunAsProgramTests(unittest.TestCase):
    def test_lines_in_answers_out(self):
        lines = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
        stdin = "\n".join(json.dumps(m) for m in lines) + "\nnot json\n"
        done = subprocess.run([sys.executable, os.path.join(ROOT, "voicecast_mcp.py")], input=stdin.encode("utf-8"),
                              capture_output=True, timeout=30)
        answers = [json.loads(line) for line in done.stdout.decode("utf-8").splitlines() if line.strip()]
        self.assertEqual([a.get("id") for a in answers], [1, 2, None])
        self.assertEqual(answers[2]["error"]["code"], -32700)
        self.assertEqual(done.returncode, 0)


if __name__ == "__main__":
    unittest.main()

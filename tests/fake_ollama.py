"""A small stand-in for Ollama's local server (its own API), so tests never need the real program.
Set .models / .loaded for the lists and queue fake_lm_studio.Reply objects in .replies for /api/chat."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fake_lm_studio import Reply


def model(name, context_length=32768, capabilities=("completion",), family="llama"):
    return {"name": name, "context_length": context_length, "family": family,
            "capabilities": list(capabilities) if capabilities is not None else None}


class FakeOllama:
    def __init__(self):
        self.models = [model("llama3.2:3b", 131072), model("qwen3:8b", 40960, ("completion", "thinking")),
                       model("nomic-embed-text:latest", 2048, ("embedding",))]
        self.loaded = ["llama3.2:3b"]
        self.ps_answer = None  # when set, /api/ps answers this instead of the loaded list
        self.replies, self.requests, self.shows = [], [], []
        self.mid_stream_error = None  # sent as {"error": ...} after the content, like a crashed runner
        self.server = None
        self._stopping = threading.Event()

    def start(self) -> str:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def send_json(self, status, data):
                raw = json.dumps(data).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def wait(self, seconds):
                end = time.monotonic() + seconds
                while time.monotonic() < end and not fake._stopping.is_set():
                    time.sleep(0.02)

            def do_GET(self):
                if self.path == "/api/tags":
                    self.send_json(200, {"models": [{"name": m["name"], "model": m["name"], "size": 1,
                                                     "details": {"family": "llama"}} for m in fake.models]})
                elif self.path == "/api/ps":
                    self.send_json(200, fake.ps_answer if fake.ps_answer is not None
                                   else {"models": [{"name": n, "model": n} for n in fake.loaded]})
                else:
                    self.send_json(404, {"error": "not found"})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                if self.path == "/api/show":
                    fake.shows.append(body.get("model"))
                    m = next((m for m in fake.models if m["name"] == body.get("model")), None)
                    if m is None:
                        self.send_json(404, {"error": "model not found"})
                        return
                    info = {"general.architecture": m["family"]}
                    if m["context_length"] is not None:
                        info[f"{m['family']}.context_length"] = m["context_length"]
                    data = {"model_info": info, "details": {"family": m["family"], "quantization_level": "Q4_K_M"}}
                    if m["capabilities"] is not None:
                        data["capabilities"] = m["capabilities"]
                    self.send_json(200, data)
                    return
                if self.path != "/api/chat":
                    self.send_json(404, {"error": "not found"})
                    return
                fake.requests.append(body)
                reply = fake.replies.pop(0) if fake.replies else Reply(content="{}")
                self.wait(reply.delay)
                try:
                    if reply.status != 200:
                        self.send_json(reply.status, {"error": "something went wrong"})
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-ndjson")
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()

                    def line(data):
                        piece = (json.dumps(data) + "\n").encode("utf-8")
                        self.wfile.write(f"{len(piece):X}\r\n".encode("ascii") + piece + b"\r\n")
                        self.wfile.flush()

                    for _ in range(reply.thinking):
                        line({"model": body.get("model"), "message": {"role": "assistant", "content": "",
                                                                      "thinking": "Hmm. "}, "done": False})
                        self.wait(reply.chunk_delay)
                    content = reply.content or ""
                    size = max(1, -(-len(content) // max(1, reply.pieces)))
                    for i in range(0, len(content), size):
                        line({"model": body.get("model"),
                              "message": {"role": "assistant", "content": content[i:i + size]}, "done": False})
                        self.wait(reply.chunk_delay)
                    if fake.mid_stream_error:
                        line({"error": fake.mid_stream_error})
                        self.wfile.write(b"0\r\n\r\n")
                        self.wfile.flush()
                        return
                    line({"model": body.get("model"), "message": {"role": "assistant", "content": ""}, "done": True,
                          "done_reason": "length" if reply.finish_reason == "length" else "stop"})
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
                except OSError:
                    pass  # the caller hung up (Stop or a time limit)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def stop(self):
        self._stopping.set()
        self.server.shutdown()
        self.server.server_close()

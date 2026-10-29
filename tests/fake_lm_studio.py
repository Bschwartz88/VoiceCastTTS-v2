"""A small stand-in for LM Studio's local server, so tests never need the real app.
Set .models for the model list and queue .replies for chat requests."""

import copy
import json
import os
import socket
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _model(key, name, loaded, context=12288, model_type="llm", architecture="llama", reasoning=None,
           publisher="test", quantization="Q4_K_M"):
    caps = {"vision": False, "trained_for_tool_use": False}
    if reasoning:
        caps["reasoning"] = {"allowed_options": reasoning, "default": "on"}
    return {
        "type": model_type, "publisher": publisher, "key": key, "display_name": name,
        "architecture": architecture, "quantization": {"name": quantization, "bits_per_weight": 4},
        "max_context_length": 131072, "format": "gguf", "capabilities": caps,
        "loaded_instances": [{"id": key, "config": {"context_length": context}}] if loaded else [],
    }


LOADED = _model("fablewright-12b-v1.1", "Fablewright 12B v1.1", loaded=True, publisher="ExampleLabs", quantization="Q6_K")
UNLOADED = _model("sparrow-12b-it", "Sparrow 12B Instruct", loaded=False)
EMBEDDING = _model("text-embedding-nomic", "Nomic Embed", loaded=True, model_type="embedding")
IMAGE = _model("picture-maker", "Picture Maker", loaded=True, architecture="qwen_image")
THINKER = _model("ponder-9b", "Ponder 9B", loaded=True, reasoning=["off", "on"],
                 publisher="lmstudio-community", quantization="Q6_K")

# A stand-in for LM Studio's per-model settings folder
# (~/.lmstudio/.internal/user-concrete-model-default-config), laid out like a real one.
FABLEWRIGHT_PROMPT = "You write long, vivid stories."


def _prompt(text):
    return {"preset": "", "operation": {"fields": [{"key": "llm.prediction.systemPrompt", "value": text}]}}


SETTINGS_FILES = {
    "ExampleLabs/Fablewright-12B-v1.1-GGUF/Fablewright-12B-v1.1-Q4_K_M.gguf.json": _prompt("Q4 prompt."),
    "ExampleLabs/Fablewright-12B-v1.1-GGUF/Fablewright-12B-v1.1-Q6_K.gguf.json": {
        "preset": "",
        "operation": {"fields": [
            {"key": "llm.prediction.systemPrompt", "value": FABLEWRIGHT_PROMPT},
            {"key": "llm.prediction.temperature", "value": 0.7},
            {"key": "llm.prediction.topKSampling", "value": 0},
            {"key": "llm.prediction.topPSampling", "value": {"checked": True, "value": 0.95}},
            {"key": "llm.prediction.repeatPenalty", "value": {"checked": True, "value": 1.05}},
            {"key": "llm.prediction.minPSampling", "value": {"checked": True, "value": 0.05}},
            {"key": "llm.prediction.stopStrings", "value": ["<|im_end|>"]},
        ]},
        "load": {"fields": [{"key": "llm.load.contextLength", "value": 12288}]},
    },
    "lmstudio-community/Ponder-9B-GGUF/Ponder-9B-Q6_K.gguf.json": {
        "preset": "", "load": {"fields": [{"key": "llm.load.numParallelSessions", "value": 1}]}},
    "alpha/Twin-7B-GGUF/Twin-7B-Q8_0.gguf.json": _prompt("A"),
    "beta/Twin-7B-GGUF/Twin-7B-Q8_0.gguf.json": _prompt("B"),
    "x/Broken-3B-GGUF/Broken-3B-Q4_0.gguf.json": "{not json",
    "p/Preset-1B-GGUF/Preset-1B-Q4_0.gguf.json": {"preset": "@local:my-stories", "operation": {"fields": []}},
    "q/Tokens-2B-GGUF/Tokens-2B-Q4_K_M.gguf.json": _prompt("M"),
    "solo/Solo-5B-GGUF/Solo-5B-Q4_0.gguf.json": _prompt("Solo."),
    "lmstudio-community/sparrow-12b-it-GGUF/sparrow-12b-it-Q6_K.gguf.json": _prompt("Plain."),
    "lmstudio-community/sparrow-12b-it-qat-GGUF/sparrow-12b-it-qat-Q6_K.gguf.json": _prompt("QAT."),
}


def write_settings_folder(root) -> str:
    """Fills root with SETTINGS_FILES and returns it, for lm_studio.SETTINGS_DIR in tests."""
    for rel, content in SETTINGS_FILES.items():
        path = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content if isinstance(content, str) else json.dumps(content))
    return root


@dataclass
class Reply:
    content: str = None        # the assistant's message text
    status: int = 200
    raw: str = None            # send this body as-is instead (for broken answers)
    delay: float = 0.0         # seconds to wait before answering
    finish_reason: str = "stop"
    # When the request asks for "stream", the content is sent in pieces like LM Studio does:
    pieces: int = 4            # how many pieces the content is cut into
    chunk_delay: float = 0.0   # seconds between pieces
    thinking: int = 0          # "thinking" pieces sent before the content
    pause_after: int = -1      # after this piece (0-based), wait `pause` seconds instead
    pause: float = 0.0
    no_done: bool = False      # end without "data: [DONE]"
    ignore_stream: bool = False  # answer in one piece even though streaming was asked for
    junk: bool = False         # send a keep-alive comment and a broken line first


class FakeLMStudio:
    def __init__(self):
        self.models = [copy.deepcopy(LOADED), copy.deepcopy(UNLOADED)]
        self.basic_models = ["story-model-7b", "helper-3b"]  # what /v1/models lists (basic mode)
        self.serve_models_list = True
        self.replies = []
        self.requests = []
        self.server = None
        self._stopping = threading.Event()

    def start(self) -> str:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def send_body(self, status, text):
                data = text.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def send_stream(self, reply):
                """Sends reply.content as LM Studio's streamed answer (chunked Server-Sent Events)."""
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()

                def chunk(data):
                    piece = f"data: {data}\n\n".encode("utf-8")
                    self.wfile.write(f"{len(piece):X}\r\n".encode("ascii") + piece + b"\r\n")
                    self.wfile.flush()

                def wait(seconds):
                    end = time.monotonic() + seconds
                    while time.monotonic() < end and not fake._stopping.is_set():
                        time.sleep(0.02)

                def delta(part, finish=None):
                    return json.dumps({"choices": [{"index": 0, "delta": part, "finish_reason": finish}]})

                if reply.junk:
                    comment = b": keep-alive\n\n"
                    self.wfile.write(f"{len(comment):X}\r\n".encode("ascii") + comment + b"\r\n")
                    chunk("{not json")
                for _ in range(reply.thinking):
                    chunk(delta({"reasoning_content": "Hmm, let me think. "}))
                    wait(reply.chunk_delay)
                content = reply.content or ""
                size = max(1, -(-len(content) // max(1, reply.pieces)))
                parts = [content[i:i + size] for i in range(0, len(content), size)] or [""]
                for i, part in enumerate(parts):
                    last = i == len(parts) - 1
                    chunk(delta({"content": part}, reply.finish_reason if last else None))
                    wait(reply.pause if i == reply.pause_after else reply.chunk_delay)
                if not reply.no_done:
                    chunk("[DONE]")
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()

            def do_GET(self):
                if self.path == "/api/v1/models":
                    self.send_body(200, json.dumps({"models": fake.models}))
                elif self.path == "/v1/models" and fake.serve_models_list:
                    self.send_body(200, json.dumps({"object": "list", "data": [
                        {"id": m, "object": "model", "owned_by": "test"} for m in fake.basic_models]}))
                else:
                    self.send_body(404, json.dumps({"error": "Unexpected endpoint"}))

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                if self.path != "/v1/chat/completions":
                    self.send_body(404, json.dumps({"error": "Unexpected endpoint"}))
                    return
                fake.requests.append(body)
                reply = fake.replies.pop(0) if fake.replies else Reply(content="{}")
                end = time.monotonic() + reply.delay
                while time.monotonic() < end and not fake._stopping.is_set():
                    time.sleep(0.02)
                try:
                    if reply.raw is not None:
                        self.send_body(reply.status, reply.raw)
                    elif body.get("stream") and reply.status == 200 and not reply.ignore_stream:
                        self.send_stream(reply)
                    else:
                        self.send_body(reply.status, json.dumps({"choices": [{
                            "message": {"role": "assistant", "content": reply.content},
                            "finish_reason": reply.finish_reason}]}))
                except OSError:
                    pass  # the caller hung up (Stop, a time limit, or the tab closed)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def stop(self):
        self._stopping.set()
        self.server.shutdown()
        self.server.server_close()


def free_port_url() -> str:
    """An address on this computer where nothing is listening (for "not running" tests)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{s.getsockname()[1]}"

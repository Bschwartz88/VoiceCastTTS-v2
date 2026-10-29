"""
The AI programs the Script Assistant can use, all running on this computer: LM Studio, Ollama, and
programs that understand the common OpenAI-style request (Jan, KoboldCpp, llama.cpp server, others).
This module lists a program's models and sends one chat request in that program's way; lm_studio.py
builds the requests and reads the answers. Standard library only; it only ever connects to this computer.
"""

import http.client
import json
import socket
import threading
import time
import urllib.parse

# id -> name, usual address, kind ("lmstudio", "ollama" or "basic"). The page gets this list from /api/config.
PROGRAMS = {
    "lmstudio": {"name": "LM Studio", "default_url": "http://127.0.0.1:1234", "kind": "lmstudio"},
    "ollama": {"name": "Ollama", "default_url": "http://127.0.0.1:11434", "kind": "ollama"},
    "jan": {"name": "Jan", "default_url": "http://127.0.0.1:1337", "kind": "basic"},
    "koboldcpp": {"name": "KoboldCpp", "default_url": "http://127.0.0.1:5001", "kind": "basic"},
    "llamacpp": {"name": "llama.cpp server", "default_url": "http://127.0.0.1:8080", "kind": "basic"},
    "other": {"name": "Your AI program", "default_url": "", "kind": "basic"},
}
DEFAULT_PROGRAM = "lmstudio"

LOCAL_HOSTS = ("127.0.0.1", "localhost")
LOCAL_ONLY_MESSAGE = ("Only an address on this computer (127.0.0.1 or localhost) is allowed, "
                      "for example http://127.0.0.1:1234.")
STATUS_TIMEOUT = 5
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_STREAM_LINE_BYTES = 256 * 1024
MAX_RESPONSE_SECONDS = 1800


class AIProgramError(Exception):
    """A problem with the AI program that is safe to show to the user."""


class Cancelled(Exception):
    """The user pressed Stop."""


def program_info(program) -> dict:
    """The program's entry in PROGRAMS; anything unknown means LM Studio."""
    return PROGRAMS.get(program) or PROGRAMS[DEFAULT_PROGRAM]


def check_local_url(url) -> str:
    """Returns the address as "http://host:port" if it points at this computer.
    Raises ValueError for anything else, so text is never sent off this PC."""
    if not isinstance(url, str) or len(url) > 100:
        raise ValueError(LOCAL_ONLY_MESSAGE)
    try:
        parts = urllib.parse.urlsplit(url.strip())
        port = parts.port
    except ValueError:
        raise ValueError(LOCAL_ONLY_MESSAGE) from None
    if (parts.scheme != "http" or parts.hostname not in LOCAL_HOSTS or parts.username or parts.password
            or parts.query or parts.fragment or parts.path.rstrip("/") not in ("", "/v1") or not port):
        raise ValueError(LOCAL_ONLY_MESSAGE)
    return f"http://{parts.hostname}:{port}"


def not_running_message(base_url: str, program=DEFAULT_PROGRAM) -> str:
    host = base_url.split("://", 1)[-1]
    if program_info(program)["kind"] == "lmstudio":
        return (f"LM Studio isn't answering at {host}. Start the server in LM Studio's Developer tab, "
                "then press the refresh button next to the model list.")
    return f"{program_info(program)['name']} isn't answering at {host}. Start it, or check the address."


def _abort(conn, sock=None):
    """Closes the connection to the program at once; it then stops writing.
    sock is the request's socket: once an answer that ends the connection has started,
    http.client forgets conn.sock while the answer is still being read from it."""
    sock = sock or conn.sock
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
    conn.close()


def request(base_url, method, path, body=None, timeout=STATUS_TIMEOUT, cancel_event=None, on_line=None,
            program=DEFAULT_PROGRAM):
    """Sends one request to the AI program and returns (status, body text).
    With on_line, a 200 answer is read line by line as it arrives and each line is handed to
    on_line (the body text returned is then empty); timeout is then the longest silence allowed.
    The wait runs in a helper thread so Stop (cancel_event) can end it at once.
    Raises TimeoutError, Cancelled, or AIProgramError when the program isn't running."""
    base = urllib.parse.urlsplit(check_local_url(base_url))
    conn = http.client.HTTPConnection(base.hostname, base.port, timeout=timeout)
    outcome = {}

    def run():
        try:
            data = json.dumps(body).encode("utf-8") if body is not None else None
            headers = {"Content-Type": "application/json"} if data is not None else {}
            conn.request(method, path, body=data, headers=headers)
            outcome["sock"] = conn.sock  # kept for Stop (see _abort)
            response = conn.getresponse()
            outcome["status"] = response.status
            if on_line is not None and response.status == 200:
                total = 0
                while True:
                    raw = response.readline(MAX_STREAM_LINE_BYTES + 1)
                    if not raw:
                        break
                    total += len(raw)
                    if len(raw) > MAX_STREAM_LINE_BYTES or total > MAX_RESPONSE_BYTES:
                        raise AIProgramError('The AI response exceeded the safe size limit. Try a shorter request.')
                    on_line(raw.decode('utf-8', 'replace'))
                outcome["text"] = ""
            else:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise AIProgramError('The AI response exceeded the safe size limit. Try a shorter request.')
                outcome["text"] = raw.decode('utf-8', 'replace')
        except BaseException as e:  # handed to the waiting thread below
            outcome["error"] = e
        finally:
            conn.close()

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    deadline = time.monotonic() + min(MAX_RESPONSE_SECONDS, max(30, timeout * 4))
    while worker.is_alive():
        worker.join(0.1)
        if time.monotonic() > deadline:
            _abort(conn, outcome.get('sock'))
            worker.join(2)
            raise TimeoutError('The AI response took too long.')
        if cancel_event is not None and cancel_event.is_set():
            _abort(conn, outcome.get("sock"))
            worker.join(2)
            raise Cancelled()
    error = outcome.get("error")
    if isinstance(error, TimeoutError):
        raise TimeoutError() from error
    if isinstance(error, (OSError, http.client.HTTPException)):
        raise AIProgramError(not_running_message(f"http://{base.hostname}:{base.port}", program)) from error
    if error is not None:
        raise error
    return outcome["status"], outcome["text"]


# Model lists. Every kind gives the same shape: key, display_name, context_length (tokens of room),
# can_disable_thinking, publisher, quantization, base_key, thinks, ready (loaded, or loads by itself).

DEFAULT_CONTEXT_LENGTH = 4096
# Loaded models LM Studio lists as "llm" that can't write text.
NON_TEXT_ARCHITECTURES = ("qwen_image",)


def _not_like(base_url, program) -> AIProgramError:
    host = check_local_url(base_url).split("://", 1)[-1]
    name = program_info(program)["name"]
    if name.startswith("Your "):
        name = "y" + name[1:]  # mid-sentence: "your AI program"
    return AIProgramError(f"Something answered at {host}, but not like {name}. Check the AI program setting.")


def _is_ollama(base_url) -> bool:
    """True when Ollama answers at base_url. (Old LM Studio versions also answer /v1/models, so only
    Ollama's own list tells another program apart.)"""
    try:
        status, text = request(base_url, "GET", "/api/tags")
        data = json.loads(text) if status == 200 else None
    except (TimeoutError, AIProgramError, ValueError):
        return False
    return isinstance(data, dict) and isinstance(data.get("models"), list)


def _lmstudio_models(base_url) -> list:
    """The text models loaded in LM Studio right now (never loads one)."""
    old_version = ("LM Studio answered but didn't list its models. VoiceCast needs LM Studio 0.4 or newer. "
                   "If this isn't LM Studio, check the AI program setting.")
    try:
        status, text = request(base_url, "GET", "/api/v1/models")
    except TimeoutError:
        raise AIProgramError(not_running_message(check_local_url(base_url))) from None
    try:
        data = json.loads(text) if status == 200 else None
    except ValueError:
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("models"), list):
        if _is_ollama(base_url):
            raise _not_like(base_url, "lmstudio")
        raise AIProgramError(old_version)
    models = []
    for m in data["models"]:
        if not isinstance(m, dict) or m.get("type") != "llm" or m.get("architecture") in NON_TEXT_ARCHITECTURES:
            continue
        instances = [i for i in (m.get("loaded_instances") or []) if isinstance(i, dict)]
        if not instances:
            continue
        config = instances[0].get("config") or {}
        reasoning = (m.get("capabilities") or {}).get("reasoning") or {}
        key = str(instances[0].get("id") or m.get("key"))
        quantization = m.get("quantization")
        models.append({
            "key": key,
            "display_name": str(m.get("display_name") or m.get("key")),
            "context_length": int(config.get("context_length") or m.get("max_context_length") or 4096),
            "can_disable_thinking": "off" in (reasoning.get("allowed_options") or []),
            "publisher": str(m.get("publisher") or ""),
            "quantization": str(quantization.get("name") or "") if isinstance(quantization, dict) else "",
            "base_key": key.split("@")[0],
            "thinks": bool(reasoning),  # it may reason before answering, which uses up answer room
            "ready": True,
        })
    return models


def _basic_models(base_url, program, context_length) -> list:
    """The models a program offers at /v1/models (Jan, KoboldCpp, llama.cpp server, others)."""
    try:
        status, text = request(base_url, "GET", "/v1/models", program=program)
    except TimeoutError:
        raise AIProgramError(not_running_message(check_local_url(base_url), program)) from None
    try:
        data = json.loads(text) if status == 200 else None
    except ValueError:
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        raise _not_like(base_url, program)
    models = []
    for m in data["data"]:
        key = m.get("id") if isinstance(m, dict) else None
        if isinstance(key, str) and key.strip():
            models.append({"key": key, "display_name": key, "context_length": int(context_length),
                           "can_disable_thinking": False, "publisher": "", "quantization": "",
                           "base_key": key, "thinks": False, "ready": True})
    return models


def _get_json(base_url, method, path, program, body=None) -> tuple:
    """(status, parsed JSON or None); a program that isn't running raises AIProgramError."""
    try:
        status, text = request(base_url, method, path, body, program=program)
    except TimeoutError:
        raise AIProgramError(not_running_message(check_local_url(base_url), program)) from None
    try:
        return status, json.loads(text)
    except ValueError:
        return status, None


# Ollama model families that ignore think: false and only take a level ("low", "medium", "high").
LEVEL_THINKERS = ("gptoss",)


def _ollama_models(base_url) -> list:
    """Ollama's installed text models; loaded ones are ready, others load when first used."""
    status, tags = _get_json(base_url, "GET", "/api/tags", "ollama")
    if status != 200 or not isinstance(tags, dict) or not isinstance(tags.get("models"), list):
        raise _not_like(base_url, "ollama")
    _, ps = _get_json(base_url, "GET", "/api/ps", "ollama")
    if ps is not None and not (isinstance(ps, dict) and isinstance(ps.get("models") or [], list)):
        raise _not_like(base_url, "ollama")
    loaded = {m.get("name") for m in ((ps or {}).get("models") or []) if isinstance(m, dict)}
    models = []
    for entry in tags["models"]:
        name = entry.get("name") if isinstance(entry, dict) else None
        if not isinstance(name, str) or not name:
            continue
        status, show = _get_json(base_url, "POST", "/api/show", "ollama", {"model": name})
        show = show if status == 200 and isinstance(show, dict) else {}
        capabilities = show.get("capabilities")
        if isinstance(capabilities, list) and "completion" not in capabilities:
            continue  # an embedding model can't write text
        thinks = isinstance(capabilities, list) and "thinking" in capabilities
        info = show.get("model_info") if isinstance(show.get("model_info"), dict) else {}
        context = next((v for k, v in info.items()
                        if k.endswith(".context_length") and isinstance(v, int) and not isinstance(v, bool) and v > 0),
                       DEFAULT_CONTEXT_LENGTH)
        details = show.get("details") if isinstance(show.get("details"), dict) else {}
        levels = thinks and (details.get("family") in LEVEL_THINKERS or name.startswith("gpt-oss"))
        models.append({"key": name, "display_name": name, "context_length": context,
                       "can_disable_thinking": thinks and not levels, "publisher": "",
                       "quantization": str(details.get("quantization_level") or ""),
                       "base_key": name, "thinks": thinks, "ready": name in loaded,
                       "thinking_level": "low" if levels else None})
    if not models:
        raise AIProgramError("Ollama has no models installed yet. Get one with: ollama pull llama3.2 "
                             "(in a Command Prompt), then press the refresh button next to the model list.")
    return models


def list_models(program, base_url, context_length=DEFAULT_CONTEXT_LENGTH) -> list:
    """The text models the chosen program can use now, all in the same shape."""
    kind = program_info(program)["kind"]
    if kind == "lmstudio":
        return _lmstudio_models(base_url)
    if kind == "ollama":
        return _ollama_models(base_url)
    return _basic_models(base_url, program, context_length)


# Sending one chat request and reading the streamed answer.

class FormatRefused(Exception):
    """The program refused the fixed answer format (lm_studio then asks for plain lines)."""


def _problem_message(program, status) -> str:
    if program_info(program)["kind"] == "lmstudio":
        return f"LM Studio reported a problem (error {status}). Its Developer tab shows the details."
    return (f"{program_info(program)['name']} reported a problem (error {status}). "
            "Its own window or log shows the details.")


def chat(program, base_url, body, timeout, cancel_event, model, on_progress=None) -> tuple:
    """Sends one chat request in the program's way and reads the streamed answer.
    body is the OpenAI-style request lm_studio.build_request makes; timeout is the longest silence allowed.
    on_progress(text_so_far, thinking_seen) is called for each piece. Returns (content, finish, record).
    Raises TimeoutError, Cancelled, FormatRefused or AIProgramError."""
    if program_info(program)["kind"] == "ollama":
        return _ollama_chat(base_url, body, timeout, cancel_event, model, on_progress)
    return _openai_chat(program, base_url, body, timeout, cancel_event, on_progress)


OLLAMA_MARGIN = 256  # tokens of room on top of the prompt and the answer
# The room asked for is rounded up to steps: Ollama reloads a model whenever the room changes, so an
# exact figure would reload it for nearly every request.
OLLAMA_CONTEXT_STEP = 8192
_OPTION_FIELDS = ("temperature", "top_p", "top_k", "repeat_penalty")


def _estimate(text) -> int:
    return len(text) // 4 + 1  # the same rough count as lm_studio.estimate_tokens


def ollama_body(body, model) -> dict:
    """lm_studio's OpenAI-style request in Ollama's own form, asking for room for the whole request
    (Ollama's default room is small, and its OpenAI-style route can't change it)."""
    schema = ((body.get("response_format") or {}).get("json_schema") or {}).get("schema")
    prompt = sum(_estimate(m["content"]) for m in body["messages"]) + (_estimate(json.dumps(schema)) if schema else 0)
    options = {k: body[k] for k in _OPTION_FIELDS if k in body}
    options["num_predict"] = body["max_tokens"]
    needed = prompt + body["max_tokens"] + OLLAMA_MARGIN
    steps = -(-needed // OLLAMA_CONTEXT_STEP)
    options["num_ctx"] = min(steps * OLLAMA_CONTEXT_STEP, model["context_length"])
    out = {"model": body["model"], "messages": body["messages"], "stream": True, "options": options}
    if schema:
        out["format"] = schema
    if model.get("can_disable_thinking"):
        out["think"] = False  # thinking would use up the answer room
    elif model.get("thinking_level"):
        out["think"] = model["thinking_level"]  # can't be switched off, so keep it short (it gets thinking room)
    return out


def _ollama_chat(base_url, body, timeout, cancel_event, model, on_progress) -> tuple:
    """Ollama's own /api/chat, streamed as one JSON object per line."""
    answer = {"parts": [], "finish": "", "thinking": False, "thinking_chars": 0, "error": ""}

    def on_line(line):
        try:
            data = json.loads(line)
            message = data.get("message") or {}
            content, thinking = message.get("content"), message.get("thinking")
        except (ValueError, AttributeError):
            return  # blank or broken lines are skipped
        if data.get("error"):  # Ollama reports a failure mid-answer (a crashed runner, too little memory)
            answer["error"] = str(data["error"])[:300]
            return
        if isinstance(content, str) and content:
            answer["parts"].append(content)
        if isinstance(thinking, str) and thinking:
            answer["thinking"] = True
            answer["thinking_chars"] += len(thinking)
        if data.get("done"):
            answer["finish"] = "length" if data.get("done_reason") == "length" else "stop"
        if on_progress is not None:
            on_progress("".join(answer["parts"]), answer["thinking"])

    status, _ = request(base_url, "POST", "/api/chat", ollama_body(body, model), timeout, cancel_event,
                        on_line=on_line, program="ollama")
    if status == 400 and "response_format" in body:
        raise FormatRefused()
    if status != 200:
        raise AIProgramError(_problem_message("ollama", status))
    if answer["error"]:
        raise AIProgramError(f"Ollama reported a problem: {answer['error']}")
    content = "".join(answer["parts"])
    return content, answer["finish"], {"finish": answer["finish"], "thinking_chars": answer["thinking_chars"]}


def _openai_chat(program, base_url, body, timeout, cancel_event, on_progress) -> tuple:
    """The common OpenAI-style /v1/chat/completions request, streamed as Server-Sent Events."""
    answer = {"parts": [], "other": [], "finish": "", "thinking": False, "thinking_chars": 0}

    def on_line(line):
        stripped = line.strip()
        if not stripped.startswith("data:"):
            if stripped:
                answer["other"].append(line)  # a whole JSON answer when the program didn't stream
            return
        data = stripped[len("data:"):].strip()
        if data == "[DONE]":
            return
        try:
            choice = json.loads(data)["choices"][0]
            delta = choice.get("delta") or {}
            content = delta.get("content")
            thinking = delta.get("reasoning_content") or delta.get("reasoning")
            finish = choice.get("finish_reason")
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            return  # keep-alives and broken lines are skipped
        if isinstance(content, str) and content:
            answer["parts"].append(content)
        elif thinking:
            answer["thinking"] = True
        if isinstance(thinking, str):
            answer["thinking_chars"] += len(thinking)
        if finish:
            answer["finish"] = str(finish)
        if on_progress is not None:
            on_progress("".join(answer["parts"]), answer["thinking"])

    status, _ = request(base_url, "POST", "/v1/chat/completions", body, timeout, cancel_event,
                        on_line=on_line, program=program)
    if status == 400 and "response_format" in body:
        raise FormatRefused()
    if status != 200:
        raise AIProgramError(_problem_message(program, status))
    content = "".join(answer["parts"])
    if not content and answer["other"]:
        try:
            choice = json.loads("".join(answer["other"]))["choices"][0]
            content, answer["finish"] = choice["message"]["content"] or "", choice.get("finish_reason") or ""
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            pass
    return content, answer["finish"], {"finish": answer["finish"], "thinking_chars": answer["thinking_chars"]}

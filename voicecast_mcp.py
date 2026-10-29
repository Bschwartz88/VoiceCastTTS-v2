"""
Send to VoiceCast: a small MCP server that LM Studio (or another AI program with MCP tools) starts,
so a model can send a script into VoiceCast's Script box.

Standard library only. The AI program talks to it over stdin/stdout, one JSON-RPC 2.0 message per line.
It passes scripts on to the VoiceCast studio on this PC (http://127.0.0.1, on the port the studio wrote
to output/studio_port.txt when it started; VOICECAST_PORT overrides it) and contacts nothing else. VoiceCast must be running, with "Receive scripts from LM Studio" ticked.
"""

import json
import session_access
import os
import sys
import urllib.error
import urllib.request


PORT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "studio_port.txt")
DEFAULT_PORT = 7871
PORT = None  # a fixed port (tests); None finds the studio each time with studio_port()


def _port_number(text):
    try:
        port = int(str(text).strip())
    except ValueError:
        return None
    return port if 0 < port < 65536 else None


def studio_port() -> int:
    """VOICECAST_PORT if set, else the port the studio wrote when it started
    (it moves off 7871 when another app holds it), else 7871."""
    port = _port_number(os.environ.get("VOICECAST_PORT", ""))
    if port:
        return port
    try:
        with open(PORT_FILE, encoding="utf-8") as f:
            port = _port_number(f.read(20))
    except OSError:
        port = None
    return port or DEFAULT_PORT

TIMEOUT = 10  # seconds
MAX_REQUEST_BYTES = 10 * 1024 * 1024  # the studio's request limit (web_server.MAX_BODY_BYTES)
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")  # newest first
SERVER_INFO = {"name": "voicecast", "title": "VoiceCastTTS v2", "version": "2.0"}

SENT = "Sent to VoiceCast. It goes into the Script box (if the VoiceCast page isn't open, it waits there until it is)."
NOT_RUNNING = "VoiceCast isn't running. Open VoiceCast, then send again."
TOO_LONG = "That script is too long for VoiceCast. Send a shorter part."
UNEXPECTED = ("Something other than VoiceCast answered on port {port}. "
              "Close VoiceCast and open it again, then send again.")
NO_TEXT = "There's no script to send."

TOOL = {
    "name": "send_to_voicecast",
    "title": "Send to VoiceCast",
    "description": (
        "Sends a script to VoiceCast, the text-to-speech studio on this computer, where it appears in the "
        "Script box ready to be read aloud. Use it only when the user asks to send something to VoiceCast. "
        "Send the full text, not a summary. Put each line on its own line, starting with the speaker's name "
        "in square brackets: [Narrator] for narration, and the character's name for speech, for example "
        "[Narrator] The fox looked up. then [Fox] Who's there? Don't add anything else, such as a heading "
        "or notes, unless it should be read aloud."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The script, one [Speaker] line per line."},
            "title": {"type": "string", "description": "A short name for the script, such as the story's title."},
        },
        "required": ["text"],
    },
}


def send_script(text: str, title: str = "") -> tuple:
    """Posts the script to the studio's inbox. Returns (sent, message for the model)."""
    port = PORT or studio_port()
    host = f"127.0.0.1:{port}"
    body = json.dumps({"text": text, "title": title}, ensure_ascii=False).encode("utf-8")
    if len(body) > MAX_REQUEST_BYTES:  # the studio would refuse it, and Windows may reset the connection mid-send
        return False, TOO_LONG
    token = session_access.read_session(port)
    if not token:
        return False, NOT_RUNNING
    request = urllib.request.Request(f"http://{host}/api/inbox", data=body, method="POST",
                                     headers={"Content-Type": "application/json", "Origin": f"http://{host}", "Authorization": f"Bearer {token}"})
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=TIMEOUT) as response:
            answer = json.loads(response.read(1024 * 1024).decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 413:
            return False, TOO_LONG
        return False, UNEXPECTED.format(port=port)
    except (urllib.error.URLError, OSError):  # refused, timed out or reset: nothing is listening
        return False, NOT_RUNNING
    except ValueError:  # not JSON
        return False, UNEXPECTED.format(port=port)
    if not isinstance(answer, dict):
        return False, UNEXPECTED.format(port=port)
    if answer.get("success") is True:
        return True, SENT
    return False, str(answer.get("error") or UNEXPECTED.format(port=port))


def _tool_result(text: str, is_error: bool) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _error(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def handle_message(message) -> dict:
    """Answers one JSON-RPC message from the AI program; None for notifications (they get no answer)."""
    if not isinstance(message, dict):
        return _error(None, -32600, "Invalid request")
    if "method" not in message and "id" in message:
        return None  # an answer to a request; this helper never asks anything, so ignore it
    msg_id = message.get("id")
    if message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
        return _error(msg_id, -32600, "Invalid request")
    if "id" not in message:
        return None  # a notification, such as notifications/initialized
    method, params = message["method"], message.get("params", {})
    if method == "initialize":
        asked = params.get("protocolVersion") if isinstance(params, dict) else None
        result = {"protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                  "capabilities": {"tools": {}}, "serverInfo": SERVER_INFO}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [TOOL]}
    elif method == "tools/call":
        if not isinstance(params, dict) or params.get("name") != TOOL["name"]:
            return _error(msg_id, -32602, "Unknown tool")
        arguments = params.get("arguments") or {}
        text = arguments.get("text") if isinstance(arguments, dict) else None
        title = arguments.get("title", "") if isinstance(arguments, dict) else ""
        if not isinstance(text, str) or not text.strip():
            result = _tool_result(NO_TEXT, True)
        else:
            sent, note = send_script(text, title if isinstance(title, str) else "")
            result = _tool_result(note, not sent)
    else:
        return _error(msg_id, -32601, f"Unknown method: {method}")
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def main():
    for raw in sys.stdin.buffer:
        line = raw.strip()
        if not line:
            continue
        try:
            message = json.loads(line.decode("utf-8"))
        except ValueError:
            reply = _error(None, -32700, "Parse error")
        else:
            try:
                reply = handle_message(message)
            except Exception as e:  # keep running; the AI program gets a plain error
                print(f"voicecast_mcp: {e!r}", file=sys.stderr)
                reply = _error(message.get("id") if isinstance(message, dict) else None, -32603, "Internal error")
        if reply is not None:
            sys.stdout.buffer.write(json.dumps(reply, ensure_ascii=False).encode("utf-8") + b"\n")
            sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()

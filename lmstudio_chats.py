"""Reads the chats LM Studio saves on this computer, so an AI answer can be imported into the Script box.

LM Studio keeps each chat as %USERPROFILE%\\.lmstudio\\conversations\\<id>.conversation.json. LM Studio says
that format may change, so reading is careful: anything unexpected is skipped or gives a plain message.
Nothing is ever sent anywhere; the files are only read.
"""

import json
import os
import re

from lm_studio import strip_thinking

CHATS_DIR = os.path.join(os.path.expanduser("~"), ".lmstudio", "conversations")
CHAT_SUFFIX = ".conversation.json"
MAX_CHAT_BYTES = 20 * 1024 * 1024
MAX_CHATS = 500  # the newest files; older ones are left out of the list
NAME_CHARS = 50

NO_ANSWERS = "That chat has no answers from the AI to import."
UNREADABLE = "LM Studio's chat file couldn't be read. LM Studio may have changed how it saves chats."
NOT_FOUND = "That chat isn't in LM Studio's chats folder any more. Close the Import window and open it again."
BAD_CHOICE = "Choose the last answer or all the answers."


class ChatProblem(Exception):
    """A problem with a saved chat that is safe to show to the user."""


def _texts(content) -> str:
    """The text of a message's content: a plain string, or LM Studio's list of {"type": "text", "text": …}."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content
                       if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str))
    return ""


def _thinking(block) -> bool:
    style = block.get("style")
    return block.get("type") in ("reasoning", "thinking") or (isinstance(style, dict) and style.get("type") == "thinking")


def _message(message) -> tuple:
    """(role, text) of one saved message: the version shown in LM Studio, without thinking."""
    if not isinstance(message, dict):
        return "", ""
    versions = message.get("versions")
    if isinstance(versions, list) and versions:
        chosen = message.get("currentlySelected")
        message = versions[chosen] if isinstance(chosen, int) and 0 <= chosen < len(versions) else versions[-1]
        if not isinstance(message, dict):
            return "", ""
    role = message.get("role") if isinstance(message.get("role"), str) else ""
    if isinstance(message.get("steps"), list):
        text = "".join(_texts(step.get("content")) for step in message["steps"]
                       if isinstance(step, dict) and step.get("type") == "contentBlock" and not _thinking(step))
    else:
        text = _texts(message.get("content"))
    return role, strip_thinking(text).strip()


def _messages(data) -> list:
    messages = data.get("messages") if isinstance(data, dict) else None
    return [_message(m) for m in messages] if isinstance(messages, list) else []


def _answers(data) -> list:
    return [text for role, text in _messages(data) if role == "assistant" and text]


def _read(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _name(data, messages) -> str:
    name = data.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()[:200]
    first = next((text for role, text in messages if role == "user" and text), "")
    first = re.sub(r"\s+", " ", first)
    if len(first) > NAME_CHARS:
        first = first[:NAME_CHARS].rsplit(" ", 1)[0] + "…"
    return first or "Untitled chat"


def list_chats(folder=None) -> list:
    """The saved chats that have at least one AI answer, newest first:
    {"file", "name", "model", "when" (LM Studio's time stamp), "answers"}."""
    folder = folder or CHATS_DIR
    try:
        files = [f for f in os.listdir(folder) if f.endswith(CHAT_SUFFIX)]
    except OSError:
        return []
    paths = []
    for f in files:
        try:
            info = os.stat(os.path.join(folder, f))
        except OSError:
            continue
        if info.st_size <= MAX_CHAT_BYTES:
            paths.append((info.st_mtime, f))
    chats = []
    for _, f in sorted(paths, reverse=True)[:MAX_CHATS]:
        try:
            data = _read(os.path.join(folder, f))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        messages = _messages(data)
        answers = sum(1 for role, text in messages if role == "assistant" and text)
        if not answers:
            continue
        model = data.get("lastUsedModel")
        model = model.get("identifier") if isinstance(model, dict) else ""
        when = data.get("createdAt")
        chats.append({"file": f, "name": _name(data, messages), "model": model if isinstance(model, str) else "",
                      "when": when if isinstance(when, (int, float)) and not isinstance(when, bool) else 0,
                      "answers": answers})
    chats.sort(key=lambda c: c["when"], reverse=True)
    return chats


def chat_text(folder, file, which) -> str:
    """The AI's last answer ("last") or all its answers ("all", a blank line between them) in a saved chat.
    file must be a chat file directly in the folder (no paths)."""
    folder = folder or CHATS_DIR
    if which not in ("last", "all"):
        raise ChatProblem(BAD_CHOICE)
    if (not isinstance(file, str) or not file.endswith(CHAT_SUFFIX) or os.path.basename(file) != file
            or file in (".", "..")):
        raise ChatProblem(NOT_FOUND)
    try:
        if file not in os.listdir(folder):
            raise ChatProblem(NOT_FOUND)
        path = os.path.join(folder, file)
        if os.path.getsize(path) > MAX_CHAT_BYTES:
            raise ChatProblem(UNREADABLE)
        data = _read(path)
    except OSError:
        raise ChatProblem(NOT_FOUND) from None
    except ValueError:
        raise ChatProblem(UNREADABLE) from None
    if not isinstance(data, dict):
        raise ChatProblem(UNREADABLE)
    answers = _answers(data)
    if not answers:
        raise ChatProblem(NO_ANSWERS)
    return answers[-1] if which == "last" else "\n\n".join(answers)

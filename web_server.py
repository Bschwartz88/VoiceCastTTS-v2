"""
VoiceCastTTS Web Studio Server
Provides a zero-dependency local browser interface for script editing, voice casting, and audio playback.
Runs on http://localhost:7871 using standard library http.server with ThreadingHTTPServer.
"""

import os
import sys
import re
import collections
import json
import threading
import time
import traceback
import secrets
import socket
from http.cookies import SimpleCookie
from pathlib import Path
import session_access
import urllib.parse
import webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from threading import Timer

import import_text
import lm_studio
import lmstudio_chats
import performance
import speakability
import ai_programs
import describe_voice
import voice_blends
import voice_cast
import speaker_labels
import live_audio

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(SCRIPT_DIR, "web")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output")
_LIVE_AUDIO = live_audio.LiveAudioSpool(OUTPUT_DIR)
CONFIG_PATH = os.path.join(SCRIPT_DIR, "voice_config.json")
SAMPLE_SCRIPT_PATH = os.path.join(SCRIPT_DIR, "sample_script.txt")
# Favorite voices and which voice-list folders are closed; kept between sessions.
USER_SETTINGS_PATH = os.path.join(SCRIPT_DIR, "user_settings.json")
# The port the studio got, for voicecast_mcp.py (Send to VoiceCast), since 7871 may be taken.
PORT_FILE = os.path.join(OUTPUT_DIR, "studio_port.txt")

PORT = 7871

REQUEST_TIMEOUT = 15
MAX_CLIENTS = 24

MAX_BODY_BYTES = 10 * 1024 * 1024
MAX_SCRIPT_CHARS = 2_000_000
MAX_SPEAKERS = 200
MAX_SETTINGS_ITEMS = 200
MAX_SETTINGS_TEXT = 100
MAX_NICKNAME = 30
MAX_FOLDER_NAME = 30  # your own voice folders ("Tier 2")
SPEED_RANGE = (0.5, 2.0)
FOLDER_NAME_TAKEN = "That folder name is taken by VoiceCast; pick another."
RESERVED_FOLDERS = ("favorites", "my names")  # the voice list's own Favorites and My names folders
USER_SETTINGS_KEYS = ("favorite_voices", "closed_voice_folders")
THEME_CHOICES = ("system", "light", "dark")
LLM_MODES = ("write", "split", "continue")
MAX_LLM_TEXT = 20_000
MAX_SPLIT_TEXT = 200_000  # Split my text sends a longer text in pieces (lm_studio.split_pieces)
MAX_LLM_REQUEST = 2_000  # Continue the story's optional "what should happen next"
INBOX_TITLE_CHARS = 100  # Send to VoiceCast: the title shown in the status line
CAST_VOICE_TYPES = ("Female", "Male", "Child", "Neutral")
MAX_LLM_INSTRUCTIONS = 4000
LLM_TIMEOUT_RANGE = (30, 1800)
LLM_CONTEXT_RANGE = (2048, 262144)  # tokens, for programs that don't report a model's room (basic mode)
DEFAULT_LLM_TIMEOUT = 180  # the longest silence from LM Studio, in seconds
LLM_LENGTHS = tuple(lm_studio.LENGTH_WORDS)
MAX_MODEL_PROMPTS = 50
MAX_OWN_INSTRUCTIONS = 20_000
PACING_KEYS = ("dialogue_turn_pause", "narrator_transition_pause", "same_speaker_sentence_pause")
# Only the studio's own files may run or style the page (no inline scripts or style attributes).
CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self'",
    "img-src 'self' data: blob:",
    "media-src 'self' blob:",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'none'",
    "frame-ancestors 'none'",
])
AI_OFF_MESSAGE = "Script Assistant is turned off."
GENERIC_ERROR = "Something went wrong. See the VoiceCast console window for details."

# Only one script renders at a time; a second request gets a "busy" error.
_GENERATION_LOCK = threading.Lock()
# Settings are read and written one request at a time (Windows can't replace a file that is open).
_SETTINGS_LOCK = threading.Lock()
# Set by the Stop button (or a closed browser tab); the render checks it before each line.
_CANCEL_EVENT = threading.Event()
# Script Assistant: one request to LM Studio at a time, and its own Stop signal.
_LLM_LOCK = threading.Lock()
_LLM_CANCEL_EVENT = threading.Event()


def log_exception(context: str):
    sys.stderr.write(f"[WebStudio] {context}:\n{traceback.format_exc()}\n")


class RequestError(ValueError):
    """A problem with the request that is safe to show to the user."""


def validate_user_settings(data) -> dict:
    """Checks a /api/user_settings body: favorites and folders, the theme, and the Script Assistant
    settings (off by default; the LM Studio address must be on this computer)."""
    if not isinstance(data, dict):
        raise RequestError("The settings must be a JSON object.")
    settings = {}
    for key in USER_SETTINGS_KEYS:
        items = data.get(key, [])
        if not isinstance(items, list) or len(items) > MAX_SETTINGS_ITEMS:
            raise RequestError(f"'{key}' must be a list of at most {MAX_SETTINGS_ITEMS} items.")
        clean = []
        for item in items:
            if not isinstance(item, str) or not item.strip() or len(item) > MAX_SETTINGS_TEXT:
                raise RequestError(f"Each '{key}' entry must be text of up to {MAX_SETTINGS_TEXT} characters.")
            if item not in clean:
                clean.append(item)
        settings[key] = clean
    theme = data.get("theme", "system")
    if theme not in THEME_CHOICES:
        raise RequestError("'theme' must be one of: " + ", ".join(THEME_CHOICES) + ".")
    settings["theme"] = theme

    enabled = data.get("ai_writer_enabled", False)
    if not isinstance(enabled, bool):
        raise RequestError("'ai_writer_enabled' must be true or false.")
    settings["ai_writer_enabled"] = enabled

    model = data.get("llm_model", "")
    if not isinstance(model, str) or len(model) > MAX_SETTINGS_TEXT:
        raise RequestError(f"'llm_model' must be text of up to {MAX_SETTINGS_TEXT} characters.")
    settings["llm_model"] = model

    base_url = data.get("llm_base_url", "")
    if base_url:
        try:
            base_url = lm_studio.check_local_url(base_url)
        except ValueError as e:
            raise RequestError(str(e)) from None
    settings["llm_base_url"] = base_url

    program = data.get("llm_program", ai_programs.DEFAULT_PROGRAM)
    if program not in ai_programs.PROGRAMS:
        raise RequestError("'llm_program' must be one of: " + ", ".join(ai_programs.PROGRAMS) + ".")
    settings["llm_program"] = program

    context = data.get("llm_context_length", ai_programs.DEFAULT_CONTEXT_LENGTH)
    if (isinstance(context, bool) or not isinstance(context, int)
            or not LLM_CONTEXT_RANGE[0] <= context <= LLM_CONTEXT_RANGE[1]):
        raise RequestError(f"The longest text must be a whole number from {LLM_CONTEXT_RANGE[0]:,} to "
                           f"{LLM_CONTEXT_RANGE[1]:,} tokens.")
    settings["llm_context_length"] = context

    timeout = data.get("llm_timeout_seconds", DEFAULT_LLM_TIMEOUT)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise RequestError("'llm_timeout_seconds' must be a number of seconds.")
    settings["llm_timeout_seconds"] = int(min(max(timeout, LLM_TIMEOUT_RANGE[0]), LLM_TIMEOUT_RANGE[1]))

    instructions = data.get("llm_instructions", {})
    if not isinstance(instructions, dict) or any(k not in LLM_MODES for k in instructions):
        raise RequestError("'llm_instructions' may only hold 'write' and 'split'.")
    for text in instructions.values():
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_LLM_INSTRUCTIONS:
            raise RequestError(f"Instructions must be text of up to {MAX_LLM_INSTRUCTIONS} characters.")
    settings["llm_instructions"] = dict(instructions)

    length = data.get("llm_length", "medium")
    if length not in LLM_LENGTHS:
        raise RequestError("'llm_length' must be short, medium or long.")
    settings["llm_length"] = length

    auto_audio = data.get("llm_auto_audio", False)
    if not isinstance(auto_audio, bool):
        raise RequestError("'llm_auto_audio' must be true or false.")
    settings["llm_auto_audio"] = auto_audio

    auto_split = data.get("llm_auto_split", False)
    if not isinstance(auto_split, bool):
        raise RequestError("'llm_auto_split' must be true or false.")
    settings["llm_auto_split"] = auto_split

    import_links = data.get("import_links_enabled", False)
    if not isinstance(import_links, bool):
        raise RequestError("'import_links_enabled' must be true or false.")
    settings["import_links_enabled"] = import_links

    inbox = data.get("inbox_enabled", False)
    if not isinstance(inbox, bool):
        raise RequestError("'inbox_enabled' must be true or false.")
    settings["inbox_enabled"] = inbox

    prompts = data.get("llm_model_prompts", {})
    if not isinstance(prompts, dict) or len(prompts) > MAX_MODEL_PROMPTS:
        raise RequestError(f"'llm_model_prompts' can hold at most {MAX_MODEL_PROMPTS} models.")
    clean_prompts = {}
    for key, entry in prompts.items():
        if not key.strip() or len(key) > MAX_SETTINGS_TEXT or not isinstance(entry, dict):
            raise RequestError("Each 'llm_model_prompts' entry needs a model name and its instructions.")
        text, use_own = entry.get("text", ""), entry.get("use_own", False)
        if not isinstance(text, str) or len(text) > MAX_OWN_INSTRUCTIONS:
            raise RequestError(f"Instructions can be up to {MAX_OWN_INSTRUCTIONS:,} characters.")
        if not isinstance(use_own, bool):
            raise RequestError("'use_own' must be true or false.")
        if text.strip() or use_own:
            clean_prompts[key] = {"text": text, "use_own": use_own}
    settings["llm_model_prompts"] = clean_prompts

    nicknames = data.get("voice_nicknames", {})
    if not isinstance(nicknames, dict) or len(nicknames) > MAX_SETTINGS_ITEMS:
        raise RequestError(f"'voice_nicknames' can hold at most {MAX_SETTINGS_ITEMS} voices.")
    clean_nicknames, taken = {}, set()
    for voice, nickname in nicknames.items():
        if not voice.strip() or len(voice) > MAX_SETTINGS_TEXT or not isinstance(nickname, str):
            raise RequestError("Each nickname needs a voice name and some text.")
        nickname = " ".join(nickname.split())
        if not nickname:
            continue
        if len(nickname) > MAX_NICKNAME:
            raise RequestError(f"Nicknames can be up to {MAX_NICKNAME} characters.")
        if nickname.lower() in taken:
            raise RequestError(f"Two voices can't have the same nickname: \"{nickname}\".")
        taken.add(nickname.lower())
        clean_nicknames[voice] = nickname
    settings["voice_nicknames"] = clean_nicknames

    narrator = data.get("default_narrator", {})
    if not isinstance(narrator, dict):
        raise RequestError("'default_narrator' must be a voice and a speed.")
    if narrator:
        voice, speed = narrator.get("voice"), narrator.get("speed")
        if not isinstance(voice, str) or not voice.strip() or len(voice) > MAX_SETTINGS_TEXT:
            raise RequestError("The default Narrator needs a voice name.")
        if (isinstance(speed, bool) or not isinstance(speed, (int, float))
                or not SPEED_RANGE[0] <= speed <= SPEED_RANGE[1]):
            raise RequestError(f"The default Narrator's speed must be from {SPEED_RANGE[0]} to {SPEED_RANGE[1]}.")
        narrator = {"voice": voice, "speed": round(float(speed), 2)}
    settings["default_narrator"] = narrator

    folders = data.get("voice_folders", {})
    if not isinstance(folders, dict) or len(folders) > MAX_SETTINGS_ITEMS:
        raise RequestError(f"'voice_folders' can hold at most {MAX_SETTINGS_ITEMS} voices.")
    clean_folders = {}
    for voice, folder in folders.items():
        if not voice.strip() or len(voice) > MAX_SETTINGS_TEXT or not isinstance(folder, str):
            raise RequestError("Each folder entry needs a voice name and a folder name.")
        folder = " ".join(folder.split())
        if not folder:
            continue
        if len(folder) > MAX_FOLDER_NAME:
            raise RequestError(f"Folder names can be up to {MAX_FOLDER_NAME} characters.")
        if re.sub(r"^[^\w]+", "", folder).lower() in RESERVED_FOLDERS:
            raise RequestError(FOLDER_NAME_TAKEN)
        clean_folders[voice] = folder
    settings["voice_folders"] = clean_folders
    return settings


def _retry_if_busy(action, attempts=5):
    """Runs action(), retrying briefly if Windows reports the file busy (antivirus, indexing)."""
    for attempt in range(attempts):
        try:
            return action()
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.05 * (attempt + 1))


def _read_settings_file():
    with open(USER_SETTINGS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_user_settings() -> dict:
    """Returns the saved user settings, or empty ones if the file is missing or damaged."""
    try:
        with _SETTINGS_LOCK:
            return validate_user_settings(_retry_if_busy(_read_settings_file))
    except FileNotFoundError:
        pass
    except Exception:
        log_exception("Could not read user_settings.json; starting with empty settings")
    return validate_user_settings({})


def save_user_settings(settings: dict):
    """Writes the settings atomically, so a crash mid-write can't leave a half-written file."""
    tmp_path = USER_SETTINGS_PATH + ".tmp"
    with _SETTINGS_LOCK:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
        _retry_if_busy(lambda: os.replace(tmp_path, USER_SETTINGS_PATH))



ASSISTANT_PROBLEM_PATH = os.path.join(OUTPUT_DIR, "script_assistant_problem.txt")


def save_assistant_problem(mode, model, length) -> str:
    """Saves what the model sent when VoiceCast couldn't use it, for troubleshooting.
    Returns a sentence for the error message (empty if the file couldn't be written)."""
    parts = [f"Script Assistant problem, {time.strftime('%Y-%m-%d %H:%M:%S')}",
             f"Model: {model} | mode: {mode} | length: {length}", ""]
    for i, a in enumerate(lm_studio.last_answers, 1):
        parts += [f"--- Request {i}: {'fixed format' if a['structured'] else 'plain text'} | "
                  f"max_tokens {a['max_tokens']} | finish: {a['finish'] or 'none'} | "
                  f"thinking characters: {a['thinking_chars']} | answer characters: {len(a['answer'])} ---",
                  a["answer"], ""]
    try:
        os.makedirs(os.path.dirname(ASSISTANT_PROBLEM_PATH), exist_ok=True)
        with open(ASSISTANT_PROBLEM_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(parts))
    except OSError:
        return ""
    return " What the model sent is saved in output/script_assistant_problem.txt."


def llm_settings() -> dict:
    """The Script Assistant settings: the user's choices over the voice_config.json defaults."""
    settings = load_user_settings()
    defaults = voice_cast.load_config(CONFIG_PATH).get("llm_defaults", {})
    instructions = dict(defaults.get("instructions", {}))
    instructions.update(settings["llm_instructions"])
    program = settings["llm_program"]
    info = ai_programs.program_info(program)
    default_url = (defaults.get("base_url") or lm_studio.DEFAULT_BASE_URL) if info["kind"] == "lmstudio" else info["default_url"]
    return {
        "enabled": settings["ai_writer_enabled"],
        "program": program,
        "program_name": info["name"],
        "context_length": settings["llm_context_length"],
        "base_url": settings["llm_base_url"] or default_url or lm_studio.DEFAULT_BASE_URL,
        "timeout": settings["llm_timeout_seconds"],
        "instructions": instructions,
        "last_model": settings["llm_model"],
        "length": settings["llm_length"],
        "model_prompts": settings["llm_model_prompts"],
    }


def validate_generate_payload(data, config: dict):
    """Checks a /api/generate body and returns (script, voices, pacing) with values clamped to safe ranges."""
    if not isinstance(data, dict):
        raise RequestError("The request body must be a JSON object.")

    script = data.get("script", "")
    if not isinstance(script, str):
        raise RequestError("The script must be text.")
    if len(script) > MAX_SCRIPT_CHARS:
        raise RequestError(f"The script is too long (limit {MAX_SCRIPT_CHARS:,} characters).")

    voices = data.get("voices")
    clean_voices = None
    if voices is not None:
        if not isinstance(voices, dict) or len(voices) > MAX_SPEAKERS:
            raise RequestError(f"The cast must be a list of at most {MAX_SPEAKERS} speakers.")
        known = voice_cast.known_voice_ids(config)
        clean_voices = {}
        for speaker, cfg in voices.items():
            if not isinstance(speaker, str) or not isinstance(cfg, dict):
                raise RequestError("Each cast entry needs a speaker name and settings.")
            voice_id = cfg.get("voice")
            if voice_id not in known:
                raise RequestError(f"Unknown voice for {speaker[:40]!r}.")
            clean_voices[speaker[:200]] = {"voice": voice_id, "speed": voice_cast.clamp_speed(cfg.get("speed", 1.0))}

    pacing = data.get("pacing")
    clean_pacing = None
    if pacing is not None:
        if not isinstance(pacing, dict):
            raise RequestError("Pacing must be a set of pause lengths.")
        defaults = config.get("pacing_seconds", {})
        clean_pacing = {
            key: voice_cast.clamp_pause(pacing.get(key, defaults.get(key, 0.0)), defaults.get(key, 0.0))
            for key in PACING_KEYS
        }

    try:
        pronunciations = speakability.normalize_pronunciations(data.get("pronunciations"))
    except ValueError as e:
        raise RequestError(str(e))

    return script, clean_voices, clean_pacing, pronunciations


BLEND_PATHS = ("/api/blend/preview", "/api/blend/save", "/api/blend/delete")
BLEND_BUSY = "Wait until the audio is finished, then try again."


def blend_action(path: str, data) -> dict:
    """The blender's actions. Save and delete change the voices a render uses, so they wait for it."""
    if not isinstance(data, dict):
        raise RequestError("The request could not be read.")
    if path == "/api/blend/preview":
        text = data.get("text") or ""
        if not isinstance(text, str):
            raise RequestError("The sample sentence must be text.")
        url = voice_cast.generate_blend_preview(data.get("recipe"), text[:voice_cast.MAX_PREVIEW_CHARS],
                                                pitch=data.get("pitch", 0))
        return {"success": True, "preview_url": url}
    if not _GENERATION_LOCK.acquire(blocking=False):
        return {"success": False, "error": BLEND_BUSY}
    try:
        if path == "/api/blend/save":
            description = data.get("description") or ""
            if not isinstance(description, str) or len(description.strip()) > describe_voice.MAX_DESCRIPTION:
                raise RequestError(f"Keep the description to {describe_voice.MAX_DESCRIPTION} characters or fewer.")
            name = voice_cast.save_blend(data.get("name"), data.get("recipe"), data.get("voice_type"),
                                         replace=data.get("replace") is True, description=description.strip(),
                                         pitch=data.get("pitch", 0), start_speed=data.get("start_speed"))
            return {"success": True, "voice": name}
        voice_cast.delete_blend(data.get("name"))
        return {"success": True}
    finally:
        _GENERATION_LOCK.release()


DESCRIBE_EMPTY = "Describe the voice first, for example: a grumpy old troll with a deep voice."


_DESCRIBE_CANCEL_EVENT = threading.Event()  # Describe a voice's own Stop, so it never stops a script being written
_DESCRIBE_STOPPED = collections.deque(maxlen=20)  # the page's request ids that were stopped: a Stop can arrive first
_DESCRIBE_ID = None  # the id of the describe request using the AI program, if any
_LLM_USER = None  # "describe" while Describe a voice holds _LLM_LOCK (for the Script Assistant's message)
DESCRIBE_HOLDS_AI = "Describe a voice is using the AI program. Wait a moment, or press Stop next to the description."


def describe_action(data) -> dict:
    """Describe a voice: the AI program reads the description when the Script Assistant is on
    (one AI request at a time, Stop via /api/blend/describe/cancel); word matching otherwise or when that fails."""
    global _LLM_USER, _DESCRIBE_ID
    if not isinstance(data, dict):
        raise RequestError("The request could not be read.")
    request_id = data.get("id") if isinstance(data.get("id"), str) else None
    text = data.get("description")
    text = text.strip() if isinstance(text, str) else ""
    if not text:
        raise RequestError(DESCRIBE_EMPTY)
    if len(text) > describe_voice.MAX_DESCRIPTION:
        raise RequestError(f"Keep the description to {describe_voice.MAX_DESCRIPTION} characters or fewer.")
    traits, helper, note = None, "words", ""
    llm = llm_settings()
    if llm["enabled"]:
        if not llm["last_model"]:
            note = "No model is chosen in the Script Assistant, so I used word matching."
        elif not _LLM_LOCK.acquire(blocking=False):
            note = "The Script Assistant is busy, so I used word matching."
        else:
            try:
                _LLM_USER = "describe"
                _DESCRIBE_CANCEL_EVENT.clear()
                _DESCRIBE_ID = request_id
                if request_id is not None and request_id in _DESCRIBE_STOPPED:  # its Stop came first
                    raise lm_studio.Cancelled()
                traits = lm_studio.describe_voice_traits(
                    llm["base_url"], llm["last_model"], text, min(llm["timeout"], lm_studio.DESCRIBE_TIMEOUT),
                    _DESCRIBE_CANCEL_EVENT, program=llm["program"], context_length=llm["context_length"])
                helper = "ai"
                if describe_voice.nothing_understood(traits):
                    note = f"{llm['program_name']} couldn't make sense of the description, so I used word matching."
            except lm_studio.Cancelled:
                return {"success": False, "error": "Stopped.", "stopped": True}
            except lm_studio.LMStudioError as e:
                note = lm_studio.as_sentence(e) + " I used word matching instead."
            finally:
                _LLM_USER = _DESCRIBE_ID = None
                _LLM_LOCK.release()
    if traits is None or describe_voice.nothing_understood(traits):
        traits, helper = describe_voice.read_description(text), "words"
    config = voice_cast.load_config(CONFIG_PATH)
    available = voice_cast.known_voice_ids(config)
    # The built-in voices' traits, then the owner's own voices' (a private file next to those voices).
    table = {**config.get("voice_traits", {}),
             **describe_voice.read_traits_file(os.path.join(voice_cast.CUSTOM_VOICES_DIR, describe_voice.TRAITS_FILE))}
    result = describe_voice.suggest(traits, table, available)
    if result is None:
        return {"success": False, "error": describe_voice.NOTHING_TO_GO_ON, "helper": helper, "note": note,
                "unknown": traits["unknown"]}
    return {"success": True, "recipe": result["recipe"], "voice_type": result["voice_type"],
            "explanation": result["explanation"], "name": describe_voice.suggest_name(text, available),
            "helper": helper, "note": " ".join(n for n in (note, result["note"]) if n),
            "unknown": traits["unknown"]}


IMPORT_PATHS = ("/api/import/text", "/api/import/google_doc", "/api/import/lmstudio_chat")
IMPORT_LINKS_OFF = ("Google Docs links are turned off. Tick \"Allow Google Docs links\" "
                    "in the Import window first.")


def import_action(path: str, data) -> dict:
    """Import: tidies a file's text, or fetches a Google Doc shared by link (only when allowed)."""
    if not isinstance(data, dict):
        raise RequestError("The request could not be read.")
    if path == "/api/import/text":
        filename, text = data.get("filename"), data.get("text")
        if not isinstance(filename, str) or not isinstance(text, str):
            raise RequestError("The file could not be read.")
        result = import_text.tidy_file(filename, text)
    elif path == "/api/import/lmstudio_chat":  # a chat saved by LM Studio on this computer
        result = lmstudio_chats.chat_text(lmstudio_chats.CHATS_DIR, data.get("file"), data.get("which"))
    else:
        if not load_user_settings()["import_links_enabled"]:
            raise RequestError(IMPORT_LINKS_OFF)
        link = data.get("url")
        if not isinstance(link, str):
            raise RequestError(import_text.NOT_A_DOC_LINK)
        result = import_text.fetch_google_doc(link)
    if not result.strip():
        raise RequestError(import_text.EMPTY)
    if len(result) > MAX_SCRIPT_CHARS:
        raise RequestError(f"That's too much text to import (the limit is {MAX_SCRIPT_CHARS:,} characters).")
    return {"success": True, "text": result, "words": import_text.count_words(result)}


INBOX_PATHS = ("/api/inbox", "/api/inbox/take")
INBOX_OFF = ("Receiving scripts is switched off in VoiceCast. Tick \"Receive scripts from LM Studio\" "
             "in VoiceCast's Import window, then send again.")
_INBOX_LOCK = threading.Lock()
_inbox_item = None  # Send to VoiceCast: one script waiting for the page ({"text", "title"}); a newer one replaces it


def clear_inbox():
    global _inbox_item
    with _INBOX_LOCK:
        _inbox_item = None


def inbox_action(path: str, data) -> dict:
    """Send to VoiceCast: voicecast_mcp.py posts a script to /api/inbox; the open page takes it
    with /api/inbox/take (once). Both are refused while "Receive scripts from LM Studio" is off."""
    global _inbox_item
    if not load_user_settings()["inbox_enabled"]:
        clear_inbox()
        return {"success": False, "code": "inbox_off", "error": INBOX_OFF}
    if path == "/api/inbox/take":
        with _INBOX_LOCK:
            item, _inbox_item = _inbox_item, None
        return {"success": True, "item": item}
    if not isinstance(data, dict):
        raise RequestError("The request could not be read.")
    text, title = data.get("text"), data.get("title", "")
    if not isinstance(text, str) or not text.strip():
        raise RequestError("There's no script to send.")
    if len(text) > MAX_SCRIPT_CHARS:
        raise RequestError(f"That script is too long for VoiceCast (the limit is {MAX_SCRIPT_CHARS:,} characters).")
    if not isinstance(title, str):
        raise RequestError("The title must be text.")
    with _INBOX_LOCK:
        _inbox_item = {"text": text, "title": " ".join(title.split())[:INBOX_TITLE_CHARS]}
    return {"success": True}


def validate_llm_payload(data):
    """Checks a /api/llm/write_script body and returns (mode, text, model, cast, length, request).
    In continue mode, text is the story so far and request the optional "what should happen next"."""
    if not isinstance(data, dict):
        raise RequestError("The request body must be a JSON object.")
    mode = data.get("mode")
    if mode not in LLM_MODES:
        raise RequestError("'mode' must be 'write', 'split' or 'continue'.")
    text = data.get("text")
    if not isinstance(text, str) or not text.strip():
        raise RequestError({"write": "Type what you want written, or paste the text to split.",
                            "split": "Paste the text to split into speakers.",
                            "continue": "There's no story to continue."}[mode])
    limit = MAX_SPLIT_TEXT if mode == "split" else MAX_LLM_TEXT
    if len(text) > limit:
        raise RequestError(f"The story is too long to continue (the limit is {limit:,} characters)."
                           if mode == "continue" else
                           f"That's too much text at once (the limit is {limit:,} characters).")
    model = data.get("model")
    if not isinstance(model, str) or not model or len(model) > MAX_SETTINGS_TEXT:
        raise RequestError("Pick a model first.")
    cast = data.get("cast", [])
    if not isinstance(cast, list) or len(cast) > MAX_SPEAKERS:
        raise RequestError("'cast' must be a list of speakers.")
    clean_cast = []
    for c in cast:
        if (not isinstance(c, dict) or not isinstance(c.get("name"), str) or not c["name"].strip()
                or len(c["name"]) > MAX_SETTINGS_TEXT or c.get("voice_type") not in CAST_VOICE_TYPES):
            raise RequestError("Each cast entry needs a name and a voice type.")
        clean_cast.append({"name": c["name"].strip(), "voice_type": c["voice_type"]})
    length = data.get("length", "medium")
    if length not in LLM_LENGTHS:
        raise RequestError("'length' must be short, medium or long.")
    request = data.get("request") or ""
    if not isinstance(request, str) or len(request) > MAX_LLM_REQUEST:
        raise RequestError(f"'What should happen next' can be at most {MAX_LLM_REQUEST:,} characters.")
    return mode, text.strip(), model, clean_cast, length, request.strip()


def served_file(root, relative):
    """Resolve Windows junctions/symlinks before enforcing the fixed served root."""
    base = Path(root).resolve()
    target = (base / relative).resolve()
    if not target.is_relative_to(base) or target == base:
        raise ValueError('Outside served root')
    return str(target)


class StudioHandler(BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(REQUEST_TIMEOUT)
        super().setup()

    def has_session(self):
        expected = self.server.session_token
        supplied = self.headers.get('Authorization', '')
        if supplied.startswith('Bearer ') and secrets.compare_digest(supplied[7:].encode('utf-8'), expected.encode('ascii')):
            return True
        try:
            cookies = SimpleCookie(self.headers.get('Cookie', ''))
            item = cookies.get(self.server.cookie_name)
            return item is not None and secrets.compare_digest(item.value.encode('utf-8'), expected.encode('ascii'))
        except Exception:
            return False

    def trusted_fetch(self):
        origin = self.headers.get('Origin')
        site = self.headers.get('Sec-Fetch-Site')
        return (not origin or origin.lower() in {f'http://{h}' for h in self.allowed_hosts()}) and site in (None, 'same-origin', 'none')

    def authorize_read(self):
        if not self.is_trusted_host():
            return False
        if not self.trusted_fetch():
            self.send_error(403, 'Forbidden')
            return False
        path = urllib.parse.urlsplit(self.path).path
        # Public bootstrap contains no settings, stories, session token or media.
        public = path in ('/', '/index.html', '/logo.png', '/logo_128.png', '/favicon.ico') or path.startswith('/static/')
        if not public and not self.has_session():
            self.send_error(401, 'Open VoiceCastTTS v2 from its desktop launcher.')
            return False
        return True

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        super().end_headers()

    def allowed_hosts(self):
        port = self.server.server_address[1]
        return {f"127.0.0.1:{port}", f"localhost:{port}"}

    def is_trusted_host(self) -> bool:
        """Blocks DNS-rebinding: the browser must be talking to us by our local address."""
        if (self.headers.get("Host") or "").lower() in self.allowed_hosts():
            return True
        self.send_error(403, "Forbidden")
        return False

    def is_trusted_origin(self) -> bool:
        """Blocks other websites from sending requests to the studio (cross-site request forgery)."""
        origin = (self.headers.get("Origin") or "").lower()
        if origin in {f"http://{h}" for h in self.allowed_hosts()}:
            return True
        self.send_error(403, "Forbidden")
        return False

    def send_json(self, payload: dict, status: int = 200):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        if not self.is_trusted_host():
            return
        self.send_response(204)
        self.send_header("Allow", "GET, POST, HEAD, OPTIONS")
        self.end_headers()

    def do_HEAD(self):
        if self.authorize_read():
            self.handle_get_or_head(is_head=True)

    def do_GET(self):
        if self.authorize_read():
            self.handle_get_or_head(is_head=False)

    def send_audio_bytes(self, audio_data: bytes, is_head: bool = False, filename: str = None):
        total_size = len(audio_data)
        range_header = self.headers.get("Range")

        if range_header and range_header.startswith("bytes="):
            try:
                range_val = range_header[len("bytes="):].strip()
                start_str, end_str = range_val.split("-")
                start = int(start_str) if start_str else 0
                end = int(end_str) if end_str else total_size - 1
                if start >= total_size:
                    self.send_error(416, "Requested Range Not Satisfiable")
                    return
                end = min(end, total_size - 1)
                content_len = end - start + 1

                self.send_response(206)
                self.send_header("Content-Type", "audio/wav")
                if filename:
                    self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                self.send_header("Content-Range", f"bytes {start}-{end}/{total_size}")
                self.send_header("Content-Length", str(content_len))
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()

                if not is_head:
                    self.wfile.write(audio_data[start:end + 1])
                return
            except Exception:
                pass

        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(total_size))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

        if not is_head:
            self.wfile.write(audio_data)

    def send_zip_bytes(self, zip_data: bytes, filename: str = "VoiceCast_All_Tracks.zip", is_head: bool = False):
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(zip_data)))
        self.end_headers()
        if not is_head:
            self.wfile.write(zip_data)

    def send_audio_file(self, filepath: str, is_head: bool = False):
        if not os.path.exists(filepath) or not os.path.isfile(filepath):
            self.send_error(404, "Audio file not found")
            return
        try:
            with open(filepath, "rb") as f:
                data = f.read()
            self.send_audio_bytes(data, is_head=is_head)
        except Exception:
            log_exception("Error reading audio file")
            self.send_error(500, "Error reading audio file")

    def handle_get_or_head(self, is_head: bool = False):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ["/", "/index.html"]:
            html_path = os.path.join(WEB_DIR, "index.html")
            if os.path.exists(html_path):
                with open(html_path, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                if not is_head:
                    self.wfile.write(content)
            else:
                self.send_error(404, "UI template not found.")
            return

        if path.startswith("/static/") or path in ["/logo.png", "/logo_128.png", "/favicon.ico"]:
            # The browser tab icon is the small logo.
            clean_rel = "logo_128.png" if path == "/favicon.ico" else path.lstrip("/")
            try:
                safe_target = served_file(WEB_DIR, urllib.parse.unquote(clean_rel))
                if os.path.isfile(safe_target):
                    ext = os.path.splitext(safe_target)[1].lower()
                    mime_types = {
                        ".png": "image/png",
                        ".jpg": "image/jpeg",
                        ".jpeg": "image/jpeg",
                        ".svg": "image/svg+xml",
                        ".ico": "image/x-icon",
                        ".css": "text/css; charset=utf-8",
                        ".js": "text/javascript; charset=utf-8"
                    }
                    content_type = mime_types.get(ext, "application/octet-stream")
                    with open(safe_target, "rb") as f:
                        file_data = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(file_data)))
                    # Always check for a newer copy, so page updates show up after a restart.
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    if not is_head:
                        self.wfile.write(file_data)
                    return
            except Exception:
                pass

        if path == "/api/lmstudio/chats":  # LM Studio's saved chats, for the Import window
            try:
                payload = {"success": True, "chats": lmstudio_chats.list_chats(lmstudio_chats.CHATS_DIR)}
            except Exception:
                log_exception("Could not list LM Studio's chats")
                payload = {"success": False, "error": GENERIC_ERROR}
            self.send_json(payload)
            return

        if path == "/api/llm/status":
            try:
                llm = llm_settings()
                if not llm["enabled"]:
                    payload = {"success": False, "error": AI_OFF_MESSAGE}
                else:
                    status = lm_studio.get_status(llm["base_url"], llm["program"], llm["context_length"])
                    payload = {"success": True, "base_url": llm["base_url"], "models": status["models"],
                               "last_model": llm["last_model"], "program": llm["program"],
                               "program_name": llm["program_name"]}
            except lm_studio.LMStudioError as e:
                payload = {"success": False, "error": str(e)}
            except Exception:
                log_exception("Checking the AI program failed")
                payload = {"success": False, "error": GENERIC_ERROR}
            self.send_json(payload)
            return

        if path == "/api/llm/model_settings":
            try:
                llm = llm_settings()
                if not llm["enabled"]:
                    raise RequestError(AI_OFF_MESSAGE)
                key = urllib.parse.parse_qs(parsed.query).get("model", [""])[0]
                if not key or len(key) > MAX_SETTINGS_TEXT:
                    raise RequestError("Pick a model first.")
                info = lm_studio.describe_model_settings(llm["base_url"], key, llm["model_prompts"].get(key),
                                                         llm["instructions"]["write"], program=llm["program"],
                                                         context_length=llm["context_length"])
                payload = {"success": True, **info}
            except (RequestError, lm_studio.LMStudioError) as e:
                payload = {"success": False, "error": str(e)}
            except Exception:
                log_exception("Reading the model's settings failed")
                payload = {"success": False, "error": GENERIC_ERROR}
            self.send_json(payload)
            return

        if path == "/api/config":
            config = voice_cast.load_config(CONFIG_PATH)
            sample_script = ""
            if os.path.exists(SAMPLE_SCRIPT_PATH):
                with open(SAMPLE_SCRIPT_PATH, "r", encoding="utf-8") as f:
                    sample_script = f.read()

            payload = {
                "config": config,
                "speaker_label_rules": speaker_labels.RULES,
                "sample_script": sample_script,
                "user_settings": load_user_settings(),
                "ai_programs": [{"id": k, **v} for k, v in ai_programs.PROGRAMS.items()],
            }
            data = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if not is_head:
                self.wfile.write(data)
            return

        if path == "/api/preview_voice":
            query = urllib.parse.parse_qs(parsed.query)
            raw_voice_id = query.get("voice", ["af_heart"])[0]
            voice_id = re.sub(r'[^a-zA-Z0-9_\-]', '', raw_voice_id) or "af_heart"
            speed_val = voice_cast.clamp_speed(query.get("speed", ["1.0"])[0])
            sample_text = query.get("text", [""])[0][:voice_cast.MAX_PREVIEW_CHARS]
            # The page sends only the dictionary entries that appear in the sample text.
            try:
                pronunciations = speakability.normalize_pronunciations(json.loads(query.get("pron", ["[]"])[0][:20000]))
            except (ValueError, TypeError):
                pronunciations = []

            try:
                preview_url = voice_cast.generate_voice_preview(voice_id, speed=speed_val, text=sample_text,
                                                                pronunciations=pronunciations)
                payload = {"success": True, "preview_url": preview_url}
            except Exception:
                log_exception(f"Preview failed for voice {voice_id!r}")
                payload = {"success": False, "error": GENERIC_ERROR}

            data = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if not is_head:
                self.wfile.write(data)
            return

        if path == "/api/audio/latest.wav":
            query = urllib.parse.parse_qs(parsed.query)
            is_dl = query.get("download", ["0"])[0] == "1"
            latest_bytes = voice_cast.get_latest_audio()
            if latest_bytes:
                fn = "full_dialogue.wav" if is_dl else None
                self.send_audio_bytes(latest_bytes, is_head=is_head, filename=fn)
            else:
                self.send_error(404, "No generated audio available yet")
            return

        if path == "/api/audio/track":
            query = urllib.parse.parse_qs(parsed.query)
            speaker = query.get("speaker", [""])[0]
            track_bytes = voice_cast.get_speaker_track_audio(speaker)
            if track_bytes:
                self.send_audio_bytes(track_bytes, is_head=is_head, filename=voice_cast.track_filename(speaker))
            else:
                self.send_error(404, f"Track for speaker '{speaker}' not found")
            return

        if path == "/api/audio/tracks.zip":
            zip_bytes = voice_cast.get_all_tracks_zip_bytes()
            if zip_bytes:
                self.send_zip_bytes(zip_bytes, filename="VoiceCast_All_Tracks.zip", is_head=is_head)
            else:
                self.send_error(404, "No tracks available to zip")
            return

        if path.startswith("/output/"):
            rel_path = urllib.parse.unquote(path[len("/output/"):].lstrip("/\\"))
            try:
                safe_target = served_file(OUTPUT_DIR, rel_path)
                if Path(safe_target).suffix.lower() != '.wav':
                    raise ValueError('Only generated WAV audio is served')
            except (OSError, ValueError):
                self.send_error(403, 'Forbidden')
                return

            self.send_audio_file(safe_target, is_head=is_head)
            return

        self.send_error(404, "Not Found")

    def do_POST(self):
        self.close_connection = True
        if not (self.is_trusted_host() and self.is_trusted_origin()):
            return
        if not self.trusted_fetch():
            self.send_error(403, 'Forbidden')
            return
        if self.path == '/api/session':
            # Secret is supplied by the trusted launcher in a URL fragment, never by bootstrap HTML.
            if not self.has_session():
                self.send_error(401, 'Invalid session')
                return
            self.send_response(204)
            self.send_header('Set-Cookie', f'{self.server.cookie_name}={self.server.session_token}; HttpOnly; SameSite=Strict; Path=/')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if not self.has_session():
            self.send_error(401, 'Open VoiceCastTTS v2 from its desktop launcher.')
            return
        lengths = self.headers.get_all('Content-Length', [])
        try:
            content_length = int(lengths[0]) if len(lengths) == 1 else (0 if not lengths else -1)
        except ValueError:
            content_length = -1
        if content_length < 0 or self.headers.get('Transfer-Encoding'):
            self.send_error(400, 'Invalid request framing')
            return
        if content_length > MAX_BODY_BYTES:
            self.send_error(413, 'Payload Too Large: maximum script payload is 10 MB')
            return
        try:
            deadline = time.monotonic() + REQUEST_TIMEOUT
            pieces = []
            remaining = content_length
            while remaining:
                self.connection.settimeout(max(0.01, deadline - time.monotonic()))
                if time.monotonic() >= deadline:
                    raise TimeoutError()
                chunk = self.rfile.read1(min(65536, remaining))
                if not chunk:
                    break
                pieces.append(chunk)
                remaining -= len(chunk)
            body = b''.join(pieces)
            self.connection.settimeout(REQUEST_TIMEOUT)
        except (TimeoutError, OSError):
            self.send_error(408, 'Request body timed out')
            return
        if len(body) != content_length:
            self.send_error(400, 'Incomplete request body')
            return

        if self.path == "/api/cancel":
            rendering = _GENERATION_LOCK.locked()
            if rendering:
                _CANCEL_EVENT.set()
            self.send_json({"success": True, "was_rendering": rendering})
            return

        if self.path == "/api/blend/describe/cancel":
            try:
                stop_id = json.loads(body.decode("utf-8") or "{}").get("id")
            except (ValueError, AttributeError):
                stop_id = None
            if isinstance(stop_id, str):
                _DESCRIBE_STOPPED.append(stop_id[:100])
            if not isinstance(stop_id, str) or stop_id == _DESCRIBE_ID:  # no id: stop whatever is running
                _DESCRIBE_CANCEL_EVENT.set()
            self.send_json({"success": True})
            return

        if self.path == "/api/llm/cancel":
            writing = _LLM_LOCK.locked()
            if writing:
                _LLM_CANCEL_EVENT.set()
            self.send_json({"success": True, "was_writing": writing})
            return

        if self.path == "/api/llm/write_script":
            content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if content_type != "application/json":
                self.send_error(415, "Content-Type must be application/json")
                return
            # Stream progress via Server-Sent Events, then one "result" event (like /api/generate).
            # The headers go out before the lock is taken, so a tab that is already gone can't keep it.
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            holding = _LLM_LOCK.acquire(blocking=False)

            def send_event(event):
                try:
                    self.wfile.write(f"data: {json.dumps(event)}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except OSError:
                    # The tab closed or reloaded: stop LM Studio writing for nobody.
                    if holding:
                        _LLM_CANCEL_EVENT.set()

            if not holding:
                send_event({"type": "result", "success": False,
                            "error": DESCRIBE_HOLDS_AI if _LLM_USER == "describe"
                            else "Already writing a script. Wait for it to finish or press Stop."})
                return
            try:
                _LLM_CANCEL_EVENT.clear()
                llm = llm_settings()
                if not llm["enabled"]:
                    raise RequestError(AI_OFF_MESSAGE)
                mode, text, model, cast, length, request = validate_llm_payload(json.loads(body.decode("utf-8")))
                result = lm_studio.write_script(llm["base_url"], model, mode, text, cast,
                                                llm["instructions"]["split" if mode == "split" else "write"],
                                                llm["timeout"], _LLM_CANCEL_EVENT,
                                                length=length, own=llm["model_prompts"].get(model),
                                                on_progress=lambda p: send_event({"type": "progress", **p}),
                                                request=request, program=llm["program"],
                                                context_length=llm["context_length"])
                payload = {"success": True, **result}
            except lm_studio.Cancelled:
                payload = {"success": False, "error": "Stopped.", "stopped": True}
            except (RequestError, lm_studio.LMStudioError) as e:
                payload = {"success": False, "error": str(e)}
                if str(e) in (lm_studio.NOTHING_USABLE, lm_studio.THOUGHT_TOO_LONG):
                    payload["error"] += save_assistant_problem(mode, model, length)
            except ValueError:  # bad JSON or text encoding
                payload = {"success": False, "error": "The request could not be read."}
            except Exception:
                log_exception("Script Assistant failed")
                payload = {"success": False, "error": GENERIC_ERROR}
            finally:
                _LLM_LOCK.release()
            send_event({"type": "result", **payload})
            return

        if self.path == "/api/blend/describe":
            content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if content_type != "application/json":
                self.send_error(415, "Content-Type must be application/json")
                return
            try:
                data = json.loads(body.decode("utf-8"))
            except ValueError:  # bad JSON or text encoding
                self.send_json({"success": False, "error": "The request could not be read."})
                return
            try:
                payload = describe_action(data)
            except RequestError as e:
                payload = {"success": False, "error": str(e)}
            except Exception:  # a real problem: logged, and a generic message for the page
                log_exception("Describe a voice failed")
                payload = {"success": False, "error": GENERIC_ERROR}
            self.send_json(payload)
            return

        if self.path in BLEND_PATHS:
            content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if content_type != "application/json":
                self.send_error(415, "Content-Type must be application/json")
                return
            try:
                data = json.loads(body.decode("utf-8"))
            except ValueError:  # bad JSON or text encoding
                self.send_json({"success": False, "error": "The request could not be read."})
                return
            try:
                payload = blend_action(self.path, data)
            except voice_blends.BlendExists as e:
                payload = {"success": False, "exists": True, "error": str(e)}
            except (voice_blends.BlendError, RequestError) as e:
                payload = {"success": False, "error": str(e)}
            except Exception:  # anything else is a real problem: logged, and a generic message for the page
                log_exception(f"Voice blending failed ({self.path})")
                payload = {"success": False, "error": GENERIC_ERROR}
            self.send_json(payload)
            return

        if self.path in IMPORT_PATHS:
            content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if content_type != "application/json":
                self.send_error(415, "Content-Type must be application/json")
                return
            try:
                data = json.loads(body.decode("utf-8"))
            except ValueError:  # bad JSON or text encoding
                self.send_json({"success": False, "error": "The request could not be read."})
                return
            try:
                payload = import_action(self.path, data)
            except (RequestError, import_text.ImportProblem, lmstudio_chats.ChatProblem) as e:
                payload = {"success": False, "error": str(e)}
            except Exception:  # a real problem: logged, and a generic message for the page
                log_exception(f"Import failed ({self.path})")
                payload = {"success": False, "error": GENERIC_ERROR}
            self.send_json(payload)
            return

        if self.path in INBOX_PATHS:
            content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if content_type != "application/json":
                self.send_error(415, "Content-Type must be application/json")
                return
            try:
                data = json.loads(body.decode("utf-8"))
            except ValueError:  # bad JSON or text encoding
                self.send_json({"success": False, "error": "The request could not be read."})
                return
            try:
                payload = inbox_action(self.path, data)
            except RequestError as e:
                payload = {"success": False, "error": str(e)}
            except Exception:  # a real problem: logged, and a generic message
                log_exception(f"Send to VoiceCast failed ({self.path})")
                payload = {"success": False, "error": GENERIC_ERROR}
            self.send_json(payload)
            return

        if self.path == "/api/user_settings":
            content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if content_type != "application/json":
                self.send_error(415, "Content-Type must be application/json")
                return
            try:
                settings = validate_user_settings(json.loads(body.decode("utf-8")))
                save_user_settings(settings)
                if not settings["inbox_enabled"]:
                    clear_inbox()  # a script waiting when the box is unticked is dropped
                payload = {"success": True, "user_settings": settings}
            except RequestError as e:
                payload = {"success": False, "error": str(e)}
            except ValueError:  # bad JSON or text encoding
                payload = {"success": False, "error": "The settings could not be read."}
            except Exception:
                log_exception("Saving user settings failed")
                payload = {"success": False, "error": GENERIC_ERROR}
            self.send_json(payload)
            return

        if self.path == "/api/export_latest":
            try:
                saved_path = voice_cast.export_saved_audio("full_dialogue.wav")
                payload = {"success": True, "path": saved_path, "url": "/output/full_dialogue.wav"}
            except ValueError as e:  # "No audio has been generated yet" is safe to show
                payload = {"success": False, "error": str(e)}
            except Exception:
                log_exception("Export failed")
                payload = {"success": False, "error": GENERIC_ERROR}
            self.send_json(payload)
            return

        if self.path == "/api/generate":
            content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if content_type != "application/json":
                self.send_error(415, "Content-Type must be application/json")
                return

            try:
                data = json.loads(body.decode("utf-8"))
            except Exception:
                self.send_error(400, "Invalid JSON payload")
                return

            # Stream responses via Server-Sent Events (SSE) for real-time progress
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()

            def send_event(payload):
                try:
                    self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except Exception:
                    # The browser went away (tab closed or reloaded): stop rendering for nobody.
                    if _GENERATION_LOCK.locked():
                        _CANCEL_EVENT.set()

            def on_progress(p_data):
                send_event({"type": "progress", "data": p_data})

            if not _GENERATION_LOCK.acquire(blocking=False):
                result = {"success": False, "error": "Another script is still rendering. Try again when it finishes.", "lines": []}
            else:
                _CANCEL_EVENT.clear()
                try:
                    current_cfg = voice_cast.load_config(CONFIG_PATH)
                    script_text, voices_override, pacing_override, pronunciations = validate_generate_payload(data, current_cfg)
                    if voices_override:
                        current_cfg["default_voices"] = voices_override
                    if pacing_override:
                        current_cfg["pacing_seconds"] = pacing_override
                    current_cfg["pronunciations"] = pronunciations
                    current_cfg["even_volume"] = data.get("even_volume", True) is not False
                    current_cfg["performance"] = performance.clean_level(data.get("performance"))

                    live_enabled = data.get("live_listen") is True and not data.get("dry_run", False)
                    try:
                        _LIVE_AUDIO.clear()
                        if live_enabled:
                            _LIVE_AUDIO.begin()
                    except OSError:
                        live_enabled = False
                        send_event({"type": "listen_error", "error": "Live listening is unavailable; the full recording will still be prepared."})

                    def on_audio(audio, rate):
                        nonlocal live_enabled
                        if not live_enabled or _CANCEL_EVENT.is_set():
                            return
                        try:
                            for piece in _LIVE_AUDIO.append(audio, rate, voice_cast.audio_to_wav_bytes):
                                send_event({"type": "audio", "data": piece})
                        except OSError:
                            live_enabled = False
                            send_event({"type": "listen_error", "error": "Live listening stopped; the full recording will still be prepared."})

                    result = voice_cast.render_dialogue_content(
                        script_raw=script_text,
                        config=current_cfg,
                        output_filename="full_dialogue.wav",
                        dry_run=bool(data.get("dry_run", False)),
                        export_wav=bool(data.get("export_wav", False)),
                        progress_callback=on_progress,
                        should_cancel=_CANCEL_EVENT.is_set,
                        audio_callback=on_audio if live_enabled else None
                    )
                except RequestError as e:
                    result = {"success": False, "error": str(e), "lines": []}
                except Exception:
                    log_exception("Generation failed")
                    result = {"success": False, "error": GENERIC_ERROR, "lines": []}
                finally:
                    _GENERATION_LOCK.release()

            send_event({"type": "complete", "result": result})
            self.close_connection = True
            return

        self.send_error(404, "Not Found")

    def log_message(self, format, *args):
        # Keep server log clean
        sys.stderr.write(f"[WebStudio] {self.address_string()} - {getattr(self, 'command', 'HTTP')}\n")


class StudioServer(ThreadingHTTPServer):
    # On Windows, SO_REUSEADDR lets a second process bind a port that is already
    # listening, so two apps would share 7871. Without it, a busy port fails cleanly.
    allow_reuse_address = os.name != "nt"
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        self.session_token = secrets.token_urlsafe(32)
        self._clients = threading.BoundedSemaphore(MAX_CLIENTS)
        super().__init__(*args, **kwargs)
        self.cookie_name = f'voicecast_v2_{self.server_port}'
        session_access.register(self.server_port, self.session_token)

    def process_request(self, request, client_address):
        if not self._clients.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._clients.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._clients.release()


def create_server(host="127.0.0.1"):
    """Binds strictly to localhost (127.0.0.1) for local-only security.
    If a port is busy (for example another app on 7871), the next candidate is tried; nothing is stopped."""
    candidate_ports = [7871, 0]  # Prefer the v2 port; use an available local port when busy.
    for p in candidate_ports:
        try:
            # Bind strictly to 127.0.0.1 (local machine only)
            server = StudioServer((host, p), StudioHandler)
            actual_port = server.server_address[1]
            return server, actual_port
        except OSError:
            continue

    raise RuntimeError("Could not bind to any local port on 127.0.0.1. Please check local permissions.")


def write_port_file(port):
    """Tells voicecast_mcp.py (Send to VoiceCast) where the studio is."""
    try:
        with open(PORT_FILE, "w", encoding="utf-8") as f:
            f.write(str(port))
    except OSError:
        log_exception("Could not write output/studio_port.txt; Send to VoiceCast will try port 7871")


def open_browser(port):
    webbrowser.open(session_access.launch_url(port))


def run_server():
    os.makedirs(WEB_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    try:
        server, actual_port = create_server()
    except Exception as e:
        print(f"\n[!] Socket error: {e}")
        return

    session_access.write_session(actual_port, server.session_token)
    write_port_file(actual_port)
    url = f"http://127.0.0.1:{actual_port}"
    print(f"\n=======================================================")
    print(f" VoiceCastTTS v2 Local Web Studio is running (Local Only)!")
    print(f" URL: {url}")
    print(f" Press Ctrl+C in this window anytime to stop.")
    print(f"=======================================================\n")

    if os.environ.get("VOICECAST_NO_BROWSER") != "1":
        Timer(1.0, lambda: open_browser(actual_port)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping VoiceCastTTS Web Studio.")
    finally:
        server.server_close()
        session_access.clear_session(server.session_token)


if __name__ == "__main__":
    run_server()


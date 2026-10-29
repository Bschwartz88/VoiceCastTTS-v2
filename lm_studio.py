"""
Builds the Script Assistant's requests and reads the answers, for LM Studio and the other AI programs
in ai_programs.py (the file keeps its first name), so the studio's "Script Assistant" panel can have a
model write a script or split pasted text into speakers. Standard library only; it only ever connects
to this computer.
"""

import difflib
import json
import os
import re
import time

import ai_programs
import delivery_cues
import speaker_labels
import describe_voice
from speakability import drop_repeated_name
from ai_programs import (LOCAL_HOSTS, LOCAL_ONLY_MESSAGE, STATUS_TIMEOUT, Cancelled, check_local_url,
                         not_running_message)

DEFAULT_BASE_URL = "http://127.0.0.1:1234"
# LM Studio keeps each model file's saved system prompt and settings here, one .gguf.json per file.
# VoiceCast only reads them (for "Write new"); tests point this at a fake folder.
SETTINGS_DIR = os.path.join(os.path.expanduser("~"), ".lmstudio", ".internal", "user-concrete-model-default-config")
MAX_SETTINGS_FILES = 2000
MAX_SETTINGS_FILE_BYTES = 256 * 1024
MAX_PROMPT_CHARS = 20_000
NO_SETTINGS = {"found": False, "file": None, "system_prompt": "", "params": {}, "note": "none"}
# LM Studio setting -> (request field, lowest, highest, type)
PARAM_FIELDS = {
    "llm.prediction.temperature": ("temperature", 0, 2, float),
    "llm.prediction.topPSampling": ("top_p", 0, 1, float),
    "llm.prediction.topKSampling": ("top_k", 0, 1000, int),
    "llm.prediction.repeatPenalty": ("repeat_penalty", 0, 3, float),
}

LMStudioError = ai_programs.AIProgramError  # one error class for every AI program (the old name stays)


def get_status(base_url, program="lmstudio", context_length=4096) -> dict:
    """Lists the text models the chosen AI program can use now (LM Studio: loaded ones only, never loads one)."""
    return {"models": ai_programs.list_models(program, base_url, context_length)}


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _has_quantization(file_name: str, quantization: str) -> bool:
    """True if the file name holds the quantization as a whole token (Q4_K doesn't match Q4_K_M)."""
    if not quantization:
        return True
    return re.search(r"(?<![a-z0-9])" + re.escape(quantization.lower()) + r"(?![a-z0-9]|_[a-z0-9])",
                     file_name.lower()) is not None


def _settings_files() -> list:
    """LM Studio's per-model settings files (*.gguf.json), at most three folders deep."""
    if not os.path.isdir(SETTINGS_DIR):
        return []
    root_depth = os.path.normpath(SETTINGS_DIR).count(os.sep)
    found = []
    for dirpath, dirnames, filenames in os.walk(SETTINGS_DIR):
        dirnames.sort()
        if os.path.normpath(dirpath).count(os.sep) - root_depth >= 3:
            dirnames[:] = []
        for name in sorted(filenames):
            if name.lower().endswith(".gguf.json"):
                found.append(os.path.join(dirpath, name))
                if len(found) >= MAX_SETTINGS_FILES:
                    return found
    return found


def _field_value(value):
    """LM Studio stores some settings as {"checked": bool, "value": x}; unchecked means not set."""
    if isinstance(value, dict) and "checked" in value:
        return value.get("value") if value.get("checked") is True else None
    return value


def _read_fields(data) -> tuple:
    """The system prompt and the request settings from a settings file's "operation" fields."""
    operation = data.get("operation") if isinstance(data, dict) else None
    fields = operation.get("fields") if isinstance(operation, dict) else None
    prompt, params = "", {}
    for field in fields if isinstance(fields, list) else []:
        if not isinstance(field, dict):
            continue
        key, value = field.get("key"), _field_value(field.get("value"))
        if key == "llm.prediction.systemPrompt" and isinstance(value, str):
            prompt = value.strip()[:MAX_PROMPT_CHARS]
        elif key in PARAM_FIELDS:
            name, low, high, kind = PARAM_FIELDS[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            if kind is int and value != int(value):
                continue
            if low <= value <= high:
                params[name] = kind(value)
        elif key == "llm.prediction.stopStrings" and isinstance(value, list):
            stops = [s for s in value if isinstance(s, str) and s and len(s) <= 100][:10]
            if stops:
                params["stop"] = stops
    return prompt, params


def find_model_settings(model) -> dict:
    """The system prompt and settings saved for this model in LM Studio, read from its settings file.
    Matches by model name, quantization and publisher folder (an exact name wins over a longer one),
    and never guesses between two files."""
    base = _squash(model.get("base_key") or model.get("key") or "")
    if not base:
        return dict(NO_SETTINGS)
    quantization = model.get("quantization") or ""
    matches = [p for p in _settings_files()
               if base in _squash(os.path.basename(p)) and _has_quantization(os.path.basename(p), quantization)]
    publisher = (model.get("publisher") or "").lower()
    if publisher:  # only the publisher's own folder, so another model's prompt is never borrowed
        matches = [p for p in matches
                   if publisher in (part.lower() for part in os.path.relpath(p, SETTINGS_DIR).split(os.sep)[:-1])]
    exact = [p for p in matches if _squash(os.path.basename(p)) == base + _squash(quantization) + "ggufjson"]
    if len(exact) == 1:  # "sparrow-12b-it" over "sparrow-12b-it-qat"
        matches = exact
    if not matches:
        return dict(NO_SETTINGS)
    if len(matches) > 1:
        return dict(NO_SETTINGS, note="several")
    path = matches[0]
    rel = os.path.relpath(path, SETTINGS_DIR)
    try:
        if os.path.getsize(path) > MAX_SETTINGS_FILE_BYTES:
            raise ValueError("too big")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("not an object")
    except (OSError, ValueError):
        return dict(NO_SETTINGS, file=rel, note="unreadable")
    prompt, params = _read_fields(data)
    preset = data.get("preset")
    note = "preset" if not prompt and isinstance(preset, str) and preset.strip() else ""
    return {"found": True, "file": rel, "system_prompt": prompt, "params": params, "note": note}


VOICE_TYPES = ("Female", "Male", "Child / Youth", "Neutral")
WRITE_ANSWER_TOKENS = 4096
MAX_ANSWER_TOKENS = 8192
# Extra answer room for models that think first (a Short story's 1,262 tokens were all used
# thinking by a 9B thinking model). Only a ceiling: the model stops when it is done.
THINKING_ROOM = 8192
MIN_WRITE_TOKENS = 1024
MAX_SPEAKER_CHARS = 60
LENGTH_WORDS = {"short": 300, "medium": 800, "long": 2000}
# Steadier lengths: the range a story should land in (low, aim, top), how many times a story that ends
# too early is topped up, and how the model should pace it.
LENGTH_RANGES = {"short": (250, 300, 400), "medium": (650, 800, 1000), "long": (1700, 2000, 2400)}
TOP_UPS = {"short": 1, "medium": 2, "long": 3}
PACING = {"short": "Plan it as one scene.", "medium": "Plan it as 2-4 scenes.",
          "long": "Plan it as 5-8 scenes with a clear beginning, middle and end."}
MIN_TOP_UP_WORDS = 50  # a top-up adding less means the model thinks the story is finished
ENDING_TOKENS = 512
STEADY_LENGTHS = True  # tests of other features turn this off (their fake stories are a few words long)
STORY_MODES = ("write", "continue", "more", "ending")  # requests that write story text
_NUMBER_WORD = ("one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|forty|fifty"
                "|sixty|seventy|eighty|ninety|hundred|thousand")
# "100 words", "1.5k words", "a 2k-word story", "a thousand words", "two hundred and fifty words", "ten lines"
_NUMBER = (r"(?:\d[\d,.]*k?|(?:an?\s+)?(?:" + _NUMBER_WORD + r")(?:[\s-]+(?:and[\s-]+)?(?:" + _NUMBER_WORD
           + r"))*)")
_UNIT = r"(?:words?|lines?|sentences?|paragraphs?|pages?|minutes?|chapters?|scenes?)"
# "half a page", "a page about a fox", "a short paragraph", "a few paragraphs", "a couple of sentences"
_FEW = (r"(?:an?|half\s+an?|a\s+few|a\s+couple\s+of|several|a\s+single)\s+(?:(?:short|long)\s+)?"
        r"(?:pages?|paragraphs?|sentences?|minutes?)")
_OWN_LENGTH = re.compile(r"\b(?:" + _NUMBER + r"[\s-]*" + _UNIT + "|" + _FEW + r")\b", re.IGNORECASE)
WRITE_TEMPERATURE = 0.8
WRITE_MODES = ("write", "continue")  # Continue the story writes like Write new
_NUMBER_WORDS = {w: i for i, w in enumerate(
    "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen".split(), 1)}
_TENS = {w: 10 * i for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split(), 2)}
_ROMAN = {"i": 1, "v": 5, "x": 10, "l": 50}  # up to LXXXIX; enough for chapters, and few real words
# A heading: "Chapter 3", "Chapter Twenty-One", "Chapter IV", followed by punctuation (not a comma) or the end
# of the line, so "she read the chapter three times" and "chapter five, then" don't count.
_CHAPTER = re.compile(r"\bchapter[ \t]+(\d{1,4}|[a-z]+(?:[ \t-][a-z]+)?)[ \t]*(?=[.:!?;—–-]|$)",
                      re.IGNORECASE | re.MULTILINE)


def _chapter_number(text: str) -> int:
    """3 for "3", "three" or "III"; 21 for "Twenty-One"; 0 when it isn't a number."""
    text = text.lower()
    if text.isdigit():
        return int(text)
    words = re.split(r"[\s-]", text)
    if len(words) == 1 and words[0] in _NUMBER_WORDS:
        return _NUMBER_WORDS[words[0]]
    if words[0] in _TENS and (len(words) == 1 or _NUMBER_WORDS.get(words[1], 10) < 10):
        return _TENS[words[0]] + (_NUMBER_WORDS[words[1]] if len(words) == 2 else 0)
    if len(words) == 1 and all(c in _ROMAN for c in text):
        values = [_ROMAN[c] for c in text]
        return sum(-v if i + 1 < len(values) and v < values[i + 1] else v for i, v in enumerate(values))
    return 0


def next_chapter(script: str) -> int:
    """The number for the next chapter: the highest chapter heading ("Chapter 3", "Chapter Three",
    "Chapter Twenty-One", "Chapter IV") plus one; 2 if none."""
    found = [_chapter_number(n) for n in _CHAPTER.findall(script)]
    return max((n for n in found if n), default=1) + 1


# Added after the user's or LM Studio's own prompt so the answer still fits VoiceCast.
FORMAT_NOTE = ("For VoiceCast: put all description and action in lines spoken by Narrator; characters only "
               "say what they speak aloud. Give each character a short name and a voice type: Female, Male, "
               "Child / Youth or Neutral. Speaker names contain only the name: Ben, not Ben (angry). "
               "Put delivery cues such as (whispers) at the start of the text, never inside the speaker name.")

# Added to Write new and Continue requests (not Split, which must keep the user's words), in the request
# itself so it also reaches the model's own LM Studio prompt and the user's own instructions.
CUE_NOTE = ("You may add delivery cues in round brackets inside a line, such as "
            + ", ".join(f"({word})" for word in delivery_cues.PROMPT_CUES)
            + ", and pauses such as [pause 1.5s]. Use them sparingly, only where they help the listener. "
              "Don't use any other stage directions.")

SCRIPT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "cast": {"type": "array", "items": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "voice_type": {"type": "string", "enum": list(VOICE_TYPES)}},
            "required": ["name", "voice_type"]}},
        "lines": {"type": "array", "items": {
            "type": "object",
            "properties": {"speaker": {"type": "string"}, "text": {"type": "string"}},
            "required": ["speaker", "text"]}},
    },
    "required": ["title", "cast", "lines"],
}

PLAIN_FORMAT = ("Answer with one line per spoken part. Start each line with the speaker's name in square "
                "brackets, like this:\n[Narrator] It was late.\n[Tom] Who's there?\nWrite nothing else.")


def estimate_tokens(text: str) -> int:
    """A rough count of the model's word pieces: about 4 characters each."""
    return len(text) // 4 + 1


def clean_speaker(name) -> str:
    """Tidies a speaker name from the model: no brackets or line breaks, at most 60
    characters, and any spelling of "narrator" becomes "Narrator"."""
    if not isinstance(name, str):
        return ""
    return speaker_labels.split_speaker_label(name)["name"][:MAX_SPEAKER_CHARS].strip()


def strip_thinking(text: str) -> str:
    """Removes a model's <think>…</think> reasoning so it is never read aloud."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S | re.I)
    if "</think>" in text.lower():  # reasoning whose opening tag was in the prompt
        text = re.split(r"</think>", text, flags=re.I)[-1]
    return re.sub(r"<think>.*", "", text, flags=re.S | re.I)


def _strip_fences(text: str) -> str:
    return re.sub(r"^\s*```[\w-]*\s*$", "", text, flags=re.M)


def _voice_label(voice_type) -> str:
    return "Child / Youth" if voice_type == "Child" else str(voice_type)


def _length_words(length) -> int:
    return LENGTH_WORDS.get(length, LENGTH_WORDS["medium"])


def own_length(text) -> bool:
    """True when the request names its own length ("a 100-word poem", "two pages")."""
    return bool(_OWN_LENGTH.search(text or ""))


def length_range(length) -> tuple:
    return LENGTH_RANGES.get(length, LENGTH_RANGES["medium"])


def length_checked(mode, text, request, length) -> bool:
    """Write new and Continue aim for the length range and are topped up, unless the owner typed a length."""
    if not STEADY_LENGTHS or mode not in WRITE_MODES:
        return False
    return not own_length(request if mode == "continue" else text)


def _length_line(length, checked) -> str:
    if not checked:
        return (f"Length: about {_length_words(length):,} words, "
                "unless the request above asks for a different length.")
    low, aim, top = length_range(length)
    pacing = PACING.get(length, PACING["medium"])
    return (f"Length: between {low:,} and {top:,} words (aim for {aim:,}). {pacing} Don't finish early. "
            "This length overrides any other length mentioned earlier, except one asked for in the request above.")


def build_messages(mode, text, cast, instructions, structured, length="medium", request="", more_words=0) -> list:
    known = ", ".join(f"{c['name']} ({_voice_label(c['voice_type'])})" for c in cast) or "none yet"
    checked = length_checked(mode, text, request, length)
    length_note = _length_line(length, checked)
    if mode == "write":
        task = f"Write this script:\n{text}"
    elif mode == "continue":
        task = (f"Here is the story so far:\n<<<\n{text}\n>>>\n\n"
                "Write the next chapter of this story, with the same characters and speaker names. "
                f'Start with a Narrator line that says "Chapter {next_chapter(text)}." '
                "Don't repeat what already happened.")
        if request:
            task += f"\n\nWhat should happen next: {request}"
    elif mode == "more":
        task = (f"Here is the story so far:\n<<<\n{text}\n>>>\n\n"
                "Carry on this story from exactly where it stops: same characters and speaker names, "
                f"about {more_words:,} more words. Don't repeat anything, don't start a new chapter, "
                "and give it a proper ending.")
    elif mode == "ending":
        task = (f"Here is the story so far:\n<<<\n{text}\n>>>\n\n"
                "This story stops before its ending. Write only the ending: a few lines, at most 120 words, "
                "same speaker names.")
    else:
        task = ("Split this text into speakers. Keep every word exactly as written, in the same order:\n"
                f"<<<\n{text}\n>>>")
    if mode in WRITE_MODES and not checked:
        task += f"\n\n{length_note}"
    if mode in STORY_MODES:
        task += f"\n\n{CUE_NOTE}"
    answer = "Answer in the JSON format you were given." if structured else PLAIN_FORMAT
    user = (f"Speakers already in the cast (reuse these names when they fit): {known}\n"
            "Use the speaker name Narrator for all narration.\n\n"
            f"{task}\n\n{answer}")
    if checked:
        user += f"\n\n{length_note}"  # last, where models pay most attention
    return [{"role": "system", "content": instructions}, {"role": "user", "content": user}]


def answer_tokens(mode, text, prompt_tokens, model, length="medium", program="lmstudio", request="",
                  more_words=0) -> int:
    """How long the model's answer may be, or LMStudioError if the text can't fit."""
    if mode == "more":
        wanted, needed = more_words * 2 + 256, 256
    elif mode == "ending":
        wanted = needed = ENDING_TOKENS
    elif mode in WRITE_MODES and length_checked(mode, text, request, length):
        wanted, needed = length_range(length)[2] * 2 + 256, MIN_WRITE_TOKENS
    elif mode in WRITE_MODES:
        wanted, needed = _length_words(length) * 5 // 2 + 512, MIN_WRITE_TOKENS
    else:  # the answer repeats the text, plus room for names and the JSON around it
        wanted = needed = min(MAX_ANSWER_TOKENS, estimate_tokens(text) * 3 + 256)
    if model.get("thinks") and not model.get("thinking_off"):  # thinking uses the same room, so leave space for it
        wanted += THINKING_ROOM
    room = model["context_length"] - prompt_tokens
    if ai_programs.program_info(program)["kind"] == "ollama":
        room -= ai_programs.OLLAMA_MARGIN  # ollama_body asks for this much on top, within the model's maximum
    if room < needed:
        kind = ai_programs.program_info(program)["kind"]
        fix = {"lmstudio": "raise the context length when loading the model in LM Studio",
               "basic": ("raise \"Longest text the model can handle\" under Edit instructions → Connection "
                         "if your AI program allows more")}.get(kind, "use a model with more room")
        raise LMStudioError(
            f"This text is too long for {model['display_name']}'s current setting (about "
            f"{model['context_length']:,} tokens). Shorten it, or {fix}.")
    return min(wanted, room)


def build_request(mode, text, model, cast, instructions, structured=True, length="medium", params=None,
                  request="", program="lmstudio", more_words=0) -> dict:
    """The chat request. In write and continue modes, params (the model's LM Studio settings) are included."""
    messages = build_messages(mode, text, cast, instructions, structured, length, request, more_words)
    prompt_tokens = sum(estimate_tokens(m["content"]) for m in messages)
    if structured:
        prompt_tokens += estimate_tokens(json.dumps(SCRIPT_SCHEMA))
    body = {"model": model["key"], "messages": messages,
            "temperature": WRITE_TEMPERATURE if mode in STORY_MODES else 0.1}
    if mode in STORY_MODES and params:
        body.update(params)
    body["max_tokens"] = answer_tokens(mode, text, prompt_tokens, model, length, program, request, more_words)
    body["stream"] = True
    if structured:
        body["response_format"] = {"type": "json_schema", "json_schema": {
            "name": "voicecast_script", "strict": True, "schema": SCRIPT_SCHEMA}}
    return body


def choose_write_instructions(lm, own, default) -> tuple:
    """Which instructions "Write new" sends: the user's own when chosen, else the model's LM Studio
    prompt, else the user's own text, else VoiceCast's default. Returns (instructions, source)."""
    own = own or {}
    own_text = (own.get("text") or "").strip()
    if own.get("use_own") and own_text:
        return f"{own_text}\n\n{FORMAT_NOTE}", "own"
    if lm.get("system_prompt"):
        return f"{lm['system_prompt']}\n\n{FORMAT_NOTE}", "lm_studio"
    if own_text:
        return f"{own_text}\n\n{FORMAT_NOTE}", "own"
    return default, "default"


def parse_structured_reply(content) -> dict:
    """Reads a fixed-format answer. Raises ValueError if it holds no usable lines."""
    if not isinstance(content, str):
        raise ValueError("no text")
    data = json.loads(_strip_fences(strip_thinking(content)))
    if not isinstance(data, dict) or not isinstance(data.get("lines"), list):
        raise ValueError("no lines")
    lines = []
    for item in data["lines"]:
        if not isinstance(item, dict):
            continue
        speaker, text = speaker_labels.normalize_speaker_line(
            item.get("speaker"), item.get("text"), MAX_SPEAKER_CHARS)
        if speaker and text:
            lines.append({"speaker": speaker, "text": text})
    if not lines:
        raise ValueError("no usable lines")
    cast = [c for c in data.get("cast") or [] if isinstance(c, dict)]
    title = data.get("title") if isinstance(data.get("title"), str) else ""
    return {"title": title.strip()[:200], "cast": cast, "lines": lines}


_LINE_OBJECT = re.compile(r'\{\s*"speaker"\s*:\s*("(?:[^"\\]|\\.)*")\s*,\s*"text"\s*:\s*("(?:[^"\\]|\\.)*")\s*\}')


def salvage_lines(content) -> list:
    """The complete {"speaker", "text"} lines of a fixed-format answer that was cut off at the room limit."""
    lines = []
    for raw_speaker, raw_text in _LINE_OBJECT.findall(strip_thinking(content) if isinstance(content, str) else ""):
        try:
            speaker, text = speaker_labels.normalize_speaker_line(
                json.loads(raw_speaker), json.loads(raw_text), MAX_SPEAKER_CHARS)
        except ValueError:
            continue
        if speaker and text:
            lines.append({"speaker": speaker, "text": text})
    return lines

def lines_to_script(lines) -> str:
    return "\n".join(f"[{line['speaker']}] {line['text']}" for line in lines)


def script_cast(lines, model_cast) -> list:
    """Every speaker except the Narrator, in order of first line, with the voice type
    the model gave (VoiceCast's names: Female, Male, Child, Neutral) or None."""
    types = {}
    for c in model_cast or []:
        name = clean_speaker(c.get("name"))
        vt = c.get("voice_type")
        if name and vt in VOICE_TYPES:
            types[name.lower()] = "Child" if vt == "Child / Youth" else vt
    cast, seen = [], set()
    for line in lines:
        name = line["speaker"]
        if name == "Narrator" or name.lower() in seen:
            continue
        seen.add(name.lower())
        cast.append({"name": name, "voice_type": types.get(name.lower())})
    return cast


_WORD = re.compile(r"[^\W_]+(?:'[^\W_]+)*")


def _words(text: str) -> list:
    return _WORD.findall(text.replace("’", "'").replace("‘", "'").lower())


def word_check(original, lines, limit=50) -> list:
    """Compares the words of the pasted text with the words of the split script, ignoring
    capitals, punctuation and the speaker tags. Returns what was added, dropped or changed."""
    before = _words(original)
    after = _words(" ".join(line["text"] for line in lines))
    diffs = []
    for op, a1, a2, b1, b2 in difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        kind = {"insert": "added", "delete": "dropped", "replace": "changed"}[op]
        diffs.append({"kind": kind, "original": " ".join(before[a1:a2]), "result": " ".join(after[b1:b2])})
        if len(diffs) >= limit:
            break
    return diffs


NOTHING_USABLE = "The model didn't return a usable script. Try again or try a different model."
CUT_SHORT = "The model ran out of room, so the script may be cut short."
THOUGHT_TOO_LONG = ("The model used up all its room thinking before it wrote anything. Try again, or try Short, "
                    "or a model that doesn't think first.")
PROGRESS_EVERY = 0.5  # seconds between progress reports to the panel
# What the model sent in the latest write_script call (one entry per request), so a script
# VoiceCast couldn't use can be looked into. Only one script is written at a time.
last_answers = []
# Names used by the fixed answer format; they are not words of the story.
FORMAT_KEYS = {"title", "cast", "name", "voice_type", "lines", "speaker", "text"}
_COUNT_WORD = re.compile(r"[\w']+")


def count_words(answer) -> int:
    """About how many words an unfinished answer holds, for the panel's counter.
    A word still being written (at the very end) isn't counted yet, so the count never goes down."""
    words = _COUNT_WORD.findall(answer)
    if words and _COUNT_WORD.fullmatch(answer[-1]):
        words.pop()
    return sum(1 for word in words if word not in FORMAT_KEYS)


class _NeedsFallback(Exception):
    """The fixed-format answer was refused or unusable; ask again for plain lines."""


def duration_text(seconds) -> str:
    seconds = int(seconds)
    if seconds >= 60 and seconds % 60 == 0:
        minutes = seconds // 60
        return f"{minutes} minute" + ("" if minutes == 1 else "s")
    return f"{seconds} second" + ("" if seconds == 1 else "s")


def _find_loaded_model(base_url, model_key, program="lmstudio", context_length=4096) -> dict:
    """Checks the model is still there, so a request can never make LM Studio load one."""
    kind = ai_programs.program_info(program)["kind"]
    for model in ai_programs.list_models(program, base_url, context_length):
        if model["key"] == model_key:
            if kind == "ollama" and model["can_disable_thinking"]:
                model["thinking_off"] = True  # ollama_body sends think: false
            return model
    if kind == "lmstudio":
        raise LMStudioError(f"{model_key} is no longer loaded. Press the refresh button next to the model list "
                            "and pick a loaded model.")
    raise LMStudioError(f"{model_key} is no longer available. Press the refresh button next to the model list "
                        "and pick another model.")


def _ask(base_url, body, timeout, cancel_event, on_progress=None, program="lmstudio", model=None) -> tuple:
    """Sends one chat request to the AI program and reads the answer as it streams in.
    timeout is the longest silence allowed. Reports {"words", "thinking"} to on_progress.
    Returns (message text, finish_reason)."""
    last_report = [0.0]
    seen = {"text": False}
    loads_first = not (model or {}).get("ready", True)

    def report(text, thinking_seen):
        if on_progress is None:
            return
        now = time.monotonic()
        first = bool(text) and not seen["text"]
        seen["text"] = seen["text"] or bool(text)
        if first or now - last_report[0] >= PROGRESS_EVERY:
            last_report[0] = now
            story = strip_thinking(text)  # thinking written inside the answer isn't counted
            inside_thinking = "<think>" in text.lower() and not story.strip()
            event = {"words": count_words(story), "thinking": (thinking_seen and not text) or inside_thinking}
            if loads_first:
                event["loading"] = False  # something arrived, so the model is loaded
            on_progress(event)

    name = ai_programs.program_info(program)["name"]
    if loads_first and on_progress is not None:
        on_progress({"words": 0, "thinking": False, "loading": True})  # Ollama loads the model before answering
    try:
        content, finish, record = ai_programs.chat(program, base_url, body, timeout, cancel_event, model or {}, report)
    except TimeoutError:
        raise LMStudioError(f"{name} sent nothing for {duration_text(timeout)}, so I stopped waiting.") from None
    except ai_programs.FormatRefused:
        raise _NeedsFallback() from None
    last_answers.append({"structured": "response_format" in body, "max_tokens": body.get("max_tokens"),
                         "finish": finish, "thinking_chars": record["thinking_chars"], "answer": content[:50_000]})
    if not content and record["thinking_chars"] and finish == "length":
        raise LMStudioError(THOUGHT_TOO_LONG)  # no plain-text retry: it would think just as long
    if not content:
        raise LMStudioError(NOTHING_USABLE)
    return content, finish


# A tag at the start of a line: [Name], **Name:** / **Name**:, or Name: (a short
# capitalized name of at most four words, so "The time was 10:30" isn't a speaker).
_TAG = re.compile(r"^(?:\[([^\]\n]{1,60})\]"
                  r"|\*\*([^*\n]{1,60}?):?\*\*:?"
                  r"|([A-Z][\w'.-]*(?: [\w'.-]+){0,3}):(?!\d))\s*(.*)$")


def clean_plain_reply(text) -> list:
    """Turns a plain answer into speaker lines: drops thinking, code fences and blank
    lines; reads [Name], Name: and **Name:** tags; gives untagged lines to the previous
    speaker (the Narrator at the start)."""
    if not isinstance(text, str):
        return []
    lines, speaker = [], "Narrator"
    for raw in _strip_fences(strip_thinking(text)).splitlines():
        line = raw.strip()
        if not line:
            continue
        match = _TAG.match(line)
        if match and match.group(3) is not None and not match.group(4).strip():
            match = None  # "Here is your script:" is a sentence; a plain Name: tag needs words after it
        if match and match.group(1) is not None and delivery_cues.is_cue_tag(match.group(1)):
            match = None  # [whispers] or [pause 1.5s] is a delivery cue for the current speaker
        if match:
            name, content = speaker_labels.normalize_speaker_line(
                next(g for g in match.groups()[:3] if g is not None), match.group(4), MAX_SPEAKER_CHARS)
            if name:
                speaker = name
                line = content
                if not line:
                    continue
        lines.append({"speaker": speaker, "text": re.sub(r"\s+", " ", line)})
    return lines


def _plain_fallback(base_url, model, mode, text, cast, instructions, timeout, cancel_event, length, params,
                    on_progress=None, request="", program="lmstudio", more_words=0) -> tuple:
    """Asks once more without the fixed format, for plain [Speaker] lines, and cleans the answer.
    Keeps the chosen length and the model's LM Studio settings; the counter keeps counting."""
    body = build_request(mode, text, model, cast, instructions, structured=False, length=length, params=params,
                         request=request, program=program, more_words=more_words)
    content, finish = _ask(base_url, body, timeout, cancel_event, on_progress, program, model)
    lines = clean_plain_reply(content)
    if not lines:
        raise LMStudioError(NOTHING_USABLE)
    return lines, finish


ENDING_NOTE = "Added an ending (the model ran out of room)."
_TIMES = {1: "once", 2: "twice"}


def _story_words(lines) -> int:
    return len(_words(" ".join(line["text"] for line in lines)))


def _write_round(base_url, model, mode, text, cast, instructions, timeout, cancel_event, length, params,
                 on_progress, request, program, more_words=0) -> tuple:
    """One request: the fixed format first (a cut-off answer keeps its complete lines), then plain lines once.
    Returns (lines, model_cast, title, finish, used_fallback)."""
    try:
        body = build_request(mode, text, model, cast, instructions, structured=True, length=length, params=params,
                             request=request, program=program, more_words=more_words)
        content, finish = _ask(base_url, body, timeout, cancel_event, on_progress, program, model)
        try:
            parsed = parse_structured_reply(content)
            return parsed["lines"], parsed["cast"], parsed["title"], finish, False
        except ValueError:
            lines = salvage_lines(content) if finish == "length" else []
            if lines:
                return lines, [], "", finish, False
            raise _NeedsFallback() from None
    except _NeedsFallback:
        lines, finish = _plain_fallback(base_url, model, mode, text, cast, instructions, timeout, cancel_event,
                                        length, params, on_progress, request, program, more_words)
        return lines, [], "", finish, True


def as_sentence(text) -> str:
    """An error message ending in one full stop, or in its own "?" or "!" (no "?.")."""
    text = str(text).strip().rstrip(".")
    return text if re.search(r"[?!][)\"'”’]*$", text) else f"{text}."


def _length_note(length, words, top_ups) -> str:
    low, aim, _ = length_range(length)
    if not top_ups:
        return ""
    if words >= low:
        return f"Topped up {_TIMES.get(top_ups, f'{top_ups} times')} to reach {words:,} words."
    name = length.capitalize() if length in LENGTH_RANGES else "Medium"
    return f"The model kept ending early: {words:,} words ({name} is about {aim:,})."


def _merge_cast(model_cast, later):
    """The first round's cast plus characters a later round adds (an earlier type is never changed)."""
    known = {str(c.get("name", "")).lower() for c in model_cast or [] if isinstance(c, dict)}
    return list(model_cast or []) + [c for c in later or []
                                     if isinstance(c, dict) and str(c.get("name", "")).lower() not in known]


SPLIT_PIECE_MARGIN = 600  # tokens for VoiceCast's own words around a piece (rules, cast, answer format)
MIN_PIECE_TOKENS = 200  # with less room than this, the text is sent whole and the usual "too long" message shows
# Where a long text may be cut, best first: paragraph breaks, line breaks, sentence ends, spaces.
_CUTS = ((re.compile(r"\n\s*\n"), "\n\n"), (re.compile(r"\n"), "\n"),
         (re.compile(r"(?<=[.!?…])\s+|(?<=[.!?…][\"'”’)\]])\s+"), " "), (re.compile(r"\s+"), " "))


def piece_tokens(model, instructions, program="lmstudio") -> int:
    """The largest piece Split my text sends in one request. The answer repeats the piece (about three times
    its size with names and the answer format), and the piece, the rules and the answer must fit the model's room."""
    room = (model["context_length"] - estimate_tokens(instructions) - estimate_tokens(json.dumps(SCRIPT_SCHEMA))
            - SPLIT_PIECE_MARGIN - 256)
    if model.get("thinks") and not model.get("thinking_off"):
        room -= THINKING_ROOM
    if ai_programs.program_info(program)["kind"] == "ollama":
        room -= ai_programs.OLLAMA_MARGIN
    return max(0, min((MAX_ANSWER_TOKENS - 256) // 3, room // 4))


def split_pieces(text, max_tokens) -> list:
    """The text cut into pieces of at most max_tokens, at paragraph breaks where possible, else at line breaks,
    sentence ends or spaces. The pieces hold every word, in order."""
    if estimate_tokens(text) <= max_tokens:
        return [text]
    return _pack(text, max_tokens, 0)


def _pack(text, max_tokens, level) -> list:
    pattern, joiner = _CUTS[level]
    pieces, current = [], ""
    for part in (p for p in pattern.split(text.strip()) if p.strip()):
        if estimate_tokens(part) > max_tokens:  # too big on its own: cut it finer
            if current:
                pieces.append(current)
                current = ""
            if level + 1 < len(_CUTS):
                pieces += _pack(part, max_tokens, level + 1)
            else:  # one enormous "word"
                size = max(1, (max_tokens - 1) * 4)
                pieces += [part[i:i + size] for i in range(0, len(part), size)]
            continue
        joined = f"{current}{joiner}{part}" if current else part
        if estimate_tokens(joined) <= max_tokens:
            current = joined
        else:
            pieces.append(current)
            current = part
    if current:
        pieces.append(current)
    return pieces


def _cast_so_far(cast, lines, model_cast) -> list:
    """The cast sent with each later piece: the one given, plus the speakers the earlier pieces found."""
    known = {c["name"].lower() for c in cast}
    found = [{"name": c["name"], "voice_type": c["voice_type"] or "Neutral"}
             for c in script_cast(lines, model_cast) if c["name"].lower() not in known]
    return list(cast) + found


def _as_narration(pieces) -> list:
    """Text left unsplit, as Narrator lines (one per paragraph), so no words are lost."""
    return [{"speaker": "Narrator", "text": re.sub(r"\s+", " ", para).strip()}
            for piece in pieces for para in re.split(r"\n\s*\n", piece) if para.strip()]


def _split_in_pieces(base_url, model, text, cast, instructions, timeout, cancel_event, size, on_progress,
                     program) -> dict:
    """Split my text for a text too long for one request: piece by piece, each piece knowing the speakers found
    before it. Stop or a failure after the first piece keeps the finished parts and the rest as narration."""
    pieces = split_pieces(text, size)
    total = len(pieces)
    lines, model_cast, title, used_fallback, warning, note, stopped = [], [], "", False, "", "", False
    known = list(cast)
    for k, piece in enumerate(pieces, 1):
        done = _story_words(lines)

        def report(event, done=done, k=k):
            if on_progress is not None:
                on_progress({**event, "piece": k, "pieces": total, "words": done + event.get("words", 0)})

        report({"words": 0})
        try:
            more = _write_round(base_url, model, "split", piece, known, instructions, timeout, cancel_event,
                                "medium", {}, report, "", program)
        except (Cancelled, LMStudioError) as e:
            if k == 1:
                raise
            lines += _as_narration(pieces[k - 1:])
            stopped = True  # either way the split ended early: the panel warns, and no audio starts by itself
            if isinstance(e, Cancelled):
                note = (f"Stopped after part {k - 1} of {total}: the rest is in the script unchanged, "
                        "read by the Narrator.")
            else:
                note = (f"Couldn't split part {k} of {total}, so the rest is in the script unchanged, "
                        f"read by the Narrator. {as_sentence(e)}")
            break
        lines += more[0]
        model_cast = _merge_cast(model_cast, more[1])
        title, used_fallback = title or more[2], used_fallback or more[4]
        warning = CUT_SHORT if more[3] == "length" else warning
        known = _cast_so_far(cast, lines, model_cast)
        report({"words": _story_words(more[0])})
    else:
        note = f"Split in {total} parts."
    return {
        "title": title,
        "script": lines_to_script(lines),
        "cast": script_cast(lines, model_cast),
        "used_fallback": used_fallback,
        "word_differences": word_check(text, lines),
        "warning": warning,
        "length_note": note,
        "stopped_early": stopped,
    }


def write_script(base_url, model_key, mode, text, cast, instructions, timeout, cancel_event,
                 length="medium", own=None, on_progress=None, request="", program="lmstudio",
                 context_length=4096) -> dict:
    """Asks the model to write (mode "write"), continue (mode "continue") or split (mode "split") a script.
    With LM Studio, write uses the model's LM Studio prompt and settings (see choose_write_instructions);
    other programs (ai_programs.PROGRAMS) use VoiceCast's instructions.
    on_progress gets {"words", "thinking"} while the answer arrives (words: the whole story so far; while
    adding more also "phase", "round" and "rounds").
    Each request tries the fixed answer format first, then plain [Speaker] lines once.
    Write new and Continue aim for the length range: a story that ends too early is topped up (TOP_UPS),
    and one cut off at the room limit gets a short ending."""
    last_answers.clear()
    model = _find_loaded_model(base_url, model_key, program, context_length)
    if mode == "split":
        size = piece_tokens(model, instructions, program)
        if size >= MIN_PIECE_TOKENS and estimate_tokens(text) > size:
            return _split_in_pieces(base_url, model, text, cast, instructions, timeout, cancel_event, size,
                                    on_progress, program)
    params = {}
    if mode in WRITE_MODES:
        lm = find_model_settings(model) if ai_programs.program_info(program)["kind"] == "lmstudio" else NO_SETTINGS
        instructions, _ = choose_write_instructions(lm, own, instructions)
        params = lm["params"]
    checked = length_checked(mode, text, request, length)
    progress = {"base": 0, "extra": {}}

    def report(event):
        if on_progress is not None:
            on_progress({**event, **progress["extra"], "words": progress["base"] + event.get("words", 0)})

    lines, model_cast, title, finish, used_fallback = _write_round(
        base_url, model, mode, text, cast, instructions, timeout, cancel_event, length, params, report, request,
        program)
    note, stopped, warning = "", False, CUT_SHORT if finish == "length" else ""
    if checked:
        words, top_ups = _story_words(lines), 0

        def so_far():  # Continue: the earlier chapters stay in view, but only the new chapter counts
            script = lines_to_script(lines)
            return f"{text}\n\n{script}" if mode == "continue" else script

        def add_ending():  # one short round; False when the ending was cut off too
            nonlocal lines, used_fallback, model_cast
            progress.update(base=_story_words(lines), extra={"phase": "ending"})
            report({"words": 0})  # the counter says so at once, before the model's first piece
            more = _write_round(base_url, model, "ending", so_far(), cast, instructions, timeout, cancel_event,
                                length, params, report, "", program)
            lines, used_fallback = lines + more[0], used_fallback or more[4]
            model_cast = _merge_cast(model_cast, more[1])
            return more[3] != "length"

        try:
            if finish == "length":
                ended = add_ending()
                note, warning = (ENDING_NOTE, "") if ended else ("", CUT_SHORT)
            else:
                low, aim, _ = length_range(length)
                limit = TOP_UPS.get(length, TOP_UPS["medium"])
                ended = None  # an ending round runs only when a top-up was cut off at its room limit
                while words < low and top_ups < limit:
                    top_ups += 1
                    progress.update(base=words, extra={"phase": "more", "round": top_ups, "rounds": limit})
                    report({"words": 0})
                    more = _write_round(base_url, model, "more", so_far(), cast, instructions, timeout,
                                        cancel_event, length, params, report, "", program,
                                        more_words=max(150, aim - words))
                    added = _story_words(more[0])
                    lines, used_fallback, words = lines + more[0], used_fallback or more[4], words + added
                    model_cast = _merge_cast(model_cast, more[1])
                    if more[3] == "length":
                        ended = add_ending()
                        break
                    if added < MIN_TOP_UP_WORDS:
                        break
                note = _length_note(length, words, top_ups)
                if ended is not None:
                    note = f"{note} {ENDING_NOTE}" if ended else note
                    warning = "" if ended else CUT_SHORT
        except Cancelled:
            doing = "writing the ending" if progress["extra"].get("phase") == "ending" else "adding more"
            stopped, note = True, f"Stopped while {doing}: kept {words:,} words."
        except LMStudioError as e:
            doing = "write an ending" if progress["extra"].get("phase") == "ending" else "add more"
            note = f"Couldn't {doing}, so I kept {words:,} words. {as_sentence(e)}"
    return {
        "title": title,
        "script": lines_to_script(lines),
        "cast": script_cast(lines, model_cast),
        "used_fallback": used_fallback,
        "word_differences": word_check(text, lines) if mode == "split" else [],
        "warning": warning,
        "length_note": note,
        "stopped_early": stopped,
    }


DESCRIBE_ANSWER_TOKENS = 300
DESCRIBE_TIMEOUT = 60  # seconds of silence at most, whatever the Assistant's own limit
DESCRIBE_SCHEMA = {
    "type": "object",
    "properties": {
        "gender": {"type": "string", "enum": [*describe_voice.GENDERS, "any"]},
        "age": {"type": "string", "enum": [*describe_voice.AGES, "any"]},
        "accent": {"type": "string", "enum": [*describe_voice.ACCENTS, "any"]},
        "sounds": {"type": "array", "items": {"type": "string", "enum": list(describe_voice.SOUNDS)}},
    },
    "required": ["gender", "age", "accent", "sounds"],
    "additionalProperties": False,
}
DESCRIBE_INSTRUCTIONS = (
    'You help VoiceCast choose a voice for a character. Read the description and imagine how this character '
    'sounds. Answer only with JSON like this: {"gender": "female", "male", "neutral" or "any"; '
    '"age": "child", "teen", "adult", "elder" or "any"; "accent": "american", "british", '
    '"chinese" or "any"; "sounds": up to 5 words from this list: ' + ", ".join(describe_voice.SOUNDS) + '}. '
    'Use "any" when the description does not say or suggest it. Example: the grumpy troll under the bridge -> '
    '{"gender": "male", "age": "adult", "accent": "any", "sounds": ["deep", "gruff", "booming"]}')
UNREADABLE_TRAITS = "The AI program's answer couldn't be read."


def _first_json_object(content):
    text = _strip_fences(strip_thinking(content or "")).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object")
    return json.loads(text[start:end + 1])


def describe_voice_traits(base_url, model_key, description, timeout, cancel_event, program="lmstudio",
                          context_length=4096) -> dict:
    """Asks the AI program how the described character sounds (describe_voice's traits).
    Tries the fixed answer format, then once without it. Raises LMStudioError or Cancelled."""
    model = _find_loaded_model(base_url, model_key, program, context_length)
    messages = [{"role": "system", "content": DESCRIBE_INSTRUCTIONS}, {"role": "user", "content": description}]
    wanted = DESCRIBE_ANSWER_TOKENS
    if model.get("thinks") and not model.get("thinking_off"):
        wanted += THINKING_ROOM
    room = (model["context_length"] - sum(estimate_tokens(m["content"]) for m in messages)
            - estimate_tokens(json.dumps(DESCRIBE_SCHEMA)))
    body = {"model": model["key"], "messages": messages, "temperature": 0.2,
            "max_tokens": max(64, min(wanted, room)), "stream": True,
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "voicecast_voice", "strict": True, "schema": DESCRIBE_SCHEMA}}}
    try:
        content, _ = _ask(base_url, body, timeout, cancel_event, None, program, model)
    except _NeedsFallback:
        body.pop("response_format")
        content, _ = _ask(base_url, body, timeout, cancel_event, None, program, model)
    try:
        return describe_voice.clean_traits(_first_json_object(content))
    except ValueError:
        raise LMStudioError(UNREADABLE_TRAITS) from None


def describe_model_settings(base_url, model_key, own, default_write, program="lmstudio",
                            context_length=4096) -> dict:
    """What "Write new" would use for this model, for the panel's prompt line and editor."""
    model = _find_loaded_model(base_url, model_key, program, context_length)
    if ai_programs.program_info(program)["kind"] == "lmstudio":
        lm = find_model_settings(model)
    else:
        lm = {**NO_SETTINGS, "note": "not_lm_studio"}  # only LM Studio keeps a prompt VoiceCast can read
    _, source = choose_write_instructions(lm, own, default_write)
    own = own or {}
    return {
        "model_key": model["key"],
        "display_name": model["display_name"],
        "quantization": model.get("quantization", ""),
        "lm_studio": lm,
        "own": {"text": own.get("text") or "", "use_own": bool(own.get("use_own"))},
        "source": source,
        "program": program,
    }

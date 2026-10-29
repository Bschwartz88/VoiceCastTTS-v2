"""One source of speaker-label rules for LM answers, rendering, and the browser.

Annotations remain visible as inline directions. Only a numbered placeholder can take
its name from an annotation; descriptions and recognized delivery cues never become names.
No models, third-party dependencies, or network access.
"""
import re

from delivery_cues import CUES
from speakability import drop_repeated_name

# Sent to the browser through /api/config; both implementations use these same rules.
RULES = {
    "numbered": r"^speaker\s*\d+$",
    "alias_name": r"^[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’.-]*(?:\s+[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’.-]*){0,3}$",
    "metadata": r"\b(?:male|female|neutral|child|youth|teen|adult|elderly|young|old|voice|years?|aged?|"
                r"angry|sad|happy|excited|calm|gruff|gruffly|soft|softly|quiet|quietly|loud|loudly|"
                r"whisper|whispers|whispering|shout|shouts|shouting|looks?|looking|turns?|turning|"
                r"smiles?|smiling|laughs?|laughing|sighs?|sighing|speaks?|speaking)\b",
    "delivery_aliases": {**{word: word for word in CUES}, "angry": "angrily", "sad": "sadly",
                         "excited": "excitedly", "whisper": "whispers", "shout": "shouts",
                         "yell": "yells", "slow": "slowly", "quick": "quickly"},
}
_NUMBERED = re.compile(RULES["numbered"], re.I)
_ALIAS_NAME = re.compile(RULES["alias_name"])
_METADATA = re.compile(RULES["metadata"], re.I)


def split_speaker_label(raw) -> dict:
    """Returns {name, cue}; cue includes unspoken annotations, in their original order.
    Balanced nested annotations are supported. Malformed suffixes are kept unchanged.
    """
    if not isinstance(raw, str):
        return {"name": "", "cue": ""}
    label = re.sub(r"\s+", " ", re.sub(r"[\[\]*]", "", raw)).strip()
    name, notes = label, []
    first = label.find("(")
    if first > 0:
        depth, start, valid = 0, 0, True
        for i in range(first, len(label)):
            ch = label[i]
            if ch == "(":
                if depth == 0:
                    start = i + 1
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth < 0:
                    valid = False
                    break
                if depth == 0:
                    notes.append(label[start:i].strip())
            elif depth == 0 and not ch.isspace():
                valid = False
                break
        if valid and depth == 0 and notes:
            name = label[:first].strip()
        else:
            notes = []
    if (_NUMBERED.fullmatch(name) and notes and _ALIAS_NAME.fullmatch(notes[0])
            and not _METADATA.search(notes[0]) and notes[0].lower() not in RULES["delivery_aliases"]):
        name = notes.pop(0)
    name = "Narrator" if name.lower() == "narrator" else name
    cues = [RULES["delivery_aliases"].get(note.lower(), note) for note in notes if note]
    return {"name": name, "cue": " ".join(f"({cue})" for cue in cues)}


def normalize_speaker_line(raw_speaker, text, max_name_chars=None):
    """Separates identity from label directions without reading a repeated speaker name."""
    label = split_speaker_label(raw_speaker)
    name = label["name"]
    if max_name_chars is not None:
        name = name[:max_name_chars].strip()
    text = re.sub(r"\s+", " ", text).strip() if isinstance(text, str) else ""
    if not text:
        return name, ""
    text = drop_repeated_name(raw_speaker, text) if isinstance(raw_speaker, str) else text
    text = drop_repeated_name(name, text)
    return name, f"{label['cue']} {text}".strip()

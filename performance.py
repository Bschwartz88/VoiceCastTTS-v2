"""
Performance: Natural preserves native questions/plain replies and uses modest terminal pacing.
Lively adds explicit punctuation effects. Narrator pacing gets half the effect; volume normalization
may suppress automatic gains while explicit written cues remain intentional. voice_cast.py applies the results; this module has no audio code. Standard library only.
"""

import re

LEVELS = ("off", "natural", "lively")
DEFAULT_LEVEL = "natural"

_CLUE_SCALE = {"off": 0.0, "natural": 1.0, "lively": 1.6}  # how far each change goes from neutral
_CUE_SCALE = {"off": 1.0, "natural": 1.0, "lively": 1.6}   # written cues' loudness, e.g. (whispers)
_SHORT_REPLY_GAP = {"off": 1.0, "natural": 0.6, "lively": 0.4}
NARRATOR_SHARE = 0.5
SHORT_REPLY_WORDS = 3
CUT_OFF_GAP = 0.05  # seconds before the next speaker after a line that is cut off

# (speed factor, loudness in dB) for characters at Natural.
EXCLAIM = (1.06, 2.0)
QUESTION = (1.03, 0.0)
TRAILING = (0.93, -2.0)
EMPHASIS_DB = 2.0

_CLOSERS = " \t\"'”’»)]"
_QUESTION_END = re.compile(r"\?[!?]*$")
_TRAILING_END = re.compile(r"(?:\.\.\.|…)$")
_CUT_OFF_END = re.compile(r"(?:—|–|--)$")
_SHOUTED = re.compile(r"\b[A-Z]{3,}\b")


def clean_level(value) -> str:
    """One of LEVELS; anything else means the default."""
    return value if isinstance(value, str) and value in LEVELS else DEFAULT_LEVEL


def _end(text: str) -> str:
    """The text without closing quotes, brackets and spaces at the end."""
    return text.rstrip(_CLOSERS)


def _emphasis(text: str) -> bool:
    if "!!" in text:
        return True
    if any(c.islower() for c in text):
        return bool(_SHOUTED.search(text))
    # A line all in capitals counts once, unless it is a tiny reply like "OK." or "I." with no "!".
    letters = [c for c in text if c.isalpha()]
    return any(c.isupper() for c in letters) and (len(letters) >= 3 or "!" in text)


def line_delivery(text: str, is_narrator: bool, level: str) -> tuple:
    """(speed factor, loudness change in dB) for a line's spoken text."""
    share = _CLUE_SCALE[clean_level(level)] * (NARRATOR_SHARE if is_narrator else 1.0)
    if share == 0 or not text.strip():
        return 1.0, 0.0
    end = _end(text)
    speed, gain, clue = 1.0, 0.0, False
    if re.search(r"![!?]*$", end):
        speed, gain, clue = speed * EXCLAIM[0], gain + EXCLAIM[1], True
    if _QUESTION_END.search(end):
        speed, gain, clue = speed * QUESTION[0], gain + QUESTION[1], True
    if _TRAILING_END.search(end):
        speed, gain, clue = speed * TRAILING[0], gain + TRAILING[1], True
    if _emphasis(text):
        gain, clue = gain + EMPHASIS_DB, True
    if clean_level(level) == "natural":
        # Preserve model intonation, especially short replies/questions. No random per-line variation.
        speed = 0.97 if _TRAILING_END.search(end) else 1.02 if re.search(r"![!?]*$", end) else 1.0
        gain = 0.0
    if not clue and clean_level(level) != "natural":
        speed, gain = 1.0, 0.0
    return round(1.0 + (speed - 1.0) * share, 3), round(gain * share, 2)


def cue_gain(gain_db: float, level: str) -> float:
    """A written cue's loudness change, pushed further at livelier levels."""
    return gain_db * _CUE_SCALE[clean_level(level)]


def gap_seconds(base_gap, previous_text, previous_is_narrator, text, is_narrator, speaker_changed, level) -> float:
    """The silence before a line. Only changes between two characters; Narrator gaps stay as they are."""
    level = clean_level(level)
    if level == "off" or not speaker_changed or previous_is_narrator or is_narrator:
        return base_gap
    if previous_text and _CUT_OFF_END.search(_end(previous_text)):
        return min(base_gap, CUT_OFF_GAP)
    if len(text.split()) <= SHORT_REPLY_WORDS:
        return base_gap * _SHORT_REPLY_GAP[level]
    return base_gap

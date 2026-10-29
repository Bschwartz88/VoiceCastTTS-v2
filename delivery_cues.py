"""
Delivery cues inside a script line: (whispers), (shouts), [pause 1.5s] and so on.

split_cues() cuts one line into spoken pieces and pauses. A cue changes the speed and loudness from
where it stands to the end of the line (a later cue takes over). Brackets that aren't cues are left in
the text; speakability.clean_for_speech removes them later, as it always has.
Kokoro can't really whisper or shout: a cue only changes speed and loudness.
"""

import re

MAX_PAUSE_SECONDS = 5.0      # same limit as the pause settings (voice_cast.MAX_PAUSE_SECONDS)
DEFAULT_PAUSE_SECONDS = 1.0

# (spellings, speed factor, loudness change in dB). Keep CUE_WORDS in web/static/studio.js in step.
_CUE_GROUPS = (
    (("whispers", "whispering", "whispered"), 0.90, -10.0),
    (("softly", "quietly"), 0.95, -5.0),
    (("shouts", "shouting", "shouted", "yells", "yelling", "yelled"), 1.10, 5.0),
    (("angrily",), 1.05, 3.0),
    (("excitedly",), 1.10, 2.0),
    (("sadly",), 0.88, -3.0),
    (("slowly",), 0.80, 0.0),
    (("quickly",), 1.20, 0.0),
)
CUES = {word: (speed, gain) for words, speed, gain in _CUE_GROUPS for word in words}
PROMPT_CUES = tuple(words[0] for words, _, _ in _CUE_GROUPS)  # the spellings the Script Assistant is told about

_BRACKET = re.compile(r"\(([^()]*)\)|\[([^\[\]]*)\]")
# "pause", or "pause" and something that starts like a number; "(pause for effect)" is not a pause.
_PAUSE = re.compile(r"pauses?(?:\s+([-+]?[\d.].*))?", re.IGNORECASE | re.DOTALL)
_HAS_WORD = re.compile(r"[^\W_]")  # a letter or digit
_SECONDS = re.compile(r"(\d+(?:\.\d+)?|\.\d+)\s*(?:s|secs?|seconds?)?", re.IGNORECASE)


def _pause_seconds(content: str):
    """Seconds of silence for a pause cue, or None when `content` isn't a pause."""
    match = _PAUSE.fullmatch(content.strip())
    if not match:
        return None
    amount = _SECONDS.fullmatch((match.group(1) or "").strip())
    if not amount:
        return DEFAULT_PAUSE_SECONDS
    return min(float(amount.group(1)), MAX_PAUSE_SECONDS)


def is_cue_tag(content: str) -> bool:
    """True when a bracket's content is a cue word or a pause, so it isn't a speaker name."""
    return content.strip().lower() in CUES or _pause_seconds(content) is not None


def _drop_unclosed(chunk: str) -> str:
    """`chunk` up to its first bracket that isn't closed before the cue: "Hi (laughs. (whispers)" speaks
    only "Hi", as the old bracket stripping did."""
    opened = []
    for i, ch in enumerate(chunk):
        if ch in "([":
            opened.append(i)
        elif ch in ")]" and opened:
            opened.pop()
    return chunk[:opened[0]] if opened else chunk


def split_cues(text: str) -> list:
    """Cuts one line into pieces, in order: spoken text with its speed factor and loudness change,
    and pauses. Spoken pieces with nothing but spaces are left out."""
    pieces = []
    speed, gain, cue = 1.0, 0.0, None
    start = 0

    def add_speech(chunk):
        chunk = chunk.strip()
        if not chunk:
            return
        if not _HAS_WORD.search(chunk):
            # Only punctuation ("Come here (whispers)."): it ends the words before it. Spoken on its own,
            # Kokoro would make a short sound. With no words before it, it is dropped.
            for piece in reversed(pieces):
                if piece["kind"] == "speech":
                    piece["text"] += chunk
                    break
            return
        pieces.append({"kind": "speech", "text": chunk, "speed_factor": speed, "gain_db": gain, "cue": cue})

    for match in _BRACKET.finditer(text):
        content = match.group(1) if match.group(1) is not None else match.group(2)
        word = content.strip().lower()
        seconds = _pause_seconds(word)
        if word not in CUES and seconds is None:
            continue  # not a cue: stays in the text
        add_speech(_drop_unclosed(text[start:match.start()]))
        start = match.end()
        if seconds is not None:
            pieces.append({"kind": "pause", "seconds": seconds})
        else:
            speed, gain = CUES[word]
            cue = word
    add_speech(text[start:])
    return pieces

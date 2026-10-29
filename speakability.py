"""
Speakability Filter for VoiceCastTTS
Cleans and normalizes script text to make it sound natural when spoken by TTS engines:
- Expands common abbreviations (e.g., Dr., vs., etc.)
- Normalizes symbols and currency ($50 -> 50 dollars, % -> percent)
- Strips stage directions / parentheticals like (laughs); delivery cues such as (whispers) are handled before this (delivery_cues.py)
- Formats punctuation and ellipses for natural breathing pauses
"""

import re

# Each pattern ends with (?!\w): a trailing \b would require a letter right
# after the period, so "Dr. Smith" would never match.
ABBREVIATIONS = {
    r"\bDr\.(?!\w)": "Doctor",
    r"\bMr\.(?!\w)": "Mister",
    r"\bMrs\.(?!\w)": "Missus",
    r"\bMs\.(?!\w)": "Miz",
    r"\bProf\.(?!\w)": "Professor",
    r"\bvs\.(?!\w)": "versus",
    r"\be\.g\.,?(?!\w)": "for example,",
    r"\bi\.e\.,?(?!\w)": "that is,",
    r"\betc\.(?!\w)": "et cetera",
    r"\bapprox\.(?!\w)": "approximately",
    r"\bdept\.(?!\w)": "department",
    r"\bgovt\.(?!\w)": "government",
    r"\bno\.(?=\s*\d)": "number",  # only "No. 5", never "I said no."
    r"\bmin\.(?!\w)": "minutes",
    r"\bsec\.(?!\w)": "seconds",
}


UNITS = {"min": "minute", "mins": "minute", "sec": "second", "secs": "second", "hr": "hour", "hrs": "hour"}

# Titles always come before a name, so their period never ends a sentence.
TITLES = {"Doctor", "Mister", "Missus", "Miz", "Professor"}


def _ends_sentence(m) -> bool:
    """True when a matched abbreviation's period also ends a sentence:
    at the end of the line, or before a new sentence ("10 min. We just...")."""
    if not m.group(0).endswith("."):
        return False
    rest = m.string[m.end():]
    return not rest.strip() or bool(re.match(r"\s+[A-Z\"“]", rest))


def _expand(replacement):
    def repl(m):
        if replacement not in TITLES and _ends_sentence(m):
            return replacement + "."
        return replacement
    return repl


def _unit(m):
    word = UNITS[m.group(2).lower()]
    text = f"{m.group(1)} {word if m.group(1) == '1' else word + 's'}"
    return text + "." if _ends_sentence(m) else text

def clean_for_speech(text: str, remove_stage_cues: bool = True) -> str:
    """Preprocesses a single line of dialogue or narration for text-to-speech."""
    if not text:
        return ""

    cleaned = text.strip()

    # 1. Optionally remove stage cues in parentheses or brackets e.g. (sighs), [whispering]
    # (Note: we only remove cues that are INSIDE the spoken line, not character tags)
    if remove_stage_cues:
        cleaned = re.sub(r"\([^\)]*\)", "", cleaned)
        cleaned = re.sub(r"\[[^\/]*?\]", "", cleaned)

    # 2. Units after a number: "2 min" -> "2 minutes", "1 hr." -> "1 hour."
    cleaned = re.sub(r"\b(\d+)\s*(mins?|secs?|hrs?)\b\.?", _unit, cleaned, flags=re.IGNORECASE)

    # Expand common abbreviations
    for pattern, replacement in ABBREVIATIONS.items():
        cleaned = re.sub(pattern, _expand(replacement), cleaned, flags=re.IGNORECASE)

    # 3. Currency symbols: $100 -> 100 dollars, $1,000 -> 1000 dollars
    amount = r"(\d{1,3}(?:,\d{3})+|\d+)(\.\d{2})?"
    for symbol, unit in (("\\$", "dollars"), ("£", "pounds"), ("€", "euros")):
        cleaned = re.sub(
            symbol + amount,
            lambda m, unit=unit: m.group(1).replace(",", "") + (m.group(2) or "") + " " + unit,
            cleaned,
        )

    # 4. Common symbols
    cleaned = re.sub(r"(?<=\w)\s*&\s*(?=\w)", " and ", cleaned)
    cleaned = re.sub(r"%", " percent", cleaned)
    cleaned = re.sub(r"#(\d+)", r"number \1", cleaned)
    cleaned = re.sub(r"(?<=\d)\s*\+\s*(?=\d)", " plus ", cleaned)  # 2+2, not C++

    # 5. Clean up markdown formatting (*emphasis*, _italics_, `code`)
    cleaned = re.sub(r"[\*_`]", "", cleaned)

    # 6. Normalize multiple spaces, dashes, and ellipses
    cleaned = re.sub(r"\s*--\s*", " — ", cleaned)
    cleaned = re.sub(r"\.{3,}", "...", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)

    return cleaned.strip()


MAX_PRONUNCIATIONS = 500
MAX_PRONUNCIATION_WORD = 60
MAX_PRONUNCIATION_SAY = 120


def normalize_pronunciations(entries) -> list:
    """Turns [{"word": ..., "say": ...}] (or [word, say] pairs) into a clean list of (word, say).
    Raises ValueError for anything that isn't a list of such entries; skips blank rows."""
    if entries is None:
        return []
    if not isinstance(entries, list) or len(entries) > MAX_PRONUNCIATIONS:
        raise ValueError(f"Pronunciations must be a list of at most {MAX_PRONUNCIATIONS} entries.")
    cleaned = []
    for entry in entries:
        if isinstance(entry, dict):
            word, say = entry.get("word"), entry.get("say")
        elif isinstance(entry, (list, tuple)) and len(entry) == 2:
            word, say = entry
        else:
            raise ValueError("Each pronunciation needs a word and how to say it.")
        if not isinstance(word, str) or not isinstance(say, str):
            raise ValueError("Each pronunciation needs a word and how to say it.")
        word, say = word.strip(), say.strip()
        if not word or not say:
            continue
        if len(word) > MAX_PRONUNCIATION_WORD or len(say) > MAX_PRONUNCIATION_SAY:
            raise ValueError(f"Pronunciation for {word[:30]!r} is too long.")
        cleaned.append((word, say))
    return cleaned


def apply_pronunciations(text: str, pronunciations) -> str:
    """Replaces whole words or phrases (ignoring case) with how they should be said.
    All entries are matched in one pass, longest first, so a replacement is never replaced again."""
    if not text or not pronunciations:
        return text
    table = {}
    for word, say in pronunciations:
        table.setdefault(word.lower(), say)
    alternatives = "|".join(re.escape(w) for w in sorted(table, key=len, reverse=True))
    pattern = re.compile(r"(?<!\w)(?:" + alternatives + r")(?!\w)", re.IGNORECASE)
    return pattern.sub(lambda m: table[m.group(0).lower()], text)


def drop_repeated_name(speaker: str, text: str) -> str:
    """Removes the speaker's own name written again at the start of the line ("[Timmy] Timmy: hi",
    also **Timmy:** and [Timmy]), so it isn't read out. Leaves the text alone if nothing would be left."""
    name = re.escape((speaker or "").strip())
    if not name or not text:
        return text
    pattern = (r"^\s*(?:\[\s*" + name + r"\s*\]\s*:?|\*\*\s*" + name + r"\s*:?\s*\*\*\s*:?|"
               + name + r"\s*:)\s*(?=\S)")
    return re.sub(pattern, "", text, count=1, flags=re.IGNORECASE)


def format_prose_to_script(text: str) -> str:
    """
    Converts mixed novel prose containing dialogue in quotes and narration tags
    into clean, multi-speaker script format with [Speaker] and [Narrator] tags.
    Example:
        [Speaker 1] "These pretzels are making me thirsty," he groaned painfully. "I need a drink."
    Becomes:
        [Speaker 1] These pretzels are making me thirsty.
        
        [Narrator] He groaned painfully.
        
        [Speaker 1] I need a drink.
    """
    if not text or not text.strip():
        return ""

    lines = text.split("\n")
    formatted_blocks = []
    quotes_regex = r'([“"«][^”"»]*[”"»])'

    for raw_line in lines:
        trimmed = raw_line.strip()
        if not trimmed:
            if formatted_blocks and formatted_blocks[-1] != "":
                formatted_blocks.append("")
            continue

        if trimmed.startswith("#"):
            formatted_blocks.append(trimmed)
            formatted_blocks.append("")
            continue

        tag_match = re.match(r"^\[([^\]]+)\]\s*(.*)$", trimmed)
        explicit_speaker = None
        content = trimmed

        if tag_match:
            explicit_speaker = tag_match.group(1).strip()
            content = tag_match.group(2).strip()

        quote_matches = re.findall(quotes_regex, content)
        if not quote_matches:
            formatted_blocks.append(trimmed)
            formatted_blocks.append("")
            continue

        tokens = re.split(quotes_regex, content)
        active_speaker = explicit_speaker

        if not active_speaker:
            for token in tokens:
                clean_tok = token.strip()
                if not clean_tok.startswith(('"', "“", "«")):
                    verb_match = re.search(
                        r"\b(?:said|groaned|shouted|whispered|asked|replied|cried|sighed|laughed|exclaimed|muttered|yelled)\s+([A-Z][a-zA-Z0-9_\-]+)",
                        clean_tok
                    )
                    if verb_match:
                        active_speaker = verb_match.group(1)
                        break
                    name_verb_match = re.search(
                        r"\b([A-Z][a-zA-Z0-9_\-]+)\s+(?:said|groaned|shouted|whispered|asked|replied|cried|sighed|laughed|exclaimed|muttered|yelled)\b",
                        clean_tok
                    )
                    if name_verb_match:
                        active_speaker = name_verb_match.group(1)
                        break
        if not active_speaker:
            active_speaker = "Speaker"

        for raw_token in tokens:
            token = raw_token.strip()
            if not token:
                continue

            is_quote = (
                (token.startswith('"') and token.endswith('"')) or
                (token.startswith('“') and token.endswith('”')) or
                (token.startswith('«') and token.endswith('»'))
            )

            if is_quote:
                quote_text = token[1:-1].strip()
                if not quote_text:
                    continue
                quote_text = quote_text[0].upper() + quote_text[1:]
                if quote_text.endswith(","):
                    quote_text = quote_text[:-1].strip() + "."
                elif quote_text[-1] not in ".!?…":
                    quote_text += "."

                formatted_blocks.append(f"[{active_speaker}] {quote_text}")
                formatted_blocks.append("")
            else:
                narr_text = token
                narr_text = re.sub(r"^[,\s—–\-:]+", "", narr_text)
                narr_text = re.sub(r"[,\s—–\-:]+$", "", narr_text).strip()
                if not narr_text:
                    continue
                narr_text = narr_text[0].upper() + narr_text[1:]
                if narr_text[-1] not in ".!?…":
                    narr_text += "."

                formatted_blocks.append(f"[Narrator] {narr_text}")
                formatted_blocks.append("")

    result = "\n".join(formatted_blocks)
    return re.sub(r"\n{3,}", "\n\n", result).strip()


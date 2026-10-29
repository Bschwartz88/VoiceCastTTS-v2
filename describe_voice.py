"""
Describe a voice: turns a description ("a grumpy old troll with a deep voice") into a blend of voices
VoiceCast already has. Word matching lives here; the AI program's reading of a description comes from
lm_studio.describe_voice_traits and is tidied by clean_traits. Standard library only.
"""

import json
import re
import unicodedata

GENDERS = ("female", "male", "neutral")
AGES = ("child", "teen", "adult", "elder")
ACCENTS = ("american", "british", "chinese")
SOUNDS = ("deep", "gruff", "booming", "rugged", "warm", "soft", "gentle", "calm", "smooth", "bright",
          "light", "crisp", "clear", "playful", "lively", "expressive", "dramatic", "theatrical", "formal",
          "refined", "friendly", "casual", "authoritative", "thoughtful", "sweet", "jolly", "magical", "nervous")
MAX_SOUNDS = 5
MAX_DESCRIPTION = 200
MAX_NAME = 30
MAX_UNKNOWN = 8

# Words that carry no trait; they are skipped without being listed as "not used".
SMALL_WORDS = set("""a an the and or but with without who whose that this it its is are was be has have
having in on of at by for to from under over into very quite really rather bit sort type voice voiced
voices sounding sounds sound like speaks speaking speak talks talking talker someone somebody person
character one s also so too not us""".split())

_WORDS = {}  # word -> (kind, value); kind "person" is (gender, implied age)


def _add(kind, value, text):
    for word in text.split():
        _WORDS[word] = (kind, value)


_add("gender", "female", "woman women lady female she her mother mom mum queen princess witch aunt sister "
                         "wife daughter")
_add("gender", "male", "man men gentleman male he his father dad king prince wizard uncle brother husband son guy")
_add("gender", "neutral", "neutral androgynous genderless")
_add("person", ("female", "child"), "girl")
_add("person", ("male", "child"), "boy")
_add("person", ("female", "elder"), "grandma granny grandmother")
_add("person", ("male", "elder"), "grandpa grandfather")
_add("age", "child", "child children kid baby toddler")
_add("age", "teen", "teen teenage teenager teenaged adolescent")
# Weak hints: "little old lady" is old and "young woman" a grown-up, so these only count when nothing stronger does.
_add("hint", "child", "little")
_add("hint", "teen", "young youth")
GROWN_UP_WORDS = {"woman", "women", "lady", "ladies", "man", "men", "gentleman", "guy"}
_add("age", "adult", "adult grown grownup")
_add("age", "elder", "old elderly aged ancient elder senior")
_add("accent", "british", "british english uk")
_add("accent", "american", "american usa")  # "US" in capitals: see read_description
_add("accent", "chinese", "chinese mandarin")
for _sound, _synonyms in {
    "deep": "low bass baritone resonant",
    "gruff": "raspy gravelly rough husky growly grumpy gritty hoarse",
    "booming": "loud big huge giant powerful boisterous",
    "rugged": "tough strong burly",
    "warm": "kind kindly loving caring motherly fatherly",
    "soft": "quiet whispery hushed breathy",
    "gentle": "tender mild",
    "calm": "relaxed measured steady serene peaceful",
    "smooth": "silky velvety mellow",
    "bright": "sunny upbeat",
    "light": "airy delicate tiny",
    "crisp": "sharp precise",
    "clear": "",
    "playful": "mischievous cheeky silly impish",
    "lively": "energetic fast quick excited excitable bubbly",
    "expressive": "animated emotional",
    "dramatic": "intense villainous villain evil menacing sinister dark",
    "theatrical": "storyteller grand",
    "formal": "stern strict proper",
    "refined": "posh elegant sophisticated aristocratic",
    "friendly": "nice pleasant",
    "casual": "chill easygoing",
    "authoritative": "commanding confident bossy",
    "thoughtful": "wise scholarly intelligent",
    "sweet": "cute",
    "jolly": "cheerful merry happy",
    "magical": "mystical mysterious ethereal",
    "nervous": "shy timid anxious scared worried",
}.items():
    _add("sound", _sound, f"{_sound} {_synonyms}")

_TOKEN = re.compile(r"[a-z0-9]+")
_YEAR_NUMBERS = {w: i for i, w in enumerate(
    "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen".split(), 1)}
_YEAR_NUMBERS.update({w: 10 * i for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split(), 2)})
# "5 year old", "five-year-old", "80 years old": an age in years, whose "old" doesn't mean elderly.
_AGE_IN_YEARS = re.compile(r"\b(\d{1,3}|" + "|".join(_YEAR_NUMBERS) + r") (?:years?|yrs?) old\b")


def words(text) -> list:
    """The description's words in lower case, with accents removed (é → e)."""
    plain = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    return _TOKEN.findall(plain.lower())


def _lookup(word):
    entry = _WORDS.get(word)
    if entry is None and len(word) > 3 and word.endswith("s"):
        entry = _WORDS.get(word[:-1])
    return entry


_US_IN_CAPITALS = re.compile(r"\bU\.?S\.?(?![A-Za-z])")


def read_description(text) -> dict:
    """Word matching: the traits a description names. The first gender, age and accent word wins;
    "girl", "boy", "grandma" and "grandpa" also imply an age, used only when no age word is given,
    and "little" / "young" are weaker still ("young" next to woman or man means a grown-up)."""
    traits = {"gender": "any", "age": "any", "accent": "any", "sounds": [], "unknown": []}
    implied_age = hint = years_age = None
    grown_up = False
    text = text or ""
    if any(c.islower() for c in text):  # "a US news anchor", "U.S." mean usa; lower-case or all-capitals "us" is just "us"
        text = _US_IN_CAPITALS.sub(" usa ", text)
    plain = " ".join(words(text))
    match = _AGE_IN_YEARS.search(plain)
    if match:
        number = match.group(1)
        years = int(number) if number.isdigit() else _YEAR_NUMBERS[number]
        years_age = "child" if years < 13 else "teen" if years < 20 else "adult" if years < 60 else "elder"
        plain = f"{plain[:match.start()]} {plain[match.end():]}"
    for word in plain.split():
        if word.isdigit():
            continue
        entry = _lookup(word)
        if entry is None:
            if word not in SMALL_WORDS and word not in traits["unknown"] and len(traits["unknown"]) < MAX_UNKNOWN:
                traits["unknown"].append(word)
            continue
        kind, value = entry
        grown_up = grown_up or word in GROWN_UP_WORDS
        if kind == "hint":
            hint = hint or value
        elif kind == "person":
            gender, age = value
            if traits["gender"] == "any":
                traits["gender"] = gender
            implied_age = implied_age or age
        elif kind == "sound":
            if value not in traits["sounds"] and len(traits["sounds"]) < MAX_SOUNDS:
                traits["sounds"].append(value)
        elif traits[kind] == "any":
            traits[kind] = value
    if years_age:
        traits["age"] = years_age
    elif traits["age"] == "any":
        if implied_age:
            traits["age"] = implied_age
        elif hint:
            traits["age"] = "adult" if hint == "teen" and grown_up else hint
    return traits


def nothing_understood(traits) -> bool:
    return (traits["gender"] == "any" and traits["age"] == "any" and traits["accent"] == "any"
            and not traits["sounds"])


NOTHING_TO_GO_ON = ("I couldn't find anything to go on in that description. "
                    "Try words like old, British, deep or gentle.")
SHARES = {1: (1.0,), 2: (0.65, 0.35), 3: (0.55, 0.30, 0.15)}
AGE_WORDS = {"child": "child", "teen": "teenage", "adult": "grown-up", "elder": "old"}


def check_voice_traits(table) -> list:
    """Problems in voice_config.json's voice_traits (empty when every record is fine)."""
    if not isinstance(table, dict):
        return ["voice_traits must be an object"]
    problems = []
    for voice, t in table.items():
        if not isinstance(t, dict):
            problems.append(f"{voice}: not an object")
        elif (t.get("gender") not in ("female", "male") or t.get("age") not in AGES
              or t.get("accent") not in ACCENTS or not isinstance(t.get("sounds"), list)
              or not 1 <= len(t["sounds"]) <= MAX_SOUNDS or any(s not in SOUNDS for s in t["sounds"])):
            problems.append(f"{voice}: needs gender female/male, an age, an accent and 1-{MAX_SOUNDS} sound words")
    return problems


TRAITS_FILE = "voice_traits.json"  # in custom_voices/: traits of the owner's own voices (never uploaded)


def read_traits_file(path) -> dict:
    """The valid trait records in a private voice_traits.json; {} when there is none or it is damaged."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            table = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(table, dict):
        return {}
    return {voice: t for voice, t in table.items() if not check_voice_traits({voice: t})}


def _fits(t, gender, age, accent) -> bool:
    if gender in ("female", "male") and t["gender"] != gender:
        return False
    if age != "any" and t["age"] != age:
        return False
    return t["accent"] == accent if accent != "any" else t["accent"] != "chinese"


def suggest(traits, voice_traits, available):
    """Picks up to 3 installed voices for the traits and their shares. Returns None when the traits
    say nothing, or no installed voice has traits."""
    if nothing_understood(traits):
        return None
    gender, age, accent = traits["gender"], traits["age"], traits["accent"]
    voices = [(v, t) for v, t in voice_traits.items() if v in available]
    order = {v: i for i, (v, _) in enumerate(voices)}
    tries = ((age, accent, ""),
             ("any", accent, f"I have no {AGE_WORDS.get(age, age)} voice like that, so I left out the age."),
             (age, "any", f"I have no {accent.title()} voice like that, so I left out the accent."),
             ("any", "any", "I have no voice of that age and accent, so I left both out."))
    for try_age, try_accent, note in tries:
        candidates = [(v, t) for v, t in voices if _fits(t, gender, try_age, try_accent)]
        if candidates:
            break
    else:
        return None
    wanted = set(traits["sounds"])

    def rank(item):
        voice, t = item
        bonus = 0.5 if age == "any" and t["age"] == "adult" else 0.0  # with no age asked for, prefer grown-ups
        return (-(len(wanted & set(t["sounds"])) + bonus), order[voice])

    ranked = sorted(candidates, key=rank)
    if gender == "any":  # don't mix: follow the best voice's gender
        lead = ranked[0][1]["gender"]
        ranked = [item for item in ranked if item[1]["gender"] == lead]
    picked = [item for item in ranked if wanted & set(item[1]["sounds"])][:3] or ranked[:2]
    if gender == "neutral" and len({t["gender"] for _, t in picked}) == 1:  # in between: a woman and a man
        other = next((item for item in ranked if item[1]["gender"] != picked[0][1]["gender"]), None)
        if other:
            picked = (picked[:-1] if len(picked) > 1 else picked) + [other]
    shares = SHARES[len(picked)]
    genders = {t["gender"] for _, t in picked}
    if all(t["age"] in ("child", "teen") for _, t in picked):
        voice_type = "Child"
    elif len(genders) == 1:
        voice_type = genders.pop().title()
    else:
        voice_type = "Neutral"
    asked = [w for w in (gender if gender != "any" else "", AGE_WORDS.get(age, ""),
                         accent.title() if accent != "any" else "") if w] + list(traits["sounds"])
    names = ", ".join(f"{v} ({', '.join(t['sounds'])})" for v, t in picked)
    because = f" because you asked for: {', '.join(asked)}" if asked else ""
    return {"recipe": [{"voice": v, "share": s} for (v, _), s in zip(picked, shares)],
            "voice_type": voice_type, "explanation": f"Picked {names}{because}.", "note": note}


def suggest_name(text, taken) -> str:
    """A blend name from the description's words: grumpy_troll_bridge. Never one of `taken` (capitals ignored)."""
    name = ""
    for word in (w for w in words(text) if w not in SMALL_WORDS):
        longer = f"{name}_{word}" if name else word
        if len(longer) > MAX_NAME:
            break
        name = longer
    name = name or "voice"
    used = {t.lower() for t in taken}
    candidate, number = name, 2
    while candidate.lower() in used:
        candidate, number = f"{name}_{number}", number + 1
    return candidate


def clean_traits(data) -> dict:
    """Tidies the AI program's answer into read_description's shape. Words outside the lists are dropped."""
    if not isinstance(data, dict):
        raise ValueError("not an object")

    def pick(key, allowed):
        value = data.get(key)
        value = value.strip().lower() if isinstance(value, str) else ""
        return value if value in allowed else "any"

    sounds = []
    for sound in data.get("sounds") if isinstance(data.get("sounds"), list) else []:
        sound = sound.strip().lower() if isinstance(sound, str) else ""
        if sound in SOUNDS and sound not in sounds and len(sounds) < MAX_SOUNDS:
            sounds.append(sound)
    return {"gender": pick("gender", GENDERS), "age": pick("age", AGES), "accent": pick("accent", ACCENTS),
            "sounds": sounds, "unknown": []}

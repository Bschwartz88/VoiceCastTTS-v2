"""
Voice blends: mixing Kokoro voices by percentage and storing the result in custom_voices/.

A blend is <name>.npy (the voice the model reads) plus <name>.json, its recipe:
{"recipe": [{"voice": "af_sky", "share": 0.7}, ...], "voice_type": "Female"}.
Voices made before the blender (or by blend_voices.py) have no recipe file and can't be edited.
No audio code here: voice_cast.py and web_server.py use these functions.
"""

import io
import json
import math
import os
import re

import numpy as np

MAX_BLEND_VOICES = 3
MAX_NAME_LENGTH = 40
VOICE_SHAPE = (510, 1, 256)
VOICE_TYPES = ("Female", "Male", "Child", "Neutral")
PITCH_RANGE = (-3.0, 3.0)  # semitones, in half steps: bigger changes sound sped-up
START_SPEED_RANGE = (0.5, 2.0)
BAD_PITCH = "Pitch must be from -3 to +3, in half steps."
BAD_START_SPEED = "Starting speed must be from 0.5 to 2.0."
_NAME = re.compile(r"[A-Za-z0-9_-]+")
# A hand-copied voice may have spaces or accents, but nothing that leads to another folder.
_UNSAFE_IN_FILE_NAME = re.compile(r"[/\\:]|^\.")


class BlendError(ValueError):
    """A problem with a blend that is safe to show to the user."""


class BlendExists(BlendError):
    """Saving would replace an existing voice; the page asks first."""


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def check_pitch(pitch) -> float:
    """A pitch change in semitones: -3 to +3 in half steps."""
    if not _is_number(pitch) or not PITCH_RANGE[0] <= pitch <= PITCH_RANGE[1] or (pitch * 2) % 1:
        raise BlendError(BAD_PITCH)
    return float(pitch)


def check_start_speed(speed):
    """The speed a character gets when this voice is picked, or None."""
    if speed is None:
        return None
    if not _is_number(speed) or not START_SPEED_RANGE[0] <= speed <= START_SPEED_RANGE[1]:
        raise BlendError(BAD_START_SPEED)
    return round(float(speed), 2)


def check_name(name, base_ids) -> str:
    name = name.strip() if isinstance(name, str) else ""
    if not name:
        raise BlendError("Give the new voice a name.")
    if len(name) > MAX_NAME_LENGTH:
        raise BlendError(f"Use at most {MAX_NAME_LENGTH} characters for the name.")
    if not _NAME.fullmatch(name):
        raise BlendError("Use only letters, numbers, _ and - in the name (no spaces).")
    if name in base_ids:
        raise BlendError("That name belongs to a Kokoro voice. Choose another name.")
    return name


def normalize_recipe(recipe, max_voices=MAX_BLEND_VOICES) -> list:
    """[(voice, share)] with shares adding up to 1. Rows are {"voice": id, "weight": number}
    (or "share"); the same voice twice is added up; rows at 0 drop out."""
    limit = max_voices or float("inf")
    if not isinstance(recipe, list) or not 1 <= len(recipe) <= limit:
        raise BlendError(f"A blend mixes 1 to {max_voices or 'any number of'} voices.")
    weights = {}
    for row in recipe:
        voice = row.get("voice") if isinstance(row, dict) else None
        if not isinstance(voice, str) or not voice.strip():
            raise BlendError("Each row needs a voice.")
        weight = row.get("weight", row.get("share"))
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not math.isfinite(weight) or weight < 0:
            raise BlendError("Each slider needs a number of 0 or more.")
        voice = voice.strip()
        if _UNSAFE_IN_FILE_NAME.search(voice):  # it becomes part of a file path when the recipe is read
            raise BlendError(f"{voice} isn't a voice name.")
        weights[voice] = weights.get(voice, 0.0) + float(weight)
    total = sum(weights.values())
    if total <= 0:
        raise BlendError("Move at least one slider above 0.")
    return [(voice, weight / total) for voice, weight in weights.items() if weight > 0]


def mix(recipe, voices, max_voices=MAX_BLEND_VOICES):
    """The weighted sum of the recipe's voice arrays (float32, VOICE_SHAPE)."""
    mixed = None
    for voice, share in normalize_recipe(recipe, max_voices):
        if voice not in voices:
            raise BlendError(f"The voice {voice} isn't installed any more. Choose another voice for that row.")
        array = np.asarray(voices[voice], dtype=np.float32)
        if array.shape != VOICE_SHAPE:
            raise BlendError(f"The voice {voice} isn't a Kokoro voice file.")
        mixed = array * share if mixed is None else mixed + array * share
    return mixed.astype(np.float32)


def _paths(folder, name):
    return os.path.join(folder, f"{name}.npy"), os.path.join(folder, f"{name}.json")


def _write_atomic(path, data: bytes):
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def save_blend(folder, name, recipe, voice_type, voices, base_ids, replace=False, description="",
               pitch=0.0, start_speed=None):
    """Mixes the recipe and writes <name>.npy and <name>.json. Raises BlendExists for an existing
    name unless `replace`. Pitch (semitones) and start_speed are stored only when set. Returns the mixed array."""
    name = check_name(name, base_ids)
    if voice_type not in VOICE_TYPES:
        raise BlendError("Choose a voice type.")
    pitch = check_pitch(pitch)
    start_speed = check_start_speed(start_speed)
    parts = normalize_recipe(recipe)
    if any(voice == name for voice, _ in parts):
        raise BlendError("A blend can't use itself as one of its voices.")
    npy_path, json_path = _paths(folder, name)
    # Windows file names ignore capitals, so "Hero" would overwrite "hero" while the old name lives on.
    taken = {f[:-4].lower(): f[:-4] for f in os.listdir(folder) if f.endswith(".npy")} if os.path.isdir(folder) else {}
    other = taken.get(name.lower())
    if other and other != name:
        raise BlendError(f"A voice called {other} already exists. Capitals don't make a name different, "
                         f"so choose another name, or use Edit on {other}.")
    if os.path.exists(npy_path) and not replace:
        raise BlendExists(f"A voice called {name} already exists. Replace it?")
    array = mix(recipe, voices)
    os.makedirs(folder, exist_ok=True)
    buffer = io.BytesIO()
    np.save(buffer, array)
    _write_atomic(npy_path, buffer.getvalue())
    stored = {"recipe": [{"voice": v, "share": round(s, 4)} for v, s in parts], "voice_type": voice_type}
    if description:
        stored["description"] = description
    if pitch:
        stored["pitch"] = pitch
    if start_speed is not None:
        stored["start_speed"] = start_speed
    _write_atomic(json_path, json.dumps(stored, indent=2).encode("utf-8"))
    return array


def delete_blend(folder, name):
    """Removes <name>.npy and <name>.json."""
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise BlendError("There is no voice with that name.")
    paths = [p for p in _paths(folder, name) if os.path.exists(p)]
    if not paths:
        raise BlendError(f"There is no voice called {name}.")
    for path in paths:
        os.remove(path)


def forget_recipe(folder, name):
    """Removes a recipe that no longer matches its voice file (the command line replaced the voice)."""
    _, json_path = _paths(folder, name)
    if os.path.exists(json_path):
        os.remove(json_path)


def read_recipe(folder, name):
    """The stored recipe, or None when there is none or it is damaged."""
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        return None
    _, json_path = _paths(folder, name)
    try:
        with open(json_path, encoding="utf-8-sig") as f:  # a recipe saved by Notepad may start with a BOM
            data = json.load(f)
        recipe, voice_type = data["recipe"], data["voice_type"]
        if voice_type not in VOICE_TYPES or not isinstance(recipe, list) or not 1 <= len(recipe) <= MAX_BLEND_VOICES:
            return None
        shares = [row["share"] for row in recipe]
        if not all(isinstance(row["voice"], str) and isinstance(s, (int, float)) and not isinstance(s, bool)
                   and 0 < s <= 1 for row, s in zip(recipe, shares)) or abs(sum(shares) - 1) > 0.01:
            return None
        result = {"recipe": [{"voice": row["voice"], "share": float(row["share"])} for row in recipe],
                  "voice_type": voice_type}
        if isinstance(data.get("description"), str) and data["description"].strip():
            result["description"] = data["description"].strip()[:200]
        try:  # a hand-edited odd value is ignored, not fatal
            if check_pitch(data.get("pitch", 0)):
                result["pitch"] = check_pitch(data["pitch"])
        except BlendError:
            pass
        try:
            if check_start_speed(data.get("start_speed")) is not None:
                result["start_speed"] = check_start_speed(data["start_speed"])
        except BlendError:
            pass
        return result
    except (OSError, ValueError, KeyError, TypeError):
        return None


def recipe_label(recipe_rows) -> str:
    return " + ".join(f"{round(row['share'] * 100)}% {row['voice']}" for row in recipe_rows)

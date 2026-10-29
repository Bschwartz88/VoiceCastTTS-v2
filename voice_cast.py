"""
VoiceCastTTS: Multi-Speaker Script-to-Speech Engine
Generates multi-character dialogue and narration using Kokoro TTS on local hardware.
Optimized for Windows and NVIDIA GPUs (including GTX 1060 6GB) with automatic CPU fallback.
"""

import os
import preview_retention
import sys
import json
import re
import time
import hashlib
import argparse
import threading
import urllib.parse
from collections import OrderedDict
from collections.abc import Mapping
try:
    import numpy as np
    import soundfile as sf
    AUDIO_LIBS_AVAILABLE = True
except ImportError:
    np = None
    sf = None
    AUDIO_LIBS_AVAILABLE = False

from speakability import apply_pronunciations, clean_for_speech, drop_repeated_name, normalize_pronunciations
from delivery_cues import is_cue_tag, split_cues
import voice_blends
import performance
import speaker_labels

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG_PATH = os.path.join(SCRIPT_DIR, "voice_config.json")
DEFAULT_OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output")

_LATEST_AUDIO_BYTES = None
_LATEST_SPEAKER_TRACKS = {}
_LATEST_AUDIO_META = {}
_LATEST_AUDIO_LOCK = threading.Lock()

# Allowed ranges for values that can arrive from the browser.
MIN_SPEED, MAX_SPEED = 0.5, 2.0
MAX_PAUSE_SECONDS = 5.0

# Serializes model loading and inference across server threads.
_PIPELINE_LOCK = threading.RLock()


def clamp_speed(value, default: float = 1.0) -> float:
    try:
        speed = float(value)
    except (TypeError, ValueError):
        return default
    if speed != speed:  # NaN
        return default
    return max(MIN_SPEED, min(MAX_SPEED, speed))


def clamp_pause(value, default: float) -> float:
    try:
        pause = float(value)
    except (TypeError, ValueError):
        return default
    if pause != pause:  # NaN
        return default
    return max(0.0, min(MAX_PAUSE_SECONDS, pause))


def get_latest_audio():
    """Returns the most recently generated WAV audio bytes from memory."""
    with _LATEST_AUDIO_LOCK:
        return _LATEST_AUDIO_BYTES


def set_latest_audio(audio_bytes: bytes, meta: dict = None, speaker_tracks: Mapping = None):
    """Sets the most recently generated WAV audio bytes and speaker tracks in memory."""
    global _LATEST_AUDIO_BYTES, _LATEST_AUDIO_META, _LATEST_SPEAKER_TRACKS
    with _LATEST_AUDIO_LOCK:
        _LATEST_AUDIO_BYTES = audio_bytes
        _LATEST_AUDIO_META = meta or {}
        _LATEST_SPEAKER_TRACKS = speaker_tracks if speaker_tracks is not None else {}


def get_latest_speaker_tracks() -> Mapping:
    """Returns a read-only mapping of speaker names to WAV bytes (encoded lazily)."""
    with _LATEST_AUDIO_LOCK:
        return _LATEST_SPEAKER_TRACKS


def get_speaker_track_audio(speaker_name: str):
    """Returns WAV audio bytes for a specific speaker track."""
    if not speaker_name:
        return None
    target = speaker_name.strip().lower()
    tracks = get_latest_speaker_tracks()
    for spk in tracks:
        if spk.strip().lower() == target:
            return tracks[spk]
    return None


def track_filename(speaker: str) -> str:
    """Safe download name for a speaker's track, e.g. "Speaker 1" -> "track_Speaker_1.wav"."""
    clean_name = re.sub(r'[^a-zA-Z0-9_\-]', '', re.sub(r'\s+', '_', speaker.strip())) or "speaker"
    return f"track_{clean_name}.wav"


def get_all_tracks_zip_bytes() -> bytes:
    """Creates an in-memory ZIP archive containing the master dialogue and all speaker tracks."""
    import zipfile
    import io
    with _LATEST_AUDIO_LOCK:
        master, tracks = _LATEST_AUDIO_BYTES, _LATEST_SPEAKER_TRACKS
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if master:
            zf.writestr("full_dialogue.wav", master)
        for spk, data in tracks.items():
            zf.writestr(track_filename(spk), data)
    return buf.getvalue()


def audio_to_wav_bytes(audio_array, sample_rate: int = 24000) -> bytes:
    """Encodes numpy audio samples directly into standard WAV bytes in memory."""
    import io
    buf = io.BytesIO()
    sf.write(buf, audio_array, sample_rate, format="WAV")
    return buf.getvalue()


class LazySpeakerTracks(Mapping):
    """Read-only, time-aligned downloads built one at a time, only on request.

    Hold sparse speech segments instead of a full silent WAV per speaker. Exported
    bytes are deliberately not retained; repeated downloads trade cheap encoding
    for bounded memory. An older mapping remains valid when a new render completes.
    """
    def __init__(self, segments, total_samples, sample_rate):
        self._segments = {name: tuple(rows) for name, rows in segments.items()}
        self._total_samples = total_samples
        self._sample_rate = sample_rate

    def __iter__(self):
        return iter(self._segments)

    def __len__(self):
        return len(self._segments)

    def __getitem__(self, speaker):
        segments = self._segments[speaker]
        audio = np.zeros(self._total_samples, dtype=np.float32)
        for start, samples in segments:
            audio[start:start + len(samples)] = samples
        return audio_to_wav_bytes(audio, self._sample_rate)


def waveform_peaks(audio, bins=2048):
    """Compact actual amplitude peaks, covering every sample with bounded scratch space."""
    count = min(max(1, bins), len(audio))
    if count == 0:
        return []
    return [float(np.abs(audio[i * len(audio) // count:(i + 1) * len(audio) // count]).max())
            for i in range(count)]


def export_saved_audio(output_filename: str = "full_dialogue.wav") -> str:
    """Exports the in-memory audio to the specified WAV file on disk."""
    global _LATEST_AUDIO_BYTES
    if not _LATEST_AUDIO_BYTES:
        raise ValueError("No audio has been generated yet to export.")
    os.makedirs(DEFAULT_OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(DEFAULT_OUTPUT_DIR, output_filename)
    with open(out_path, "wb") as f:
        f.write(_LATEST_AUDIO_BYTES)
    return out_path


CUSTOM_VOICES_DIR = os.path.join(SCRIPT_DIR, "custom_voices")
_BASE_VOICE_IDS = None
VOICE_TYPE_LABELS = {"Female": "Female", "Male": "Male", "Child": "Child / Youth", "Neutral": "Neutral"}


def base_voice_ids() -> frozenset:
    """The Kokoro voice names in voices-v1.0.bin (empty until the file is downloaded)."""
    global _BASE_VOICE_IDS
    if _BASE_VOICE_IDS is None:
        path = os.path.join(SCRIPT_DIR, "voices-v1.0.bin")
        if not os.path.exists(path):
            return frozenset()
        with np.load(path, allow_pickle=False) as data:
            _BASE_VOICE_IDS = frozenset(data.files)
    return _BASE_VOICE_IDS


def get_custom_voice_entries():
    """Discovers all custom voices in custom_voices/ and formats them for the UI."""
    os.makedirs(CUSTOM_VOICES_DIR, exist_ok=True)
    entries = []
    for f in sorted(os.listdir(CUSTOM_VOICES_DIR)):
        if f.endswith(".npy"):
            v_name = f[:-4]
            recipe = voice_blends.read_recipe(CUSTOM_VOICES_DIR, v_name)
            if recipe:  # made in the studio's blender: its own folder, type and recipe
                entry = {
                    "id": v_name,
                    "accent": "Blend",
                    "gender": VOICE_TYPE_LABELS[recipe["voice_type"]],
                    "category": "My blends",
                    "tone": voice_blends.recipe_label(recipe["recipe"]),
                    "recipe": recipe["recipe"],
                    "voice_type": recipe["voice_type"],
                    "description": recipe.get("description", ""),
                }
                if recipe.get("pitch"):
                    entry["pitch"] = recipe["pitch"]
                    entry["tone"] += f", pitch {recipe['pitch']:+g}"
                if recipe.get("start_speed") is not None:
                    entry["start_speed"] = recipe["start_speed"]
                entries.append(entry)
                continue
            name_lower = v_name.lower()
            if any(k in name_lower for k in ["child", "kid", "boy", "girl", "youth", "teen"]):
                category = "Children & Youth"
                tone = "Youthful / child character voice"
            elif any(k in name_lower for k in ["fable", "story", "fairy", "tale"]):
                category = "Storybook & Fairy Tale Narrators"
                tone = "Enchanting fairy tale & bedtime narrator"
            else:
                category = "Custom Voices"
                tone = "Custom blended voice style"

            entries.append({
                "id": v_name,
                "accent": "Custom",
                "gender": "Blended",
                "category": category,
                "tone": tone
            })
    return entries


def load_config(config_path: str = DEFAULT_CONFIG_PATH) -> dict:
    """Loads voice configuration and pause timings, including any custom voices."""
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    # Merge custom voices into available_popular_voices list
    custom_entries = get_custom_voice_entries()
    if custom_entries and "available_popular_voices" in cfg:
        existing_ids = {v["id"] for v in cfg["available_popular_voices"]}
        new_entries = [c for c in custom_entries if c["id"] not in existing_ids]
        cfg["available_popular_voices"] = new_entries + cfg["available_popular_voices"]

    return cfg


def known_voice_ids(config: dict) -> set:
    """Voice IDs the app offers: the configured list, the cast defaults, and custom voices."""
    ids = {v.get("id") for v in config.get("available_popular_voices", []) if isinstance(v, dict)}
    ids |= {v.get("voice") for v in config.get("default_voices", {}).values() if isinstance(v, dict)}
    ids |= {entry["id"] for entry in get_custom_voice_entries()}
    with _PIPELINE_LOCK:
        if _CACHED_PIPELINE is not None and _CACHED_PIPELINE.backend == "onnx":
            ids |= set(_CACHED_PIPELINE.model.voices.keys())
    ids.discard(None)
    return ids


def parse_script(script_text: str):
    """
    Parses dialogue script with [SpeakerName] tags.
    Returns a list of tuples: (speaker_name, text_content).
    """
    lines = script_text.strip().split("\n")
    parsed_entries = []
    current_speaker = "Narrator"
    
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        # Match speaker tag e.g. [Narrator] or [Alice]: or Bob:
        match = re.match(r"^\[([^\]]+)\]\s*(.*)$", line)
        if match and not is_cue_tag(match.group(1)):  # [whispers] or [pause 2s] is a cue, not a speaker
            speaker, text = speaker_labels.normalize_speaker_line(match.group(1), match.group(2))
            if text:
                parsed_entries.append((speaker, text))
            current_speaker = speaker
        else:
            # Fallback if line continues previous speaker without bracket tag
            parsed_entries.append((current_speaker, line))

    return parsed_entries


def extract_speakers(script_text: str):
    """
    Extracts all unique speaker names from bracket tags [SpeakerName] in script order.
    Returns a list of unique speaker names.
    """
    lines = script_text.strip().split("\n")
    speakers = []
    seen = set()
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^\[([^\]]+)\]", line)
        if match and not is_cue_tag(match.group(1)):
            spk = speaker_labels.split_speaker_label(match.group(1))["name"]
            spk_lower = spk.lower()
            if spk_lower not in seen:
                seen.add(spk_lower)
                speakers.append(spk)
    return speakers


# Kokoro voices differ by up to ~14 dB in loudness, and some peak above full scale (which clips
# when written to WAV). Leveling brings every line to the same speech loudness.
TARGET_SPEECH_DBFS = -20.0   # loudness of the spoken parts of a line
PEAK_CEILING_DBFS = -1.0     # never louder than this, so nothing clips
MAX_LEVEL_GAIN_DB = 12.0     # don't over-boost very quiet or very short lines


def _db_to_amplitude(db: float) -> float:
    return 10 ** (db / 20)


def speech_loudness_dbfs(audio, sample_rate: int = 24000):
    """Loudness of the spoken parts of `audio` in dBFS (pauses between words are ignored), or None if silent."""
    frame = max(1, sample_rate // 50)  # 20 ms frames
    if len(audio) == 0:
        return None
    full, remainder = divmod(len(audio), frame)
    energies = np.square(audio[:full * frame].reshape(full, frame).astype(np.float64)).mean(axis=1)
    if remainder:
        energies = np.append(energies, np.square(audio[full * frame:].astype(np.float64)).mean())
    counts = np.full(full, frame, dtype=np.float64)
    if remainder:
        counts = np.append(counts, remainder)
    frames_rms = np.sqrt(energies)
    loudest = frames_rms.max()
    if loudest <= 1e-6:
        return None
    active = frames_rms > loudest * 0.1
    return 10 * np.log10(np.average(energies[active], weights=counts[active]))


def level_line(audio, even_volume: bool = True, sample_rate: int = 24000):
    """Returns a leveled copy of a line: brought to TARGET_SPEECH_DBFS when `even_volume` is on,
    and always kept under PEAK_CEILING_DBFS. The input array is never modified (it may be cached)."""
    if audio is None or len(audio) == 0:
        return audio
    peak = float(np.abs(audio).max())
    if peak <= 1e-6:
        return audio

    gain = 1.0
    if even_volume:
        loudness = speech_loudness_dbfs(audio, sample_rate)
        if loudness is not None:
            gain_db = min(TARGET_SPEECH_DBFS - loudness, MAX_LEVEL_GAIN_DB)
            gain = _db_to_amplitude(gain_db)
    gain = min(gain, _db_to_amplitude(PEAK_CEILING_DBFS) / peak)
    if abs(gain - 1.0) < 1e-4:
        return audio
    return (audio * gain).astype(np.float32)


def apply_gain(audio, gain_db: float):
    """Returns `audio` made louder or quieter by `gain_db` (a delivery cue), never above PEAK_CEILING_DBFS.
    The input array is never modified (it may be cached)."""
    if audio is None or len(audio) == 0 or abs(gain_db) < 1e-6:
        return audio
    peak = float(np.abs(audio).max())
    if peak <= 1e-6:
        return audio
    gain = min(_db_to_amplitude(gain_db), _db_to_amplitude(PEAK_CEILING_DBFS) / peak)
    return (audio * gain).astype(np.float32)


def get_audio_silence(duration_seconds: float, sample_rate: int = 24000):
    """Generates silent audio samples for natural conversational pauses."""
    if np is None:
        return None
    num_samples = int(duration_seconds * sample_rate)
    return np.zeros(num_samples, dtype=np.float32)


def voice_lang(voice, _depth: int = 0) -> str:
    """British pronunciation for British voices (bf_/bm_ ...) and for a studio blend whose largest part is British."""
    if not isinstance(voice, str):
        return "en-us"
    # The recipe decides first, so a blend called "bella_mix" made of American voices stays American.
    recipe = voice_blends.read_recipe(CUSTOM_VOICES_DIR, voice) if _depth < 3 else None
    if recipe:
        largest = max(recipe["recipe"], key=lambda row: row["share"])["voice"]
        return voice_lang(largest, _depth + 1)
    return "en-gb" if voice.startswith("b") else "en-us"


class UnifiedPipeline:
    def __init__(self, backend, model):
        self.backend = backend
        self.model = model

    def generate(self, text, voice, speed, lang=None):
        """`voice` is a voice name, or (ONNX only) a voice array with `lang` given."""
        with _PIPELINE_LOCK:
            if self.backend == "onnx":
                samples, _ = self.model.create(text, voice=voice, speed=speed, lang=lang or voice_lang(voice))
                return [samples]
            elif self.backend == "pytorch":
                parts = []
                for _, _, audio in self.model(text, voice=voice, speed=speed):
                    parts.append(audio)
                return parts


MODEL_RELEASE_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"

# SHA-256 of the official model-files-v1.0 release assets. Downloads that don't match are rejected.
MODEL_FILES = {
    "kokoro-v1.0.onnx": "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5",
    "voices-v1.0.bin": "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
}


def sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def ensure_onnx_models():
    """Ensures kokoro-v1.0.onnx and voices-v1.0.bin are present, downloading and verifying them once if needed."""
    import urllib.request

    paths = []
    for filename, expected_sha256 in MODEL_FILES.items():
        path = os.path.join(SCRIPT_DIR, filename)
        paths.append(path)
        if os.path.exists(path) and os.path.getsize(path) >= 1000:
            continue

        temp_path = path + ".tmp"
        print(f"[+] Downloading {filename} from official release (~one-time download)...")
        try:
            urllib.request.urlretrieve(f"{MODEL_RELEASE_URL}/{filename}", temp_path)
            actual_sha256 = sha256_of(temp_path)
            if actual_sha256 != expected_sha256:
                raise RuntimeError(
                    f"Checksum mismatch for {filename}: expected {expected_sha256}, got {actual_sha256}. "
                    "The download was discarded."
                )
            os.replace(temp_path, path)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        print(f"[OK] {filename} downloaded and verified.")

    return paths[0], paths[1]


_CACHED_PIPELINE = None

def load_custom_voices_into_model(onnx_model):
    """Loads any .npy voice style files found in custom_voices/ into the ONNX model."""
    os.makedirs(CUSTOM_VOICES_DIR, exist_ok=True)
    if not isinstance(onnx_model.voices, dict):
        onnx_model.voices = dict(onnx_model.voices)

    for f in os.listdir(CUSTOM_VOICES_DIR):
        if f.endswith(".npy"):
            v_name = f[:-4]
            try:
                onnx_model.voices[v_name] = np.load(os.path.join(CUSTOM_VOICES_DIR, f), allow_pickle=False)
            except Exception as e:
                print(f"[!] Could not load custom voice '{f}': {e}")


def get_pipeline(device: str = "auto"):
    """Loads and caches the Kokoro pipeline in memory for fast repeated generations."""
    with _PIPELINE_LOCK:
        return _load_pipeline(device)


def _load_pipeline(device: str):
    global _CACHED_PIPELINE
    if _CACHED_PIPELINE is not None:
        if _CACHED_PIPELINE.backend == "onnx":
            load_custom_voices_into_model(_CACHED_PIPELINE.model)
        return _CACHED_PIPELINE

    # 1. Try kokoro-onnx (compatible with Python 3.10, 3.11, 3.12, 3.13, 3.14)
    try:
        from kokoro_onnx import Kokoro
        print("[+] Initializing Kokoro ONNX Engine (Python 3.13+ compatible)...")
        model_path, voices_path = ensure_onnx_models()
        onnx_model = Kokoro(model_path, voices_path)
        load_custom_voices_into_model(onnx_model)
        print("[+] Kokoro ONNX model loaded successfully into memory.\n")
        _CACHED_PIPELINE = UnifiedPipeline("onnx", onnx_model)
        return _CACHED_PIPELINE
    except ImportError:
        pass

    # 2. Try official PyTorch kokoro (Python 3.10 - 3.12)
    try:
        from kokoro import KPipeline
        import torch

        if device == "auto":
            actual_device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            actual_device = device

        print(f"[+] Initializing Kokoro PyTorch TTS (device: {actual_device.upper()})...")
        pipeline = KPipeline(lang_code="a")
        print("[+] Model loaded successfully into memory.\n")
        _CACHED_PIPELINE = UnifiedPipeline("pytorch", pipeline)
        return _CACHED_PIPELINE
    except ImportError:
        pass

    raise ImportError(
        "Neither 'kokoro-onnx' nor 'kokoro' is installed. Please run: pip install -r requirements.txt"
    )


PREVIEWS_DIR = os.path.join(DEFAULT_OUTPUT_DIR, "previews")


MAX_PREVIEW_CHARS = 300


def default_preview_text(voice_id: str) -> str:
    """A passage long enough (about 8 seconds) for speed differences to be audible."""
    friendly_name = re.sub(r"^[ab][fm]_", "", voice_id).replace("_", " ").title()
    return (f"Hello, I am {friendly_name}. Here is how I sound reading a longer passage, "
            "so you can judge my pace and rhythm before you render the whole script.")


def generate_voice_preview(voice_id: str, speed: float = 1.0, device: str = "auto", text: str = None,
                           pronunciations=None) -> str:
    """Generates and caches a preview clip for a voice and speed, reading `text` or a default passage.
    `pronunciations` is a list of (word, say) pairs applied to the text first."""
    clean_voice_id = re.sub(r'[^a-zA-Z0-9_\-]', '', voice_id) or "af_heart"
    speed = clamp_speed(speed)

    spoken = apply_pronunciations(text or "", pronunciations or [])
    sample_text = clean_for_speech(spoken)[:MAX_PREVIEW_CHARS].strip() or default_preview_text(clean_voice_id)

    # The file name includes the speed and a hash of the text, so a changed line or speed is never served stale.
    speed_pct = int(round(speed * 100))
    speed_suffix = f"_spd{speed_pct}" if speed_pct != 100 else ""
    text_hash = hashlib.sha256(sample_text.encode("utf-8")).hexdigest()[:10]
    os.makedirs(PREVIEWS_DIR, exist_ok=True)
    preview_retention.prune(PREVIEWS_DIR)
    # "_lv" marks samples made with volume leveling, so older unleveled samples aren't reused.
    # A custom voice's file can be replaced (an edited blend), so its samples carry the file's signature.
    signature = _voice_signature(clean_voice_id)
    voice_suffix = "" if signature == "base" else "_v" + hashlib.sha256(signature.encode("utf-8")).hexdigest()[:8]
    pitch = voice_pitch(clean_voice_id)
    pitch_suffix = f"_p{pitch}" if pitch else ""
    filename = f"preview_{clean_voice_id}{voice_suffix}{speed_suffix}{pitch_suffix}_{text_hash}_lv.wav"
    filepath = os.path.join(PREVIEWS_DIR, filename)
    rel_url = f"/output/previews/{filename}"

    if os.path.exists(filepath) and os.path.getsize(filepath) > 1000:
        return rel_url

    pipeline = get_pipeline(device=device)

    spoken_audio = _speak_pitched(pipeline, sample_text, clean_voice_id, speed, pitch)
    if spoken_audio is not None and AUDIO_LIBS_AVAILABLE:
        audio = level_line(spoken_audio)
        sf.write(filepath, audio, 24000)
        preview_retention.prune(PREVIEWS_DIR, keep=filepath)
        return rel_url
    return None


BLEND_SAMPLE_TEXT = "Hello there. This is how my new voice sounds when I read a few sentences from a story."
ONNX_ONLY = "Blending needs the standard voice engine (kokoro-onnx)."


def generate_blend_preview(recipe, text: str = None, device: str = "auto", pitch: float = 0.0) -> str:
    """A sample of an unsaved mix: mixed in memory and passed straight to the model (nothing is saved).
    The file name hashes the mix (including each ingredient's signature) and the text, so it is reused."""
    parts = voice_blends.normalize_recipe(recipe)
    sample_text = clean_for_speech(text or "")[:MAX_PREVIEW_CHARS].strip() or BLEND_SAMPLE_TEXT
    pitch = voice_blends.check_pitch(pitch)
    key = json.dumps([[v, round(s, 4), _voice_signature(v)] for v, s in parts] + [pitch]) + "\n" + sample_text
    filename = f"preview_blend_{hashlib.sha256(key.encode('utf-8')).hexdigest()[:16]}_lv.wav"
    os.makedirs(PREVIEWS_DIR, exist_ok=True)
    preview_retention.prune(PREVIEWS_DIR)
    filepath = os.path.join(PREVIEWS_DIR, filename)
    rel_url = f"/output/previews/{filename}"
    if os.path.exists(filepath) and os.path.getsize(filepath) > 1000:
        return rel_url

    pipeline = get_pipeline(device=device)
    if pipeline.backend != "onnx":
        raise voice_blends.BlendError(ONNX_ONLY)
    voice = voice_blends.mix(recipe, pipeline.model.voices)
    largest = max(parts, key=lambda p: p[1])[0]
    audio = level_line(_speak_pitched(pipeline, sample_text, voice, 1.0, pitch, lang=voice_lang(largest)))
    sf.write(filepath, audio, 24000)
    preview_retention.prune(PREVIEWS_DIR, keep=filepath)
    return rel_url


def save_blend(name: str, recipe, voice_type: str, replace: bool = False, description: str = "",
               pitch: float = 0.0, start_speed=None) -> str:
    """Saves a blend into custom_voices/ and makes it usable at once. Returns its name.
    Holds the model lock, so a model loading at the same time doesn't read the folder half-changed."""
    with _PIPELINE_LOCK:
        pipeline = get_pipeline()
        if pipeline.backend != "onnx":
            raise voice_blends.BlendError(ONNX_ONLY)
        name = voice_blends.check_name(name, base_voice_ids())
        array = voice_blends.save_blend(CUSTOM_VOICES_DIR, name, recipe, voice_type, pipeline.model.voices,
                                        base_voice_ids(), replace=replace, description=description,
                                        pitch=pitch, start_speed=start_speed)
        pipeline.model.voices[name] = array
        _PITCH_CACHE.pop(name, None)  # re-read, even if the files' times look unchanged
        return name


def forget_custom_voice(name: str):
    """Removes a deleted voice from the loaded model (the folder is read again at each load)."""
    pipeline = _CACHED_PIPELINE
    if pipeline is not None and pipeline.backend == "onnx" and isinstance(pipeline.model.voices, dict):
        pipeline.model.voices.pop(name, None)
    _PITCH_CACHE.pop(name, None)


def delete_blend(name: str):
    with _PIPELINE_LOCK:  # see save_blend
        voice_blends.delete_blend(CUSTOM_VOICES_DIR, name)
        forget_custom_voice(name)


# Rendered lines are kept in memory, so re-rendering a script only synthesizes lines whose
# text, voice or speed changed. Kokoro is deterministic, so a cached line is identical to a fresh one.
MAX_LINE_CACHE_BYTES = 256 * 1024 * 1024  # about 45 minutes of audio
_LINE_CACHE = OrderedDict()
_LINE_CACHE_BYTES = 0
_LINE_CACHE_LOCK = threading.Lock()


def shift_pitch(audio, semitones: float):
    """Raises (or lowers) the pitch by `semitones`, also making the audio shorter (or longer) by the same
    ratio: callers ask Kokoro for a matching slower (or faster) reading first. A band-limited resample
    with NumPy's FFT, so no extra package is needed. No change returns the same array."""
    if not semitones or audio is None or len(audio) == 0:
        return audio
    factor = 2 ** (semitones / 12)
    n = len(audio)
    m = max(1, int(round(n / factor)))
    spectrum = np.fft.rfft(audio)
    keep = m // 2 + 1
    if keep <= len(spectrum):
        spectrum = spectrum[:keep]
    else:
        spectrum = np.concatenate([spectrum, np.zeros(keep - len(spectrum), dtype=spectrum.dtype)])
    out = (np.fft.irfft(spectrum, n=m) * (m / n)).astype(np.float32)
    # The resample treats the piece as a loop, so its two ends can bleed into each other: fade 5 ms at each end.
    fade = min(PITCH_FADE_SAMPLES, m // 4)
    if fade > 1:
        ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        out[:fade] *= ramp
        out[-fade:] *= ramp[::-1]
    return out


PITCH_FADE_SAMPLES = 120  # 5 ms at 24 kHz
_PITCH_CACHE = {}  # voice id -> (file times, pitch): the recipe is read again only when the voice is saved again


def voice_pitch(voice_id) -> float:
    """A blend's pitch change in semitones (0 for Kokoro's voices and voices without one)."""
    if not isinstance(voice_id, str):
        return 0.0
    npy_path = os.path.join(CUSTOM_VOICES_DIR, f"{voice_id}.npy")
    json_path = os.path.join(CUSTOM_VOICES_DIR, f"{voice_id}.json")
    try:
        times = (os.path.getmtime(npy_path), os.path.getmtime(json_path))
    except OSError:
        return 0.0  # a Kokoro voice, or a custom voice without a recipe
    cached = _PITCH_CACHE.get(voice_id)
    if cached and cached[0] == times:
        return cached[1]
    recipe = voice_blends.read_recipe(CUSTOM_VOICES_DIR, voice_id)
    pitch = float(recipe.get("pitch", 0.0)) if recipe else 0.0
    _PITCH_CACHE[voice_id] = (times, pitch)
    return pitch


def _speak_pitched(pipeline, text, voice, speed, pitch, lang=None):
    """Kokoro's reading at `speed`, with the pitch moved by `pitch` semitones (the pace stays as asked)."""
    factor = 2 ** (pitch / 12)
    kokoro_speed = clamp_speed(speed / factor)  # kokoro-onnx refuses speeds outside 0.5-2.0; the pace drifts a little at the ends
    if lang is None:
        parts = pipeline.generate(text, voice=voice, speed=kokoro_speed)
    else:
        parts = pipeline.generate(text, voice=voice, speed=kokoro_speed, lang=lang)
    if not parts:
        return None
    return shift_pitch(np.concatenate(parts).astype(np.float32, copy=False), pitch)


def _voice_signature(voice_id: str) -> str:
    """Changes when a custom voice file is replaced, so its cached lines are not reused."""
    path = os.path.join(CUSTOM_VOICES_DIR, f"{voice_id}.npy")
    return str(os.path.getmtime(path)) if os.path.exists(path) else "base"


def _line_cache_key(text: str, voice_id: str, speed: float):
    return (voice_id, _voice_signature(voice_id), float(speed), voice_pitch(voice_id), text)


def _line_cache_get(key):
    with _LINE_CACHE_LOCK:
        audio = _LINE_CACHE.get(key)
        if audio is not None:
            _LINE_CACHE.move_to_end(key)
        return audio


def _line_cache_put(key, audio):
    global _LINE_CACHE_BYTES
    with _LINE_CACHE_LOCK:
        if key in _LINE_CACHE or audio.nbytes > MAX_LINE_CACHE_BYTES:
            return
        _LINE_CACHE[key] = audio
        _LINE_CACHE_BYTES += audio.nbytes
        while _LINE_CACHE_BYTES > MAX_LINE_CACHE_BYTES:
            _, evicted = _LINE_CACHE.popitem(last=False)
            _LINE_CACHE_BYTES -= evicted.nbytes


def clear_line_cache():
    global _LINE_CACHE_BYTES
    with _LINE_CACHE_LOCK:
        _LINE_CACHE.clear()
        _LINE_CACHE_BYTES = 0


def _line_parts(raw_text: str, pronunciations) -> list:
    """The line cut at its delivery cues (delivery_cues.split_cues), each spoken piece cleaned for speech.
    Pronunciations are applied per piece, after the cut, so they can't change a cue.
    Spoken pieces left with nothing to say are dropped."""
    parts = []
    for piece in split_cues(raw_text):
        if piece["kind"] == "speech":
            text = clean_for_speech(apply_pronunciations(piece["text"], pronunciations))
            if not text:
                continue
            piece = {**piece, "text": text}
        parts.append(piece)
    return parts


def _speak_piece(pipeline, part: dict, voice_id: str, speed: float, even_volume: bool, sample_rate: int):
    """Audio for one spoken piece: reused from the line cache when possible, then leveled and given its
    cue's loudness change. Returns (audio or None, whether it was reused)."""
    piece_speed = clamp_speed(speed * part["speed_factor"])
    cache_key = _line_cache_key(part["text"], voice_id, piece_speed)
    audio = _line_cache_get(cache_key)
    was_cached = audio is not None
    if audio is None:
        audio = _speak_pitched(pipeline, part["text"], voice_id, piece_speed, voice_pitch(voice_id))
        if audio is None:
            return None, False
        _line_cache_put(cache_key, audio)
    # Both steps return new arrays (or the input untouched), so the cached original stays as it was.
    return apply_gain(level_line(audio, even_volume, sample_rate), part["gain_db"]), was_cached


def render_dialogue_content(
    script_raw: str,
    config: dict = None,
    output_filename: str = "full_dialogue.wav",
    device: str = "auto",
    dry_run: bool = False,
    export_wav: bool = False,
    progress_callback = None,
    should_cancel = None,
    audio_callback = None
) -> dict:
    """Core generation logic that operates directly on script text and config dict.
    `should_cancel` is an optional callable checked before each line; when it returns True the
    render stops and returns {"success": False, "cancelled": True}. Lines rendered so far stay cached."""
    start_total_time = time.perf_counter()
    if config is None:
        config = load_config()

    voices_map = config.get("default_voices", {})
    pronunciations = normalize_pronunciations(config.get("pronunciations"))
    even_volume = bool(config.get("even_volume", True))
    pacing = config.get("pacing_seconds", {})

    dialogue_pause = clamp_pause(pacing.get("dialogue_turn_pause", 0.35), 0.35)
    narrator_pause = clamp_pause(pacing.get("narrator_transition_pause", 0.65), 0.65)
    same_speaker_pause = clamp_pause(pacing.get("same_speaker_sentence_pause", 0.15), 0.15)
    # Performance (performance.py). With no setting at all (command line, older callers) it is off,
    # so the audio is exactly as before; the studio always sends one.
    level = performance.clean_level(config["performance"]) if "performance" in config else "off"

    if not dry_run and not AUDIO_LIBS_AVAILABLE:
        return {"success": False, "error": "Required audio libraries are unavailable. Check the local installation.",
                "lines": []}

    entries = parse_script(script_raw)
    if not entries:
        return {"success": False, "error": "Script is empty or has no recognized lines.", "lines": []}

    pipeline = None
    sample_rate = 24000

    if not dry_run:
        try:
            pipeline = get_pipeline(device=device)
        except Exception as e:
            print(f"[!] Could not initialize TTS pipeline: {e}")
            return {"success": False, "error": "The speech engine could not start. Check the local model files and restart VoiceCast.",
                    "lines": []}

    # Identify all distinct speakers for multi-track stems
    unique_speakers = []
    seen_speakers = set()
    for spk, _ in entries:
        spk_clean = spk.strip()
        if spk_clean.lower() not in seen_speakers:
            seen_speakers.add(spk_clean.lower())
            unique_speakers.append(spk_clean)

    stitched_audio_chunks = []
    line_segments = []  # (speaker, start_sample, audio) for building per-speaker tracks
    position = 0        # running length of the stitched audio, in samples
    previous_speaker = None
    previous_text = None  # the last spoken line's text, for the cut-off rule
    total_estimated_pause_sec = 0.0
    total_words = 0
    cached_lines = 0
    lines_meta = []

    emitted_chunks = 0

    def emit_pending_audio():
        nonlocal emitted_chunks
        if audio_callback is not None:
            for chunk in stitched_audio_chunks[emitted_chunks:]:
                audio_callback(chunk, sample_rate)
            emitted_chunks = len(stitched_audio_chunks)

    for idx, (speaker, raw_text) in enumerate(entries, 1):
        if should_cancel and should_cancel():
            print(f"[!] Render stopped after {idx - 1} of {len(entries)} lines.")
            return {
                "success": False,
                "cancelled": True,
                "error": "Render stopped.",
                "lines_done": idx - 1,
                "total_lines": len(entries),
                "lines": lines_meta,
            }

        parts = _line_parts(raw_text, pronunciations)
        if not any(p["kind"] == "speech" for p in parts):
            # Only pauses (or nothing to say): silence on top of the usual gap; the speaker doesn't change.
            seconds = sum(p["seconds"] for p in parts)
            if seconds > 0:
                total_estimated_pause_sec += seconds
                if not dry_run and AUDIO_LIBS_AVAILABLE:
                    silence_chunk = get_audio_silence(seconds, sample_rate)
                    stitched_audio_chunks.append(silence_chunk)
                    position += len(silence_chunk)
                print(f"[{idx:02d}/{len(entries):02d}] pause {seconds:g}s")
            emit_pending_audio()
            continue
        cleaned_text = " ".join(p["text"] for p in parts if p["kind"] == "speech")
        total_estimated_pause_sec += sum(p["seconds"] for p in parts if p["kind"] == "pause")
        is_narrator = speaker.strip().lower() == "narrator"
        # Clues are read only from the parts without a written cue, so a cue's "!" stays with its own part.
        uncued_text = " ".join(p["text"] for p in parts if p["kind"] == "speech" and not p["cue"])
        auto_speed, auto_gain = performance.line_delivery(uncued_text, is_narrator, level)
        if even_volume:
            # Levelling must not be undone by automatic punctuation/capitalization gain.
            # Explicit written cues still intentionally change relative loudness.
            auto_gain = 0.0
        parts = [p if p["kind"] != "speech"
                 else {**p, "gain_db": performance.cue_gain(p["gain_db"], level)} if p["cue"]
                 else {**p, "speed_factor": p["speed_factor"] * auto_speed, "gain_db": p["gain_db"] + auto_gain}
                 for p in parts]

        words_in_line = len(cleaned_text.split())
        total_words += words_in_line

        # Resolve speaker configuration (exact match, case-insensitive, or fallback)
        speaker_cfg = voices_map.get(speaker)
        if not speaker_cfg:
            for k, v in voices_map.items():
                if speaker_labels.split_speaker_label(k)["name"].strip().lower() == speaker.strip().lower():
                    speaker_cfg = v
                    break
        if not speaker_cfg:
            if speaker.strip().lower() == "narrator":
                speaker_cfg = voices_map.get("Narrator", {"voice": "bm_fable", "speed": 0.95})
            else:
                non_narrator = [v for k, v in voices_map.items() if k.lower() != "narrator"]
                speaker_cfg = non_narrator[0] if non_narrator else {"voice": "af_sky", "speed": 1.0}

        voice_id = speaker_cfg.get("voice", "af_heart")
        speed = clamp_speed(speaker_cfg.get("speed", 1.0))

        pause_duration = 0.0
        if previous_speaker is not None:
            if speaker.lower() == "narrator" or previous_speaker.lower() == "narrator":
                pause_duration = narrator_pause
            elif speaker.strip().lower() != previous_speaker.strip().lower():
                pause_duration = dialogue_pause
            else:
                pause_duration = same_speaker_pause
            pause_duration = performance.gap_seconds(
                pause_duration, previous_text, previous_speaker.strip().lower() == "narrator", cleaned_text,
                is_narrator, speaker.strip().lower() != previous_speaker.strip().lower(), level)

            total_estimated_pause_sec += pause_duration
            if not dry_run and AUDIO_LIBS_AVAILABLE:
                silence_chunk = get_audio_silence(pause_duration, sample_rate)
                stitched_audio_chunks.append(silence_chunk)
                position += len(silence_chunk)

        line_start_sample = position
        was_cached = False
        if not dry_run and pipeline is not None and AUDIO_LIBS_AVAILABLE:
            # Each piece between cues is made on its own (a line without cues is one piece, as before).
            chunks, was_cached = [], True
            for part in parts:
                if part["kind"] == "pause":
                    chunks.append(get_audio_silence(part["seconds"], sample_rate))
                    continue
                piece_audio, reused = _speak_piece(pipeline, part, voice_id, speed, even_volume, sample_rate)
                was_cached = was_cached and reused
                if piece_audio is not None:
                    chunks.append(piece_audio)
            line_audio = np.concatenate(chunks) if chunks else None
            if line_audio is not None and len(line_audio):
                stitched_audio_chunks.append(line_audio)
                line_segments.append((speaker.strip().lower(), position, line_audio))
                position += len(line_audio)
        emit_pending_audio()
        cached_lines += was_cached

        pause_info = f" (+{pause_duration:.2f}s pause)" if pause_duration > 0 else ""
        cache_info = " [reused]" if was_cached else ""
        cue_notes = [f"pause {p['seconds']:g}s" if p["kind"] == "pause"
                     else f"{p['cue']} {p['gain_db']:+g} dB x{p['speed_factor']:g}"
                     for p in parts if p["kind"] == "pause" or p["cue"]]
        if cue_notes:
            cache_info += f" <{', '.join(cue_notes)}>"
        if (auto_speed, auto_gain) != (1.0, 0.0):
            cache_info += f" <auto x{auto_speed:g} {auto_gain:+g} dB>"
        try:
            print(f"[{idx:02d}/{len(entries):02d}] {speaker} ({voice_id}{pause_info}){cache_info}: \"{cleaned_text}\"")
        except UnicodeEncodeError:
            print(f"[{idx:02d}/{len(entries):02d}] {speaker} ({voice_id}{pause_info}){cache_info}: [text line]")

        timing = {}
        if not dry_run and AUDIO_LIBS_AVAILABLE:
            timing = {"start": line_start_sample / sample_rate, "end": position / sample_rate}

        lines_meta.append({
            "idx": idx,
            "speaker": speaker,
            "voice": voice_id,
            "speed": speed,
            "text": cleaned_text,
            "pause_before": pause_duration,
            "cached": was_cached,
            **timing
        })

        if progress_callback:
            progress_callback({
                "current": idx,
                "total": len(entries),
                "percent": int((idx / len(entries)) * 100),
                "speaker": speaker,
                "voice": voice_id,
                "text": cleaned_text,
                "cached": was_cached
            })

        previous_speaker = speaker
        previous_text = cleaned_text

    final_output_path = None
    final_audio_rel_url = None
    total_duration_sec = 0.0
    tracks_meta = []
    speaker_wav_dict = {}
    peaks = []

    if not dry_run and stitched_audio_chunks and AUDIO_LIBS_AVAILABLE:
        final_audio = np.concatenate(stitched_audio_chunks)
        total_duration_sec = len(final_audio) / sample_rate
        wav_bytes = audio_to_wav_bytes(final_audio, sample_rate)

        # Keep only the speech segments; full-duration stems are encoded on download.
        segments = {}
        for seg_speaker, seg_start, seg_audio in line_segments:
            segments.setdefault(seg_speaker, []).append((seg_start, seg_audio))
        named_segments = {}
        for spk in unique_speakers:
            rows = segments.get(spk.strip().lower())
            if rows:
                named_segments[spk] = rows
                tracks_meta.append({
                    "speaker": spk,
                    "filename": track_filename(spk),
                    "url": f"/api/audio/track?speaker={urllib.parse.quote(spk)}"
                })
        if len(named_segments) == 1:
            # With one voice, its stem is exactly the master, including pauses.
            speaker_wav_dict = {next(iter(named_segments)): wav_bytes}
        else:
            speaker_wav_dict = LazySpeakerTracks(named_segments, len(final_audio), sample_rate)
        peaks = waveform_peaks(final_audio)

        set_latest_audio(
            wav_bytes,
            meta={
                "duration": total_duration_sec,
                "sample_rate": sample_rate
            },
            speaker_tracks=speaker_wav_dict
        )
        final_audio_rel_url = "/api/audio/latest.wav"

        if export_wav:
            os.makedirs(DEFAULT_OUTPUT_DIR, exist_ok=True)
            final_output_path = os.path.join(DEFAULT_OUTPUT_DIR, output_filename)
            with open(final_output_path, "wb") as f:
                f.write(wav_bytes)

    elapsed_sec = time.perf_counter() - start_total_time
    return {
        "success": True,
        "dry_run": dry_run,
        "audio_path": final_output_path,
        "audio_url": final_audio_rel_url,
        "exported_to_disk": bool(final_output_path),
        "duration_seconds": total_duration_sec,
        "waveform_peaks": peaks,
        "elapsed_seconds": elapsed_sec,
        "total_words": total_words,
        "estimated_pause_sec": total_estimated_pause_sec,
        "cached_lines": cached_lines,
        "lines": lines_meta,
        "tracks": tracks_meta,
        "zip_url": "/api/audio/tracks.zip" if len(tracks_meta) > 1 else None
    }


def render_dialogue(
    script_path: str,
    config_path: str = DEFAULT_CONFIG_PATH,
    output_filename: str = "full_dialogue.wav",
    device: str = "auto",
    dry_run: bool = False,
    export_wav: bool = False
):
    """Renders the entire script to audio with multi-speaker voice casting."""
    config = load_config(config_path)

    with open(script_path, "r", encoding="utf-8") as f:
        script_raw = f.read()

    mode_label = "DRY RUN (Validation Mode)" if dry_run else "FULL AUDIO GENERATION"
    print(f"\n=======================================================")
    print(f" VoiceCastTTS: {mode_label}")
    print(f" Input File:   {script_path}")
    print(f" Config File:  {config_path}")
    print(f" Export WAV:   {'Yes' if export_wav else 'No (in-memory only)'}")
    print(f"=======================================================\n")

    result = render_dialogue_content(
        script_raw=script_raw,
        config=config,
        output_filename=output_filename,
        device=device,
        dry_run=dry_run,
        export_wav=export_wav
    )

    if not result.get("success"):
        print(f"[-] Error: {result.get('error')}")
        return

    if not result.get("dry_run") and result.get("duration_seconds", 0) > 0:
        print(f"\n=======================================================")
        print(f"[OK] Render Complete!")
        if result.get("audio_path"):
            print(f"[OK] Output file:     {result['audio_path']}")
        else:
            print(f"[OK] Output:          Generated in memory (WAV export skipped)")
        print(f"[OK] Audio duration:  {result['duration_seconds']:.2f} seconds")
        print(f"[OK] Generation time: {result['elapsed_seconds']:.2f} seconds")
        print(f"=======================================================\n")
    else:
        words = result.get("total_words", 0)
        pauses = result.get("estimated_pause_sec", 0)
        est_speech = words / 2.5
        print(f"\n=======================================================")
        print(f"[OK] Dry-run Validation Succeeded!")
        print(f"[OK] Total Words:        {words}")
        print(f"[OK] Estimated Spoken:   ~{est_speech:.1f} seconds")
        print(f"[OK] Inter-turn Pauses:  {pauses:.2f} seconds")
        print(f"[OK] Est. Total Runtime: ~{est_speech + pauses:.1f} seconds")
        print(f"-------------------------------------------------------")
        print(f"[*] To generate actual WAV audio files, install dependencies:")
        print(f"    pip install -r requirements.txt")
        print(f"[*] Then run without --dry-run:")
        print(f"    python voice_cast.py --script sample_script.txt --export-wav")
        print(f"=======================================================\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="VoiceCastTTS: Multi-speaker local dialogue TTS")
    parser.add_argument(
        "--script",
        default=os.path.join(SCRIPT_DIR, "sample_script.txt"),
        help="Path to the script text file"
    )
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG_PATH,
        help="Path to the voice_config.json"
    )
    parser.add_argument(
        "--output",
        default="full_dialogue.wav",
        help="Output WAV filename"
    )
    parser.add_argument(
        "--export-wav",
        action="store_true",
        default=False,
        help="Export full dialogue WAV file to disk"
    )
    parser.add_argument(
        "--device",
        choices=["auto", "cuda", "cpu"],
        default="auto",
        help="Compute device (auto, cuda, cpu)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and validate script and config without running TTS generation"
    )

    args = parser.parse_args()
    render_dialogue(
        script_path=args.script,
        config_path=args.config,
        output_filename=args.output,
        device=args.device,
        dry_run=args.dry_run,
        export_wav=args.export_wav
    )

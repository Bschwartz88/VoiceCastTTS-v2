"""
Voice Blender for VoiceCastTTS
Creates custom blended voices by mathematically combining multiple Kokoro voice styles.
Custom voices are saved to custom_voices/<name>.npy and automatically loaded in VoiceCast Studio.

Usage:
    python blend_voices.py --list
    python blend_voices.py --name child_girl --blend "af_sky:0.7,af_bella:0.3"
    python blend_voices.py --name child_boy --blend "am_puck:0.65,am_liam:0.35"
"""

import math
import os
import sys
import re
import argparse
import urllib.parse
import urllib.request
import numpy as np

import voice_blends

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CUSTOM_VOICES_DIR = os.path.join(SCRIPT_DIR, "custom_voices")
VOICES_BIN_PATH = os.path.join(SCRIPT_DIR, "voices-v1.0.bin")

VOICE_SHAPE = (510, 1, 256)
MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024  # a Kokoro voice file is about 0.5 MB


def safe_voice_name(raw_name: str, fallback: str) -> str:
    """Keeps only letters, digits, _ and -, so a name can't point outside custom_voices/."""
    return re.sub(r'[^a-zA-Z0-9_\-]', '', raw_name or "") or fallback


def check_voice_array(data, source: str):
    if getattr(data, "shape", None) != VOICE_SHAPE:
        raise ValueError(
            f"Invalid voice embedding format in {source}: expected shape {VOICE_SHAPE}, "
            f"got {getattr(data, 'shape', type(data).__name__)}."
        )


def load_base_voices():
    """Loads all base voices from voices-v1.0.bin."""
    if not os.path.exists(VOICES_BIN_PATH):
        raise FileNotFoundError(f"Missing {VOICES_BIN_PATH}. Please run voice_cast.py once to download weights.")
    return np.load(VOICES_BIN_PATH, allow_pickle=False)


def list_voices():
    """Prints all available base voices and existing custom voices."""
    base = load_base_voices()
    print("\n=======================================================")
    print(" Available Kokoro Base Voices for Blending:")
    print("=======================================================")
    files = sorted(base.files)
    
    print("\n[Children & Youth Recommendations]")
    print("  af_sky   - American Female (Light, youthful, clear girl)")
    print("  af_kore  - American Female (Soft, gentle, shy child)")
    print("  af_nova  - American Female (Bright, upbeat, energetic)")
    print("  am_puck  - American Male   (Playful, animated, mischievous boy)")
    print("  am_liam  - American Male   (Youthful, bright, quick teenager)")
    
    print("\n[Storybook Narrators]")
    print("  bm_fable - British Male    (Theatrical fairy tale & fable storyteller)")
    print("  bm_george- British Male    (Rich classic bedtime audiobook narrator)")
    print("  af_heart - American Female (Warm, motherly flagship narrator)")
    print("  bf_emma  - British Female  (Refined, classic children's book narrator)")
    print("  bf_lily  - British Female  (Gentle, lyrical classic British tone)")

    print("\n[All Available Base IDs]")
    col_width = 16
    for i in range(0, len(files), 4):
        print("  " + "".join(f.ljust(col_width) for f in files[i:i+4]))

    os.makedirs(CUSTOM_VOICES_DIR, exist_ok=True)
    custom = [f for f in os.listdir(CUSTOM_VOICES_DIR) if f.endswith(".npy")]
    print("\n=======================================================")
    print(f" Existing Custom Blended Voices ({len(custom)} found):")
    print("=======================================================")
    for c in custom:
        print(f"  * {c[:-4]} (custom_voices/{c})")
    print()


def blend(voice_weights: dict, output_name: str, preview: bool = True):
    """
    Blends multiple voices according to weights and saves as .npy.
    Example voice_weights: {'af_sky': 0.7, 'af_bella': 0.3}
    """
    base = load_base_voices()

    # Checked here so the messages talk about weights, not the studio's sliders.
    for voice_id, weight in voice_weights.items():
        if voice_id.strip() not in base:
            raise ValueError(f"The voice {voice_id.strip()} isn't installed. Run with --list to see available voices.")
        if not (math.isfinite(weight) and weight >= 0):
            raise ValueError(f"The weight of {voice_id.strip()} must be a number of 0 or more, e.g. af_sky:0.7.")
    if sum(voice_weights.values()) <= 0:
        raise ValueError("At least one weight must be above 0.")
    recipe = [{"voice": voice_id.strip(), "weight": weight} for voice_id, weight in voice_weights.items()]
    try:
        blended_vector = voice_blends.mix(recipe, base, max_voices=None)
    except voice_blends.BlendError as e:
        raise ValueError(str(e)) from e
    norm_weights = dict(voice_blends.normalize_recipe(recipe, max_voices=None))
    output_name = safe_voice_name(output_name, "blended_voice")
    os.makedirs(CUSTOM_VOICES_DIR, exist_ok=True)
    out_path = os.path.join(CUSTOM_VOICES_DIR, f"{output_name}.npy")
    np.save(out_path, blended_vector)
    voice_blends.forget_recipe(CUSTOM_VOICES_DIR, output_name)  # the studio's recipe no longer matches this file

    print(f"\n[OK] Successfully created custom voice '{output_name}'!")
    print(f"     Recipe: {', '.join(f'{k}: {w*100:.0f}%' for k, w in norm_weights.items())}")
    print(f"     Saved to: {out_path}")
    print(f"     (This voice is now immediately available in VoiceCast Studio!)\n")

    if preview:
        try:
            import voice_cast
            preview_url = voice_cast.generate_voice_preview(output_name)
            print(f"[OK] Preview generated: {preview_url}")
        except Exception as e:
            print(f"[!] Could not generate preview: {e}")


def import_file(file_path: str, custom_name: str = None, preview: bool = True):
    """Imports an external .npy voice file into custom_voices/."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")
    
    data = np.load(file_path, allow_pickle=False)
    check_voice_array(data, file_path)

    raw_name = custom_name or os.path.splitext(os.path.basename(file_path))[0]
    out_name = safe_voice_name(raw_name, "imported_voice")
    os.makedirs(CUSTOM_VOICES_DIR, exist_ok=True)
    target = os.path.join(CUSTOM_VOICES_DIR, f"{out_name}.npy")
    np.save(target, data.astype(np.float32))
    voice_blends.forget_recipe(CUSTOM_VOICES_DIR, out_name)
    print(f"\n[OK] Imported '{file_path}' as '{out_name}'!")
    print(f"     Saved to: {target}")
    print(f"     (This voice is now immediately available in VoiceCast Studio!)\n")

    if preview:
        try:
            import voice_cast
            preview_url = voice_cast.generate_voice_preview(out_name)
            print(f"[OK] Preview generated: {preview_url}")
        except Exception as e:
            print(f"[!] Could not generate preview: {e}")


def import_url(url: str, voice_name: str, preview: bool = True):
    """Downloads a .npy voice style file from Hugging Face or the web into custom_voices/."""
    parsed_url = urllib.parse.urlparse(url)
    if parsed_url.scheme != "https":
        raise ValueError("Invalid URL scheme: only https:// URLs are supported.")

    clean_name = safe_voice_name(voice_name, "downloaded_voice")
    os.makedirs(CUSTOM_VOICES_DIR, exist_ok=True)
    temp_path = os.path.join(CUSTOM_VOICES_DIR, f"{clean_name}.tmp")
    target = os.path.join(CUSTOM_VOICES_DIR, f"{clean_name}.npy")
    print(f"[+] Downloading voice from {url}...")
    with urllib.request.urlopen(url, timeout=60) as response:
        payload = response.read(MAX_DOWNLOAD_BYTES + 1)
    if len(payload) > MAX_DOWNLOAD_BYTES:
        raise ValueError(f"Download is larger than {MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB, so it is not a voice file.")
    with open(temp_path, "wb") as f:
        f.write(payload)
    try:
        data = np.load(temp_path, allow_pickle=False)
        check_voice_array(data, url)
        np.save(target, data.astype(np.float32))
        voice_blends.forget_recipe(CUSTOM_VOICES_DIR, clean_name)
        os.remove(temp_path)
        print(f"[OK] Successfully downloaded and registered voice '{clean_name}'!")
        print(f"     Saved to: {target}")
        print(f"     (This voice is now immediately available in VoiceCast Studio!)\n")
        if preview:
            import voice_cast
            preview_url = voice_cast.generate_voice_preview(clean_name)
            print(f"[OK] Preview generated: {preview_url}")
    except Exception as e:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise ValueError(f"Downloaded file was not a valid .npy voice style: {e}")


def main():
    parser = argparse.ArgumentParser(description="VoiceCastTTS Voice Blender & Importer")
    parser.add_argument("--list", action="store_true", help="List all available voices and recommendations")
    parser.add_argument("--name", type=str, help="Name for the new voice (e.g. child_girl)")
    parser.add_argument("--blend", type=str, help="Comma-separated voice:weight pairs, e.g. 'af_sky:0.7,af_bella:0.3'")
    parser.add_argument("--import-file", type=str, help="Path to local .npy voice file to import")
    parser.add_argument("--import-url", type=str, help="Direct URL to .npy voice file on Hugging Face to download")
    parser.add_argument("--no-preview", action="store_true", help="Skip generating audio sample preview")

    args = parser.parse_args()

    if args.import_file:
        import_file(args.import_file, custom_name=args.name, preview=not args.no_preview)
        return

    if args.import_url:
        if not args.name:
            print("[-] Please provide --name for the downloaded voice, e.g.: --name my_voice")
            return
        import_url(args.import_url, voice_name=args.name, preview=not args.no_preview)
        return

    if args.list or not (args.name and args.blend):
        list_voices()
        if not args.list:
            print("[Tip] To create a blended voice, run:")
            print("  python blend_voices.py --name child_girl --blend \"af_sky:0.7,af_bella:0.3\"")
            print("  python blend_voices.py --name child_boy --blend \"am_puck:0.65,am_liam:0.35\"")
            print("\n[Tip] To import a downloaded .npy file from Hugging Face, run:")
            print("  python blend_voices.py --import-file \"C:\\path\\to\\voice.npy\" --name my_voice\n")
        return

    # Parse blend string: "af_sky:0.7,af_bella:0.3"
    voice_weights = {}
    for part in args.blend.split(","):
        part = part.strip()
        if ":" in part:
            v_id, w_str = part.split(":", 1)
            try:
                voice_weights[v_id.strip()] = float(w_str.strip())
            except ValueError:
                fail(f"The weight in '{part}' isn't a number. Write voice:weight, e.g. af_sky:0.7.")
        else:
            voice_weights[part.strip()] = 1.0

    try:
        blend(voice_weights, args.name.strip(), preview=not args.no_preview)
    except ValueError as e:
        fail(str(e))


def fail(message: str):
    print(f"[-] {message}")
    sys.exit(1)


if __name__ == "__main__":
    main()

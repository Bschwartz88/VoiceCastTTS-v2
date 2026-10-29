# VoiceCastTTS v2 usage

## Setup and launch

Use Windows x64, Chrome and a preinstalled 64-bit Python 3.10+ bootstrap. Extract the source ZIP and run `Install_Dependencies.bat`. Setup checks the pinned CPython NuGet archive digest and Python Software Foundation signature, creates private `.runtime`/`.venv` folders, installs required-hash wheels and verifies the model SHA-256 values. It initializes `voice_config.json` from the supplied example only if absent. System Python/packages/PATH are unchanged.

Start `Launch_VoiceCastTTS_v2.bat` or the shortcut from `Create_Desktop_Shortcut.bat`. A minimized terminal keeps the server running; Chrome opens the bound local port (normally 7871) with a fresh session credential. Typing the plain URL alone may not authorize it. Closing Chrome alone does not stop the server; close the terminal when finished.

Read setup refusal messages before retrying. Do not disable checksum/signature/session checks, broaden permissions or run as administrator as a workaround.

## Stories, voices and delivery

Paste a story and use formatting/Detect Speakers where appropriate. Bracketed speaker names such as `[Narrator]` and `[Eric]` are supported. Parenthetical directions are separated from speech where supported; review unusual AI formatting before a long render. Narration and character speech should have distinct roles. The cast is bounded to 199 characters plus narrator.

Character cards control names, voice type, voice and speed. Saved/custom assignments have priority; automatic casting is a suggestion. Favorites, nicknames and folders are remembered. Use Sample at the selected speed before rendering. Blending combines installed voice styles; it is not a general voice-cloning feature.

Natural and Even volume are the default starting point. Native questions/replies are preserved with modest pacing. Explicit quiet/whisper/shout cues intentionally change delivery/volume. These controls do not guarantee acted emotion. Use pronunciation substitutions for difficult names.

## Generate, playback and exports

Generate renders locally with Kokoro. Stop cancels work; one full render runs at a time. Use the main player or transcript/timeline to select lines, then export WAV audio or available speaker tracks. The default ONNX engine is CPU-capable.

Listen while preparing is optional: short WAV pieces play while the final recording continues. A disk failure or 256-MiB spool ceiling stops early listening while final rendering can continue. Preview cache is bounded by age/count/size. Marked inactive spools older than seven days can be reclaimed; OS leases protect active owners. Unknown/unmarked directories and user exports are not automatically erased. Clear removes visible output, not every local cache, profile record or export.

If a first word is clipped, record the line, voice and mode (continuous playback, transcript selection, preview or downloaded WAV). A short reproducible non-sensitive example is preferable to changing many settings at once.

## Projects and personal data

Save/open projects to preserve the script and supported casting/settings. Imports validate limits before applying state. Personal settings are in `user_settings.json`; editable base configuration is in `voice_config.json`; custom voice assets are in `custom_voices/`; recordings are in `output/`. Retained personal data, if any, is in `.local_data/`. All are local-only and excluded from source publication.

Chrome uses `.superpowers/chrome-app-profile`. Per-launch session credentials are exchanged for an HttpOnly cookie; the local MCP handoff is `.superpowers/studio-session.json`. Do not share that file, your browser profile or personal project/voice data. Restarted servers issue new sessions.

## Optional local AI and imports

The Script Assistant connects to supported local LM Studio/Ollama/OpenAI-style endpoints. Enable it intentionally, choose the running local program/model and give it your own writing request. Story presets are not provided. The separate AI program determines model context, quality and performance.

For Send to VoiceCast, configure the AI program's MCP entry to this folder's `voicecast_mcp.py` and enable Receive scripts in the app. The helper discovers the current port/session locally. Do not publish personal AI configuration, prompts or saved conversations. Saved-chat imports use local files when invoked. Google Docs import is separately opt-in and connects to that external service.

## Offline/repeated setup

Advanced users with verified public-asset caches can run:

```text
python -E -s setup_v2.py --runtime-archive PATH_TO_PYTHON_NUPKG --wheelhouse PATH_TO_WHEELS --model-cache PATH_TO_MODEL_FOLDER
```

The runtime digest/signature, wheel hashes and model digests are still enforced. Only declared public runtime/model assets are copied; no personal settings, stories, voices, profiles or existing environment are imported. Use a clean source copy and setup on another PC rather than copying `.venv`.

The lock targets Windows x64 CPython 3.14. This beta does not certify other platforms or independent minimum RAM. LICENSING.md and RELEASE_READINESS.md explain source/dependency terms and the additional requirements before shipping a binary bundle.

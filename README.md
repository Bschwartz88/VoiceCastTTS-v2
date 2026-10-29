# VoiceCastTTS v2

Local, multi-speaker text-to-speech for Windows. Paste a story, assign character/narrator voices, and listen or export WAV audio. The default ONNX engine can run on CPU; no dedicated GPU is required.

**Source-only beta:** this repository contains code, tests and license notices. It does not contain Python runtimes, installed packages, model weights, personal voices, settings or recordings. Setup downloads verified public dependencies into this folder; there is no compiled installer or hosted speech service.

## Fresh setup

Prerequisites: **Windows x64**, Chrome and an existing **64-bit Python 3.10+** from [python.org](https://www.python.org/downloads/windows/). That Python bootstraps a pinned project-private runtime. Internet is needed for first setup. Independent minimum-RAM requirements are not established.

1. Choose **Code → Download ZIP** and extract to a short writable path, for example `C:\VoiceCastTTS-v2`.
2. Run **Install_Dependencies.bat**. It provisions private CPython 3.14.10, verifies its fixed checksum and PSF executable signature, creates `.venv`, installs hash-locked wheels and verifies the two model files. It creates local configuration from the example only when no configuration exists.
3. Run **Launch_VoiceCastTTS_v2.bat**. The minimized terminal keeps the server running; Chrome opens an authenticated local session. Use the launcher after a server restart.
4. Optionally run **Create_Desktop_Shortcut.bat** to create the v2 desktop icon.

The private base runtime is under `.runtime/`, packages under `.venv/`, and the dedicated Chrome profile under `.superpowers/`. Global Python/packages/PATH are not changed. Keep the server terminal open while using the app; close it when finished. If setup refuses a prerequisite or checksum, fix the stated issue rather than disabling checks or running as administrator.

See [USAGE_GUIDE.md](USAGE_GUIDE.md) for features, optional AI integration and offline setup.

## Features

- Separate narrator/character casting, individual voice/speed controls, favorites, nicknames, folders and voice blending.
- Story formatting/speaker detection that separate supported labels and parenthetical directions from speech.
- Natural delivery, optional even volume and explicit delivery cues. These controls do not guarantee actor-like emotional performance.
- Transcript/timeline playback, WAV exports, speaker tracks and optional **Listen while preparing**.
- Project save/open, pronunciation corrections, and optional local LM Studio/Ollama/OpenAI-style Script Assistant and MCP handoff.

Speech synthesis runs locally. Optional AI integration requires a separate local AI program. Google Docs import is explicitly opt-in and uses an external service; setup/model downloads also use the network. No cloud speech account is required.

## Privacy and security

Keep stories, settings, custom voices, recordings, profiles, session files and backups local. `voice_config.json` is local; only `voice_config.example.json` is shipped. Do not attach private material to public issues. Publication checks cover private-file categories, credentials, personal paths and commit identity, but cannot recognize every possible sensitive detail.

The default server binds to loopback, requires per-launch session access and rejects cross-origin sensitive requests. A private runtime/virtual environment is not an operating-system sandbox. See [the security review](SECURITY_RETEST_REPORT.md) and [release readiness](RELEASE_READINESS.md).

## Licensing and status

VoiceCast's own source retains [Apache-2.0](LICENSE). The assembled speech stack also uses GPL/LGPL and other components; that source license does not replace their terms. See [LICENSING.md](LICENSING.md), [NOTICE](NOTICE), [the dependency inventory](DEPENDENCY_INVENTORY.md) and [third-party notices](THIRD_PARTY_NOTICES/README.md). No runtime/model/voice binaries are shipped in this source release.

Listening and normal use were accepted on the maintainer's Windows installation. Other PCs, minimum resources and every optional AI program are not certified. See [STATUS.md](STATUS.md). Support is best effort; report reproducible defects using non-sensitive examples.

## Development

After normal setup, development packages can be installed with the full verified lock:

```text
.venv\Scripts\python.exe -E -s -m pip install --require-hashes --only-binary=:all: -r requirements-v2-lock.txt
.venv\Scripts\python.exe -E -s -X utf8 -m unittest discover -s tests
node --test tests/javascript/*.cjs
```

Chrome tests require explicit executable/profile-root configuration and disposable local profiles; they do not use a personal profile. Icon rebuilding is optional in `requirements-icon.txt`.

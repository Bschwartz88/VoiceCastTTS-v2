# VoiceCastTTS v2 status — 2026-10-26

## Current revision

- Story presets removed from the UI and request handling. Write new requires an explicit request; legacy preset settings are ignored/dropped. Unrelated AI-program model settings remain supported.
- Casting uses whole-token name hints, available unused voices and recognizable honorifics to correct Neutral AI suggestions. Saved assignments, nicknames and owner role voices retain priority. Fresh/demo male suggestions use the auditioned Eric reference.
- Natural preserves native questions/plain replies with no random speed/gain changes, plus modest terminal punctuation pacing and the existing dialogue gaps. Even-volume mode prevents automatic gain from undoing normalization; explicit whisper/shout cues remain intentional. Partial-frame speech is included in level measurement. These controls do not create true emotional acting.
- App and desktop identity are VoiceCastTTS v2. Canonical launchers require this checkout's verified isolated .venv with environment overrides/user-site packages disabled. Python now uses the project-private 3.14.10 runtime; Windows runtime remains shared. Only the v2 launcher is supported; the physical project folder is VoiceCastTTS-v2, with its environment rebuilt and desktop paths updated.
- Existing formatting improvements, on-demand speaker exports, compact waveforms and optional early listening remain. Other roadmap additions are deferred.

## Verification

578 Python tests and 24 JavaScript checks passed. Ten Chrome suites ran 176 tests: 174 passed, two optional real-AI-service checks skipped. Includes real local Kokoro generation/early listening, preset removal, actual v2 casting/identity and saved project behavior. The updated entry point refuses base Python before application imports. pip check passes; installed distributions/core modules were verified inside .venv, user site disabled and system site-packages excluded. requirements-v2-lock.txt records the tested installed runtime/development environment.

An eight-line Eric before/v2 trial reduced ordinary-line active-speech level range from 4.000 dB to about 0.00002 dB with even volume on. Same local model/base voice and one shared estimator; this is not LUFS, a multi-voice guarantee or a subjective quality score. Local before/after auditions are not published.

## Earlier performance work

A synthetic ten-minute/eight-speaker assembly trial reduced peak traced allocation from 494.47 to 194.57 MiB; excludes inference and is not whole-process memory. Warm early-listening diagnostic: first piece 0.901 s; complete normal/live renders 5.724/5.861 s, two runs per mode with model load/acoustic browser startup excluded. Streamed PCM reconstructed the final WAV exactly.

## Assets and next phase

This source repository excludes environments, models, personal voices/settings, audio, browser profiles and local checkpoints/handoffs. They remain local; GitHub is not a complete asset backup.  Pocket is not integrated; cloning/MP3/new imports and other additions are future work.

The initial security assessment was performed on 2026-10-26 against source 4119c50. Application/runtime remediation and retesting are recorded in SECURITY_RETEST_REPORT.md; the initial evidence remains in SECURITY_REVIEW_REPORT.md. Binary-distribution notice completeness and an owner release decision remain separate.

## Owner acceptance update — 2026-10-29

The owner accepted the listening sample on October 26 and confirmed normal use on October 29. The current private installation is accepted; no further voice tuning is requested. A few friends/testers are the intended next audience. Tester delivery/licensing is still a planning decision; see TESTER_DISTRIBUTION_OPTIONS.md. Public/packaged distribution remains subject to RELEASE_READINESS.md.

## Source-beta preparation — 2026-10-29

Public source-only beta selected. Current checks:603 Python,24 JavaScript and174-of176 Chrome passed(two optional AI checks skipped), plus final focused checks and real speech from a clean online-setup copy. Editable voice_config.json is local; source provides an example. No installer/runtime/model/voice binaries are shipped. See PUBLIC_RELEASE_REVIEW.md for exact scope and limitations.

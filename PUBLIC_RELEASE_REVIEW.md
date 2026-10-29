# VoiceCastTTS v2 source-beta release review

Date: 2026-10-29. Scope: reviewed v2 source, tests, example configuration, documentation and license notices. The maintainer accepted listening and normal use. The approved publication is source-only; it contains no compiled installer, Python runtime, dependency wheels, model weights, custom voice data or recordings.

## Setup and functional verification

- Exported a source snapshot into a fresh folder with no personal settings/configuration, virtual environment or models.
- Ran the real online setup: pinned CPython NuGet download/checksum and PSF signature; isolated environment; hash-locked PyPI wheel downloads; pip check; both model downloads/SHA-256 checks; stock local configuration creation. Repeated setup also passed without overwriting configuration.
- Actual Chrome bootstrap removed the launch fragment, used an HttpOnly cookie, loaded the fresh-copy app and generated/fetched a real two-line speech WAV successfully. Browser profiles were disposable and local.
- **603 Python tests passed**, including ten bootstrap integrity/refusal checks. **24 JavaScript checks passed.** **176 Chrome tests ran: 174 passed, two optional external-AI checks skipped.** After final identity/default-port cleanup, MCP20 and launcher3 checks passed; the renamed audio UI suite3 passed; the fresh-copy real-speech smoke passed again.
- The source setup requires an existing 64-bit Python3.10+ bootstrap and Chrome on Windows x64. It disables launcher automatic global installation for that process. It does not install a global Python, change PATH, alter execution policies or modify the certificate store.

These checks used a clean folder on the tested Windows PC, not a separately provisioned computer or fresh Windows VM. Other hardware/platforms and minimum RAM are not certified. The accepted speech engine and delivery behavior were retained; the work adds reproducible setup, privacy isolation and v2-only identity/maintenance cleanup.

## Publication privacy review

The release workflow applies generic private-file/credential/path/commit-identity checks plus an independent local-only private-terms pass. Private terms are never embedded in published tooling. The final exact candidate and its full visible history must pass before visibility changes. Code/history, image metadata and the GitHub metadata/content surfaces were separately reviewed.

Only the reviewed example configuration is shipped; editable configuration is local. Settings, stories, custom voices, output, retained personal data, browser/session files, runtime/environment caches, raw evidence and checkpoints are excluded. The application icon assets were visually/metadata reviewed; only intended branding images are included. No private terms or credential patterns were found in the reviewed prior publishing content. Its historical configuration was verified to be the same stock data now supplied as the example.

GitHub audit found one branch, no tags and no existing issues, PRs, comments, releases, Actions runs/log artifacts or deployments; wiki/discussions were disabled. Final source history is to contain only the current v2 snapshot, with a public GitHub no-reply commit identity. Repository visibility changes do not start the local app or publish local folders.

These are scoped checks, not proof against every possible sensitive detail, provider-retained object, backup or prior clone. Treat a public repository as permanently copyable regardless of expected audience. Never add local project folders, environments, personal configuration or data to a future commit/release.

## Licensing and support scope

LICENSING.md preserves Apache-2.0 for the project's own source and documents the GPL/LGPL/other terms relevant to the combined speech stack. Required attribution/license texts remain; versioned dependency source references are in DEPENDENCY_SOURCES.json. This is not blanket compliance clearance for a future binary distribution. Exact native binary/source/build/relinking obligations remain a separate requirement before an installer/runtime bundle is shipped.

No independent penetration test, native fuzzing, destructive load test or whole-OS audit is claimed. Optional AI programs run separately. Report bugs with non-sensitive examples and use private vulnerability reporting when available. Support is best effort for a beta.

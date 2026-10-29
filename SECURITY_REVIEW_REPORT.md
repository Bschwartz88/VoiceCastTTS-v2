# VoiceCastTTS v2 — security assessment

**Historical baseline assessment.** Subsequent application/runtime fixes and verification are recorded in [SECURITY_RETEST_REPORT.md](SECURITY_RETEST_REPORT.md). Findings below describe the pre-remediation source.

**Assessment date:** 2026-10-26. **Result:** findings identified; remediation and release clearance pending. This is an application/source and controlled local-testing assessment, not a guarantee that the software is free of vulnerabilities.

**Reviewed source:** `4119c503a63a76ba125292e50d0bf38d76b563a1`, with naming/documentation corrections made during this assessment. Private published functional snapshot at assessment start: `ccb60b7c1dd12ed4a2b2fd1059c417ed9e7bc1e3`. At this historical baseline no runtime security fixes had yet been applied; recommended changes below require a remediation round and retest.

## Scope and method

Reviewed the local HTTP server and routes, browser rendering/project loading, AI-program connections, imports, models/custom voices, CLI conversion/import helpers, launchers, environment, cache/output lifecycle and private publication workflow. Tests used synthetic files/settings/model responses, two temporary loopback servers, and a disposable project-only Chrome profile. No personal browser profile, real saved AI conversations, credentials, external attack target was accessed. Public advisory queries sent only package names and versions; source and personal data were not uploaded to a scanning service.

The active environment contains 39 distributions including pip; the 38 locked packages, pip, 38 native library files and two pinned model assets were inventoried. Both model SHA-256 values match the application's official-release pins. Installed modules/distributions remain inside the rebuilt `.venv`; base Python and Windows are shared. The private repository is still private/not a fork, with no environments, models, personal voices/settings, generated audio or local handoffs in its published source tree.

Evidence is retained locally under `.superpowers/security-review/`. Raw evidence may contain local paths and is excluded from publication. Public-source references are linked with the relevant findings.

## Trust boundaries and route inventory

The protected assets are story/chat text, saved settings and custom voices, generated audio, the owner's file access and CPU/disk availability. The browser sends text/settings to the loopback Python service; that service runs local synthesis and writes/cache-serves audio. Optional Script Assistant requests cross to the configured local AI service. Opt-in Google Docs import and explicit setup/model downloads cross to external HTTPS services. MCP is a separate local client of the HTTP service and must remain usable when access control is introduced.

| Method | Routes / function | Boundary or side effect |
|---|---|---|
| GET/HEAD | `/`, `/index.html`, `/static/*`, logo/favicon | Browser bootstrap and static-file reads |
| GET/HEAD | `/api/config`, `/api/lmstudio/chats` | Configuration and local saved-chat metadata |
| GET/HEAD | `/api/llm/status`, `/api/llm/model_settings` | Queries to the configured local AI service |
| GET/HEAD | `/api/preview_voice` | Synthesis/cache/file side effect despite read method |
| GET/HEAD | `/api/audio/latest.wav`, `/api/audio/track`, `/api/audio/tracks.zip`, `/output/*` | Generated media and filesystem reads |
| POST | `/api/generate`, `/api/cancel`, `/api/export_latest` | Full synthesis/cancellation/export |
| POST | `/api/llm/write_script`, `/api/llm/cancel` | Local AI generation/cancellation |
| POST | `/api/blend/preview`, `/api/blend/save`, `/api/blend/delete`, `/api/blend/describe`, `/api/blend/describe/cancel` | Voice synthesis, persistence and AI descriptions |
| POST | `/api/import/text`, `/api/import/google_doc`, `/api/import/lmstudio_chat` | Text/optional network/local-chat imports |
| POST | `/api/inbox`, `/api/inbox/take`, `/api/user_settings` | Local handoff and settings persistence |

This is the source route inventory, not a claim that every route received every attack case. Synthetic evidence covers the cases described in the findings. Other same-machine callers are a meaningful boundary; a caller already able to replace the owner's application code is outside the protection offered by an HTTP session secret.

## Priority findings

| ID | Priority in this application | Finding | Status |
|---|---|---|---|
| S1 | High for shared/untrusted-local-process environments | Local API has no session authentication; Origin is not native-client authentication | Reproduced |
| S2 | Medium | Another local browser origin can trigger preview generation through GET | Reproduced in Chrome |
| S3 | Medium | Request, AI-response and project-import resource limits are incomplete | Reproduced in bounded tests; full exhaustion deliberately not attempted |
| S4 | Medium for unintended diagnostics; low for junction prerequisite | File serving exposes non-audio output and uses lexical rather than resolved containment | Reproduced with synthetic files |
| S5 | Medium development/setup risk | Installed Pillow/pip versions have published advisories | Version/range matches verified; application reachability assessed separately |
| S6 | Medium maintenance/release concern | Base Python is 3.14.0, behind current security maintenance | Version and official release verified |
| S7 | Low / release hardening | Retention, install reproducibility and transitive/native notices need a clearer policy | Source/inventory observations |

Priorities describe this application and the demonstrated prerequisites. Upstream CVSS labels are not automatically the severity of VoiceCastTTS. In particular, normal network users cannot directly reach a loopback listener, and development-only packages are not equivalent to remotely reachable speech-service vulnerabilities.

### S1 — local API access is not authenticated

Locations: `web_server.py:609`, `web_server.py:616`, `web_server.py:808`, `web_server.py:908`; MCP integration in `voicecast_mcp.py`.

Host checks restrict the address name and POST Origin checks protect ordinary browser cross-site writes. They do not identify the native client. A local HTTP client can supply the accepted Origin header without a session secret. With no HTTP credentials, the controlled client read `/api/config` and successfully changed a **synthetic** settings file using POST `/api/user_settings` and a forged correct local Origin. GET saved-chat metadata also has no session access check; no real chat files were read.

This matters especially on shared machines or where an untrusted local program/service can reach loopback: the service operates with its owner's file access and can disclose data or perform actions for another caller. ACL inspection found no broad Everyone/Authenticated Users/Users grants on the sampled project/settings/interpreter files, so API access is not the same boundary as direct file access. Browser cross-origin response reading remained blocked by CORS in the test.

**Recommended fix:** define an explicit single-user trust model and add an unpredictable per-launch session capability delivered only through the trusted launcher/owner-readable local handoff, protecting API and media access. Do not publish that capability in unauthenticated bootstrap endpoints or logs. Keep Host/Origin checks as additional controls. Update MCP intentionally and test missing/invalid/cross-session credentials, another local client and normal audio playback. A local process already able to modify the application as its owner is outside what an HTTP token can protect.

### S2 — GET preview generation crosses the browser origin boundary

Locations: `web_server.py:643`, `web_server.py:831`.

A synthetic page on a second loopback port called the preview endpoint using a simple `no-cors` GET. The patched preview generator was invoked, proving the side effect occurred; the browser could **not** read the cross-origin config response. Direct requests with a foreign Origin and `Sec-Fetch-Site: cross-site` were also accepted for preview generation.

A caller does not need to read the response to cause model work, queue requests or create preview files. Browser local-network permission policy can limit some public-website scenarios; the demonstrated attacker was another local origin. This is not evidence that every public website can read the application's responses.

**Recommended fix:** move generation to a protected POST or require the authenticated session capability. Apply origin/fetch-metadata policy consistently to sensitive GET routes and downloads as appropriate. Retest local cross-origin requests and ordinary browser playback; CORS alone does not prevent GET side effects.

### S3 — availability limits need completion

Locations: `web_server.py:908-923`, `web_server.py:1222`; `ai_programs.py:81-105`; `web/static/studio.js:3369`, `web/static/studio.js:3422-3453`.

Three controlled observations:

- One client sent a foreign Host/Origin, declared a body and withheld its bytes. The server waited on the body instead of rejecting promptly. Source reads the body before trust checks, has no configured connection read timeout and uses thread-per-request serving without an application concurrency cap. The client was closed after a short observation; no load/exhaustion test was run.
- A fake local AI server returned 2 MiB of metadata and `ai_programs.request` accepted it. Non-stream reads and individual streamed lines have no explicit response-byte cap. Configured timeouts are primarily socket-idle bounds, not a full response-size policy.
- A project containing 5,000 cast entries was accepted by `applyProjectState` with DOM rendering stubbed for safety. The 10-MiB file limit does not cap the number/size of DOM cards before rendering; server generation later limits a cast to 200. This demonstrates missing validation, not a measured browser crash.

**Recommended fix:** validate headers before body reads, set finite read/write timeouts and bounded concurrent clients; cap AI response bytes/line lengths and use appropriate total deadlines; validate project script/cast/name limits before applying state or constructing the DOM. Preserve cancellation and readable refusal messages. Keep generation serialization and existing request/cache limits.

### S4 — tighten file-serving policy and containment

Locations: `web_server.py:732`, `web_server.py:889-905`.

The output route returned a synthetic `.txt` diagnostic file as `audio/wav`. The real output directory can also contain troubleshooting text and the port file, so it should not be treated as entirely public audio. A synthetic junction under the static root pointed to a synthetic “private” directory elsewhere **inside the approved project**; `/static/outside/sentinel.txt` returned its bytes because `abspath/commonpath` checked the lexical path rather than the resolved target. Plain `../` traversal was refused.

The junction test requires someone able to plant a link in a served directory; no remote junction-creation route was found. It is a containment hardening issue, not proof of arbitrary internet file theft. No real private file or path outside the approved project was accessed.

**Recommended fix:** allow only intended audio exports on `/output`, keep diagnostics/private metadata in a non-served location, and verify resolved targets/reparse points against fixed served roots. Add tests for junctions, encoded/drive/UNC paths, diagnostics and legitimate preview/live audio.

### S5 — vulnerable development/setup dependencies

`requirements-dev.txt:4` installs Pillow 12.1.1. The official OSV exact-version query returned 77 advisory identifiers, grouped into 59 alias-linked findings; installed pip 25.2 returned 12 identifiers grouped into six. Duplicate identifiers are not separate vulnerabilities. Detailed records are retained locally, with withdrawn/range context available for remediation triage.

Pillow is used by the optional `make_icon.py` tool against the bundled logo, not normal speech serving; no untrusted-image upload path was found. Some advisories require optional image plugins that are not installed. Consequently this is a development-dependency finding, not 59 proven remote attacks on v2. The maintainer's [MIC advisory](https://github.com/python-pillow/Pillow/security/advisories/GHSA-22hg-r758-rrw9) explicitly identifies affected versions through 12.3.0 and a 13.0.0 fix, with additional plugin prerequisites. [Pillow 13.0.0](https://pypi.org/project/pillow/13.0.0/) is published; its exact-version OSV query returned no matches at assessment time.

Pip findings concern malicious distributions/index URLs/archive/entry-point handling during package installation. They are not normal TTS HTTP request paths. For example, the [pip fallback-extraction advisory](https://github.com/pypa/pip/security/advisories/GHSA-4xh5-x5gv-qwph) fixes one affected path in 25.3; other matched records require newer fixes, through 26.2.0. An individual old fix version is not sufficient for all matched pip records.

**Recommended fix:** upgrade/remove the unnecessary image-tool dependency from the normal development environment, update pip to a verified patched version, refresh the dependency lock and rerun checks. Recheck current advisories at implementation time. No package upgrades were made during this assessment. Zero matches for other locked packages is not a guarantee of absence of unknown or bundled-native issues.

### S6 — base Python needs a separately planned patch update

The isolated environment uses Python 3.14.0 from the existing base installation. The official [Python 3.14.10 release](https://www.python.org/downloads/release/python-31410/) dated 2026-10-25 is an expedited security release, including distrust of compromised CA keys. This is relevant to HTTPS downloads/setup; it is not evidence that the current installation has been exploited. Existing model hash checks provide a useful additional integrity control.

**Recommended fix:** plan a patched base-runtime installation/rebuild without inadvertently changing the original VoiceCast runtime. A `.venv` does not patch its base standard library/OpenSSL. This requires an explicit runtime-change/download scope before implementation; no system Python or certificate store was modified here.

### S7 — retention and release hardening

- Preview files and old/crashed live-listening spools can persist without an overall disk quota/age policy. Clearing the UI does not erase all server caches or disk artifacts. Define limits and explicit cleanup behavior that never removes another active process's files.
- The installer uses `requirements.txt`, while `requirements-v2-lock.txt` inventories the tested full environment. Top-level pins do not make transitive fresh installs identical, and no hash-enforced wheel lock is used. Use a reproducible runtime-specific lock and verified artifacts for release packaging.
- Apache-2.0 source LICENSE/NOTICE are present. Installed metadata includes transitive/native components not fully represented by the short NOTICE; for example phonemizer-fork reports GPLv3 metadata and espeakng-loader lacks a simple license-expression field. This is an inventory/compliance follow-up before binary redistribution, not a legal conclusion about current private use.
- Runtime soundfile bundles libsndfile 1.2.2. Its native decoding advisories need tracking (for example the [upstream WAV parser report](https://github.com/libsndfile/libsndfile/issues/1150)). This application currently writes generated arrays rather than accepting external audio for decoding, so reachability differs from a general audio importer. Do not treat a Python-package-only scan as full native-library clearance.
- `convert_pt_to_npy.py` avoids PyTorch/pickle, but reads a ZIP member without a decompressed-size ceiling. This is an optional CLI untrusted-file availability risk; add an explicit size/shape budget before reading.

## Controls that passed or were observed

- Loopback service defaults, foreign Host rejection and foreign/missing POST Origin rejection.
- Plain path traversal refusal, request-body/script/cast limits on the generation API, finite numeric validation/clamps, one active full render and bounded line cache.
- Browser project markup escaped, attacker-supplied character IDs regenerated, no injected image/script/event attributes in the test. CSP, no-sniff, frame denial and no-referrer headers present. Browser CORS blocked cross-origin config reads.
- Local AI URL validation rejects nonlocal schemes/hosts, userinfo and unexpected paths; requests use direct local HTTP connections. Google Docs import is opt-in and checks redirects/allowed hosts and size/time bounds; existing synthetic redirect/refusal regressions pass.
- Custom/model numeric loading uses `allow_pickle=False`; no runtime `eval`, shell=True, pickle.load or torch.load path was found in the reviewed code. This does not certify native parsers against malformed inputs.
- Two installed model hashes match pinned release values. Package imports/distributions are isolated in the rebuilt `.venv`; sampled ACLs show no broad allow entries. Private publication excludes local-only assets and inherited original history.
- Complete post-migration regression: **578 tests passed**. Existing functional JavaScript/browser results remain in the prior checkpoint; this assessment additionally exercised real Chrome XSS/CORS/preview behavior and controlled HTTP/resource cases.

## Recommended remediation order

1. Agree on the local-user trust boundary and implement session access control; close cross-origin generation paths.
2. Add file allowlists/resolved containment and availability/project-response limits with refusal/cancellation regressions.
3. Approve a safe runtime/tooling update plan, refresh verified dependency locks and repeat advisory checks.
4. Add bounded retention and distribution/license evidence, then rerun functional and security regression suites.
5. Issue a retest report and an explicit owner release decision. This assessment is **not a clean security approval**.

## Naming migration and remaining operational detail

The active folder is now `VoiceCastTTS-v2`; desktop targets and the rebuilt environment use it. After a partial Windows move, 11,615 remaining files were reconciled and SHA-256 compared before old contents were removed; old environment/index bytes were retained as local rollback. Git integrity and the post-move regression pass. Windows temporarily held the empty old directory entry open. A later non-recursive cleanup succeeded after the handle was released; the old path is now absent. No unrelated process was killed or permission changed. The application is unchanged.

## Limitations

No destructive stress test, internet exposure test, other-user login test, native-code reverse engineering/fuzzing or independent third-party penetration test was performed. Real private conversations/credentials were excluded; chat/model-config handling was assessed from code and synthetic fixtures. No global certificate-store audit or changes were made. Browser/OS background telemetry and every transitive/native advisory cannot be ruled out by the scoped tests. Findings and reachability need reassessment after each remediation or new feature, especially external audio/voice cloning.

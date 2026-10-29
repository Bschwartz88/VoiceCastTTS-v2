# Current source-beta release target — 2026-10-29

The maintainer selected a public, source-only v2 beta. Listening and normal use are accepted. Source setup was verified from a clean folder with real online downloads and repeat setup; the final verified results are recorded in PUBLIC_RELEASE_REVIEW.md. No installer/runtime/model/voice binary assets are to be uploaded. LICENSING.md clarifies the own-source and combined dependency terms; DEPENDENCY_SOURCES.json provides source references. Binary redistribution gaps below remain relevant to a future binary package, not a claim that one is being released now.

Historical review evidence follows. This current source-only scope supersedes prior planning/approval status; it does not approve a future binary bundle.

# VoiceCastTTS v2 — final acceptance and release readiness

Review date: 2026-10-26; owner acceptance updated 2026-10-29. Reviewed application source: b40772eade7ab260407f491333a0a30a62012914. This stage changes documentation/notices only; runtime behavior remains the security-remediated version.

## Current decision

Private local use: accepted. Owner listening-sample acceptance was received on 2026-10-26 ("sample is good enough") and normal-use confirmation on 2026-10-29 ("normal use confirmed"). Source-only beta publication is approved by the maintainer; packaged/binary redistribution is **not approved**; the specific notice/source-provenance gaps below remain. No installer, models, voices or binary release is being published by this review.

| Area | Evidence | Status |
|---|---|---|
| Application/security regression | 593 Python and 24 JavaScript checks passed; Chrome 174 passed/two optional AI checks skipped in the preceding remediation round | Passed for reviewed scope |
| Real local speech | This stage generated a synthetic three-voice 18.723-second WAV at 24 kHz in 8.258 seconds including model load; engine trace omitted parenthetical stage directions | Owner accepted the listening sample on 2026-10-26 |
| Owner normal-use/listening | Local acceptance clip and checklist supplied; check desktop launch, story import, casting, delivery/volume, first words and project save/reopen | Owner confirmed normal use on 2026-10-29 |
| Runtime/setup | Private patched CPython, isolated packages, verified hash locks and clean offline runtime install from remediation round | Passed |
| Dependency notice evidence | 39 exact wheel versions examined; 108 embedded notice/license files copied verbatim, plus CPython LICENSE; hashes/provenance in THIRD_PARTY_NOTICES | Evidence collected; not full distribution clearance |
| Missing wheel notices | flatbuffers and espeakng-loader had no separate license file in these wheels; upstream reference supplements added and distinguished from wheel evidence | Partly filled; loader exact-release attribution/provenance unresolved |
| Native components | eSpeak reports 1.52.0; soundfile bundles libsndfile and other media libraries; NumPy/ONNX/CPython include native/vendor components | Exact binary/source and final package obligations need closure |
| Publication/privacy | Private v2 clean-snapshot workflow and full proposed-history privacy gate retained; no personal settings, voices, audio, runtime, credentials or local backup in intended publication | Final exact-candidate privacy gate required before public visibility |

## Notice and license findings

1. The installed phonemizer-fork includes a GPLv3 license and metadata identifying GPLv3-or-later. [Upstream phonemizer license](https://github.com/bootphon/phonemizer/blob/master/LICENSE) is corroborating evidence; the fork's installed license is the reviewed artifact. [eSpeak NG](https://github.com/espeak-ng/espeak-ng#license-information) is also GPLv3-or-later. Its loader wrapper is separately advertised as MIT. The wrapper's license does not relicense its bundled native speech library.
2. VoiceCast's Apache-2.0 source license must not be represented as the license for the entire runtime bundle. The [Apache Software Foundation's compatibility guidance](https://www.apache.org/licenses/GPL-compatibility.html) discusses how GPLv3 combinations differ from Apache-only distribution. No source license or copyright ownership has been silently changed. Before distributing a combined application, resolve its distribution terms and corresponding-source obligations for the actual integration/package, with qualified review where needed.
3. The installed soundfile wheel includes LGPL-2.1 text and a media-library license/source note. Its BSD Python wrapper does not replace the native libraries' terms. Preserve those notices and determine the applicable source/relinking requirements for the precise binaries selected for a package. The [libsndfile source license](https://github.com/libsndfile/libsndfile/blob/1.2.2/COPYING) is a primary reference; it is not proof of every bundled codec's build provenance.
4. Collected wheel files are byte-for-byte copies with SHA-256 values and exact wheel hashes. Separate supplements fill the flatbuffers upstream license at its matching version tag and eSpeak COPYING at its reported version tag. The loader supplement comes from upstream main because a matching release-tag license could not be retrieved; it is explicitly a reference, not proof of the 0.2.4 wheel's exact licensing history. A version string or current branch license alone is insufficient binary/source correspondence evidence.
5. [Kokoro-82M's publisher page](https://huggingface.co/hexgrad/Kokoro-82M/tree/main) identifies Apache-2.0 model terms. Existing model hashes/provenance remain recorded. No model weights, personal/custom/imported voice packs or cloned voices are included here. A future redistribution must identify the exact model/voice artifacts and their applicable notices/permissions; do not infer permission for arbitrary imported voices from the base model label.

This is a technical license/notice inventory and release recommendation, not a legal determination that a future distribution satisfies every obligation.

## Smallest path to completion

Owner acceptance for the current private installation is complete. Keep the verified application/runtime unchanged unless a defect is reported. The intended next audience is a few friends/testers; see TESTER_DISTRIBUTION_OPTIONS.md for the recommendation and choices. That audience preference is not permission to invite users, change licensing or ship a package.

If a distributable installer is later requested: select its exact contents and distribution terms; obtain the matching native source/build/dependency provenance, complete any applicable source/relinking/notice obligations, and verify the final package contents against the manifest. Review package/model/voice rights and obtain an explicit release decision before publishing. Merely adding a notice folder, omitting binaries from this source repo, or moving a library into a separate download is not a blanket clearance for every combined distribution.

## Source publication privacy

Only reviewed v2 code, tests and notices are intended for source publication. Personal settings, stories, custom voices, recordings, browser/session files, runtime/environment caches and local checkpoints are excluded. The final publication audit covers code/history and repository metadata; it is not a guarantee against every possible sensitive pattern or provider-retained record.

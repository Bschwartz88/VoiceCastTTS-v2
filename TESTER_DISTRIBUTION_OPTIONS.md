# VoiceCastTTS v2 source beta decision

The maintainer selected public source-only beta preparation on 2026-10-29. No compiled installer or runtime/model/voice binaries are to be shipped. The alternatives below remain engineering reference, not additional app versions or approved distribution changes. See README.md for the current source setup, LICENSING.md for source/dependency terms and RELEASE_READINESS.md for verification.

## Recommendation

Start with two or three Windows x64 testers, the existing Kokoro engine, source access and assisted setup. Freeze the accepted runtime, close the pilot's applicable license/source requirements, and try one clean PC before widening the pilot. Keep personal settings, stories, voices, recordings, browser profiles and environments out of every handoff. Treat access to software source separately from access to personal data.

This route avoids reworking working speech code or maintaining an opaque copied environment. The current installer is not yet a complete friend-ready setup: it expects a verified project-private Python runtime to have been provisioned first. The next engineering work would be a clean tester setup/handoff, with package/model integrity checks and setup verification; do not tell testers to copy the owner's project folder or assume the present batch file handles everything.

| Option | Owner/setup effort | Tester experience | Suitable now? |
|---|---|---|---|
| A. Source-based pilot with assisted setup | Lowest packaging effort; still needs pilot-specific license/source review and a clean setup path | Some support at installation; normal desktop operation afterward | Recommended first step for a small pilot |
| B. Self-contained installer/portable bundle, with applicable source and notices | More packaging QA and native binary/source-provenance work; larger download and update responsibility | Most convenient once ready | Sensible second step if testers cannot tolerate assisted setup |
| C. Replace GPL-dependent speech components to pursue a different licensing model | Highest engineering effort; requires new dependency/model/license research and speech/performance regression | Could change pronunciation, voices or resource use | Consider only if source/redistribution rights conflict with a firm product goal |

A source-only or downloader-based setup can reduce what binaries the owner ships. It does not automatically decide the licensing of a combined program or remove all obligations. A ready-made bundle is also possible; it needs the exact provenance/compliance work already identified in RELEASE_READINESS.md. Engine replacement is not necessary simply because the pilot is small.

## The licensing decision, separately from packaging

The stack includes GPL-covered phonemizer/eSpeak components and LGPL-covered native audio components. Calling the audience friends or a private beta does not itself exempt transferred copies from applicable distribution terms. GPLv3 distinguishes running privately from conveying copies to others. [GPLv3 terms](https://www.gnu.org/licenses/gpl-3.0.en.html)

A GPL-compatible route can limit the initial audience without requiring a publicly visible GitHub repo, but recipients must receive the applicable source/rights and cannot be bound to restrictions that contradict those rights. They may redistribute covered work. If exclusive control or an NDA that prohibits that redistribution is essential, this route needs reconsideration before sharing. [GNU GPL FAQ](https://www.gnu.org/licenses/gpl-faq.en.html#DoesTheGPLAllowModNDA)

Existing Apache-2.0 source notices should be retained; they are not permission to label an entire combined runtime Apache-only. Determine the terms for the specific combination and intended handoff rather than blindly replacing the project's LICENSE. [Apache compatibility guidance](https://www.apache.org/licenses/GPL-compatibility.html)

For a few volunteer testers with no requirement to restrict redistribution, the recommended direction is to keep the engine and prepare a GPL-compatible pilot. Exact obligations/source completeness should be confirmed for the selected contents before sending copies; this document is planning guidance rather than legal clearance.

## Proposed next steps if this route is chosen

1. Confirm whether source access and the applicable recipient redistribution rights are acceptable. Keep repository visibility private initially; grant access only to explicitly selected recipients with owner authorization.
2. Define the source pilot's exact contents and resolve its licensing/source/notice requirements. If choosing a binary bundle instead, also close the exact native source/build/provenance gaps; license copies alone are insufficient.
3. Prepare a clean Windows x64 tester setup using the pinned private runtime and verified dependency/model downloads. Exclude personal artifacts and do not copy the owner's .venv.
4. Verify setup, launch, import, casting, rendering, export, project save/reopen and local-data handling on a clean tester environment. Current owner-PC results are not proof of another PC's compatibility or minimum RAM.
5. Invite only the chosen initial testers after the owner authorizes the concrete handoff; collect defects before investing in a polished installer. No cloud speech, engine replacement or new feature is required by this proposal.

No tester invitation, repository visibility change, license change, installer build or public release has been performed by this acceptance-documentation update.

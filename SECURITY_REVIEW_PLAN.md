# VoiceCastTTS v2 — comprehensive security-review plan

Status: initial scoped assessment performed 2026-10-26; see SECURITY_REVIEW_REPORT.md. Application/runtime remediation and retest are recorded in SECURITY_RETEST_REPORT.md; owner release clearance and binary-distribution evidence remain separate. The assessed functional source is frozen at 4119c50; owner listening acceptance remains a separate quality decision. Other feature work stays deferred during this review.

## Scope and boundaries

Review this independent v2 repository, its pinned Python environment, launchers, desktop shortcut creation, local HTTP server, browser UI, AI-program integrations, imports, models/custom voices, generated audio, logs, temporary files and release workflow.  Use synthetic files, fake local AI services and disposable project-only browser profiles. Do not read personal browser data, credentials, real LM Studio conversations/configuration or unrelated folders without a separate explicit scope grant. Any external testing, upload or new security-tool installation needs its own authorization.

The threat model includes hostile websites targeting localhost, untrusted imported files/links, malicious or malformed model responses, unsafe model/voice assets, other local processes and dependency compromise. Document the trust boundaries and the limits of relying on a local-only service. A virtual environment separates packages; it is not an operating-system sandbox.

## Review sequence and evidence

1. **Freeze and inventory.** Record source and private publication commits, Python/runtime versions, the tested dependency lock, model hashes/provenance, enabled optional integrations and a list of HTTP routes. Create a dependency/license inventory, including native DLLs. Review upstream advisories against exact installed versions using official sources; distinguish installed, shipped and optional components.
2. **Server and browser boundaries.** Examine loopback binding, host/origin checks, cross-site requests, CORS, content types, HTTP methods, same-origin assumptions and any authentication needs. Test relevant routes with allowed/refused origins and malformed headers. Inspect dynamic DOM updates, imported/model-generated text and downloaded filenames for injection. Use synthetic payloads on local test servers only.
3. **Filesystem and import handling.** Review path containment, Windows drive/UNC/device paths, encoded traversal, symlinks/reparse points, extensions, file-size limits and atomic writes. Cover project JSON, text imports, saved-chat parsing, custom voices and output downloads. Review optional URL import for redirects, DNS/address checks and SSRF. Keep optional online import off in baseline tests.
4. **Executable and model boundaries.** Review batch/PowerShell quoting, subprocess arguments, Chrome executable/profile selection, minimized startup and installer behavior. Trace model downloads and custom voice loading for integrity checks, unsafe deserialization or arbitrary code execution. Verify the v2 interpreter and imports stay in its environment and assess DLL search paths and inherited process environment.
5. **Availability and concurrency.** Test request sizes, long/nested text, pathological parsing, waveform/cache limits, streaming lookahead, spool disk consumption, interrupted connections, cancellation races and simultaneous renders. Confirm errors release locks/resources, cleanup cannot affect unrelated paths, and retained data has clear limits.
6. **Privacy and data lifecycle.** Trace text/voice/audio/model requests and all network destinations. Review logs, error messages, progress events, temporary audio, the dedicated Chrome profile and cached assets for unintended disclosure or persistence. Check private repository contents and history, ignore rules and publication refs. Verify local personal assets remain excluded and document what is not backed up.
7. **Distribution and release.** Check dependency/model/voice license notices and redistribution rights against intended packaging, supply-chain/reproducibility controls, setup instructions, launcher paths and private repository visibility. Use a clean checkout/environment test with no developer secrets or personal assets. No public release is implied by completing the code work.

## Deliverables and completion criteria

- Threat model and route/data-flow inventory tied to the reviewed commit.
- Findings with reproducible synthetic steps, affected locations, practical impact and severity; separate confirmed issues from assumptions and defense improvements.
- Remediation commits and regression checks for confirmed issues, followed by retesting of the affected boundary and normal workflows.
- A final report listing resolved/unresolved findings, accepted risks, exact evidence and untested scope. Record any external-access/tool approvals used.
- Do not call the review complete with unresolved critical/high issues without an explicit owner decision. Security approval and any public-release decision are separate from functional acceptance.

## Order after this functional revision

1. Owner listens to the v2 delivery comparison and tests normal story rendering.
2. Freeze the accepted v2 commit and confirm review scope/access.
3. Carry out the review above, fix and retest findings, then present the report for the owner's release decision.

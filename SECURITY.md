# Security and local-data handling

VoiceCastTTS v2 is a local Windows source beta. It is not a remotely hosted service. Keep the project directory private and use the launcher to establish a new browser session after a server restart.

The server defaults to loopback, validates Host/Origin and sensitive request metadata, and requires per-launch session access for API/media. Request sizes/timeouts/client counts, AI response sizes, project/cast sizes and temporary media storage are bounded. Served paths resolve containment; output serves WAV audio only. Model/runtime/package integrity is checked during setup. The runtime and packages are project-private, not an OS sandbox against same-user/admin programs.

Settings, editable configuration, stories, custom voices, output, browser profiles, credentials, retained data, caches and checkpoints are excluded from source publication. Do not attach them to public issues. Publication checks include both generic patterns and a separate local-only private-terms pass; no scanner recognizes every possible sensitive detail.

For a suspected vulnerability, use GitHub's private vulnerability reporting option when available. If unavailable, open an issue asking for a private reporting channel without exploit details, credentials or private material. For ordinary bugs, provide a short non-sensitive reproduction and relevant voice/action.

See SECURITY_RETEST_REPORT.md for the historical remediation evidence and PUBLIC_RELEASE_REVIEW.md for the current source-beta checks. No native fuzzing, destructive load test, independent penetration test or whole-OS audit is claimed. A future installer/binary redistribution or remote-access feature needs separate review.

# VoiceCastTTS v2 licensing

This is a source-only release. It contains the project's source, tests, configuration example and license/notice evidence. It does not convey a Python runtime, installed dependency wheels, native libraries, model weights, custom voices or generated audio. Setup obtains public dependencies directly from their upstream distributors and checks their integrity.

## Own source and the combined speech stack

The project's own source remains licensed under Apache-2.0 in LICENSE. Existing copyright/attribution notices are retained. That license does not relicense third-party code or make the assembled application Apache-only.

The default speech stack uses GPLv3-covered phonemizer/eSpeak components as well as LGPL and permissively licensed components. When conveying a combination covered by GPLv3, its applicable GPL terms must be followed for the combination while retaining the compatible source notices. COPYING.GPL-3.0 supplies the GPL text; it does not silently replace the license headers of independently licensed files. Recipients must receive applicable source/access and redistribution rights; a small beta or download-based setup is not a blanket exemption.

Primary references: [GNU GPL FAQ](https://www.gnu.org/licenses/gpl-faq.en.html#GPLModuleLicense), [GPLv3 terms](https://www.gnu.org/licenses/gpl-3.0.en.html), [Apache compatibility guidance](https://www.apache.org/licenses/GPL-compatibility.html). The actual dependency licenses take precedence over this technical summary.

## Notices and source access

- NOTICE retains application and component attributions.
- DEPENDENCY_INVENTORY.md identifies tested package versions and declared terms.
- THIRD_PARTY_NOTICES/ contains verbatim wheel/runtime notices, hashes and separately identified upstream supplements. Presence of a notice alone is not a complete compliance determination.
- DEPENDENCY_SOURCES.json lists version-specific PyPI source archives and their published hashes, plus upstream project links. Some wheels have no sdist or lack their own separate notice; those limitations are explicit. Upstream sources are not represented as a verified match for every native binary.

The code/build/setup scripts supplied in this repository are available to recipients. Individual third-party packages retain their own terms. Unmodified licenses are included as reference texts; they do not imply that the corresponding binaries are bundled.

## Models and personal material

The pinned Kokoro model release originates from the publisher's [Apache-2.0 model project](https://huggingface.co/hexgrad/Kokoro-82M). Setup checks the selected release's model hashes. This does not grant rights to arbitrary imported/custom/cloned voices, stories or recordings. Users supply their own material and permissions. Personal files are not part of this source release.

## Future binary packages

A compiled installer, portable runtime bundle or other redistribution of third-party binaries is a separate release decision. It requires exact contents, applicable source/build/relinking obligations and the native/source-provenance gaps documented in RELEASE_READINESS.md to be resolved. A source release is not blanket clearance for those future packages or a legal compliance certificate.

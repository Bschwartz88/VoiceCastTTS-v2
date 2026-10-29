# Third-party notice evidence

These are license/notice TEXTS, not application dependencies or bundled binaries. Do not replace or edit the verbatim upstream notices. They supplement VoiceCast's source LICENSE/NOTICE and DEPENDENCY_INVENTORY.md; they do not change its license or provide binary distribution clearance.

- manifest.json ties 108 embedded notice files to the exact 39 verified Windows wheels and records every copied file hash. Nested wheel paths are preserved as provenance; saved names are numbered to avoid collisions. Runtime, development, optional icon and pip/vendor notices are all included as evidence; the runtime-only lock determines ordinary application dependencies.
- CPython-3.14.10/LICENSE.txt is copied from the verified project-private runtime. That archive's catalog hash and Python Software Foundation executable signature were checked in the local runtime-setup evidence.
- upstream-supplements.json records separately fetched primary-source references, their hashes and limitations. espeakng-loader and flatbuffers wheels omitted separate license files. Flatbuffers' supplement uses the matching upstream tag; the loader reference is upstream main and needs exact-release confirmation. eSpeak COPYING is from its reported 1.52.0 version tag, not a proof that this is the exact source/build for the bundled DLL.
- The soundfile wheel's licensing notes identify additional native media components. NumPy, ONNX Runtime, pip and CPython notices include vendor components. A complete final binary-package bill of materials and corresponding-source/relinking review remain required if distributing an installer.

See RELEASE_READINESS.md for the recommendation and unresolved items. No model weights, voice assets, personal data, runtime executable or package wheel belongs in this directory.

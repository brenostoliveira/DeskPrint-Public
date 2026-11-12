# Exact optional Orca patch source supplement — 2026-11-11

Base: 345efec64c7b95a05ce5cf96df0dde66a158835c (v2.5.0-beta).
Compiled source: 6fa95284f91c07666f3470a358c0b65c5d336cf2.
The matching DLL remains unchanged. SOURCE-RECONCILIATION.json records the
comment-only notice delta. No binary runtime acceptance is inferred.

This tar already contains the modifications. Do not apply the standalone patch
twice. To use the standalone patch, start with the exact base revision and apply
0001-device-web-with-printer-agents.patch. Normalize source CRCRLF/CRLF to LF
when comparing a Windows checkout; the patch uses normalized text.

Use build_win.bat and deps/*.cmake from this exact source tree, Visual Studio
17 2022 x64, Release configuration, MSVC 19.44. Prepare dependency builds first,
then configure the application with the resulting CMAKE_PREFIX_PATH and build
the OrcaSlicer target. Python is 3.12; wxWidgets is the pinned 3.3-series fork;
OpenCV is 4.14.0. Older alpha/4.6 statements in historical guides do not describe
this beta build. DEPENDENCY-INVENTORY.json supplies dependency URLs/revisions,
source asset hashes, actual link-input names and their notices. Git dependency
archives include populated submodules and local source patches.

Only OrcaSlicer.dll is a patch payload. Other installed Orca files must match the
compatibility hashes in manifest.json; do not substitute Desktop FFmpeg/Qt DLLs
for Orca's prerequisites. Existing About/License Info screens and AGPL notices
remain intact. Source and notices are also offered through Desktop About and
the source/notices assets for the matching release.

Source comparison, packaging and simulated installer tests are distinct from
running the patched slicer, physical printer acceptance or commercial approval.

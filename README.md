# DeskPrint Studio

Public downloads, open components, original license notices and corresponding
source materials for the DeskPrint ecosystem. Proprietary Desktop/mobile sources,
private management services, credentials and signing keys are not distributed here.

## Install DeskPrint Studio

[Download the minimal Windows installer](https://github.com/brenostoliveira/DeskPrint-Public/releases/latest/download/DeskPrint-Installer-x64.exe).

The installer lets you choose a language and destination, presents the terms and
downloads the current signed application package. Installation and updates verify
the catalog signature and file hashes against the trusted keys already bundled
with the installer. No full offline client installer is required.

[Installer releases](https://github.com/brenostoliveira/DeskPrint-Public/releases)
provide the minimal installer. Separately named technical releases provide signed
Desktop/Aurora payloads and catalogs for the existing update clients. Their URLs
are preserved for compatibility; use the normal application maintenance flow.

## Open components

- [Creality Hi source snapshot](sources/creality/hi/README.md): historical exported
  open bridges, original notices and build information. Exact distributed versions
  are available in the source library below.
- [OrcaSlicer patch](sources/orca/deskprint-345efec/README.md): modified source,
  cumulative patch and component documentation.
- [Initial snapshot provenance](SOURCE_INDEX.json).
- [License scope](LICENSES.md): licenses apply per component, not to the complete
  DeskPrint product.
- [Distribution layout and verification](docs/RELEASES.md).

Source snapshots are not complete firmware images. They do not authorize copying
modules onto a running printer or imply physical validation for every device.
Third-party product names identify compatibility, without manufacturer endorsement.

## Source code, licenses and build instructions

[Open the permanent source library](https://github.com/brenostoliveira/DeskPrint-Public/releases/tag/sources-and-licenses).

This dedicated release contains corresponding source archives, original notices,
build recipes and replacement instructions for distributed components, including
Qt/PySide, FFmpeg, OrcaSlicer and the open Aurora bridges. Installer releases do
not need duplicate source attachments.

DeskPrint uses [FFmpeg](https://ffmpeg.org/) under
[LGPL 2.1 or later](https://www.gnu.org/licenses/old-licenses/lgpl-2.1.html).
Its corresponding source and original notices are included in that library.

Desktop bundles contain `BUNDLE-INVENTORY.json` with original filenames, sizes and
SHA-256 hashes. `HISTORICAL-MATERIALS.json` maps previous Desktop versions to their
exact corresponding members. Extract a mapped member under its original name
before following that version's build instructions. Aurora and Orca materials
retain their versioned names and original notices. Modification, replacement and
debugging rights granted by applicable third-party licenses remain preserved.

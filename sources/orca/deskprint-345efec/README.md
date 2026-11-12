# DeskPrint OrcaSlicer patch — exact modified source

Base: OrcaSlicer revision `345efec64c7b95a05ce5cf96df0dde66a158835c`.
Compiled source revision: `6fa95284f91c07666f3470a358c0b65c5d336cf2`.
The eight modified source files are present below with per-file SHA-256 in
SOURCE-RECONCILIATION.json; the cumulative patch applies to the stated base.
This directory is AGPL-3.0 under the original Orca notices; no license is assigned
to the closed DeskPrint application by this source publication.

## Changes

- Embedded Device-tab web integration and printer-agent bridge, preserving the
  original About/License Info and AGPL information.
- Creality Hi/CFS material mapping, multiple colours/gradients left to right,
  and safe scoped Type2 tower-to-CFS Z travel adaptation.
- Preserve slicer tool selection, extrusion and existing native operations;
  no duplicate extra tool/extrusion commands are introduced by the travel patch.

See the cumulative patch for the complete exact change, not only this summary.
Modified-file dates/DeskPrint attribution are retained in the code. Later source
reconciliation adds notices/comments without changing the compiled DLL.

## Full sources and build materials

[Source-only release](https://github.com/brenostoliveira/DeskPrint-Public/releases/tag/orca-source-2026.11.12)
contains OrcaSlicer-corresponding-source.tar.gz, pinned dependency archives,
original notices, selected build recipe and sanitized linked-object evidence.
The archive hash is `81f4237809445fa30a024b95efe2bd771e277b9c1cc9de3329187f9adc78878d`.
Use SUPPLEMENTAL-BUILD.md and the materials in that archive. Existing externally
installed Orca prerequisites and embedded patch payload are distinguished there.
Sources are preserved for recipients of the corresponding binary. These files
are not a complete Desktop installer or printer firmware. This source-only
publication neither announces Desktop commercial approval nor changes installed
slicers, profiles or printers. No separate Orca consent clause is introduced.

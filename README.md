# DeskPrint public components and releases

Public source components, license notices and the distribution location for
DeskPrint Studio updates. The main Desktop and mobile application sources are
maintained separately and are not included in this repository.

## Available source

- [Creality Hi open components](sources/creality/hi/README.md): GPL printer and
  Moonraker bridges, MIT filament inventory publisher,
  license texts, build instructions and per-file SHA-256 inventory.
- [Source index](SOURCE_INDEX.json): exact exported snapshot and provenance.
- [License scope](LICENSES.md): licenses apply per component, not to the entire
  DeskPrint product.

The source snapshot matches the open-component export of printer package
`2026.11.08.2`. The optional qrcode dependency is excluded at the owner's request. The source
manifest describes the filtered files actually published; this is not the
complete original export. Printer runtime changes are maintained separately.

## Updates

[GitHub Releases](https://github.com/brenostoliveira/DeskPrint-Public/releases)
is the intended public location for approved update packages, signatures and
matching source archives. No binary release is published by this initial setup.
See [release requirements](docs/RELEASES.md).

The installed Desktop keeps its local channel until a release containing public
channel support is installed. That transport and a small online installer are
being prepared; no remotely installable binary is announced by this source update.

## Scope

This source snapshot is not a complete printer firmware image or an installer.
Do not copy it onto a running printer without understanding the applicable host,
configuration and installation instructions. Source availability does not imply
hardware validation, compatibility with every printer or commercial clearance
of a complete DeskPrint distribution.

Additional component and dependency sources will be associated with the exact
binary releases to which they belong. This initial repository does not claim
complete corresponding-source coverage for every DeskPrint product or patch.

DeskPrint is an independent project. Third-party product names identify compatible
components and do not imply endorsement by their manufacturers.

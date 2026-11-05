# DeskPrint public components and releases

Public source components, license notices and the distribution location for
DeskPrint Studio updates. The main Desktop and mobile application sources are
maintained separately and are not included in this repository.

## Available source

- [Creality Hi open components](sources/creality/hi/README.md): GPL printer and
  Moonraker bridges, MIT filament inventory publisher, vendored qrcode source,
  license texts, build instructions and per-file SHA-256 inventory.
- [Source index](SOURCE_INDEX.json): exact exported snapshot and provenance.
- [License scope](LICENSES.md): licenses apply per component, not to the entire
  DeskPrint product.

The initial source snapshot matches the open-component export of printer package
`2026.11.02.3`. Its export README and hash manifest are preserved verbatim, including
its historical statement that publication had not yet happened at export time.
This repository is now the public publication of that snapshot.

## Updates

[GitHub Releases](https://github.com/brenostoliveira/DeskPrint-Public/releases)
is the intended public location for approved update packages, signatures and
matching source archives. No binary release is published by this initial setup.
See [release requirements](docs/RELEASES.md).

The currently installed Desktop still uses its configured local update channel.
Creating this repository does not change client update settings automatically.

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

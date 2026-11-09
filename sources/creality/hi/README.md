# DeskPrint printer open components

This source selection contains the GPL printer/Moonraker bridges, the MIT
filament publisher and a generic MIT build helper. The optional qrcode dependency
is omitted at the project owner's request. This is a filtered source selection,
not the complete original open-component export or a printer firmware image.

There is no private capture coordinator, private Studio backend, Desktop/mobile
application, credential, customer configuration, photograph, backup or OEM firmware
in this directory. Licensing follows each component's terms in COPYING and
MIT-LICENSE.txt; see the repository's LICENSES.md.

The included sources match their entries in the printer package 2026.11.08.2
export. Build flags and source/binary hashes for the retained modules are in
build-information.json. Use Cython3.1.6, Zig0.15.2 and compatible ARM hard-float
CPython3.9 development headers. Installed firmware/interpreter dependencies are
not redistributed here. This selection does not change the installed printer.

On a root-accessible printer, replacing a chosen open extension requires safely
stopping/restarting its host and preserving configuration. Removing its .so makes
the host's normal loader select the supplied .py source. No vendor package signing
key is required to run a modified GPL bridge. Keep corresponding source, build and
installation information and notices when distributing modified binaries.

The root SOURCE_INDEX.json identifies the original export and this selection.
PUBLIC_EXPORT_MANIFEST.json hashes the files actually present here. Releases that
still distribute omitted dependencies require their own matching source/notices;
this filtered snapshot does not assert complete release compliance.

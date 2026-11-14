# Installation, updates and corresponding sources

## Public release layout

- `deskprint-v<version>`: presentation release containing the minimal Windows
  installer. The latest presentation release is the normal user download.
- `desktop-v<version>` and `desktop-channel`: technical application payload,
  catalog, signature and version pointer used by installed clients.
- `aurora-creality-hi-v<version>` and `aurora-creality-hi-channel`: independent
  printer package and signed catalog used by the Desktop maintenance flow.
- `sources-and-licenses`: permanent source, license-notice and build-instruction
  library, linked at the end of the repository home page.

The small installer downloads the same signed application payload used by the
updater. Existing technical URLs and trust keys remain compatible. Historical
technical releases may retain an installer copy for existing storefront links.
GitHub also automatically provides snapshots of this public repository; these
are distinct from the exact corresponding-source materials in the library.

## Required verification

Binary publication requires the exact reviewed package, manifest, signatures,
compatible runtime and isolated installation/update/rollback evidence. Public
download verification confirms actual bytes anonymously. A successful local build
alone is not publication, physical validation or a new legal opinion.

Factory backups, personal data, printer configurations, signing keys, credentials
and management data are never release assets. Updates preserve recovery on failure
and delete only files whose ownership and obsolescence are established by inventory.

## Source retention

Sources are copied to the permanent library and verified anonymously by SHA-256
and size before their old attachments are removed. A conflict or failed copy
preserves the originals. Historical release descriptions remain, with their source
links redirected to the library. Unknown assets are preserved.

Desktop bundles include exact inventories and a historical version/name/hash map.
Build instructions, patches, original licenses and replacement rights remain
available with those materials. Required source materials for a distributed
version are not treated as obsolete installation binaries.

The Desktop, installer and Aurora continue validating their existing signed
catalogs. This organization changes download presentation and source storage;
it neither grants a license nor authorizes automatic printer installation.

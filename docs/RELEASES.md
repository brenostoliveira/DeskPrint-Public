# Release publication

Use GitHub Release assets for binary packages; do not commit installers, caches,
private build logs or signed update payloads to the source tree.

## Separate products and versions

Use explicit version tags such as `desktop-v0.1.15` and
`aurora-creality-hi-2026.11.02.3`. A source-only tag is not an application update.
Do not treat the newest release of one component as an update for another.

## Required before publishing a binary update

1. Confirm the exact artifact and the verification receipts for that component.
2. Include its signed update catalog, detached signatures where applicable,
   payload, version, hashes, supported platform and release notes.
3. Include or link the matching source, build material and third-party notices
   required for the exact distributed files. Resolve distribution restrictions.
4. Stage the release as a draft, upload the complete reviewed set, verify hashes,
   then publish. Do not announce a package whose assets are still missing.
5. Keep immutable version references and the source needed for older distributed
   versions. Replacing local build output does not authorize deleting required
   public source history.

The client must continue validating signatures against its existing trusted
public keys and request installation through its normal maintenance flow.
Do not ship publisher credentials or private signing keys to clients, or trust
an arbitrary signing key obtained alongside a download.

## Public channel and installation

Desktop releases use `desktop-v<version>`. The `desktop-channel` release contains
only a small `desktop-latest.json` pointer. That pointer selects a version; it
does not authorize an executable. Clients verify that version's catalog and
signature against the keys already included in the application, then verify the
payload and its per-file inventory. Local maintenance packages remain supported.

The current minimal installer is a native frontend which chooses language,
shows the terms and asks for the installation directory, then downloads the
selected signed payload and installs its verified files. It does not contain
the full application. It resolves the public signed channel using embedded
trusted verification keys; it does not trust arbitrary downloaded binaries.
Older version-specific Web Setup bootstraps are historical and are not the
current store download. No new client binary is announced by this source update.

Updates replace application-owned binaries using the new inventory. Obsolete
owned files are removed only after success is confirmed; rollback material is
retained on failure. Unknown or modified files, personal data, settings and
original printer backups are preserved. Releases retire only older recognized
binary assets after the new channel is confirmed; required source, license
notices and source history are retained.

The transport/bootstrap implementation is prepared in the private build project.
Remote binary publication remains pending exact-package distribution review;
this document is not an announcement that those assets are already downloadable.

The repository now also provides an exact Orca patch source directory and the
source-only `orca-source-2026.11.12` release. Its inventory lists 42 archives/
notice/inventory assets, with SHA-256 verified against GitHub's asset digests.
It includes no client binaries and does not change the Desktop update pointer.

The initial repository setup publishes open source only. It does not enable
remote update discovery in the installed Desktop, upload existing installers,
change signing keys or declare a commercial release.

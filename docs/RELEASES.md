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

The initial repository setup publishes open source only. It does not enable
remote update discovery in the installed Desktop, upload existing installers,
change signing keys or declare a commercial release.

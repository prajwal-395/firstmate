# Ren releases

Pushing a tag named `v<version>` starts `.github/workflows/release.yml`. The
tag must match `ren/version.py`; a mismatch fails before packaging. The
workflow builds an immutable engine tree from that tag, then drafts a GitHub
release with the engine ZIP, SPDX SBOM, and `SHA256SUMS.txt`.

## Apple signing and notarization

The workflow creates and notarizes a macOS app bundle only when every required
repository Actions secret is present. Add these secrets under the repository's
Actions secrets:

| Secret | Value |
| --- | --- |
| `APPLE_DEVELOPER_ID_APPLICATION_P12` | Base64-encoded PKCS#12 export containing the Developer ID Application certificate and its private key. |
| `APPLE_DEVELOPER_ID_APPLICATION_PASSWORD` | Password used to export that PKCS#12 file. |
| `APPLE_DEVELOPER_ID_APPLICATION_IDENTITY` | Exact identity shown by `security find-identity -v -p codesigning`, for example `Developer ID Application: Organization (TEAMID)`. |
| `APPLE_ID` | Apple account email used for notarization. |
| `APPLE_TEAM_ID` | Apple Developer team ID. |
| `APPLE_APP_SPECIFIC_PASSWORD` | Apple app-specific password used by `notarytool`. |

With all six secrets, the workflow signs `Ren.app`, verifies its signature,
submits it to Apple's notary service, staples and validates the ticket, then
attaches `ren-engine-<tag>-macos.zip` to the draft release. If any secret is
missing, signing and notarization are skipped with a workflow notice; the
unsigned engine ZIP, SBOM, and checksum manifest are still drafted.

The SPDX inventory scans the same engine tree that is archived. Syft is pinned
to version 1.42.3, and its macOS arm64 and x86_64 release archives are checked
against their committed SHA-256 values in the workflow.

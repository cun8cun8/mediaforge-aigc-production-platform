# C2PA Content Credentials

This profile signs a C2PA manifest with C2PA Tool (`c2patool`) while keeping the
credential boundary explicit:

| Process | Holds | Does not hold |
| --- | --- | --- |
| `mediaforge` API | signing and verification call tokens | certificate, private key, HSM/KMS credential |
| `c2pa-signer` | signing credential or subprocess signer configuration | verifier token |
| `c2pa-verifier` | verification token and read-only artifacts | signing credential |

The API creates both a MediaForge evidence claim and a C2PA Tool manifest
definition. It asks the signing service to embed the latter into the media, then
asks a separate process to validate the signed output. A credential only reaches
`SIGNED_VERIFIED` when the source hash, MediaForge claim, C2PA manifest, signed
output hash, and independent validation all pass.

## Staging With A PEM Credential

This path creates a real cryptographic C2PA signature, but a self-signed or
private-CA certificate will normally not be trusted by public C2PA validators.
It is useful to prove the integration, not to enable release.

1. Download the approved Linux x86_64 `c2patool` release archive from the official
   `contentauth/c2pa-rs` release page. Obtain its SHA-256 through your approved
   software supply-chain channel, then install it outside Git:

   ```powershell
   .\ops\c2pa\install-c2patool.ps1 `
     -ArchivePath D:\secure\c2patool-v0.27.22-x86_64-unknown-linux-gnu.tar.gz `
     -ExpectedSha256 <approved-sha256>
   ```

   The binary is ignored by Git and copied into the signer/verifier image only
   after this hash check. Do not allow Docker to fetch a mutable tool release
   while building a production image.

2. Obtain an X.509 signing certificate chain and matching PEM private key. The
   chain must start with the end-entity certificate and include intermediates.
   The leaf must be a non-CA C2PA claim-signing credential with the
   `1.3.6.1.4.1.62558.2.1` EKU and `digitalSignature` key usage. You can run the
   non-disclosing preflight before copying any secrets:

   ```powershell
   .\ops\c2pa\preflight-c2pa-credential.ps1 `
     -PrivateKeyPath D:\secure\c2pa\private-key.pem `
     -CertificateChainPath D:\secure\c2pa\signing-chain.pem
   ```
3. Bootstrap local, ignored secrets:

   ```powershell
   .\ops\c2pa\bootstrap.ps1 `
     -PrivateKeyPath D:\secure\c2pa\private-key.pem `
     -CertificateChainPath D:\secure\c2pa\signing-chain.pem `
     -Algorithm ps256
   ```

4. Start the C2PA overlay:

   ```powershell
   docker compose -f docker-compose.staging.yml -f docker-compose.c2pa.yml --profile worker up -d --build
   ```

5. Obtain the C2PA trust-anchor bundle through the approved supply-chain process,
   verify its SHA-256, and make it available only to the verifier:

   ```powershell
   .\ops\c2pa\install-c2pa-trust-anchors.ps1 `
     -TrustAnchorPath D:\secure\C2PA-TRUST-LIST.pem `
     -ExpectedSha256 <approved-sha256>
   ```

   Uncomment `MEDIAFORGE_C2PA_VERIFIER_TRUST_ANCHORS` in `.env.c2pa`, then
   restart the overlay. An internal private-CA bundle may be used for an
   integration test, but does not establish public C2PA trust.

6. Confirm all three boundaries are healthy without exposing secret values:

   ```powershell
   docker compose -f docker-compose.staging.yml -f docker-compose.c2pa.yml ps
   Invoke-RestMethod http://127.0.0.1:8021/content-credentials/status
   ```

7. Export a final MP4, create its content credential in the Assets tab, then run
   verification. Keep `MEDIAFORGE_REQUIRE_RELEASE_CONTENT_CREDENTIALS` disabled
   until validation succeeds with the intended production certificate.

For an integration-only smoke test, remove the local PEM variables from
`.env.c2pa` and explicitly set `MEDIAFORGE_C2PA_SIGNER_ALLOW_BUILTIN_TEST_SIGNER=true`
and `MEDIAFORGE_C2PA_SIGNER_TEST_MODE=true`. The C2PA Tool test identity can embed
a signature but is untrusted; the independent verifier rejects it and strict
production acceptance remains blocked.

## Production Identity

Do not use the local PEM mode for a production identity. C2PA's own tooling
recommends a KMS/HSM or subprocess signer so private key material never enters
`c2patool` or the API process. Configure the signer service with exactly one of:

- `MEDIAFORGE_C2PA_SIGNER_PATH`: an executable implementing C2PA Tool's
  `--signer-info` and raw-signature stdin/stdout protocol. This is the preferred
  HSM/KMS integration.
- `MEDIAFORGE_C2PA_SIGNER_SETTINGS_PATH`: a read-only tool settings file managed
  by the signing platform. This is only appropriate when its signer section does
  not expose a production private key to the container filesystem.

Use an end-entity certificate from a CA that is accepted by the C2PA trust list,
include its intermediate chain, use a compatible algorithm, and set an approved
timestamp authority URL with `MEDIAFORGE_C2PA_TSA_URL`. The final verification
must be performed by `c2pa-verifier`, which has no signing-secret volume.

After a signed asset passes the independent verifier and an external C2PA
validator, record the reviewed evidence in the change ticket and set
`MEDIAFORGE_C2PA_PRODUCTION_SIGNER_ATTESTED=true`. This makes strict deployment
acceptance eligible to pass; it is deliberately not enabled by merely configuring
a command or a certificate path. Only then enable
`MEDIAFORGE_REQUIRE_RELEASE_CONTENT_CREDENTIALS=true`. Re-run the test after
certificate rotation, C2PA Tool upgrades, trust-list changes, and any HSM/KMS
policy change.

## Credential Sources

Use a certificate authority that issues C2PA-compatible signing certificates.
The official C2PA documentation explains the required chain order, certificate
extraction, and supported algorithms. It also explains why direct filesystem
keys are development-only. Do not put a `.pfx`, PEM key, settings file containing
keys, bearer token, or TSA credential in this repository or CI logs.

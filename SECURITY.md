# Security Policy

## Supported Versions

| Version / branch | Security support |
| --- | --- |
| `main` | Supported while the repository remains in active development |
| Older commits, local forks and generated artifacts | Not supported |

## Reporting A Vulnerability

Please do not create a public Issue for a potential security problem.

1. Use GitHub's **Security** tab and **Report a vulnerability** private advisory flow for this repository when it is enabled by the owner.
2. If private reporting is not available, contact the repository owner through GitHub and ask for a private reporting channel before sharing technical details, proof of concept material, customer data or credentials.
3. Include affected component/version, impact, reproduction steps, expected behavior and any mitigation you already tested.

Do not include live Provider tokens, API keys, database credentials, callback secrets, real customer material or production endpoint details in a public report, commit, pull request or CI log.

## Security Boundaries

MediaForge treats the following as security-sensitive:

- Tenant/project authorization and artifact download isolation.
- OIDC session, API key and Worker-role authentication.
- Provider callback signatures, job leases and event idempotency.
- Object storage credentials, delivery package access and artifact path validation.
- Source documents, reference assets, voice data, prompt history and audit records.
- License/rights records and provenance evidence used for release decisions.

## Disclosure Handling

Maintainers should acknowledge a private report within five business days, assess reproducibility and impact, agree on a remediation timeline, and publish a disclosure only after affected users have a reasonable upgrade path. Do not promise a fixed severity or response time before triage.

## Secure Development Requirements

- Use secrets through deployment tooling, GitHub Environments or an external secret manager.
- Keep test credentials clearly non-production and avoid public callback endpoints in automated tests.
- Verify external webhooks over the original body, timestamp and configured secret.
- Require HTTPS, secure cookies and restricted egress for production OIDC and Provider integrations.
- Run the [production release checklist](docs/release-checklist.md) before production changes.

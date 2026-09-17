[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
    [string]$PrivateKeyPath,
    [Parameter(Mandatory)]
    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
    [string]$CertificateChainPath,
    [ValidateSet("ps256", "ps384", "ps512", "es256", "es384", "es512", "ed25519")]
    [string]$Algorithm = "ps256",
    [string]$TimeAuthorityUrl = ""
)

$ErrorActionPreference = "Stop"
$repositoryRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$secretRoot = Join-Path $repositoryRoot "ops\secrets\c2pa"
$stagingRoot = Join-Path $repositoryRoot "ops\staging"
$envTemplate = Join-Path $stagingRoot ".env.c2pa.example"
$envTarget = Join-Path $stagingRoot ".env.c2pa"

& (Join-Path $PSScriptRoot "preflight-c2pa-credential.ps1") `
    -PrivateKeyPath $PrivateKeyPath `
    -CertificateChainPath $CertificateChainPath

New-Item -ItemType Directory -Force -Path $secretRoot | Out-Null
Copy-Item -LiteralPath $PrivateKeyPath -Destination (Join-Path $secretRoot "private-key.pem") -Force
Copy-Item -LiteralPath $CertificateChainPath -Destination (Join-Path $secretRoot "signing-chain.pem") -Force

function New-SecretToken {
    $bytes = New-Object byte[] 32
    [Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    return [Convert]::ToHexString($bytes).ToLowerInvariant()
}

[IO.File]::WriteAllText(
    (Join-Path $secretRoot "signer-token"),
    (New-SecretToken) + [Environment]::NewLine,
    [Text.UTF8Encoding]::new($false)
)
[IO.File]::WriteAllText(
    (Join-Path $secretRoot "verifier-token"),
    (New-SecretToken) + [Environment]::NewLine,
    [Text.UTF8Encoding]::new($false)
)

$configuration = Get-Content -LiteralPath $envTemplate -Raw
$configuration = $configuration -replace '(?m)^MEDIAFORGE_C2PA_SIGNER_ALGORITHM=.*$', "MEDIAFORGE_C2PA_SIGNER_ALGORITHM=$Algorithm"
if ($TimeAuthorityUrl.Trim()) {
    $configuration += "`nMEDIAFORGE_C2PA_TSA_URL=$($TimeAuthorityUrl.Trim())`n"
}
[IO.File]::WriteAllText($envTarget, $configuration, [Text.UTF8Encoding]::new($false))

Write-Host "C2PA local signing secrets were written under ops/secrets/c2pa."
Write-Host "Start with: docker compose -f docker-compose.staging.yml -f docker-compose.c2pa.yml --profile worker up -d --build"
Write-Host "The local PEM profile passed credential preflight. Release remains blocked until its certificate chain is trusted by the independent verifier."

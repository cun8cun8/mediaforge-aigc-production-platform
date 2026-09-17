[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
    [string]$TrustAnchorPath,
    [Parameter(Mandatory)]
    [ValidatePattern("^[A-Fa-f0-9]{64}$")]
    [string]$ExpectedSha256,
    [string]$TrustConfigPath = "",
    [string]$TrustConfigExpectedSha256 = "",
    [string]$AllowedListPath = "",
    [string]$AllowedListExpectedSha256 = ""
)

$ErrorActionPreference = "Stop"
$repositoryRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$destinationRoot = Join-Path $repositoryRoot "ops\c2pa\trust"

function Copy-VerifiedFile {
    param(
        [Parameter(Mandatory)][string]$Source,
        [Parameter(Mandatory)][string]$Destination,
        [Parameter(Mandatory)][string]$ExpectedHash
    )
    $actualHash = (Get-FileHash -LiteralPath $Source -Algorithm SHA256).Hash
    if (-not $actualHash.Equals($ExpectedHash, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Trust material SHA-256 did not match the approved value."
    }
    Copy-Item -LiteralPath $Source -Destination $Destination -Force
}

New-Item -ItemType Directory -Force -Path $destinationRoot | Out-Null
Copy-VerifiedFile -Source $TrustAnchorPath -Destination (Join-Path $destinationRoot "trust-anchors.pem") -ExpectedHash $ExpectedSha256

if ($TrustConfigPath.Trim()) {
    if ($TrustConfigExpectedSha256 -notmatch "^[A-Fa-f0-9]{64}$") {
        throw "TrustConfigExpectedSha256 is required when TrustConfigPath is provided."
    }
    Copy-VerifiedFile -Source $TrustConfigPath -Destination (Join-Path $destinationRoot "trust-config.cfg") -ExpectedHash $TrustConfigExpectedSha256
}
if ($AllowedListPath.Trim()) {
    if ($AllowedListExpectedSha256 -notmatch "^[A-Fa-f0-9]{64}$") {
        throw "AllowedListExpectedSha256 is required when AllowedListPath is provided."
    }
    Copy-VerifiedFile -Source $AllowedListPath -Destination (Join-Path $destinationRoot "allowed-list.pem") -ExpectedHash $AllowedListExpectedSha256
}

Write-Host "Verified C2PA trust material was written under ops/c2pa/trust."
Write-Host "Set MEDIAFORGE_C2PA_VERIFIER_TRUST_ANCHORS=/var/run/mediaforge-c2pa-trust/trust-anchors.pem in ops/staging/.env.c2pa."

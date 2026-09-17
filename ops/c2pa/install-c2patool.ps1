[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
    [string]$ArchivePath,
    [Parameter(Mandatory)]
    [ValidatePattern("^[A-Fa-f0-9]{64}$")]
    [string]$ExpectedSha256
)

$ErrorActionPreference = "Stop"
$repositoryRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$destinationRoot = Join-Path $repositoryRoot "ops\c2pa\bin"
$destination = Join-Path $destinationRoot "c2patool"
$archive = (Resolve-Path -LiteralPath $ArchivePath).Path
$actualSha256 = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash
if (-not $actualSha256.Equals($ExpectedSha256, [StringComparison]::OrdinalIgnoreCase)) {
    throw "C2PA Tool archive SHA-256 did not match the approved value."
}

$temporaryRoot = Join-Path $env:TEMP ("mediaforge-c2patool-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $temporaryRoot | Out-Null
try {
    tar -xzf $archive -C $temporaryRoot
    $binary = Get-ChildItem -LiteralPath $temporaryRoot -Recurse -File -Filter "c2patool" |
        Select-Object -First 1
    if ($null -eq $binary) {
        throw "The archive did not contain the Linux c2patool binary."
    }
    New-Item -ItemType Directory -Force -Path $destinationRoot | Out-Null
    Copy-Item -LiteralPath $binary.FullName -Destination $destination -Force
    Write-Host "Installed the verified Linux c2patool binary at ops/c2pa/bin/c2patool."
} finally {
    if ((Resolve-Path -LiteralPath $temporaryRoot).Path.StartsWith((Resolve-Path -LiteralPath $env:TEMP).Path, [StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $temporaryRoot -Recurse -Force
    }
}

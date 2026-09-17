[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
    [string]$PrivateKeyPath,
    [Parameter(Mandatory)]
    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
    [string]$CertificateChainPath,
    [ValidateRange(1, 3650)]
    [int]$MinimumRemainingDays = 30
)

$ErrorActionPreference = "Stop"
if ($null -eq (Get-Command openssl -ErrorAction SilentlyContinue)) {
    throw "OpenSSL is required to preflight a C2PA signing credential."
}

function Invoke-OpenSslText {
    param([Parameter(Mandatory)][string[]]$Arguments)
    $output = & openssl @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "OpenSSL rejected the supplied signing credential."
    }
    return ($output -join "`n")
}

$keyPublic = Invoke-OpenSslText @("pkey", "-in", $PrivateKeyPath, "-pubout", "-outform", "PEM")
$certificatePublic = Invoke-OpenSslText @("x509", "-in", $CertificateChainPath, "-pubkey", "-noout")
if (($keyPublic -replace "\s", "") -ne ($certificatePublic -replace "\s", "")) {
    throw "The supplied private key does not match the first certificate in the chain."
}

$minimumSeconds = $MinimumRemainingDays * 24 * 60 * 60
& openssl x509 -in $CertificateChainPath -noout -checkend $minimumSeconds *> $null
if ($LASTEXITCODE -ne 0) {
    throw "The signing certificate expires within the configured minimum validity period."
}

$details = Invoke-OpenSslText @("x509", "-in", $CertificateChainPath, "-noout", "-text")
if ($details -notmatch "CA:FALSE") {
    throw "The first certificate must be a non-CA C2PA signing certificate."
}
if ($details -notmatch "Digital Signature") {
    throw "The signing certificate must permit digital signatures."
}
if ($details -match "Certificate Sign") {
    throw "The signing certificate must not permit certificate signing."
}
if ($details -notmatch "1\.3\.6\.1\.4\.1\.62558\.2\.1") {
    throw "The signing certificate is missing the C2PA claim-signing EKU (1.3.6.1.4.1.62558.2.1)."
}
if ($details -match "Any Extended Key Usage") {
    throw "The signing certificate must not use anyExtendedKeyUsage."
}

Write-Output "C2PA signing credential preflight passed. No private key material was displayed."

[CmdletBinding()]
param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$stagingRoot = $PSScriptRoot
$destination = Join-Path $stagingRoot ".env.staging"

if ((Test-Path -LiteralPath $destination) -and -not $Force) {
    throw "Refusing to overwrite $destination. Use -Force only after saving the existing local staging configuration."
}

function New-HexSecret([int]$ByteCount) {
    $bytes = [byte[]]::new($ByteCount)
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    return [Convert]::ToHexString($bytes).ToLowerInvariant()
}

$postgresPassword = New-HexSecret 24
$redisPassword = New-HexSecret 24
$minioPassword = New-HexSecret 24
$adminToken = "stg_admin_$(New-HexSecret 24)"
$workerToken = "stg_provider_$(New-HexSecret 24)"
$callbackSecret = New-HexSecret 48
$apiKeys = @{
    $adminToken = @{ subject = "staging-operator"; role = "admin"; tenant_id = "staging" }
    $workerToken = @{ subject = "staging-worker"; role = "provider"; tenant_id = "staging" }
} | ConvertTo-Json -Compress

@"
# Generated locally by ops/staging/bootstrap.ps1. Do not commit this file.
POSTGRES_PASSWORD=$postgresPassword
REDIS_PASSWORD=$redisPassword
MINIO_ROOT_USER=mediaforge-staging
MINIO_ROOT_PASSWORD=$minioPassword

MEDIAFORGE_STORAGE_BUCKET=mediaforge-staging-artifacts
MEDIAFORGE_STORAGE_ENDPOINT=http://minio:9000
MEDIAFORGE_DATABASE_URL=postgresql://mediaforge_staging:$postgresPassword@postgres:5432/mediaforge_staging
MEDIAFORGE_REDIS_URL=redis://:$redisPassword@redis:6379/0
AWS_ACCESS_KEY_ID=mediaforge-staging
AWS_SECRET_ACCESS_KEY=$minioPassword

MEDIAFORGE_API_KEYS='$apiKeys'
MEDIAFORGE_WORKER_TOKEN=$workerToken
MEDIAFORGE_CALLBACK_SECRET=$callbackSecret
MEDIAFORGE_CALLBACK_MAX_AGE_SECONDS=300

MEDIAFORGE_PROVIDER=mock
MEDIAFORGE_LLM_MODE=disabled
MEDIAFORGE_PLANNING_MODE=langgraph
MEDIAFORGE_RATE_LIMIT_REQUESTS=120
MEDIAFORGE_RATE_LIMIT_READ_REQUESTS=600
MEDIAFORGE_RATE_LIMIT_WINDOW_SECONDS=60
"@ | Set-Content -LiteralPath $destination -Encoding ascii -NoNewline

Write-Output "Created local staging configuration at $destination."
Write-Output "It contains generated credentials and is ignored by Git. Use the Admin token inside MEDIAFORGE_API_KEYS to sign in to the Studio."

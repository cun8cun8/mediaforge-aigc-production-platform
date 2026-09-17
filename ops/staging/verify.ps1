[CmdletBinding()]
param(
    [string]$BaseUrl = "http://127.0.0.1:8021",
    [switch]$FullWorkflow,
    [switch]$RequireWorker
)

$ErrorActionPreference = "Stop"
$environmentPath = Join-Path $PSScriptRoot ".env.staging"
if (-not (Test-Path -LiteralPath $environmentPath)) {
    throw "Missing $environmentPath. Run ops/staging/bootstrap.ps1 first."
}

$values = @{}
foreach ($line in Get-Content -LiteralPath $environmentPath) {
    if (-not $line -or $line.TrimStart().StartsWith("#")) {
        continue
    }
    $parts = $line -split "=", 2
    if ($parts.Count -eq 2) {
        $values[$parts[0]] = $parts[1].Trim("'")
    }
}

$apiKeys = $values["MEDIAFORGE_API_KEYS"] | ConvertFrom-Json -AsHashtable
$adminToken = $apiKeys.Keys | Where-Object { $apiKeys[$_].role -eq "admin" } | Select-Object -First 1
if (-not $adminToken) {
    throw "No admin token was found in MEDIAFORGE_API_KEYS."
}

$headers = @{ Authorization = "Bearer $adminToken" }
$health = Invoke-RestMethod "$BaseUrl/health"
$status = Invoke-RestMethod "$BaseUrl/enterprise/status" -Headers $headers
$probe = Invoke-RestMethod "$BaseUrl/enterprise/probe" -Method Post -Headers $headers
$readiness = Invoke-RestMethod "$BaseUrl/ops/readiness" -Headers $headers
$provider = Invoke-RestMethod "$BaseUrl/providers/diagnostics" -Headers $headers
$workers = if ($RequireWorker) { Invoke-RestMethod "$BaseUrl/workers" -Headers $headers } else { $null }

$failedChecks = @($probe.checks | Where-Object { -not $_.passed })
$summary = [ordered]@{
    health = $health
    provider_mode = $provider.status.mode
    provider_grade = $provider.grade
    provider_production_ready = $provider.production_ready
    state_backend = $status.database.backend
    queue_backend = $status.queue.backend
    storage_backend = $status.storage.backend
    enterprise_probe = if ($probe.reachable -and $failedChecks.Count -eq 0) { "passed" } else { "failed" }
    readiness_grade = $readiness.grade
    production_ready = $readiness.production_ready
}
$summary | ConvertTo-Json -Depth 5

if ($failedChecks.Count -gt 0 -or -not $probe.reachable) {
    throw "Staging enterprise probe failed. Inspect docker compose logs mediaforge postgres redis minio-init."
}

if ($RequireWorker) {
    $onlineWorkers = @($workers.workers | Where-Object { $_.status -eq "ONLINE" })
    if ($onlineWorkers.Count -lt 1) {
        throw "No online Worker is registered. Start the optional worker profile and inspect its logs."
    }
    Write-Output "Online workers: $($onlineWorkers.worker_id -join ', ')"
}

if ($FullWorkflow) {
    $composePath = Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) "docker-compose.staging.yml"
    $projectId = "staging_smoke_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
    Write-Output "Running the authenticated full workflow smoke test inside the API container."
    docker compose -f $composePath exec -T mediaforge python -m mediaforge_p1.api_smoke --base-url http://127.0.0.1:8020 --project-id $projectId
    if ($LASTEXITCODE -ne 0) {
        throw "Staging full workflow smoke test failed."
    }
}

[CmdletBinding()]
param(
    [string]$BaseUrl = "http://127.0.0.1:8021",
    [switch]$Probe
)

$ErrorActionPreference = "Stop"

function Read-DotEnvFile([string]$Path) {
    $values = @{}
    foreach ($line in Get-Content -LiteralPath $Path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) {
            continue
        }
        $separator = $trimmed.IndexOf("=")
        if ($separator -lt 1) {
            continue
        }
        $name = $trimmed.Substring(0, $separator).Trim()
        $value = $trimmed.Substring($separator + 1).Trim()
        if ($value.Length -ge 2 -and $value.StartsWith("'") -and $value.EndsWith("'")) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        $values[$name] = $value
    }
    return $values
}

$providerEnvironmentPath = Join-Path $PSScriptRoot ".env.provider"
if (-not (Test-Path -LiteralPath $providerEnvironmentPath)) {
    throw "Missing $providerEnvironmentPath. Copy .env.provider.comfyui.example only after a reviewed registry and endpoint are approved."
}
$stagingEnvironmentPath = Join-Path $PSScriptRoot ".env.staging"
if (-not (Test-Path -LiteralPath $stagingEnvironmentPath)) {
    throw "Missing $stagingEnvironmentPath. Run ops/staging/bootstrap.ps1 first."
}

$providerValues = Read-DotEnvFile $providerEnvironmentPath
$providerModeValue = [string]$providerValues["MEDIAFORGE_PROVIDERS"]
if (-not $providerModeValue) {
    $providerModeValue = [string]$providerValues["MEDIAFORGE_PROVIDER"]
}
$providerModes = @(
    ($providerModeValue -split ",") |
    ForEach-Object { $_.Trim().ToLowerInvariant() } |
    Where-Object { $_ }
)
if ($providerModes -notcontains "comfyui") {
    throw "MEDIAFORGE_PROVIDERS must include comfyui before running this preflight."
}
$workflowPinValue = [string]$providerValues["COMFYUI_REQUIRE_WORKFLOW_PIN"]
if ($workflowPinValue -notmatch "^(?i:true|1|yes|on)$") {
    throw "COMFYUI_REQUIRE_WORKFLOW_PIN must be true for the staging ComfyUI profile."
}

$registryPath = [string]$providerValues["COMFYUI_WORKFLOW_REGISTRY_PATH"]
$containerConfigRoot = "/app/provider-config/"
if (-not $registryPath -or -not $registryPath.StartsWith($containerConfigRoot, [System.StringComparison]::Ordinal)) {
    throw "COMFYUI_WORKFLOW_REGISTRY_PATH must point below $containerConfigRoot in staging."
}

$hostConfigRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "provider-config")).Path
$relativeRegistry = $registryPath.Substring($containerConfigRoot.Length).Replace("/", [IO.Path]::DirectorySeparatorChar)
$hostRegistryPath = [IO.Path]::GetFullPath((Join-Path $hostConfigRoot $relativeRegistry))
$normalizedRoot = $hostConfigRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
if (-not $hostRegistryPath.StartsWith($normalizedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "COMFYUI_WORKFLOW_REGISTRY_PATH must not escape ops/staging/provider-config."
}
if (-not (Test-Path -LiteralPath $hostRegistryPath -PathType Leaf)) {
    throw "ComfyUI workflow registry is missing at $hostRegistryPath."
}

$repositoryRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$composePath = Join-Path $repositoryRoot "docker-compose.staging.yml"
$defaultTemplateId = [string]$providerValues["MEDIAFORGE_IMAGE_WORKFLOW_TEMPLATE_ID"]
$videoTemplateId = [string]$providerValues["MEDIAFORGE_VIDEO_WORKFLOW_TEMPLATE_ID"]
$preflightArgs = @(
    "compose", "-f", $composePath, "run", "--rm", "--no-deps",
    "--entrypoint", "python", "mediaforge", "-m", "mediaforge_p1.comfyui_preflight",
    "--registry", $registryPath
)
if ($defaultTemplateId) {
    $preflightArgs += @("--default-template-id", $defaultTemplateId)
}
if ($videoTemplateId) {
    $preflightArgs += @("--video-template-id", $videoTemplateId)
}
Write-Output "Validating pinned ComfyUI registry without contacting the Provider."
& docker @preflightArgs
if ($LASTEXITCODE -ne 0) {
    throw "ComfyUI registry preflight failed. Resolve the reported workflow or SHA-256 issue before restarting staging."
}

if (-not $Probe) {
    Write-Output "Registry preflight passed. Restart the staging API, then rerun with -Probe for a non-generating connectivity and model-inventory check."
    exit 0
}

$stagingValues = Read-DotEnvFile $stagingEnvironmentPath
$apiKeys = $stagingValues["MEDIAFORGE_API_KEYS"] | ConvertFrom-Json -AsHashtable
$adminToken = $apiKeys.Keys | Where-Object { $apiKeys[$_].role -eq "admin" } | Select-Object -First 1
if (-not $adminToken) {
    throw "No admin token was found in MEDIAFORGE_API_KEYS."
}

$headers = @{ Authorization = "Bearer $adminToken" }
$diagnostics = Invoke-RestMethod "$BaseUrl/providers/diagnostics" -Headers $headers
$statusRows = if ($diagnostics.status.providers) { @($diagnostics.status.providers) } else { @($diagnostics.status) }
$comfyStatus = $statusRows | Where-Object { $_.mode -eq "comfyui" } | Select-Object -First 1
if (-not $comfyStatus) {
    throw "The running API has not loaded ComfyUI. Restart docker compose staging after creating .env.provider."
}
if (-not $comfyStatus.configured) {
    throw "The running ComfyUI configuration is incomplete: $($comfyStatus.message)"
}

$healthRows = if ($diagnostics.health.providers) { @($diagnostics.health.providers) } else { @($diagnostics.health) }
$comfyHealth = $healthRows | Where-Object { $_.provider -eq $comfyStatus.provider } | Select-Object -First 1
if (-not $comfyHealth -or -not $comfyHealth.reachable -or -not $comfyHealth.healthy) {
    $message = if ($comfyHealth) { $comfyHealth.message } else { "no ComfyUI health response" }
    throw "ComfyUI non-generating probe failed: $message"
}
if (-not $diagnostics.callback_security.configured) {
    throw "MEDIAFORGE_CALLBACK_SECRET is required before using a real Provider."
}

$workflowRows = @($comfyStatus.details.workflow_registry.workflows)
$summary = [ordered]@{
    provider = $comfyStatus.provider
    configured = [bool]$comfyStatus.configured
    reachable = [bool]$comfyHealth.reachable
    healthy = [bool]$comfyHealth.healthy
    selected_image_template = $comfyStatus.details.default_template_id
    selected_video_template = $comfyStatus.details.default_video_template_id
    capabilities = @($comfyStatus.details.workflow_capabilities)
    workflow_count = $workflowRows.Count
    declared_model_requirements = @($workflowRows | ForEach-Object { @($_.model_requirements) }).Count
    callback_authentication = [bool]$diagnostics.callback_security.configured
}
$summary | ConvertTo-Json -Depth 4
Write-Output "ComfyUI connectivity and reviewed model inventory are ready. No generation job was submitted."

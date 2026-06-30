[CmdletBinding()]
param(
    [string]$OutputPath,
    [string]$CodexHome,
    [string]$LogPath
)

$ErrorActionPreference = 'Stop'

function Get-DefaultCodexHome {
    if ($env:CODEX_HOME) {
        return $env:CODEX_HOME
    }
    return (Join-Path $env:USERPROFILE '.codex')
}

function Write-ErrorSnapshot {
    param(
        [string]$Path,
        [string]$Message
    )
    $parent = Split-Path -Parent $Path
    if ($parent) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    $payload = [ordered]@{
        schema_version = 1
        status = 'error'
        message = $Message
        generated_at_local = (Get-Date).ToString('yyyy-MM-ddTHH:mm:sszzz')
        five_hour_display = '--'
        five_hour_remaining_percent = $null
        weekly_display = '--'
        weekly_remaining_percent = $null
        reset_display = 'failed'
        today_tokens_display = '--'
        rate_limits_source = 'powershell-wrapper'
        today_token_source = 'powershell-wrapper'
    }
    $payload | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Write-PluginLog {
    param([string]$Message)
    if (-not $LogPath) {
        return
    }
    $parent = Split-Path -Parent $LogPath
    if ($parent) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $Message"
    Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
}

if (-not $CodexHome) {
    $CodexHome = Get-DefaultCodexHome
}

if (-not $OutputPath) {
    if ($env:CODEX_TRAFFICMONITOR_USAGE_JSON) {
        $OutputPath = $env:CODEX_TRAFFICMONITOR_USAGE_JSON
    }
    else {
        $OutputPath = Join-Path $CodexHome 'trafficmonitor\codex_usage_status.json'
    }
}

if (-not $LogPath) {
    $LogPath = Join-Path (Split-Path -Parent $OutputPath) 'codex_usage_plugin.log'
}

Write-PluginLog "update_codex_usage.ps1 start OutputPath=$OutputPath CodexHome=$CodexHome"

$collector = Join-Path $PSScriptRoot 'collect_codex_usage.py'
if (-not (Test-Path -LiteralPath $collector)) {
    Write-PluginLog "collector missing: $collector"
    Write-ErrorSnapshot -Path $OutputPath -Message "Collector script not found: $collector"
    exit 0
}

$pythonOverride = $env:CODEX_TRAFFICMONITOR_PYTHON
if ($pythonOverride -and (Test-Path -LiteralPath $pythonOverride)) {
    Write-PluginLog "using CODEX_TRAFFICMONITOR_PYTHON=$pythonOverride"
    & $pythonOverride $collector --codex-home $CodexHome --output $OutputPath
    Write-PluginLog "python exit code: $LASTEXITCODE"
    exit $LASTEXITCODE
}

$python = Get-Command python -ErrorAction SilentlyContinue
if ($python) {
    Write-PluginLog "using python=$($python.Source)"
    & $python.Source $collector --codex-home $CodexHome --output $OutputPath
    Write-PluginLog "python exit code: $LASTEXITCODE"
    exit $LASTEXITCODE
}

$py = Get-Command py -ErrorAction SilentlyContinue
if ($py) {
    Write-PluginLog "using py=$($py.Source)"
    & $py.Source -3 $collector --codex-home $CodexHome --output $OutputPath
    Write-PluginLog "py exit code: $LASTEXITCODE"
    exit $LASTEXITCODE
}

Write-PluginLog 'python not found'
Write-ErrorSnapshot -Path $OutputPath -Message 'Python was not found. Install Python or set CODEX_TRAFFICMONITOR_PYTHON to python.exe.'
exit 0

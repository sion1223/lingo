[CmdletBinding()]
param(
    [string]$Venv = (Join-Path $HOME '.lingo-venv'),
    [int]$Port = 8000,
    [int]$MetricsPort = 20241,
    [switch]$NoTunnel,
    [switch]$NoPublish,
    [switch]$Stop
)

# Serve the scorer from this machine's GPU, expose it through a Cloudflare quick
# tunnel and point GitHub Pages at it. Safe to run again: a healthy server and a
# running tunnel are reused, and Pages is redeployed only when the URL changed.

$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $HOME '.lingo-logs'
$LocalUrl = "http://127.0.0.1:$Port"
$HealthTimeoutSeconds = 300
$TunnelTimeoutSeconds = 90
$PagesVariable = 'LINGO_API_BASE_URL'
$PagesWorkflow = 'pages.yml'
$PagesBranch = 'main'

function Find-Cloudflared {
    $command = Get-Command cloudflared -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    foreach ($candidate in @(
        "${env:ProgramFiles(x86)}\cloudflared\cloudflared.exe",
        "$env:ProgramFiles\cloudflared\cloudflared.exe"
    )) {
        if (Test-Path $candidate) { return $candidate }
    }
    throw 'cloudflared not found. Install it with: winget install --id Cloudflare.cloudflared -e'
}

function Get-Health([string]$BaseUrl) {
    try {
        return Invoke-RestMethod "$BaseUrl/health" -TimeoutSec 5
    } catch {
        return $null
    }
}

function Wait-Until([scriptblock]$Condition, [int]$TimeoutSeconds, [string]$What) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $value = & $Condition
        if ($value) { return $value }
        Start-Sleep -Seconds 2
    }
    throw "Timed out waiting for $What."
}

function Get-TunnelHost {
    try {
        return (Invoke-RestMethod "http://127.0.0.1:$MetricsPort/quicktunnel" -TimeoutSec 3).hostname
    } catch {
        return $null
    }
}

function Stop-LocalServing {
    Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
        Where-Object { $_.CommandLine -match 'uvicorn' -and $_.CommandLine -match 'scorer\.server' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host "Stopped scorer (pid $($_.ProcessId))." }
    Get-Process cloudflared -ErrorAction SilentlyContinue |
        ForEach-Object { Stop-Process -Id $_.Id -Force; Write-Host "Stopped tunnel (pid $($_.Id))." }
}

if ($Stop) {
    Stop-LocalServing
    exit 0
}

New-Item -ItemType Directory -Force $LogDir | Out-Null

# 1. Scoring server (deep model loads in the background; about 2-3 minutes).
if (Get-Health $LocalUrl) {
    Write-Host "Scorer already running at $LocalUrl/"
} else {
    $python = Join-Path $Venv 'Scripts\python.exe'
    if (-not (Test-Path $python)) {
        throw "Python venv not found at $python. See docs/SERVE.md (local GPU)."
    }
    # The venv must not pick up the CPU-only torch from the user site-packages.
    $env:PYTHONNOUSERSITE = '1'
    $env:PYTHONUTF8 = '1'
    $env:BUILD_SHA = (git -C $Root rev-parse HEAD)
    Start-Process -FilePath $python `
        -ArgumentList '-m', 'uvicorn', 'scorer.server:app', '--host', '127.0.0.1', '--port', "$Port" `
        -WorkingDirectory $Root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogDir 'server.out.log') `
        -RedirectStandardError (Join-Path $LogDir 'server.log') | Out-Null
    Write-Host "Starting scorer (log: $LogDir\server.log)..."
    Wait-Until { Get-Health $LocalUrl } $HealthTimeoutSeconds 'the scorer to answer /health' | Out-Null
    Write-Host "Scorer running at $LocalUrl/"
}

$health = Wait-Until {
    $h = Get-Health $LocalUrl
    if ($h -and ($h.model_loaded -or $h.load_error)) { $h }
} $HealthTimeoutSeconds 'the deep scoring model to load'
if ($health.load_error) {
    Write-Warning "Deep scorer failed to load ($($health.load_error)); final scoring is unavailable."
} else {
    Write-Host "Deep scorer ready on $($health.device)."
}

if ($NoTunnel) { exit 0 }

# 2. Public tunnel. Quick tunnel URLs change every time cloudflared restarts.
$tunnelHost = Get-TunnelHost
if ($tunnelHost) {
    Write-Host "Tunnel already running."
} else {
    $cloudflared = Find-Cloudflared
    Start-Process -FilePath $cloudflared `
        -ArgumentList 'tunnel', '--no-autoupdate', '--metrics', "127.0.0.1:$MetricsPort", '--url', $LocalUrl `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogDir 'tunnel.out.log') `
        -RedirectStandardError (Join-Path $LogDir 'tunnel.log') | Out-Null
    Write-Host "Starting tunnel (log: $LogDir\tunnel.log)..."
    $tunnelHost = Wait-Until { Get-TunnelHost } $TunnelTimeoutSeconds 'the tunnel URL'
}
$publicUrl = "https://$tunnelHost"
Wait-Until { Get-Health $publicUrl } $TunnelTimeoutSeconds 'the tunnel to reach the scorer' | Out-Null
Write-Host "Public scorer: $publicUrl"

if ($NoPublish) { exit 0 }

# 3. GitHub Pages uses the repository variable at build time.
Push-Location $Root
try {
    $current = (gh variable get $PagesVariable 2>$null)
    if ($current -eq $publicUrl) {
        Write-Host "GitHub Pages already points at this tunnel."
    } else {
        gh variable set $PagesVariable --body $publicUrl
        gh workflow run $PagesWorkflow --ref $PagesBranch
        Write-Host "Redeploying GitHub Pages with the new tunnel URL (takes about a minute)."
    }
} finally {
    Pop-Location
}
Write-Host 'Site: https://sion1223.github.io/lingo/'

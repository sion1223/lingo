[CmdletBinding()]
param(
    [switch]$ResetKey
)

$ErrorActionPreference = 'Stop'

$PodId = 'l8faq6mx5shxpc'
$PodName = 'lingo-scorer'
$HealthUrl = "https://$PodId-8000.proxy.runpod.net/health"
$AppUrl = "https://$PodId-8000.proxy.runpod.net/"
$CredentialDirectory = Join-Path $env:LOCALAPPDATA 'Lingo'
$CredentialPath = Join-Path $CredentialDirectory 'runpod-api-key.dpapi'
$WatchdogPath = Join-Path $PSScriptRoot 'runpod-watchdog.ps1'
$ApiBaseUrl = 'https://rest.runpod.io/v1/pods'
$mutex = $null
$ownsMutex = $false
$podMayBeRunning = $false
$exitCode = 0
$watchdogProcess = $null
$watchdogArmPath = $null
$stopSucceeded = $false

function ConvertTo-PlainText {
    param([Security.SecureString]$SecureValue)

    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureValue)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

function Save-ApiKey {
    param([Security.SecureString]$SecureKey)

    if (-not (Test-Path -LiteralPath $CredentialDirectory)) {
        New-Item -ItemType Directory -Path $CredentialDirectory -Force | Out-Null
    }

    # ConvertFrom-SecureString uses Windows DPAPI. The result can only be
    # decrypted by this Windows user on this computer.
    ConvertFrom-SecureString -SecureString $SecureKey |
        Set-Content -LiteralPath $CredentialPath -NoNewline
}

function Get-ApiKey {
    if ($ResetKey -and (Test-Path -LiteralPath $CredentialPath)) {
        Remove-Item -LiteralPath $CredentialPath -Force
        Write-Host 'Saved RunPod API key removed.' -ForegroundColor Yellow
    }

    if (-not [string]::IsNullOrWhiteSpace($env:RUNPOD_API_KEY)) {
        $secureKey = ConvertTo-SecureString $env:RUNPOD_API_KEY -AsPlainText -Force
        Save-ApiKey -SecureKey $secureKey
        return $env:RUNPOD_API_KEY.Trim()
    }

    if (Test-Path -LiteralPath $CredentialPath) {
        try {
            $serializedKey = (Get-Content -LiteralPath $CredentialPath -Raw).Trim()
            $secureKey = $serializedKey | ConvertTo-SecureString
            return (ConvertTo-PlainText -SecureValue $secureKey).Trim()
        }
        catch {
            throw "The saved API key could not be decrypted. Run run_lingo.bat -ResetKey. $($_.Exception.Message)"
        }
    }

    Write-Host 'A RunPod API key is required only on the first run.' -ForegroundColor Cyan
    Write-Host 'Create a restricted key with Pod read/write permission in RunPod Settings.'
    $secureKey = Read-Host 'RunPod API key' -AsSecureString
    $plainKey = (ConvertTo-PlainText -SecureValue $secureKey).Trim()
    if ([string]::IsNullOrWhiteSpace($plainKey)) {
        throw 'The RunPod API key was empty.'
    }

    Save-ApiKey -SecureKey $secureKey
    return $plainKey
}

function Invoke-PodAction {
    param(
        [Parameter(Mandatory = $true)][ValidateSet('start', 'stop')][string]$Action,
        [Parameter(Mandatory = $true)][string]$ApiKey
    )

    $headers = @{ Authorization = "Bearer $ApiKey" }
    return Invoke-RestMethod `
        -Method Post `
        -Uri "$ApiBaseUrl/$PodId/$Action" `
        -Headers $headers `
        -TimeoutSec 30
}

function Get-PodStatus {
    param([Parameter(Mandatory = $true)][string]$ApiKey)

    $headers = @{ Authorization = "Bearer $ApiKey" }
    $pod = Invoke-RestMethod `
        -Method Get `
        -Uri "$ApiBaseUrl/$PodId" `
        -Headers $headers `
        -TimeoutSec 30
    return $pod.desiredStatus
}

function Start-Watchdog {
    param([string]$CredentialFile)

    if (-not (Test-Path -LiteralPath $WatchdogPath)) {
        throw "Watchdog script not found: $WatchdogPath"
    }

    $powershellPath = (Get-Process -Id $PID).Path
    $readyPath = Join-Path $env:TEMP ("lingo-runpod-{0}.ready" -f [guid]::NewGuid())
    $armPath = Join-Path $env:TEMP ("lingo-runpod-{0}.armed" -f [guid]::NewGuid())
    $arguments = @(
        '-NoLogo'
        '-NoProfile'
        '-NonInteractive'
        '-ExecutionPolicy'
        'Bypass'
        '-File'
        ('"{0}"' -f $WatchdogPath)
        '-PodId'
        $PodId
        '-CredentialPath'
        ('"{0}"' -f $CredentialFile)
        '-ReadyPath'
        ('"{0}"' -f $readyPath)
        '-ArmPath'
        ('"{0}"' -f $armPath)
    )

    $process = Start-Process `
        -FilePath $powershellPath `
        -ArgumentList $arguments `
        -WindowStyle Hidden `
        -PassThru

    try {
        $deadline = (Get-Date).AddSeconds(10)
        while ((Get-Date) -lt $deadline) {
            if (Test-Path -LiteralPath $readyPath) {
                Remove-Item -LiteralPath $readyPath -Force
                return [pscustomobject]@{
                    Process = $process
                    ArmPath = $armPath
                }
            }
            if ($process.HasExited) {
                throw 'The RunPod shutdown watchdog exited during startup.'
            }
            Start-Sleep -Milliseconds 100
        }

        throw 'The RunPod shutdown watchdog did not become ready within 10 seconds.'
    }
    catch {
        if (-not $process.HasExited) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        }
        throw
    }
    finally {
        Remove-Item -LiteralPath $readyPath -Force -ErrorAction SilentlyContinue
    }
}

function Wait-ForHealth {
    param([int]$TimeoutSeconds = 300)

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $attempt = 0
    while ((Get-Date) -lt $deadline) {
        $attempt++
        try {
            $response = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 10
            if ($response.StatusCode -eq 200) {
                return $true
            }
        }
        catch {
            # The proxy normally returns an error while the container and model load.
        }

        Write-Host ("  Waiting for server... {0}s" -f ($attempt * 5)) -ForegroundColor DarkGray
        Start-Sleep -Seconds 5
    }

    return $false
}

try {
    $mutex = New-Object Threading.Mutex($false, "Global\LingoRunPodSession_$PodId")
    try {
        $ownsMutex = $mutex.WaitOne(0, $false)
    }
    catch [Threading.AbandonedMutexException] {
        # A previous session was force-closed. Windows transfers ownership of
        # the abandoned mutex to this process, so it is safe to continue.
        $ownsMutex = $true
    }
    if (-not $ownsMutex) {
        throw 'A Lingo RunPod session is already open on this computer.'
    }

    $apiKey = Get-ApiKey

    # A detached watchdog stops the Pod even if this console is closed with X.
    $watchdog = Start-Watchdog -CredentialFile $CredentialPath
    $watchdogProcess = $watchdog.Process
    $watchdogArmPath = $watchdog.ArmPath

    $initialStatus = Get-PodStatus -ApiKey $apiKey
    Set-Content -LiteralPath $watchdogArmPath -Value 'armed' -NoNewline
    $podMayBeRunning = $true
    if ($initialStatus -eq 'RUNNING') {
        Write-Host "RunPod '$PodName' is already running." -ForegroundColor Cyan
        Write-Host 'This window now controls it and will stop it when closed.' -ForegroundColor Yellow
    }
    else {
        Write-Host "Starting RunPod '$PodName' ($PodId)..." -ForegroundColor Cyan
        $startResult = Invoke-PodAction -Action start -ApiKey $apiKey
        if ($startResult.desiredStatus) {
            Write-Host "RunPod status: $($startResult.desiredStatus)"
        }
    }

    Write-Host 'Waiting for the scoring server to become ready (up to 5 minutes)...'
    if (-not (Wait-ForHealth)) {
        throw "The Pod started, but the server did not become healthy within 5 minutes. Check: $HealthUrl"
    }

    Write-Host ''
    Write-Host 'Lingo is ready.' -ForegroundColor Green
    Write-Host "App: $AppUrl"
    Write-Host 'Keep this window open while using Lingo.' -ForegroundColor Yellow
    Write-Host 'Press Enter, Ctrl+C, or close this window to stop the RunPod session.'
    Read-Host | Out-Null
}
catch {
    $exitCode = 1
    Write-Host ''
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
}
finally {
    if ($podMayBeRunning -and $apiKey) {
        Write-Host ''
        Write-Host "Stopping RunPod '$PodName'..." -ForegroundColor Cyan
        try {
            $stopResult = Invoke-PodAction -Action stop -ApiKey $apiKey
            if ($stopResult.desiredStatus) {
                Write-Host "RunPod status: $($stopResult.desiredStatus)" -ForegroundColor Green
            }
            else {
                Write-Host 'RunPod stop request sent.' -ForegroundColor Green
            }
            $stopSucceeded = $true

            # The interactive stop succeeded, so the detached fallback must not
            # wake later and stop a newly launched session.
            if ($watchdogProcess -and -not $watchdogProcess.HasExited) {
                Stop-Process -Id $watchdogProcess.Id -Force -ErrorAction SilentlyContinue
            }
        }
        catch {
            $exitCode = 1
            Write-Host "WARNING: The stop request failed. The watchdog will retry it. $($_.Exception.Message)" -ForegroundColor Red
        }
    }

    if ($ownsMutex -and $mutex) {
        $mutex.ReleaseMutex()
    }
    if ($mutex) {
        $mutex.Dispose()
    }
    if ($stopSucceeded -and $watchdogArmPath) {
        Remove-Item -LiteralPath $watchdogArmPath -Force -ErrorAction SilentlyContinue
    }
}

exit $exitCode

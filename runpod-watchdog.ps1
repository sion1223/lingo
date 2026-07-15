param(
    [Parameter(Mandatory = $true)][string]$PodId,
    [Parameter(Mandatory = $true)][string]$CredentialPath,
    [Parameter(Mandatory = $true)][string]$ReadyPath,
    [Parameter(Mandatory = $true)][string]$ArmPath
)

$ErrorActionPreference = 'SilentlyContinue'
$lastError = $null
$mutex = $null
$ownsMutex = $false
try {
    $serializedKey = (Get-Content -LiteralPath $CredentialPath -Raw).Trim()
    $secureKey = $serializedKey | ConvertTo-SecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
    try {
        $apiKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer).Trim()
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }

    $headers = @{ Authorization = "Bearer $apiKey" }
    $mutex = New-Object Threading.Mutex($false, "Global\LingoRunPodSession_$PodId")
    Set-Content -LiteralPath $ReadyPath -Value 'ready' -NoNewline

    # The interactive session owns this mutex. Waiting on it both detects that
    # session's exit and prevents a new session from starting during retries.
    try {
        $ownsMutex = $mutex.WaitOne()
    }
    catch [Threading.AbandonedMutexException] {
        $ownsMutex = $true
    }

    if (-not (Test-Path -LiteralPath $ArmPath)) {
        if ($ownsMutex) {
            $mutex.ReleaseMutex()
            $ownsMutex = $false
        }
        $mutex.Dispose()
        exit 0
    }
    Remove-Item -LiteralPath $ArmPath -Force

    for ($attempt = 1; $attempt -le 6; $attempt++) {
        try {
            Invoke-RestMethod `
                -Method Post `
                -Uri "https://rest.runpod.io/v1/pods/$PodId/stop" `
                -Headers $headers `
                -TimeoutSec 30 | Out-Null
            if ($ownsMutex) {
                $mutex.ReleaseMutex()
                $ownsMutex = $false
            }
            $mutex.Dispose()
            exit 0
        }
        catch {
            $lastError = $_.Exception.Message
            if ($attempt -lt 6) {
                Start-Sleep -Seconds 10
            }
        }
    }
}
catch {
    $lastError = $_.Exception.Message
}

if ($ownsMutex -and $mutex) {
    $mutex.ReleaseMutex()
}
if ($mutex) {
    $mutex.Dispose()
}

# This process has no UI. Keep a secret-free diagnostic if every retry failed.
try {
    $logDirectory = Split-Path -Parent $CredentialPath
    $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content `
        -LiteralPath (Join-Path $logDirectory 'runpod-watchdog.log') `
        -Value "$timestamp - Failed to stop Pod $PodId after session exit: $lastError"
}
catch {
}

exit 1

[CmdletBinding()]
param(
    [string]$Config = (Join-Path $env:LOCALAPPDATA 'YouTubeYtDlpBridge\config.json')
)

$ErrorActionPreference = 'Stop'
$configPath = [System.IO.Path]::GetFullPath($ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Config))
$pidPath = Join-Path (Split-Path -Parent $configPath) 'listener.pid'
if (-not (Test-Path -LiteralPath $pidPath)) {
    Write-Output 'Listener PID file not found; it may already be stopped.'
    exit 0
}

$listenerPid = [int](Get-Content -LiteralPath $pidPath -Raw)
$process = Get-CimInstance Win32_Process -Filter "ProcessId=$listenerPid" -ErrorAction SilentlyContinue
if (-not $process -or $process.CommandLine -notmatch 'ytdlp_listener\.py') {
    throw "PID file is stale or does not identify the listener: $listenerPid"
}
Stop-Process -Id $listenerPid
Write-Output "Stopped listener PID=$listenerPid"

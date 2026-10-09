$ErrorActionPreference = 'Stop'
$pidFile = Join-Path $env:LOCALAPPDATA 'YouTubeYtDlpBridge\listener.pid'
if (-not (Test-Path -LiteralPath $pidFile -PathType Leaf)) { exit 0 }

$listenerPid = 0
if (-not [int]::TryParse((Get-Content -LiteralPath $pidFile -Raw).Trim(), [ref]$listenerPid)) { exit 0 }
$process = Get-CimInstance Win32_Process -Filter "ProcessId=$listenerPid" -ErrorAction SilentlyContinue
if (-not $process) { exit 0 }

$isKnownListener = $process.CommandLine -match 'ytdlp_listener\.py' -or
    $process.ExecutablePath -match 'VideoDownloadCompanion\.exe$'
if (-not $isKnownListener) {
    throw "PID file points to an unrelated process; refusing to stop PID $listenerPid"
}
$taskkill = Join-Path $env:SystemRoot 'System32\taskkill.exe'
& $taskkill /PID $listenerPid /T /F | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Unable to stop the verified Companion process tree: PID $listenerPid"
}
Start-Sleep -Milliseconds 800

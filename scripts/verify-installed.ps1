[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'Programs\VideoDownloadCompanion'),
    [string]$StateDir = (Join-Path $env:LOCALAPPDATA 'YouTubeYtDlpBridge')
)

$ErrorActionPreference = 'Stop'
$install = [System.IO.Path]::GetFullPath($InstallDir)
$state = [System.IO.Path]::GetFullPath($StateDir)
$configPath = Join-Path $state 'config.json'
$requiredFiles = @(
    (Join-Path $install 'VideoDownloadCompanion.exe'),
    (Join-Path $install 'scripts\download_douyin.ps1'),
    (Join-Path $install 'scripts\refresh_douk_cookie.ps1'),
    (Join-Path $install 'tools\yt-dlp.exe'),
    (Join-Path $install 'tools\deno\deno.exe'),
    (Join-Path $install 'tools\ffmpeg\ffmpeg.exe'),
    (Join-Path $install 'tools\ffmpeg\ffprobe.exe'),
    (Join-Path $install 'tools\douk\main.exe'),
    $configPath
)
$missing = @($requiredFiles | Where-Object { -not (Test-Path -LiteralPath $_ -PathType Leaf) })
if ($missing) { throw "Missing installed files:`n$($missing -join "`n")" }

$config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ([string]$config.server.host -notin @('127.0.0.1', '::1', 'localhost')) {
    throw "Listener is not configured for loopback: $($config.server.host)"
}
if ([string]::IsNullOrWhiteSpace([string]$config.server.auth_token) -or [string]$config.server.auth_token -eq 'CHANGE_ME_TO_A_RANDOM_TOKEN') {
    throw 'Local API token is missing or still uses the example value.'
}

$doukExe = Join-Path $install 'tools\douk\main.exe'
$doukHash = (Get-FileHash -LiteralPath $doukExe -Algorithm SHA256).Hash
if ($doukHash -ne [string]$config.douk.expected_sha256) {
    throw "DouK hash does not match config. File=$doukHash Config=$($config.douk.expected_sha256)"
}

$ytDlpVersion = (& (Join-Path $install 'tools\yt-dlp.exe') --version | Select-Object -First 1)
$denoVersion = (& (Join-Path $install 'tools\deno\deno.exe') --version | Select-Object -First 1)
$ffmpegVersion = (& (Join-Path $install 'tools\ffmpeg\ffmpeg.exe') -version | Select-Object -First 1)

$healthUrl = 'http://{0}:{1}/api/health' -f $config.server.host, $config.server.port
$health = Invoke-RestMethod -Uri $healthUrl -Headers @{ 'X-YTDLP-Token' = [string]$config.server.auth_token } -TimeoutSec 10

[ordered]@{
    install_dir = $install
    state_dir = $state
    app_version = [string]$health.version
    listener_status = 'healthy'
    queue_depth = [int]$health.queueDepth
    active_jobs = [int]$health.active
    yt_dlp = [string]$ytDlpVersion
    deno = [string]$denoVersion
    ffmpeg = [string]$ffmpegVersion
    douk_sha256 = $doukHash
    douk_volume_exists = (Test-Path -LiteralPath (Join-Path $install 'tools\douk\Volume') -PathType Container)
    token_present = $true
} | ConvertTo-Json

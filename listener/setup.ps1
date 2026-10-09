[CmdletBinding()]
param(
    [string]$Config = (Join-Path $env:LOCALAPPDATA 'YouTubeYtDlpBridge\config.json'),
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$templatePath = Join-Path $scriptDir 'config.example.json'
$configPath = [System.IO.Path]::GetFullPath($ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Config))
$configDir = Split-Path -Parent $configPath

New-Item -ItemType Directory -Force -Path $configDir | Out-Null
if ((Test-Path -LiteralPath $configPath) -and -not $Force) {
    Write-Output "Config already exists: $configPath"
    Write-Output 'Use -Force only if you intentionally want a new token and default configuration.'
    exit 0
}

$settings = Get-Content -LiteralPath $templatePath -Raw -Encoding UTF8 | ConvertFrom-Json
$settings.douk.wrapper = Join-Path $scriptDir 'download_douyin.ps1'
$tokenBytes = New-Object byte[] 32
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
try { $rng.GetBytes($tokenBytes) } finally { $rng.Dispose() }
$settings.server.auth_token = [Convert]::ToBase64String($tokenBytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')

$portableRoot = Join-Path $env:LOCALAPPDATA 'Programs\yt-dlp-portable'
$ytDlpPath = Join-Path $portableRoot 'yt-dlp.exe'
if (Test-Path -LiteralPath $ytDlpPath) {
    $settings.yt_dlp.executable = $ytDlpPath
}
$ffmpegExe = Get-ChildItem -LiteralPath (Join-Path $portableRoot 'ffmpeg') -Recurse -Filter 'ffmpeg.exe' -File -ErrorAction SilentlyContinue | Select-Object -First 1
if ($ffmpegExe) {
    $settings.yt_dlp.ffmpeg_location = $ffmpegExe.DirectoryName
}
$node = Get-Command node -ErrorAction SilentlyContinue
if ($node) {
    $settings.yt_dlp.js_runtime = "node:$($node.Source)"
}

$settings | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $configPath -Encoding UTF8
Write-Output "Created config: $configPath"
Write-Output "Listener endpoint: http://$($settings.server.host):$($settings.server.port)"
Write-Output 'A random extension token was stored in server.auth_token.'
Write-Output 'Copy it from the config file into the Chrome extension options page.'

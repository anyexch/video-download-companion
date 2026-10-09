[CmdletBinding()]
param(
    [string]$Config = (Join-Path $env:LOCALAPPDATA 'YouTubeYtDlpBridge\config.json')
)

$ErrorActionPreference = 'Stop'
$configPath = [System.IO.Path]::GetFullPath(
    $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Config)
)
if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    throw "Config not found: $configPath"
}

$settings = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $settings.yt_dlp.platforms) {
    $settings.yt_dlp | Add-Member -MemberType NoteProperty -Name platforms -Value ([PSCustomObject]@{})
}
$legacyProxy = [string]$settings.yt_dlp.proxy
foreach ($platform in @('youtube', 'bilibili')) {
    if (-not $settings.yt_dlp.platforms.$platform) {
        $settings.yt_dlp.platforms | Add-Member -MemberType NoteProperty -Name $platform -Value ([PSCustomObject]@{})
    }
    if ($settings.yt_dlp.platforms.$platform.PSObject.Properties.Name -contains 'cookies_from_browser') {
        $settings.yt_dlp.platforms.$platform.cookies_from_browser = 'chrome'
    }
    else {
        $settings.yt_dlp.platforms.$platform | Add-Member -MemberType NoteProperty -Name cookies_from_browser -Value 'chrome'
    }
    $fallback = 'firefox'
    if ($settings.yt_dlp.platforms.$platform.PSObject.Properties.Name -contains 'cookies_fallback_from_browser') {
        $settings.yt_dlp.platforms.$platform.cookies_fallback_from_browser = $fallback
    }
    else {
        $settings.yt_dlp.platforms.$platform | Add-Member -MemberType NoteProperty -Name cookies_fallback_from_browser -Value $fallback
    }
    $platformProxy = if ($platform -eq 'youtube') { $legacyProxy } else { '' }
    if ($settings.yt_dlp.platforms.$platform.PSObject.Properties.Name -contains 'proxy') {
        $settings.yt_dlp.platforms.$platform.proxy = $platformProxy
    }
    else {
        $settings.yt_dlp.platforms.$platform | Add-Member -MemberType NoteProperty -Name proxy -Value $platformProxy
    }
}
$settings.yt_dlp.cookies_from_browser = 'chrome'
$settings.yt_dlp.proxy = ''

if (-not ($settings.downloads.PSObject.Properties.Name -contains 'database_file')) {
    $settings.downloads | Add-Member -MemberType NoteProperty -Name database_file -Value (Join-Path $env:LOCALAPPDATA 'YouTubeYtDlpBridge\jobs.db')
}

$scheduler = [PSCustomObject]@{
    max_total_concurrent = 3
    max_ytdlp_concurrent = 2
    max_douk_concurrent = 1
    max_attempts = 2
    retry_delay_seconds = 3
}
if ($settings.PSObject.Properties.Name -contains 'scheduler') {
    $settings.scheduler = $scheduler
}
else {
    $settings | Add-Member -MemberType NoteProperty -Name scheduler -Value $scheduler
}

$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$douk = [PSCustomObject]@{
    root = (Join-Path $env:USERPROFILE 'Downloads\DouK-Downloader_V5.7_Windows_X64 (2)')
    wrapper = (Join-Path $projectRoot 'listener\download_douyin.ps1')
    output_directory = (Join-Path $env:USERPROFILE 'Downloads\Video Downloads\Douyin')
    powershell_executable = 'powershell.exe'
    refresh_cookie_from_browser = $false
    browser = 'Firefox'
    expected_sha256 = '2E2A6E80F1298CFBB95D6E4D64461FEB625853C84C498EDE5781D7EB4BAA904D'
}
if ($settings.PSObject.Properties.Name -contains 'douk') {
    $settings.douk = $douk
}
else {
    $settings | Add-Member -MemberType NoteProperty -Name douk -Value $douk
}

$backupPath = "$configPath.v030.backup"
if (-not (Test-Path -LiteralPath $backupPath)) {
    Copy-Item -LiteralPath $configPath -Destination $backupPath
}
$utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText(
    $configPath,
    ($settings | ConvertTo-Json -Depth 12),
    $utf8WithoutBom
)

Write-Output "Migrated config: $configPath"
Write-Output 'YouTube browser cookies: chrome'
Write-Output 'Bilibili browser cookies: chrome'
Write-Output 'Chrome-lock fallbacks: YouTube=firefox, Bilibili=anonymous'
Write-Output 'Douyin downloader: DouK without per-download Cookie refresh; use Companion manual refresh'
Write-Output 'Scheduler: total=3, yt-dlp=2, DouK=1, durable SQLite queue enabled'
Write-Output "Backup: $backupPath"

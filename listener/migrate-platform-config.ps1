[CmdletBinding()]
param(
    [string]$Config = (Join-Path $env:LOCALAPPDATA 'YouTubeYtDlpBridge\config.json')
)

$ErrorActionPreference = 'Stop'
$configPath = [System.IO.Path]::GetFullPath($ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Config))
if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    throw "Config not found: $configPath"
}

$settings = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($settings.yt_dlp.platforms) {
    Write-Output 'Platform cookie configuration already exists; no changes made.'
    exit 0
}

$legacyCookie = [string]$settings.yt_dlp.cookies_from_browser
$legacyProxy = [string]$settings.yt_dlp.proxy
$platforms = [pscustomobject]@{
    youtube = [pscustomobject]@{ cookies_from_browser = $legacyCookie; cookies_fallback_from_browser = 'firefox'; proxy = $legacyProxy }
    bilibili = [pscustomobject]@{ cookies_from_browser = 'chrome'; cookies_fallback_from_browser = 'firefox'; proxy = '' }
}
$settings.yt_dlp | Add-Member -MemberType NoteProperty -Name platforms -Value $platforms
$settings.yt_dlp.proxy = ''

$temporaryPath = "$configPath.tmp-$PID"
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($temporaryPath, ($settings | ConvertTo-Json -Depth 12), $utf8NoBom)
[System.IO.File]::Replace($temporaryPath, $configPath, "$configPath.backup", $true)
Write-Output 'Added separate YouTube and Bilibili cookie and proxy settings.'
Write-Output 'YouTube inherited the previous proxy; Bilibili defaults to direct connection.'
Write-Output "Backup: $configPath.backup"

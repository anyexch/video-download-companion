[CmdletBinding()]
param(
    [string]$Config = (Join-Path $env:LOCALAPPDATA 'YouTubeYtDlpBridge\config.json')
)

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$startScript = Join-Path $scriptDir 'start-listener.ps1'
$startupDir = [Environment]::GetFolderPath('Startup')
$shortcutPath = Join-Path $startupDir 'YouTube yt-dlp Listener.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = 'powershell.exe'
$shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$startScript`" -Config `"$Config`" -Background"
$shortcut.WorkingDirectory = $scriptDir
$shortcut.Description = 'Start the loopback YouTube yt-dlp listener'
$shortcut.Save()
Write-Output "Installed startup shortcut: $shortcutPath"

[CmdletBinding()]
param(
    [string]$Config = (Join-Path $env:LOCALAPPDATA 'YouTubeYtDlpBridge\config.json'),
    [switch]$Background
)

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$listener = Join-Path $scriptDir 'ytdlp_listener.py'
$configPath = [System.IO.Path]::GetFullPath($ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Config))

if (-not (Test-Path -LiteralPath $configPath)) {
    throw "Config not found: $configPath. Run setup.ps1 first."
}

& python $listener --config $configPath --check-config
if ($LASTEXITCODE -ne 0) { throw 'Configuration check failed.' }

if ($Background) {
    $stateDir = Split-Path -Parent $configPath
    $stdout = Join-Path $stateDir 'listener.stdout.log'
    $stderr = Join-Path $stateDir 'listener.stderr.log'
    $processArgs = @("`"$listener`"", '--config', "`"$configPath`"")
    $process = Start-Process -FilePath 'python.exe' -ArgumentList $processArgs -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
    Start-Sleep -Milliseconds 800
    if ($process.HasExited) { throw "Listener exited with code $($process.ExitCode). See $stderr" }
    Write-Output "Listener started in background. PID=$($process.Id)"
    Write-Output "Logs: $stderr"
} else {
    & python $listener --config $configPath
}

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateNotNullOrEmpty()]
    [string[]]$Urls,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$DouKRoot,

    [Parameter()]
    [switch]$RefreshCookieFromBrowser,

    [Parameter()]
    [ValidateSet('Arc', 'Brave', 'Chrome', 'Chromium', 'Edge', 'Firefox', 'LibreWolf', 'Opera', 'OperaGX', 'Vivaldi')]
    [string]$Browser = 'Chrome',

    [Parameter()]
    [ValidatePattern('^[A-Fa-f0-9]{64}$')]
    [string]$ExpectedSha256 = '2E2A6E80F1298CFBB95D6E4D64461FEB625853C84C498EDE5781D7EB4BAA904D'
)

$ErrorActionPreference = 'Stop'
$OutputEncoding = [System.Text.Encoding]::GetEncoding(936)
[Console]::OutputEncoding = [System.Text.Encoding]::GetEncoding(936)

if ($Urls.Count -gt 20) {
    throw 'A single run is limited to 20 URLs.'
}

$canonicalPattern = '^https://(?:www\.)?douyin\.com/(?:video|note)/\d{15,22}/?(?:[?#].*)?$'
$sharePattern = '^https://v\.douyin\.com/[A-Za-z0-9_-]+/?(?:[?#].*)?$'
foreach ($url in $Urls) {
    if ($url.Contains("`r") -or $url.Contains("`n")) {
        throw "URL contains a line break: $url"
    }
    if (($url -notmatch $canonicalPattern) -and ($url -notmatch $sharePattern)) {
        throw "Unsupported Douyin URL: $url"
    }
}

if (-not (Test-Path -LiteralPath $DouKRoot -PathType Container)) {
    throw "DouK-Downloader directory is missing: $DouKRoot"
}

$resolvedRoot = (Resolve-Path -LiteralPath $DouKRoot).Path
$executablePath = Join-Path $resolvedRoot 'main.exe'
$volumePath = Join-Path $resolvedRoot '_internal\Volume'
if (Test-Path -LiteralPath (Join-Path $resolvedRoot 'Volume\settings.json')) {
    $volumePath = Join-Path $resolvedRoot 'Volume'
}
$settingsPath = Join-Path $volumePath 'settings.json'
$downloadPath = Join-Path $volumePath 'Download'
foreach ($requiredPath in @($executablePath, $settingsPath)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "Required DouK-Downloader path is missing: $requiredPath"
    }
}
New-Item -ItemType Directory -Path $downloadPath -Force | Out-Null

$sha256 = [System.Security.Cryptography.SHA256]::Create()
try {
    $executableHash = [BitConverter]::ToString($sha256.ComputeHash([IO.File]::ReadAllBytes($executablePath))).Replace('-', '')
} finally { $sha256.Dispose() }
if ($ExpectedSha256 -and $executableHash -ne $ExpectedSha256.ToUpperInvariant()) {
    throw "DouK-Downloader hash changed. Expected $ExpectedSha256 but found $executableHash"
}

$before = @{}
Get-ChildItem -LiteralPath $downloadPath -Recurse -File | ForEach-Object {
    $before[$_.FullName] = "$($_.Length):$($_.LastWriteTimeUtc.Ticks)"
}

$menuInput = @()
$cookieRefreshSucceeded = $false
if ($RefreshCookieFromBrowser) {
    # 5.8 menu 2 reads the clipboard, so refresh through the version-aware helper.
    $refreshHelper = Join-Path $PSScriptRoot 'refresh_douk_cookie.ps1'
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $refreshHelper -DouKRoot $resolvedRoot -Browser $Browser -ExpectedSha256 $ExpectedSha256
    if ($LASTEXITCODE -ne 0) { throw 'Browser cookie refresh failed; download was not started.' }
    $cookieRefreshSucceeded = $true
}
$menuInput += @(
    '5'
    '2'
    '1'
    ($Urls -join ' ')
    'Q'
    'Q'
)

$programOutput = @()
$exitCode = 1
Push-Location $resolvedRoot
try {
    $menuInput | & $executablePath 2>&1 | ForEach-Object {
        $line = "$_"
        $programOutput += $line
        Write-Output $line
    }
    $exitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}

$newFiles = @()
Get-ChildItem -LiteralPath $downloadPath -Recurse -File | ForEach-Object {
    $fingerprint = "$($_.Length):$($_.LastWriteTimeUtc.Ticks)"
    if ((-not $before.ContainsKey($_.FullName)) -or ($before[$_.FullName] -ne $fingerprint)) {
        $newFiles += [PSCustomObject]@{
            path = $_.FullName
            bytes = $_.Length
            last_write_time = $_.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss')
        }
    }
}

$joinedOutput = $programOutput -join "`n"
$reportedFailure = $joinedOutput -match 'Traceback|ERROR|ConnectionError|TimeoutError'
$cookieRefreshFailed = $false

$status = if (($exitCode -ne 0) -or $reportedFailure) {
    'failed'
}
elseif ($newFiles.Count -gt 0) {
    'downloaded'
}
else {
    'no-new-files'
}

$result = [PSCustomObject]@{
    status = $status
    exit_code = $exitCode
    requested_urls = $Urls
    executable = $executablePath
    executable_sha256 = $executableHash
    download_directory = $downloadPath
    cookie_refresh_requested = [bool]$RefreshCookieFromBrowser
    cookie_browser = if ($RefreshCookieFromBrowser) { $Browser } else { '' }
    cookie_refresh_succeeded = [bool]$cookieRefreshSucceeded
    cookie_refresh_failed = [bool]$cookieRefreshFailed
    new_file_count = $newFiles.Count
    new_total_bytes = [long](($newFiles | Measure-Object -Property bytes -Sum).Sum)
    new_files = $newFiles
}

$resultJson = $result | ConvertTo-Json -Depth 5 -Compress
Write-Output ('DOUK_RESULT_JSON=' + $resultJson)
if ($status -eq 'failed') {
    exit 1
}

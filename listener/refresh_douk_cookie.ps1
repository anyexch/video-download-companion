[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$DouKRoot,

    [Parameter()]
    [ValidateSet('Arc', 'Brave', 'Chrome', 'Chromium', 'Edge', 'Firefox', 'LibreWolf', 'Opera', 'OperaGX', 'Vivaldi')]
    [string]$Browser = 'Chrome',

    [Parameter()]
    [ValidatePattern('^[A-Fa-f0-9]{64}$')]
    [string]$ExpectedSha256 = '2E2A6E80F1298CFBB95D6E4D64461FEB625853C84C498EDE5781D7EB4BAA904D',

    [Parameter()]
    [string]$ResultFile = ''
)

$ErrorActionPreference = 'Stop'
$OutputEncoding = [System.Text.Encoding]::GetEncoding(936)
[Console]::OutputEncoding = [System.Text.Encoding]::GetEncoding(936)

function Get-Sha256([string]$Path) {
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString($algorithm.ComputeHash([IO.File]::ReadAllBytes($Path))).Replace('-', '')
    } finally { $algorithm.Dispose() }
}

function Save-Result {
    param([string]$Status, [string]$Message, [int]$ExitCode)
    $result = [ordered]@{
        status = $Status
        message = $Message
        browser = $Browser
        method = 'douk-native-menu'
        time = (Get-Date).ToUniversalTime().ToString('o')
    }
    if ($ResultFile) {
        $parent = Split-Path -Parent $ResultFile
        if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
        $result | ConvertTo-Json -Compress | Set-Content -LiteralPath $ResultFile -Encoding UTF8
    }
    Write-Output ($result | ConvertTo-Json -Compress)
    exit $ExitCode
}

try {
    $needsAdministrator = $Browser -in @('Chrome', 'Chromium', 'Edge')
    if ($needsAdministrator) {
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
        $principal = New-Object Security.Principal.WindowsPrincipal($identity)
        if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
            throw "Administrator permission is required for DouK to read $Browser cookies on Windows."
        }
    }

    $root = (Resolve-Path -LiteralPath $DouKRoot).Path
    $executable = Join-Path $root 'main.exe'
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
        throw "DouK main.exe was not found: $executable"
    }
    $actualHash = Get-Sha256 $executable
    if ($ExpectedSha256 -and $actualHash -ne $ExpectedSha256.ToUpperInvariant()) {
        throw "DouK verification failed. Expected $ExpectedSha256 but found $actualHash"
    }

    $targetSettingsPath = Join-Path $root 'Volume\settings.json'
    $usesLegacyReader = Test-Path -LiteralPath $targetSettingsPath -PathType Leaf
    if ($usesLegacyReader) {
        # 5.8 removed browser extraction. Retain the verified 5.7 native reader
        # solely for this operation, then transfer only the Douyin cookie.
        $root = Join-Path (Split-Path -Parent $root) 'douk-5.7-cookie-reader'
        $executable = Join-Path $root 'main.exe'
        if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
            throw 'The verified 5.7 browser-cookie helper is missing. Use DouK manual cookie entry.'
        }
        if ((Get-Sha256 $executable) -ne '2E2A6E80F1298CFBB95D6E4D64461FEB625853C84C498EDE5781D7EB4BAA904D') {
            throw 'The legacy browser-cookie helper failed SHA256 verification.'
        }
    }
    $settingsPath = Join-Path $root '_internal\Volume\settings.json'
    $settingsExistedBefore = Test-Path -LiteralPath $settingsPath -PathType Leaf
    $settingsHashBefore = if ($settingsExistedBefore) {
        Get-Sha256 $settingsPath
    } else { '' }
    $settingsWriteBefore = if ($settingsExistedBefore) {
        (Get-Item -LiteralPath $settingsPath).LastWriteTimeUtc.Ticks
    } else { 0 }

    $output = @()
    $browserSelections = @{
        Arc = '1'
        Chrome = '2'
        Chromium = '3'
        Opera = '4'
        OperaGX = '5'
        Brave = '6'
        Edge = '7'
        Vivaldi = '8'
        Firefox = '9'
        LibreWolf = '10'
    }
    $browserSelection = $browserSelections[$Browser]
    Push-Location $root
    try {
        # DouK 5.7 main menu: 2 = read Douyin cookies; Firefox = 9.
        @('2', $browserSelection, 'Q') | & $executable 2>&1 | ForEach-Object {
            $output += "$_"
        }
        $processExit = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }

    $joined = $output -join "`n"
    $settingsExistsAfter = Test-Path -LiteralPath $settingsPath -PathType Leaf
    $settingsHashAfter = if ($settingsExistsAfter) {
        Get-Sha256 $settingsPath
    } else { '' }
    $settingsWriteAfter = if ($settingsExistsAfter) {
        (Get-Item -LiteralPath $settingsPath).LastWriteTimeUtc.Ticks
    } else { 0 }
    $settingsUpdated = $settingsExistsAfter -and (
        (-not $settingsExistedBefore) -or
        ($settingsHashAfter -ne $settingsHashBefore) -or
        ($settingsWriteAfter -gt $settingsWriteBefore)
    )

    if ($processExit -ne 0) {
        Save-Result 'failed' "DouK exited with code $processExit. Companion did not modify the saved cookie." 1
    }
    if ($joined -match 'Failed to read Cookie|Cookie data is empty|No cookies found|读取 Cookie 失败|Cookie 数据为空') {
        Save-Result 'failed' "DouK could not read a $Browser cookie. Companion did not modify the saved cookie." 1
    }
    if ($settingsUpdated -or $joined -match 'Cookie read successfully|Successfully read Cookie|读取 Cookie 成功') {
        $readerSettings = [IO.File]::ReadAllText($settingsPath) | ConvertFrom-Json
        if (-not $readerSettings.cookie -or ($readerSettings.cookie | ConvertTo-Json -Compress) -in @('{}', '""', 'null')) {
            throw 'DouK returned an empty cookie; the active settings were not modified.'
        }
        if ($usesLegacyReader) {
            $activeSettings = [IO.File]::ReadAllText($targetSettingsPath) | ConvertFrom-Json
            $activeSettings.cookie = $readerSettings.cookie
            $temporaryPath = $targetSettingsPath + '.refresh-' + [Guid]::NewGuid().ToString('N') + '.tmp'
            [IO.File]::WriteAllText($temporaryPath, ($activeSettings | ConvertTo-Json -Depth 100), (New-Object Text.UTF8Encoding($false)))
            [IO.File]::Replace($temporaryPath, $targetSettingsPath, ($targetSettingsPath + '.before-cookie-refresh.bak'))
        }
        Save-Result 'completed' "DouK refreshed its cookie from $Browser." 0
    }
    Save-Result 'unknown' 'DouK exited without updating settings.json. Try the same browser-cookie command in DouK manually.' 2
}
catch {
    Save-Result 'failed' $_.Exception.Message 1
}
